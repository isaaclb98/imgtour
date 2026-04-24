# imgtour

Image tournament scorer for ML training data. Run a binary tournament bracket over a folder of images, get back a scored CSV and a flat folder of ranked images ready for training pipelines.

## How it works

1. Point `IMAGE_FOLDERS` at a directory of images (supports recursive subdirectory scanning)
2. Optionally set `SAMPLE_SIZE` to randomly sample N images before the tournament
3. Open the app, click through pairings, or use arrow keys to vote
4. On completion, download the CSV and optionally get a flat export folder with scored images

## Scoring

**Fast mode (single elimination):**
```
score = round_reached / total_rounds
```
Winner gets 1.0, runner-up gets ~0.667, semifinal losers get ~0.333, and so on.

**Slow mode (double elimination):**
```
score = (N - placement) / (N - 1)
```
Placement is determined by final position in the bracket. Winner is 1, runner-up is 2, etc. Distinguishes quarterfinal losers from runners-up.

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tournament/{uuid}` | Get full tournament state (all matches and images) |
| POST | `/api/tournament` | Create new tournament |
| POST | `/api/match/{id}/vote` | Fire-and-forget vote (instant client response) |
| POST | `/api/tournament/{uuid}/undo` | Undo last match |
| GET | `/api/tournament/{uuid}/export` | Download CSV results |
| GET | `/api/images/{path}` | Serve original image |

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAGE_FOLDERS` | `/images` | Colon-separated image directory paths |
| `EXPORT_FOLDER` | (none) | If set, copies scored images here flat with `score_stem_hash.ext` filenames |
| `SAMPLE_SIZE` | `0` | Randomly sample N images before tournament. 0 = use all |
| `RESET` | (none) | Set to `1` or `true` to wipe existing tournament DBs on startup |
| `TOURNAMENT_MODE` | `slow` | `slow` = double elimination with placement scoring; `normal` = single elimination |

DATA_DIR is hardcoded to `/data` (SQLite DBs stored as `tournament_{uuid}.db`).

## Quick start with Docker

```bash
# Clone and run
docker compose up

# With a custom image folder and sampling
IMAGE_FOLDERS=/path/to/your/images SAMPLE_SIZE=500 docker compose up
```

## Keyboard controls

- **Left arrow** — vote for left image
- **Right arrow** — vote for right image
- **Down arrow** — undo last vote

Arrows only work when a match is active and the UI is not disabled.

## Architecture

- Server: Python Starlette + aiosqlite
- Client: vanilla HTML/JS, no framework
- Bracket: pre-generated at tournament creation using seeded PRNG (same images = same bracket)
- Voting: optimistic local update, fire-and-forget POST, server persists async
- Images: served directly from filesystem, browser handles caching
- Modes: `TOURNAMENT_MODE=slow` (default) runs double elimination with placement scoring; `TOURNAMENT_MODE=normal` runs single elimination
