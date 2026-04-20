# imgtour — SPEC.md

## Overview

Image tournament scorer for ML training data. Run a binary tournament bracket over a folder of images, get scored results for dataset ranking.

## Scoring Formula

```
score = round_reached / total_rounds
```

`round_reached` = the last round the image participated in (won or lost). Winner participated in all rounds.

Example (8 images, 3 rounds):
- Winner: 3/3 = 1.000
- Runner-up: 2/3 = 0.667
- Semifinal losers: 1/3 = 0.333
- Quarterfinal losers: 0/3 = 0.000

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tournament/{uuid}` | Get current tournament state (includes all `matches[]` and `images[]`) |
| POST | `/api/tournament` | Create new tournament from image folder(s) |
| POST | `/api/match/{match_id}` | Record match result `{winner: "path"}` (legacy, full engine) |
| POST | `/api/match/{match_id}/vote` | Fire-and-forget vote for client-side tournament engine |
| GET | `/api/match/{match_id}` | Get match details |
| POST | `/api/tournament/{uuid}/undo` | Undo the last match result |
| GET | `/api/tournament/{uuid}/export` | Download CSV results |
| DELETE | `/api/tournament/{uuid}` | Delete tournament and DB |
| GET | `/api/images/{path:filepath}` | Serve original image file |

**Tournament state response:**
```json
{
  "uuid": "abc123",
  "status": "ACTIVE",
  "totalImages": 8,
  "totalRounds": 3,
  "totalMatches": 7,
  "currentMatchIndex": 0,
  "currentMatchId": 1,
  "lastMatchId": null,
  "currentMatch": { "id": 1, "round": 1, "imageA": "photo1.jpg", "imageB": "photo2.jpg", "winner": null },
  "nextMatch": { "id": 2, "round": 1, "imageA": "photo3.jpg", "imageB": "photo4.jpg", "winner": null },
  "matches": [
    { "id": 1, "round": 1, "imageA": "photo1.jpg", "imageB": "photo2.jpg", "winner": null },
    { "id": 2, "round": 1, "imageA": "photo3.jpg", "imageB": "photo4.jpg", "winner": null },
    ...
    { "id": 7, "round": 3, "imageA": "...", "imageB": "...", "winner": null }
  ],
  "images": [
    { "path": "/images/photo1.jpg", "roundReached": 0, "wins": 0 },
    ...
  ]
}
```

All rounds are pre-generated at tournament creation. `matches[]` contains every match for every round. The client scans for the first `winner == null` to find the current match.

## Database Schema (SQLite)

Stored at `/data/tournament_{uuid}.db` in the mounted volume.

```sql
CREATE TABLE tournaments (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'COMPLETE')),
  image_folder TEXT NOT NULL,
  total_images INTEGER NOT NULL,
  total_rounds INTEGER NOT NULL,
  current_round INTEGER NOT NULL DEFAULT 1,
  last_match_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at TEXT
);

CREATE TABLE matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id TEXT NOT NULL REFERENCES tournaments(id),
  round INTEGER NOT NULL,
  image_a_path TEXT NOT NULL,
  image_b_path TEXT NOT NULL,
  winner_path TEXT,
  completed_at TEXT
);

CREATE TABLE images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id TEXT NOT NULL REFERENCES tournaments(id),
  image_path TEXT NOT NULL UNIQUE,
  round_reached INTEGER NOT NULL DEFAULT 0,
  wins INTEGER NOT NULL DEFAULT 0,
  score REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX idx_matches_tournament ON matches(tournament_id);
CREATE INDEX idx_images_tournament ON images(tournament_id);
```

`match_index` removed — matches are generated round-by-round, not pre-paired.

## Tournament State Machine

```
IDLE
  └─ POST /api/tournament → creates DB, all images inserted → GENERATE_ALL_ROUNDS

GENERATE_ALL_ROUNDS
  └─ Loop round 1 to total_rounds: collect survivors, shuffle with seeded PRNG, insert matches → ACTIVE

ACTIVE (client-side tournament engine)
  └─ Client scans matches[] for first winner == null → displays match
  └─ Client vote: optimistic local update, fire-and-forget POST /api/match/{id}/vote
  └─ POST /api/match/{id}/vote: persists winner, updates winner's round_reached/wins/score
  └─ If all matches complete → status = COMPLETE

TOURNAMENT_COMPLETE
  └─ GET /api/tournament/{uuid}/export → CSV
