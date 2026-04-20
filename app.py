import asyncio
import csv
import io
import logging
import math
import os
import random
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
from PIL import Image, UnidentifiedImageError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path("/data")
INDEX_HTML = BASE_DIR / "index.html"

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
}

LOGGER = logging.getLogger("imgtour")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# Strict UUID4 pattern — prevents path traversal via ../ in tournament UUID
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I
)


def is_valid_uuid(value: str) -> bool:
    return bool(_UUID4_RE.match(value))


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def compute_total_rounds(total_images: int) -> int:
    if total_images <= 0:
        return 0
    if total_images == 1:
        return 1
    return math.ceil(math.log2(total_images))


def compute_score(round_reached: int, total_rounds: int) -> float:
    if total_rounds <= 0:
        return 0.0
    return round(round_reached / total_rounds, 3)


def parse_image_folders() -> list[Path]:
    raw = os.getenv("IMAGE_FOLDERS", "/images").strip()
    if not raw:
        return []
    folders: list[Path] = []
    for part in raw.replace(";", ",").split(","):
        value = part.strip()
        if not value:
            continue
        folders.append(Path(value).expanduser().resolve())
    return folders


def db_path_for_uuid(tournament_uuid: str) -> Path:
    if not is_valid_uuid(tournament_uuid):
        raise HTTPException(status_code=400, detail="Invalid tournament UUID")
    return DATA_DIR / f"tournament_{tournament_uuid}.db"


def extract_uuid_from_db_path(path: Path) -> str | None:
    prefix = "tournament_"
    suffix = ".db"
    if not path.name.startswith(prefix) or not path.name.endswith(suffix):
        return None
    return path.name[len(prefix) : -len(suffix)]


