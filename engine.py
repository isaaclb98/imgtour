"""
TournamentEngine — pure synchronous state machine for imgtour.

No async, no DB I/O, no starlette imports. Testable with plain assertions.
Every state transition is logged at INFO level for observability.

Usage:
    from engine import TournamentEngine, TournamentState

    engine = TournamentEngine()
    state = engine.create("uuid-123", [f"/images/{c}.jpg" for c in "ABCDEFGH"], "fast", 3)
    state = engine.vote(state, match_id=1, winner_path="/images/A.jpg")
    state = engine.undo(state)
"""

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("imgtour")


@dataclass
class ImageState:
    round_reached: int
    wins: int
    score: float
    lives: int  # 1 for fast, 2 for slow
    losers_entrance_round: Optional[int] = None


@dataclass
class MatchState:
    id: int
    round: int
    bracket: str  # winners / losers
    image_a_path: str
    losers_round: Optional[int] = None
    image_b_path: Optional[str] = None
    winner_path: Optional[str] = None
    is_final: bool = False


@dataclass
class TournamentState:
    uuid: str
    status: str  # IDLE / ACTIVE / COMPLETE
    mode: str  # fast / slow
    current_round: int
    total_images: int
    total_rounds: int
    total_matches: int
    last_match_id: Optional[int] = None
    images: dict[str, ImageState] = field(default_factory=dict)
    matches: list[MatchState] = field(default_factory=list)
    next_match_id: int = 1  # auto-increment for new matches


