"""`pipeline position-evals`: one engine evaluation per Review position.

The positions are `core.review.positions.eval_candidates`: boards Rob's games reach often
enough to be counted, most-played first. Each board is rebuilt by replaying its source game's
prefix moves from the standard start to the board's first-occurrence ply, and must hash to
the board's key (checked by bq_position_key in SQL, the one key authority). A board that does
not replay, or replays to a different key, is a defect: it is counted `failed`, nothing is
written for it, and the run fails. Otherwise Stockfish evaluates it at STOCKFISH_DEPTH, the
depth the game analyser uses, and the score is stored from White's point of view like
ply_analysis: centipawns, or a mate distance in moves.

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

from core.constants import POSITION_EVALS_PER_RUN, STOCKFISH_DEPTH
from core.review import positions
from core.settings import Settings

# (centipawns, mate in moves), White's point of view; exactly one is not None.
Score = tuple[int | None, int | None]
Analyser = Callable[[chess.Board], Score]


def replay(moves: list[str], ply: int) -> chess.Board | None:
    """The board after the first `ply` moves from the standard start, or None when a move is
    missing, does not parse, or is the null move (python-chess parses `--`, `Z0`, `0000` as
    a move without complaint)."""
    if ply < 0 or len(moves) < ply:
        return None
    board = chess.Board()
    for san in moves[:ply]:
        try:
            move = board.parse_san(san)
        except ValueError:
            return None
        if not move or move not in board.legal_moves:
            return None
        board.push(move)
    return board


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


def run(
    conn: Connection[Any],
    config: Settings,
    *,
    limit: int | None = POSITION_EVALS_PER_RUN,
    engine: Callable[[], AbstractContextManager[Analyser]] = Stockfish,
) -> dict[str, Any]:
    """Evaluate up to `limit` pending positions (None: all of them). Counts only."""
    pending, rows = positions.eval_candidates(conn, config, limit)
    conn.commit()
    summary: dict[str, Any] = {"pending": pending, "evaluated": 0, "failed": 0}
    if not rows:
        return summary
    with engine() as analyse:
        for row in rows:
            board = replay(list(row["opening_moves"] or []), int(row["ply"]))
            fen = board.fen() if board is not None else None
            same = False
            if fen is not None:
                check = conn.execute("SELECT bq_position_key(%s) = %s AS same", (fen, row["key"])).fetchone()
                same = bool(check and check["same"])
            if board is None or not same:
                summary["failed"] += 1
                conn.rollback()
                continue
            cp, mate = analyse(board)
            conn.execute(
                "INSERT INTO position_evals (board_key, fen, eval_cp, mate_in, depth) VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (board_key) DO NOTHING",
                (row["key"], fen, cp, mate, STOCKFISH_DEPTH),
            )
            conn.commit()
            summary["evaluated"] += 1
    return summary
