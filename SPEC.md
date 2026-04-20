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
| GET | `/api/tournament/{uuid}` | Get current tournament state |
| POST | `/api/tournament` | Create new tournament from image folder(s) |
| POST | `/api/match/{match_id}` | Record match result `{winner: "path"}` |
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
  "currentRound": 2,
  "currentMatchIndex": 0,
  "images": [...],
  "currentMatch": {
    "id": 5,
    "round": 2,
    "imageA": "/images/photo1.jpg",
    "imageB": "/images/photo2.png",
    "completed": false
  }
}
```

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
  └─ POST /api/tournament → creates DB, all images inserted → GENERATE_ROUND_1

GENERATE_ROUND_1
  └─ Shuffle all images, pair randomly, insert matches rows → MATCH_ACTIVE

MATCH_ACTIVE
  └─ POST /api/match/{id} {winner}
      ├─ Persist winner to matches row
      ├─ Update winner's wins++ and round_reached++
      ├─ Update last_match_id in tournament row
      ├─ If all matches in round done:
      │   ├─ If only one image remains → TOURNAMENT_COMPLETE
      │   └─ Else: GENERATE_NEXT_ROUND
      └─ Else: stay MATCH_ACTIVE (query next incomplete match)

GENERATE_NEXT_ROUND
  └─ Collect surviving images, shuffle, pair randomly, insert matches rows → MATCH_ACTIVE

TOURNAMENT_COMPLETE
  └─ GET /api/tournament/{uuid}/export → CSV
```

**State transitions are persisted to SQLite after every change.**

**Concurrent write handling:** If two tabs submit the same match, the second submission finds `winner IS NOT NULL` and returns the next current match (idempotent). No corruption, but the second tab shows stale state. Single-user tool — acceptable for Phase 1.

## Image Handling

- **Formats:** JPEG, PNG, WebP, TIFF, BMP, GIF (via Pillow)
- **HEIC/HEIF:** Requires system libs in Docker — document as supported if libheif installed
- **Unicode filenames:** Store full paths as UTF-8 in SQLite and filesystem. Test with Japanese, CJK, emoji filenames.
- **Serving:** Original files via `/api/images/{path:filepath}` — no preprocessing, no resizing. Browser handles caching.
- **Error handling:** Missing/corrupt file → skip from bracket, log warning, exclude from scoring.

## Bracket Generation

Bracket is generated **round-by-round**, not pre-paired. Pairs are randomized each round from surviving images.

For N images (rounds = ceil(log2(N))):

1. Tournament created → all `images` rows inserted, no `matches` yet
2. Round 1: collect all alive images, shuffle, pair randomly, create `matches` rows
3. After round completes: collect survivors, shuffle, pair, create next round's matches
4. Repeat until one image remains
5. If odd count in a round, one image gets a bye (advances without a match)

**Round-by-round example (8 images):**
```
Tournament created → images [A,B,C,D,E,F,G,H] in DB, no matches

Round 1: shuffled [A,B,C,D,E,F,G,H]
  → Match 0: A vs B
  → Match 1: C vs D
  → Match 2: E vs F
  → Match 3: G vs H
All 4 Round 1 matches complete

Round 2: survivors [A, C, E, H], shuffled [C,H,A,E]
  → Match 4: C vs H
  → Match 5: A vs E
Both Round 2 matches complete

Round 3: survivors [C, A], shuffled [C,A]
  → Match 6: C vs A
Match 6 complete → TOURNAMENT_COMPLETE
```

**Current match tracking:** `current_round` stored in `tournaments`. To find active match: `SELECT * FROM matches WHERE tournament_id=? AND round=? AND winner IS NULL LIMIT 1`.

**Round completion:** When `SELECT COUNT(*) FROM matches WHERE tournament_id=? AND round=? AND winner IS NULL` returns 0 and `round < total_rounds`, advance `current_round` and generate next round's matches.

**Bye handling:** If odd number of survivors in a round, one advances without a match. Its `round_reached` increments but no `matches` row is created for it.

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
2. Preloads next match's images in background (hidden img tags)
3. On selection: POST result, receive next match, update display
4. On tournament complete: show results summary + download CSV button
5. "Undo" button to reverse the last match (single-step)

No framework. No build step. Pure browser JS.

### Preload Strategy

Always keep the next pair preloaded in a hidden div:
```html
<div id="preload-pool" style="display:none">
  <img id="preload-a" src="">
  <img id="preload-b" src="">
</div>
```

On match resolution:
1. Immediately swap `preload-a`/`preload-b` into visible slots (already loaded)
2. Fire background fetch for what is now the next next pair
3. Update preload pool with new targets

Result: user never waits for an image to load after the first click. First click loads the initial pair; every subsequent click swaps preloaded images.

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
3. Decrement each image's `round_reached` by 1
4. Decrement winner's `wins` by 1
5. Set tournament `current_round` and `current_match` back to point at that match
6. Return the undone match as the current match

Constraint: Only the immediately previous match can be undone. Undo is a single-step operation.

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
