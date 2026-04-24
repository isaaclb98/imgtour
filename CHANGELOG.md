# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to Semantic Versioning.

## [0.2.0.0] - 2026-04-24

### Added
- Slow mode (double elimination): winners bracket + losers bracket, single final match decides champion
- Placement-based scoring for slow mode: score = (N - placement) / (N - 1), distinguishing quarterfinal losers from runners-up
- Lives tracking: images start with 2 lives, decremented on loss, second loss eliminates
- Losers bracket rounds batch-inserted at end of each winners bracket round
- Odd-count losers queue handling: one image waits and is paired in the next round
- DB-backed queue persistence: losers_match_id column on images table enables server restart recovery
- Bracket context in UI progress label (e.g., "Winners R3" or "Losers R2")

### Changed
- `mode` field added to tournament API responses: 'fast' (single elim) or 'slow' (double elim)
- `winners_bracket_complete` flag on tournaments table tracks slow mode winners bracket state
- `bracket` and `losers_round` fields on matches table distinguish winners vs losers bracket matches
- `lives` and `losers_entrance_round` fields on images table track slow mode state

### Fixed
- collect_round_survivors now correctly includes bye images as winners bracket survivors (was excluding them, causing premature tournament end)
- Atomic lives decrement: lives column only decremented when lives > 1, preventing double-decrement race conditions
- Score column update on tournament completion (was only writing to round_reached/wins, not the score field)

## [0.1.0.0] - 2026-04-20

### Added
- Client-side tournament engine: client holds full `matches[]` array and computes locally
- Fire-and-forget vote endpoint: `POST /api/match/{id}/vote` returns 202 immediately
- Pre-generated rounds: all tournament rounds generated at creation time using seeded PRNG
- Optimistic UI updates: vote response is instant, server persists async
- Image prefetching: next match images preloaded via `new Image()` for zero-latency transitions

### Changed
- `GET /api/tournament/{uuid}` now returns full `matches[]` and `images[]` arrays
- `vote()` no longer blocks on server response

### Fixed
- Undo button permanently disabled bug (checked wrong `match.winner`)
- Undo endpoint crash on missing `total_rounds` parameter
