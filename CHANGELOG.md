# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to Semantic Versioning.

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