async def init_db(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS tournaments (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('ACTIVE','COMPLETE')),
            image_folder TEXT NOT NULL,
            total_images INTEGER NOT NULL,
            total_rounds INTEGER NOT NULL,
            current_round INTEGER NOT NULL DEFAULT 1,
            last_match_id INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id TEXT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
            round INTEGER NOT NULL,
            image_a_path TEXT NOT NULL,
            image_b_path TEXT NOT NULL,
            winner_path TEXT,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id TEXT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
            image_path TEXT NOT NULL UNIQUE,
            round_reached INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0.0
        );

        CREATE INDEX IF NOT EXISTS idx_matches_tournament ON matches(tournament_id);
        CREATE INDEX IF NOT EXISTS idx_matches_trw ON matches(tournament_id, round, winner_path);
        CREATE INDEX IF NOT EXISTS idx_images_tournament ON images(tournament_id);
        CREATE INDEX IF NOT EXISTS idx_images_tournament_round ON images(tournament_id, round_reached);
        """
    )
    await db.commit()


@asynccontextmanager
async def open_db(tournament_uuid: str) -> Any:
    path = db_path_for_uuid(tournament_uuid)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Tournament not found")
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


def is_supported_extension(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_within_roots(candidate: Path, roots: list[Path]) -> bool:
    try:
        resolved = candidate.resolve()
    except FileNotFoundError:
        resolved = candidate
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def validate_image_file(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (FileNotFoundError, PermissionError, UnidentifiedImageError, OSError) as exc:
        LOGGER.warning("Skipping invalid image %s: %s", path, exc)
        return False


def scan_images(image_roots: list[Path]) -> tuple[list[str], str]:
    valid_images: list[str] = []
    used_roots: list[str] = []

    for root in image_roots:
        if not root.exists() or not root.is_dir():
            LOGGER.warning("Image folder missing or not a directory: %s", root)
            continue
        used_roots.append(str(root))
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            if not is_supported_extension(file_path):
                continue
            if validate_image_file(file_path):
                valid_images.append(str(file_path.resolve()))

    seen: set[str] = set()
    deduped: list[str] = []
    for image_path in valid_images:
        if image_path in seen:
            continue
        seen.add(image_path)
        deduped.append(image_path)

    return deduped, ",".join(used_roots)


async def fetchone(db: aiosqlite.Connection, query: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
    async with db.execute(query, params) as cursor:
        return await cursor.fetchone()


async def fetchall(db: aiosqlite.Connection, query: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
    async with db.execute(query, params) as cursor:
        return await cursor.fetchall()


async def update_image_score(
    db: aiosqlite.Connection,
    tournament_uuid: str,
    image_path: str,
    round_reached: int,
) -> None:
    tournament = await fetchone(db, "SELECT total_rounds FROM tournaments WHERE id = ?", (tournament_uuid,))
    if tournament is None:
        raise HTTPException(status_code=404, detail="Tournament not found")
    score = compute_score(round_reached, int(tournament["total_rounds"]))
    await db.execute(
        """
        UPDATE images
        SET round_reached = ?, score = ?
        WHERE tournament_id = ? AND image_path = ?
        """,
        (round_reached, score, tournament_uuid, image_path),
    )


async def create_round_matches(
    db: aiosqlite.Connection,
    tournament_uuid: str,
    round_number: int,
    survivors: list[str],
) -> None:
    shuffled = list(survivors)
    random.SystemRandom().shuffle(shuffled)

    if len(shuffled) % 2 == 1:
        bye_image = shuffled.pop()
        image_row = await fetchone(
            db,
            "SELECT round_reached FROM images WHERE tournament_id = ? AND image_path = ?",
            (tournament_uuid, bye_image),
        )
        if image_row is None:
            raise HTTPException(status_code=500, detail="Image record missing for bye")
        await update_image_score(db, tournament_uuid, bye_image, int(image_row["round_reached"]) + 1)

    for index in range(0, len(shuffled), 2):
        await db.execute(
            """
            INSERT INTO matches (tournament_id, round, image_a_path, image_b_path, winner_path, completed_at)
            VALUES (?, ?, ?, ?, NULL, NULL)
            """,
            (tournament_uuid, round_number, shuffled[index], shuffled[index + 1]),
        )


async def collect_round_survivors(
    db: aiosqlite.Connection,
    tournament_uuid: str,
    round_number: int,
) -> list[str]:
    rows = await fetchall(
        db,
        """
        SELECT image_path
        FROM images
        WHERE tournament_id = ? AND round_reached = ?
        ORDER BY image_path
        """,
        (tournament_uuid, round_number),
    )
    return [str(row["image_path"]) for row in rows]


async def get_current_match_row(db: aiosqlite.Connection, tournament_uuid: str, current_round: int) -> aiosqlite.Row | None:
    return await fetchone(
        db,
        """
        SELECT id, tournament_id, round, image_a_path, image_b_path, winner_path, completed_at
        FROM matches
        WHERE tournament_id = ? AND round = ? AND winner_path IS NULL
        ORDER BY id
        LIMIT 1
        """,
        (tournament_uuid, current_round),
    )


def serialize_match(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    # Strip /images/ prefix so frontend can use directly in /api/images/{path}
    def rel(path: str) -> str:
        prefix = "/images/"
        return path[len(prefix):] if path.startswith(prefix) else path

    return {
        "id": row["id"],
        "tournamentId": row["tournament_id"],
        "round": row["round"],
        "imageA": rel(row["image_a_path"]),
        "imageB": rel(row["image_b_path"]),
        "winner": row["winner_path"],
        "completedAt": row["completed_at"],
    }


async def build_tournament_state(db: aiosqlite.Connection, tournament_uuid: str) -> dict[str, Any]:
    tournament = await fetchone(
        db,
        """
        SELECT id, status, total_images, total_rounds, current_round, last_match_id
        FROM tournaments
        WHERE id = ?
        """,
        (tournament_uuid,),
    )
    if tournament is None:
        raise HTTPException(status_code=404, detail="Tournament not found")

    completed_row = await fetchone(
        db,
        "SELECT COUNT(*) AS count FROM matches WHERE tournament_id = ? AND winner_path IS NOT NULL",
        (tournament_uuid,),
    )
    completed_matches = int(completed_row["count"]) if completed_row else 0

    total_matches = max(int(tournament["total_images"]) - 1, 0)
    current_round = int(tournament["current_round"])
    current_match = None
    current_match_id = None

    if tournament["status"] == "ACTIVE":
        current_match_row = await get_current_match_row(db, tournament_uuid, current_round)
        current_match = serialize_match(current_match_row)
        current_match_id = current_match["id"] if current_match else None

    if total_matches == 0:
        current_match_index = 0
    elif tournament["status"] == "COMPLETE":
        current_match_index = total_matches
    else:
        current_match_index = min(completed_matches + 1, total_matches)

    return {
        "uuid": tournament["id"],
        "status": tournament["status"],
        "totalImages": tournament["total_images"],
        "totalRounds": tournament["total_rounds"],
        "totalMatches": total_matches,
        "currentRound": tournament["current_round"],
        "currentMatchIndex": current_match_index,
        "currentMatchId": current_match_id,
        "lastMatchId": tournament["last_match_id"],
        "currentMatch": current_match,
    }


async def create_tournament_from_folders(image_roots: list[Path]) -> str | None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # scan_images is fully synchronous — run on thread pool to avoid blocking the event loop
    image_paths, image_folder_value = await asyncio.to_thread(scan_images, image_roots)
    if not image_paths:
        LOGGER.warning("No valid images found in IMAGE_FOLDERS")
        return None

    tournament_uuid = str(uuid.uuid4())
    total_images = len(image_paths)
    total_rounds = compute_total_rounds(total_images)
    created_at = utc_now()
    db_path = db_path_for_uuid(tournament_uuid)

    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    try:
        await init_db(db)
        await db.execute(
            """
            INSERT INTO tournaments (
                id, status, image_folder, total_images, total_rounds, current_round, last_match_id, created_at, completed_at
            )
            VALUES (?, 'ACTIVE', ?, ?, ?, 1, NULL, ?, NULL)
            """,
            (tournament_uuid, image_folder_value, total_images, total_rounds, created_at),
        )
        await db.executemany(
            """
            INSERT INTO images (tournament_id, image_path, round_reached, wins, score)
            VALUES (?, ?, 0, 0, 0.0)
            """,
            [(tournament_uuid, image_path) for image_path in image_paths],
        )

        if total_images == 1:
            only_image = image_paths[0]
            await update_image_score(db, tournament_uuid, only_image, total_rounds)
            await db.execute(
                """
                UPDATE tournaments
                SET status = 'COMPLETE', current_round = ?, completed_at = ?
                WHERE id = ?
                """,
                (total_rounds, utc_now(), tournament_uuid),
            )
        else:
            await create_round_matches(db, tournament_uuid, 1, image_paths)

        await db.commit()
        LOGGER.info("Created tournament %s with %s images", tournament_uuid, total_images)
        return tournament_uuid
    except Exception:
        LOGGER.exception("Failed to create tournament %s", tournament_uuid)
        if db_path.exists():
            db_path.unlink(missing_ok=True)
        raise
    finally:
        await db.close()


async def find_active_tournament_uuid() -> str | None:
    if not DATA_DIR.exists():
        return None

    active: list[tuple[str, str]] = []
    for path in sorted(DATA_DIR.glob("tournament_*.db")):
        tournament_uuid = extract_uuid_from_db_path(path)
        if not tournament_uuid:
            continue
        try:
            db = await aiosqlite.connect(path)
            db.row_factory = aiosqlite.Row
            row = await fetchone(
                db,
                """
                SELECT id, created_at
                FROM tournaments
                WHERE status = 'ACTIVE'
                ORDER BY created_at DESC
                LIMIT 1
                """,
            )
            await db.close()
        except Exception as exc:
            LOGGER.warning("Skipping unreadable database %s: %s", path, exc)
            continue
        if row:
            active.append((str(row["created_at"]), str(row["id"])))

    if not active:
        return None

    active.sort(reverse=True)
    return active[0][1]


async def list_tournaments_metadata() -> list[dict[str, Any]]:
    tournaments: list[dict[str, Any]] = []
    if not DATA_DIR.exists():
        return tournaments

    for path in sorted(DATA_DIR.glob("tournament_*.db")):
        tournament_uuid = extract_uuid_from_db_path(path)
        if not tournament_uuid:
            continue
        try:
            db = await aiosqlite.connect(path)
            db.row_factory = aiosqlite.Row
            row = await fetchone(
                db,
                """
                SELECT id, status, total_images, total_rounds, current_round, last_match_id, created_at, completed_at
                FROM tournaments
                LIMIT 1
                """,
            )
            await db.close()
        except Exception as exc:
            LOGGER.warning("Skipping unreadable database %s: %s", path, exc)
            continue
        if row:
            tournaments.append(
                {
                    "uuid": row["id"],
                    "status": row["status"],
                    "totalImages": row["total_images"],
                    "totalRounds": row["total_rounds"],
                    "currentRound": row["current_round"],
                    "lastMatchId": row["last_match_id"],
                    "createdAt": row["created_at"],
                    "completedAt": row["completed_at"],
                }
            )
    tournaments.sort(key=lambda item: item["createdAt"], reverse=True)
    return tournaments


async def lifespan(app):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    active_uuid = await find_active_tournament_uuid()
    if active_uuid:
        app.state.active_uuid = active_uuid
        LOGGER.info("Resuming active tournament %s", active_uuid)
    else:
        created_uuid = await create_tournament_from_folders(app.state.image_roots)
        app.state.active_uuid = created_uuid
        if created_uuid:
            LOGGER.info("Started new tournament %s on startup", created_uuid)
        else:
            LOGGER.info("No tournament created on startup")
    yield


async def homepage(_: Request) -> Response:
    if not INDEX_HTML.exists():
        raise HTTPException(status_code=500, detail="index.html not found")
    return FileResponse(INDEX_HTML)


async def list_tournaments(_: Request) -> Response:
    tournaments = await list_tournaments_metadata()
    active_uuid = next((item["uuid"] for item in tournaments if item["status"] == "ACTIVE"), None)
    return JSONResponse({"active_uuid": active_uuid, "tournaments": tournaments})


async def create_tournament(_: Request) -> Response:
    async with app.state.lock:
        active_uuid = await find_active_tournament_uuid()
        if active_uuid:
            return JSONResponse({"active_uuid": active_uuid}, status_code=409)

        tournament_uuid = await create_tournament_from_folders(app.state.image_roots)
        app.state.active_uuid = tournament_uuid

    if not tournament_uuid:
        raise HTTPException(status_code=400, detail="No valid images found in IMAGE_FOLDERS")

    async with open_db(tournament_uuid) as db:
        state = await build_tournament_state(db, tournament_uuid)
    return JSONResponse(state, status_code=201)


async def get_tournament(request: Request) -> Response:
    tournament_uuid = request.path_params["uuid"]
    async with open_db(tournament_uuid) as db:
        state = await build_tournament_state(db, tournament_uuid)
    return JSONResponse(state)


async def get_match(request: Request) -> Response:
    match_id = int(request.path_params["match_id"])

    async def _find_match() -> aiosqlite.Row | None:
        # Fast path: check active tournament DB first (common case)
        active_uuid = app.state.active_uuid
        if active_uuid:
            active_path = db_path_for_uuid(active_uuid)
            if active_path.exists():
                db = await aiosqlite.connect(active_path)
                db.row_factory = aiosqlite.Row
                try:
                    row = await fetchone(
                        db,
                        """
                        SELECT id, tournament_id, round, image_a_path, image_b_path, winner_path, completed_at
                        FROM matches
                        WHERE id = ?
                        """,
                        (match_id,),
                    )
                    if row:
                        return row
                finally:
                    await db.close()
        # Slow path: scan all tournament DBs
        for path in sorted(DATA_DIR.glob("tournament_*.db")):
            db = await aiosqlite.connect(path)
            db.row_factory = aiosqlite.Row
            try:
                row = await fetchone(
                    db,
                    """
                    SELECT id, tournament_id, round, image_a_path, image_b_path, winner_path, completed_at
                    FROM matches
                    WHERE id = ?
                    """,
                    (match_id,),
                )
                if row:
                    return row
            finally:
                await db.close()
        return None

    row = await _find_match()
    if row is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return JSONResponse(serialize_match(row))


async def record_match_result(request: Request) -> Response:
    match_id = int(request.path_params["match_id"])
    payload = await request.json()
    winner = payload.get("winner")
    if not isinstance(winner, str) or not winner:
        raise HTTPException(status_code=400, detail="winner is required")

    async with app.state.lock:
        match_row = None
        target_db_path = None
        # Fast path: check active tournament DB first
        active_uuid = app.state.active_uuid
        if active_uuid:
            active_path = db_path_for_uuid(active_uuid)
            if active_path.exists():
                db = await aiosqlite.connect(active_path)
                db.row_factory = aiosqlite.Row
                try:
                    row = await fetchone(
                        db,
                        """
                        SELECT id, tournament_id, round, image_a_path, image_b_path, winner_path, completed_at
                        FROM matches
                        WHERE id = ?
                        """,
                        (match_id,),
                    )
                    if row:
                        match_row = row
                        target_db_path = active_path
                finally:
                    await db.close()
        # Slow path: scan all tournament DBs if not found in active
        if match_row is None:
            for path in sorted(DATA_DIR.glob("tournament_*.db")):
                db = await aiosqlite.connect(path)
                db.row_factory = aiosqlite.Row
                try:
                    row = await fetchone(
                        db,
                        """
                        SELECT id, tournament_id, round, image_a_path, image_b_path, winner_path, completed_at
                        FROM matches
                        WHERE id = ?
                        """,
                        (match_id,),
                    )
                    if row:
                        match_row = row
                        target_db_path = path
                        break
                finally:
                    await db.close()

        if match_row is None or target_db_path is None:
            raise HTTPException(status_code=404, detail="Match not found")

        db = await aiosqlite.connect(target_db_path)
        db.row_factory = aiosqlite.Row
        try:
            tournament_uuid = str(match_row["tournament_id"])
            tournament = await fetchone(
                db,
                """
                SELECT id, status, current_round, total_rounds
                FROM tournaments
                WHERE id = ?
                """,
                (tournament_uuid,),
            )
            if tournament is None:
                raise HTTPException(status_code=404, detail="Tournament not found")
            if tournament["status"] != "ACTIVE":
                raise HTTPException(status_code=409, detail="Tournament is complete")

            # Normalize winner to full path — frontend sends relative (photo.jpg) but DB stores full (/images/photo.jpg)
            if not winner.startswith("/"):
                winner = f"/images/{winner}"
            if winner not in {match_row["image_a_path"], match_row["image_b_path"]}:
                raise HTTPException(status_code=400, detail="winner must match one of the images")

            if match_row["winner_path"] is not None:
                state = await build_tournament_state(db, tournament_uuid)
                return JSONResponse({"match": serialize_match(match_row), "tournament": state})

            now = utc_now()
            await db.execute(
                """
                UPDATE matches
                SET winner_path = ?, completed_at = ?
                WHERE id = ?
                """,
                (winner, now, match_id),
            )

            image_row = await fetchone(
                db,
                "SELECT round_reached, wins FROM images WHERE tournament_id = ? AND image_path = ?",
                (tournament_uuid, winner),
            )
            if image_row is None:
                raise HTTPException(status_code=500, detail="Winner image record missing")

            await db.execute(
                """
                UPDATE images
                SET wins = wins + 1
                WHERE tournament_id = ? AND image_path = ?
                """,
                (tournament_uuid, winner),
            )
            await update_image_score(db, tournament_uuid, winner, int(image_row["round_reached"]) + 1)

            await db.execute(
                "UPDATE tournaments SET last_match_id = ? WHERE id = ?",
                (match_id, tournament_uuid),
            )

            round_number = int(match_row["round"])
            pending_row = await fetchone(
                db,
                """
                SELECT COUNT(*) AS count
                FROM matches
                WHERE tournament_id = ? AND round = ? AND winner_path IS NULL
                """,
                (tournament_uuid, round_number),
            )
            pending_count = int(pending_row["count"]) if pending_row else 0

            if pending_count == 0:
                survivors = await collect_round_survivors(db, tournament_uuid, round_number)
                if len(survivors) <= 1:
                    await db.execute(
                        """
                        UPDATE tournaments
                        SET status = 'COMPLETE', current_round = ?, completed_at = ?
                        WHERE id = ?
                        """,
                        (round_number, utc_now(), tournament_uuid),
                    )
                else:
                    next_round = round_number + 1
                    await db.execute(
                        "UPDATE tournaments SET current_round = ? WHERE id = ?",
                        (next_round, tournament_uuid),
                    )
                    await create_round_matches(db, tournament_uuid, next_round, survivors)

            await db.commit()
            fresh_match = await fetchone(
                db,
                """
                SELECT id, tournament_id, round, image_a_path, image_b_path, winner_path, completed_at
                FROM matches
                WHERE id = ?
                """,
                (match_id,),
            )
            state = await build_tournament_state(db, tournament_uuid)
            app.state.active_uuid = tournament_uuid if state["status"] == "ACTIVE" else None
            return JSONResponse({"match": serialize_match(fresh_match), "tournament": state})
        finally:
            await db.close()


async def rollback_generated_future_rounds(
    db: aiosqlite.Connection,
    tournament_uuid: str,
    from_round_exclusive: int,
) -> None:
    future_rounds = await fetchall(
        db,
        """
        SELECT DISTINCT round
        FROM matches
        WHERE tournament_id = ? AND round > ?
        ORDER BY round DESC
        """,
        (tournament_uuid, from_round_exclusive),
    )

    for round_row in future_rounds:
        round_number = int(round_row["round"])
        bye_rows = await fetchall(
            db,
            """
            SELECT image_path, round_reached
            FROM images
            WHERE tournament_id = ? AND round_reached = ?
              AND image_path NOT IN (
                  SELECT image_a_path FROM matches WHERE tournament_id = ? AND round = ?
                  UNION
                  SELECT image_b_path FROM matches WHERE tournament_id = ? AND round = ?
              )
            """,
            (tournament_uuid, round_number, tournament_uuid, round_number, tournament_uuid, round_number),
        )
        for bye_row in bye_rows:
            await update_image_score(
                db,
                tournament_uuid,
                str(bye_row["image_path"]),
                max(int(bye_row["round_reached"]) - 1, 0),
            )

        await db.execute(
            "DELETE FROM matches WHERE tournament_id = ? AND round = ?",
            (tournament_uuid, round_number),
        )


async def undo_last_match(request: Request) -> Response:
    tournament_uuid = request.path_params["uuid"]

    async with app.state.lock:
        async with open_db(tournament_uuid) as db:
            tournament = await fetchone(
                db,
                """
                SELECT id, status, current_round, last_match_id
                FROM tournaments
                WHERE id = ?
                """,
                (tournament_uuid,),
            )
            if tournament is None:
                raise HTTPException(status_code=404, detail="Tournament not found")
            if tournament["last_match_id"] is None:
                raise HTTPException(status_code=409, detail="No match to undo")

            last_match = await fetchone(
                db,
                """
                SELECT id, tournament_id, round, image_a_path, image_b_path, winner_path, completed_at
                FROM matches
                WHERE id = ?
                """,
                (int(tournament["last_match_id"]),),
            )
            if last_match is None or last_match["winner_path"] is None:
                raise HTTPException(status_code=409, detail="No completed match to undo")

            undone_round = int(last_match["round"])
            winner_path = str(last_match["winner_path"])

            await rollback_generated_future_rounds(db, tournament_uuid, undone_round)

            winner_image = await fetchone(
                db,
                "SELECT round_reached, wins FROM images WHERE tournament_id = ? AND image_path = ?",
                (tournament_uuid, winner_path),
            )
            if winner_image is None:
                raise HTTPException(status_code=500, detail="Winner image record missing")

            await db.execute(
                """
                UPDATE images
                SET wins = CASE WHEN wins > 0 THEN wins - 1 ELSE 0 END
                WHERE tournament_id = ? AND image_path = ?
                """,
                (tournament_uuid, winner_path),
            )
            await update_image_score(
                db,
                tournament_uuid,
                winner_path,
                max(int(winner_image["round_reached"]) - 1, 0),
            )

            await db.execute(
                """
                UPDATE matches
                SET winner_path = NULL, completed_at = NULL
                WHERE id = ?
                """,
                (int(last_match["id"]),),
            )

            previous_match = await fetchone(
                db,
                """
                SELECT id
                FROM matches
                WHERE tournament_id = ? AND winner_path IS NOT NULL AND id < ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (tournament_uuid, int(last_match["id"])),
            )

            await db.execute(
                """
                UPDATE tournaments
                SET status = 'ACTIVE',
                    current_round = ?,
                    last_match_id = ?,
                    completed_at = NULL
                WHERE id = ?
                """,
                (undone_round, previous_match["id"] if previous_match else None, tournament_uuid),
            )

            await db.commit()
            state = await build_tournament_state(db, tournament_uuid)
            app.state.active_uuid = tournament_uuid
            return JSONResponse(state)


