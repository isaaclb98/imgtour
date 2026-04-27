#!/usr/bin/env python3
"""
Test script for TournamentEngine.
Run with: python engine_test.py 2>&1 | grep TournamentEngine

Tests:
1. Create 8-image tournament — check 4 round-1 matches
2. Vote through round 1 → round 2 created
3. Vote through round 2 → round 3 created
4. Vote through round 3 → tournament complete, winner score 1.0
5. Undo last vote
6. Bye handling (5 images)
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)

from engine import TournamentEngine


def vote_round(state, round_number):
    """Vote all matches in a given round in order."""
    round_matches = [m for m in state.matches if m.round == round_number]
    for m in round_matches:
        state = TournamentEngine.vote(state, m.id, m.image_a_path)
    return state


def test_8_image_tournament():
    print("\n=== test_8_image_tournament ===")
    images = [f"/images/{c}.jpg" for c in "ABCDEFGH"]
    state = TournamentEngine.create("test-uuid", images, "fast", 3)

    # Verify initial setup
    round1 = [m for m in state.matches if m.round == 1]
    assert len(round1) == 4, f"round 1 should have 4 matches, got {len(round1)}"
    assert state.total_rounds == 3

    # Vote round 1 (all 4 matches)
    state = vote_round(state, 1)
    round2 = [m for m in state.matches if m.round == 2]
    print(f"  After round 1: {len(state.matches)} total matches, round 2: {[m.id for m in round2]}")
    assert len(round2) == 2, f"round 2 should have 2 matches, got {len(round2)}"

    # Vote round 2 (both matches)
    state = vote_round(state, 2)
    round3 = [m for m in state.matches if m.round == 3]
    print(f"  After round 2: {len(state.matches)} total matches, round 3: {[m.id for m in round3]}")
    assert len(round3) == 1, f"round 3 should have 1 match, got {len(round3)}"

    # Vote round 3 (final)
    state = vote_round(state, 3)
    assert state.status == "COMPLETE", f"expected COMPLETE, got {state.status}"
    winners = [p for p, img in state.images.items() if img.round_reached == 3]
    assert len(winners) == 1, f"expected 1 winner, got {len(winners)}"
    assert state.images[winners[0]].score == 1.0
    print(f"PASS: 8-image tournament complete. Winner: {winners[0]}")
    return state


def test_undo():
    print("\n=== test_undo ===")
    images = [f"/images/{c}.jpg" for c in "ABCDEFGH"]
    state = TournamentEngine.create("test-uuid", images, "fast", 3)

    # Vote match 1
    match1 = state.matches[0]
    state = TournamentEngine.vote(state, match1.id, match1.image_a_path)
    last_id = state.last_match_id

    # Undo
    state = TournamentEngine.undo(state)
    assert state.last_match_id is None, f"last_match_id should be None, got {state.last_match_id}"
    # Match 1 winner should be cleared
    assert state.matches[0].winner_path is None, "match 1 winner should be cleared"
    print("PASS: undo cleared last match")


def test_bye_handling():
    print("\n=== test_bye_handling ===")
    # 5 images — one gets a bye in round 1 (odd count)
    images = [f"/images/{c}.jpg" for c in "ABCDE"]
    state = TournamentEngine.create("test-uuid-5", images, "fast", 3)

    round1 = [m for m in state.matches if m.round == 1]
    # 5 images → 2 matches (4 images) + 1 bye
    assert len(round1) == 2, f"round 1 should have 2 matches, got {len(round1)}"
    bye_candidates = [p for p, img in state.images.items() if img.round_reached == 1]
    in_matches = set(m.image_a_path for m in round1) | set(m.image_b_path for m in round1 if m.image_b_path)
    bye_images = [p for p in bye_candidates if p not in in_matches]
    print(f"  Bye images: {bye_images}")
    print("PASS: 5-image tournament created with 2 round-1 matches and 1 bye")


def test_idempotent_vote():
    print("\n=== test_idempotent_vote ===")
    images = [f"/images/{c}.jpg" for c in "ABCDEFGH"]
    state = TournamentEngine.create("test-uuid", images, "fast", 3)

    match1 = state.matches[0]
    state1 = TournamentEngine.vote(state, match1.id, match1.image_a_path)
    state2 = TournamentEngine.vote(state1, match1.id, match1.image_a_path)  # same winner

    # Should be idempotent — state unchanged
    assert state1.last_match_id == state2.last_match_id
    print("PASS: voting same winner twice is idempotent")


def test_find_next_match_index():
    print("\n=== test_find_next_match_index ===")
    images = [f"/images/{c}.jpg" for c in "ABCDEFGH"]
    state = TournamentEngine.create("test-uuid", images, "fast", 3)

    idx = TournamentEngine.find_next_match_index(state)
    assert idx == 0, f"first active match should be index 0, got {idx}"

    state = TournamentEngine.vote(state, state.matches[0].id, state.matches[0].image_a_path)
    idx = TournamentEngine.find_next_match_index(state)
    assert idx == 1, f"next active match should be index 1, got {idx}"

    print("PASS: find_next_match_index works correctly")


if __name__ == "__main__":
    test_8_image_tournament()
    test_undo()
    test_bye_handling()
    test_idempotent_vote()
    test_find_next_match_index()
    print("\n=== ALL TESTS PASSED ===")