class TournamentEngine:
    @staticmethod
    def create(
        uuid: str,
        image_paths: list[str],
        mode: str,
        total_rounds: int,
        seed: Optional[int] = None,
    ) -> TournamentState:
        """
        Create a new tournament with round-1 matches pre-generated.
        Returns initial TournamentState.
        """
        lives = 2 if mode == "slow" else 1
        images = {
            path: ImageState(round_reached=0, wins=0, score=0.0, lives=lives)
            for path in image_paths
        }

        state = TournamentState(
            uuid=uuid,
            status="ACTIVE",
            mode=mode,
            current_round=1,
            total_images=len(image_paths),
            total_rounds=total_rounds,
            total_matches=0,
            last_match_id=None,
            images=images,
            matches=[],
            next_match_id=1,
        )

        state = TournamentEngine.advance_round(state, 1, image_paths, seed)

        logger.info(
            "TournamentEngine.create uuid=%s images=%d mode=%s rounds=%d seed=%s matches=%d",
            uuid, len(image_paths), mode, total_rounds, seed, state.total_matches,
        )
        return state

    @staticmethod
    def advance_round(
        state: TournamentState,
        round_number: int,
        survivors: list[str],
        seed: Optional[int] = None,
    ) -> TournamentState:
        """
        Generate matches for the given round from the survivor list.
        Handles byes (odd count) by advancing the extra image without a match.
        Returns new state with matches added.
        """
        shuffled = list(survivors)
        if seed is not None:
            random.Random(seed).shuffle(shuffled)
        else:
            random.SystemRandom().shuffle(shuffled)

        # Handle bye: odd number of survivors → one gets a bye
        bye_image: Optional[str] = None
        if len(shuffled) % 2 == 1:
            bye_image = shuffled.pop()
            bye_state = state.images[bye_image]
            state.images[bye_image] = ImageState(
                round_reached=round_number,
                wins=bye_state.wins,
                score=bye_state.score,
                lives=bye_state.lives,
                losers_entrance_round=bye_state.losers_entrance_round,
            )
            logger.info("TournamentEngine.bye image=%s round=%d", bye_image, round_number)

        new_match_ids = []
        for i in range(0, len(shuffled), 2):
            match = MatchState(
                id=state.next_match_id,
                round=round_number,
                bracket="winners",
                losers_round=None,
                image_a_path=shuffled[i],
                image_b_path=shuffled[i + 1],
                winner_path=None,
                is_final=False,
            )
            state.matches.append(match)
            new_match_ids.append(match.id)
            state.next_match_id += 1

        state.total_matches = len(state.matches)
        state.current_round = round_number

        logger.info(
            "TournamentEngine.advance_round round=%d match_ids=%s bye=%s",
            round_number, new_match_ids, bye_image,
        )
        return state

    @staticmethod
    def vote(state: TournamentState, match_id: int, winner_path: str) -> TournamentState:
        """
        Record a vote for the given match.
        If the match already has a winner (idempotent), returns state unchanged.
        Returns new state with updated match, images, and round progression if round is complete.
        """
        match = None
        for m in state.matches:
            if m.id == match_id:
                match = m
                break

        if match is None:
            raise ValueError(f"match_id={match_id} not found in tournament")

        # Idempotent: if already voted, return unchanged
        if match.winner_path is not None:
            logger.info(
                "TournamentEngine.vote match_id=%d winner=%s (already voted, idempotent)",
                match_id, winner_path,
            )
            return state

        loser_path = (
            match.image_b_path if winner_path == match.image_a_path else match.image_a_path
        )

        # Record winner
        new_matches = []
        for m in state.matches:
            if m.id == match_id:
                new_matches.append(
                    MatchState(
                        id=m.id, round=m.round, bracket=m.bracket,
                        losers_round=m.losers_round,
                        image_a_path=m.image_a_path, image_b_path=m.image_b_path,
                        winner_path=winner_path, is_final=m.is_final,
                    )
                )
            else:
                new_matches.append(m)

        state.matches = new_matches
        match = new_matches[next(i for i, m in enumerate(new_matches) if m.id == match_id)]

        # Update winner image
        winner_img = state.images[winner_path]
        new_round_reached = max(winner_img.round_reached, match.round)
        new_score = round(new_round_reached / state.total_rounds, 3) if state.total_rounds > 0 else 0.0
        state.images[winner_path] = ImageState(
            round_reached=new_round_reached,
            wins=winner_img.wins + 1,
            score=new_score,
            lives=winner_img.lives,
            losers_entrance_round=winner_img.losers_entrance_round,
        )

        # Update loser image
        loser_img = state.images[loser_path]
        state.images[loser_path] = ImageState(
            round_reached=loser_img.round_reached,
            wins=loser_img.wins,
            score=loser_img.score,
            lives=loser_img.lives - 1,
            losers_entrance_round=loser_img.losers_entrance_round,
        )

        state.last_match_id = match_id

        logger.info(
            "TournamentEngine.vote match_id=%d winner=%s loser=%s loser_lives_after=%d",
            match_id, winner_path, loser_path, state.images[loser_path].lives,
        )

        # Check if round is complete
        pending_in_round = [m for m in state.matches if m.round == match.round and m.winner_path is None]
        if pending_in_round:
            # Round not complete yet
            return state

        # Round complete — determine survivors
        winners_in_round = [
            m.winner_path for m in state.matches
            if m.round == match.round and m.winner_path is not None
        ]

        # Collect surviving images (those whose round_reached >= this round)
        survivors = [
            path for path, img in state.images.items()
            if img.round_reached >= match.round
        ]

        if len(survivors) <= 1:
            state.status = "COMPLETE"
            logger.info("TournamentEngine.is_complete winner=%s", survivors[0] if survivors else None)
            return state

        # Create next round
        next_round = match.round + 1
        state = TournamentEngine.advance_round(state, next_round, survivors)

        return state

    @staticmethod
    def undo(state: TournamentState) -> TournamentState:
        """
        Undo the last completed match.
        Restores winner's and loser's image state, clears match winner,
        updates last_match_id to the previous completed match.
        Returns new state.
        """
        if state.last_match_id is None:
            raise ValueError("No match to undo")

        # Find the last match
        last_match = None
        for m in state.matches:
            if m.id == state.last_match_id:
                last_match = m
                break

        if last_match is None or last_match.winner_path is None:
            raise ValueError(f"last_match_id={state.last_match_id} has no winner to undo")

        winner_path = last_match.winner_path
        loser_path = (
            last_match.image_b_path
            if last_match.winner_path == last_match.image_a_path
            else last_match.image_a_path
        )

        # Restore winner image: decrement wins, round_reached - 1
        winner_img = state.images[winner_path]
        state.images[winner_path] = ImageState(
            round_reached=max(winner_img.round_reached - 1, 0),
            wins=max(winner_img.wins - 1, 0),
            score=winner_img.score,
            lives=winner_img.lives,
            losers_entrance_round=winner_img.losers_entrance_round,
        )

        # Restore loser image: lives + 1 (undo the decrement from vote)
        loser_img = state.images[loser_path]
        state.images[loser_path] = ImageState(
            round_reached=loser_img.round_reached,
            wins=loser_img.wins,
            score=loser_img.score,
            lives=loser_img.lives + 1,
            losers_entrance_round=loser_img.losers_entrance_round,
        )

        # Clear winner on the match
        new_matches = []
        for m in state.matches:
            if m.id == last_match.id:
                new_matches.append(
                    MatchState(
                        id=m.id, round=m.round, bracket=m.bracket,
                        losers_round=m.losers_round,
                        image_a_path=m.image_a_path, image_b_path=m.image_b_path,
                        winner_path=None, is_final=m.is_final,
                    )
                )
            else:
                new_matches.append(m)
        state.matches = new_matches

        # Find previous match
        previous_match_id = None
        for m in reversed(state.matches):
            if m.winner_path is not None and m.id != last_match.id:
                previous_match_id = m.id
                break

        state.last_match_id = previous_match_id
        state.status = "ACTIVE"

        logger.info(
            "TournamentEngine.undo match_id=%d winner_cleared=%s round_restored=%d",
            last_match.id, winner_path, state.images[winner_path].round_reached,
        )

        return state

    @staticmethod
    def current_matches(state: TournamentState) -> list[MatchState]:
        """
        Return all active matches (no winner yet).
        In single elimination: returns list of one (the current match).
        In double elimination: can return multiple (winners + losers brackets active simultaneously).
        """
        return [m for m in state.matches if m.winner_path is None]

    @staticmethod
    def find_next_match_index(state: TournamentState) -> int:
        """
        Return the index in state.matches of the first match with no winner.
        Returns -1 if no active matches remain.
        """
        for i, m in enumerate(state.matches):
            if m.winner_path is None:
                return i
        return -1

    @staticmethod
    def is_complete(state: TournamentState) -> bool:
        """Return True if the tournament is complete."""
        return state.status == "COMPLETE"