async def export_tournament(request: Request) -> Response:
    tournament_uuid = request.path_params["uuid"]
    async with open_db(tournament_uuid) as db:
        tournament = await fetchone(
            db,
            "SELECT id FROM tournaments WHERE id = ?",
            (tournament_uuid,),
        )
        if tournament is None:
            raise HTTPException(status_code=404, detail="Tournament not found")

        image_rows = await fetchall(
            db,
            """
            SELECT image_path, score, round_reached, wins
            FROM images
            WHERE tournament_id = ?
            ORDER BY score DESC, wins DESC, image_path ASC
            """,
            (tournament_uuid,),
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["image_path", "score", "round_reached", "wins"])
    for row in image_rows:
        writer.writerow([row["image_path"], row["score"], row["round_reached"], row["wins"]])
    output.seek(0)

    headers = {
        "Content-Disposition": f'attachment; filename="tournament_{tournament_uuid}.csv"'
    }
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)


async def delete_tournament(request: Request) -> Response:
    tournament_uuid = request.path_params["uuid"]
    async with app.state.lock:
        path = db_path_for_uuid(tournament_uuid)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Tournament not found")
        path.unlink(missing_ok=True)
        if app.state.active_uuid == tournament_uuid:
            app.state.active_uuid = await find_active_tournament_uuid()
    return Response(status_code=204)