```

**Concurrent write handling:** If two tabs submit the same match, the second submission finds `winner IS NOT NULL` and returns 202 idempotently. No corruption, but the second tab shows stale state. Single-user tool — acceptable.

## Image Handling

- **Formats:** JPEG, PNG, WebP, TIFF, BMP, GIF (via Pillow)
- **HEIC/HEIF:** Requires system libs in Docker — document as supported if libheif installed
- **Unicode filenames:** Store full paths as UTF-8 in SQLite and filesystem. Test with Japanese, CJK, emoji filenames.
- **Serving:** Original files via `/api/images/{path:filepath}` — no preprocessing, no resizing. Browser handles caching.
- **Error handling:** Missing/corrupt file → skip from bracket, log warning, exclude from scoring.

## Bracket Generation

All rounds are pre-generated at tournament creation (not lazy round-by-round). Pairs are randomized using a seeded PRNG (`random.Random(tournament_uuid + round_number)`) so the bracket is deterministic.

For N images (rounds = ceil(log2(N))):

1. Tournament created → all `images` rows inserted with `round_reached=0`
2. All rounds generated in a loop from round 1 to total_rounds:
   - Collect survivors (images with `round_reached == round_num - 1`)
   - Shuffle with seeded PRNG
   - Pair randomly, insert `matches` rows
   - For byes: pop one survivor, increment its `round_reached` directly (no match row)
3. Repeat until one image remains or round generation produces ≤1 survivor

**Deterministic seeding:** The same set of images always produces the same bracket. If the browser crashes and the user reloads, the bracket is identical.

**Client current-match tracking:** The client scans `matches[]` for the first entry where `winner == null`. No round counting needed.

**Bye handling:** If odd number of survivors in a round, one gets a bye. No `matches` row is created for it. Its `round_reached` is incremented directly in the `images` table during bracket generation. The client sees the missing match and skips to the next round.

## Docker Setup

**Dockerfile:**
- Base: `python:3.12-slim`
- Install: Pillow, starlette, uvicorn, aiosqlite
- Expose: 8000
- ENTRYPOINT: `uvicorn app:app --host 0.0.0.0 --port 8000`

**docker-compose.yml:**
```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
      - /path/to/images:/images:ro
    environment:
      - IMAGE_FOLDERS=/images
```

**Startup flow:**
1. Look for `/data/tournament_*.db` with `status='ACTIVE'`
2. If found → resume, return current match
3. If not found → scan `IMAGE_FOLDERS`, create tournament, build bracket, return first match

## Frontend (Vanilla HTML/JS)

Single page at `/` that:
1. Loads and shows current match (two images, click to select winner)
2. On selection: POST result, receive next match, update display
3. On tournament complete: show results summary + download CSV button
4. "Undo" button to reverse the last match (single-step)

No framework. No build step. Pure browser JS.

### Image Loading

Images are loaded directly via `img.src = '/api/images/' + imagePath` on each match update. The browser handles caching automatically.

After each vote response, the next match's images are prefetched via `new Image()` so the browser HTTP cache is warm before the user clicks again. This eliminates perceptible latency on every transition after the first match.

**Initialization:** On page load, if an active tournament exists, the frontend fetches `/api/tournament/{uuid}` which returns full state including `currentMatch` — no second round-trip needed. If no active tournament exists, `POST /api/tournament` creates one and returns full state inline, also skipping the second round-trip. The first images appear with just 1 HTTP request.

### Double-Click Protection

**Frontend:** Both image buttons disabled immediately on first click. Re-enabled on response or timeout (5s).

**Server:** Match results are idempotent. If winner already set, return success without re-writing:
```python
if match.winner is not None:
    return {"status": "already_voted", "currentMatch": next_match}
```

### Undo Flow

`POST /api/tournament/{uuid}/undo`

1. Fetch `last_match_id` from tournament row
2. Clear that match's `winner` and `completed_at`
3. Decrement winner's `round_reached` by 1
4. Decrement winner's `wins` by 1
5. Set tournament `last_match_id` to the previous completed match (or NULL)
6. Return the updated tournament state with new `matches[]` array

Constraint: Only the immediately previous match can be undone. Undo is a single-step operation. Because all rounds are pre-generated, undo simply clears the winner on the match row — no round or match regeneration needed.

## Output Formats

**CSV export** (`/api/tournament/{uuid}/export`):
```csv
image_path,score,round_reached,wins
/images/photo1.jpg,1.000,3,3
/images/photo2.jpg,0.667,2,2
/images/photo3.jpg,0.333,1,1
```

**JSON export** (via `GET /api/tournament/{uuid}`):
Full tournament state as shown above.

## Configuration

Environment variables:
- `IMAGE_FOLDERS` — colon-separated list of image directory paths (default: `/images`)
- `DATA_DIR` — where to store tournament DBs (default: `/data`)
- `EXPORT_FOLDER` — if set, tournament completion triggers copy of all scored images to this folder with `{score}_{filename}` prefix, overwriting existing files (default: none — feature disabled)
- `RESET` — if set to `1` or `true`, wipes all existing tournament databases in DATA_DIR on startup before resuming or creating a tournament (default: none — feature disabled)

No config file. All runtime config via env vars.

## What NOT in Phase 1

- Multi-choice "fast mode" (4, 8, 16 at a time)
- Score-in-filename output
- WebSocket real-time sync
- User authentication
- Multiple simultaneous users (tool is single-user)
- Concurrent session handling (second tab shows stale state — known limitation)

## Open Questions

None resolved in this spec. Deferred to future phases.

## File Structure

```
imgtour/
  app.py              # Starlette app + tournament logic
  schema.sql          # SQLite schema (copied into app.py)
  Dockerfile
  docker-compose.yml
  requirements.txt
  README.md
  SPEC.md             # This file
```

## Next Steps

1. Write app.py — Starlette server, SQLite setup, tournament state machine, API endpoints
2. Write schema.sql (embedded in app.py init)
3. Build frontend — single HTML/JS page with binary selection UI
4. Create Dockerfile + docker-compose.yml
5. Add requirements.txt
6. Test with Unicode filenames
7. Test with HEIC images (document system deps)
