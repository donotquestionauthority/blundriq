"""`pipeline position-evals`: one engine evaluation per Review position.

The positions are `core.review.positions.eval_candidates`: boards Rob's games reach often
enough to be counted, most-played first. Each board is rebuilt by replaying a source game's
prefix moves from the standard start to the board's first-occurrence ply in that game, and must
hash to the board's key (checked by bq_position_key in SQL, the one key authority); the next
source is tried when one does not. A board no source rebuilds is a defect: it is counted
`failed`, nothing is written for it, and the run fails. The step runs last in the hourly chain,
so a board like that alerts every hour without holding up anything else. Otherwise Stockfish
evaluates it at STOCKFISH_DEPTH, the depth the game analyser uses, and the score is stored from
White's point of view like ply_analysis: centipawns, or a mate distance in moves.

Idempotent: a board that has an evaluation is never a candidate again. Each evaluation is
committed on its own, so an interrupted run keeps what it did.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import chess
import chess.engine
from psycopg import Connection

from core.chess.board import replay
from core.constants import POSITION_EVALS_PER_RUN, STOCKFISH_DEPTH
from core.review import positions
from core.settings import Settings

# (centipawns, mate in moves), White's point of view; exactly one is not None.
Score = tuple[int | None, int | None]
Analyser = Callable[[chess.Board], Score]


def _score(info: chess.engine.InfoDict) -> Score | None:
    score = info.get("score")
    if score is None:
        return None
    pov = score.white()
    mate = pov.mate()
    if mate is not None:
        return None, mate
    return pov.score(), None


class Stockfish:
    """The engine as a context manager yielding an Analyser; closed on exit."""

    def __enter__(self) -> Analyser:
        from core.analysis.engine import find_stockfish, open_engine

        engine = open_engine(find_stockfish())
        self._engine = engine

        def analyse(board: chess.Board) -> Score:
            got = _score(engine.analyse(board, chess.engine.Limit(depth=STOCKFISH_DEPTH), game=object()))
            if got is None:
                raise RuntimeError("the engine returned no score")
            return got

        return analyse

    def __exit__(self, *_: object) -> None:
        self._engine.quit()


def _rebuild(conn: Connection[Any], row: dict[str, Any]) -> tuple[chess.Board | None, bool]:
    """(the board from the first source that replays to the board's key, else None; whether an
    earlier source did not)."""
    for i, source in enumerate(row["sources"]):
        board = replay(list(source["moves"] or []), int(source["ply"]))
        if board is None:
            continue
        check = conn.execute("SELECT bq_position_key(%s) = %s AS same", (board.fen(), row["key"])).fetchone()
        if check and check["same"]:
            return board, i > 0
    return None, len(row["sources"]) > 1


def run(
    conn: Connection[Any],
    config: Settings,
    *,
    limit: int | None = POSITION_EVALS_PER_RUN,
    engine: Callable[[], AbstractContextManager[Analyser]] = Stockfish,
) -> dict[str, Any]:
    """Evaluate up to `limit` pending positions (None, or 0: all of them). Counts only."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be 0 (all) or more")
    pending, rows = positions.eval_candidates(conn, config, limit or None)
    conn.commit()
    # `fallbacks`: boards rebuilt only from a later source; an earlier game's stored prefix
    # did not replay to the board, which is worth seeing in the run log without failing it.
    summary: dict[str, Any] = {"pending": pending, "evaluated": 0, "failed": 0, "fallbacks": 0}
    if not rows:
        return summary
    with engine() as analyse:
        for row in rows:
            board, fell_back = _rebuild(conn, row)
            conn.commit()  # no transaction stays open while the engine thinks
            summary["fallbacks"] += int(fell_back and board is not None)
            if board is None:
                summary["failed"] += 1
                continue
            cp, mate = analyse(board)
            conn.execute(
                "INSERT INTO position_evals (board_key, fen, eval_cp, mate_in, depth) VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (board_key) DO NOTHING",
                (row["key"], board.fen(), cp, mate, STOCKFISH_DEPTH),
            )
            conn.commit()
            summary["evaluated"] += 1
    return summary