async def serve_image(request: Request) -> Response:
    raw_path = request.path_params["image_path"]
    image_path = Path(raw_path)
    if not image_path.is_absolute():
        # Relative path — prepend the first image root (e.g. /images from IMAGE_FOLDERS=/images)
        image_path = app.state.image_roots[0] / raw_path.lstrip("/")

    resolved = image_path.resolve(strict=False)
    if not is_within_roots(resolved, app.state.image_roots):
        raise HTTPException(status_code=404, detail="Image not found")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(resolved)


async def health(_: Request) -> Response:
    return PlainTextResponse("ok")


routes = [
    Route("/", homepage),
    Route("/healthz", health),
    Route("/api/tournament", list_tournaments, methods=["GET"]),
    Route("/api/tournament", create_tournament, methods=["POST"]),
    Route("/api/tournament/{uuid}", get_tournament, methods=["GET"]),
    Route("/api/tournament/{uuid}", delete_tournament, methods=["DELETE"]),
    Route("/api/tournament/{uuid}/undo", undo_last_match, methods=["POST"]),
    Route("/api/tournament/{uuid}/export", export_tournament, methods=["GET"]),
    Route("/api/match/{match_id:int}", get_match, methods=["GET"]),
    Route("/api/match/{match_id:int}", record_match_result, methods=["POST"]),
    Route("/api/images/{image_path:path}", serve_image, methods=["GET"]),
]

app = Starlette(routes=routes, lifespan=lifespan)
app.state.image_roots = parse_image_folders()
app.state.active_uuid = None
app.state.lock = asyncio.Lock()