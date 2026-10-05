"""`pipeline position-evals`: one engine evaluation per board Review's opening mistakes read.

The boards are `core.review.mistakes.eval_candidates`: every board the player moved from often enough,
and every board their moves from it led to, most-played first. Each is rebuilt by replaying a
source game's prefix moves from the standard start to the board's ply in that game, and must
hash to the board's key (checked by bq_position_key in SQL, the one key authority); the next
source is tried when one does not. A board no source rebuilds is a defect: it is counted
`failed`, nothing is written for it, and the run fails.

What is analysed is never the replayed board. The stored row is shared by every game reaching
the key, and a replayed board carries its game: the move stack (python-chess hands it to the
engine, which then scores repetitions of that history) and the halfmove clock (which the engine
reads too). So the engine gets the CANONICAL board: the key's four FEN fields with the clocks
`0 1` and no history. A board that is over by itself (checkmate, stalemate, insufficient
material, judged on that same canonical board) is never sent to the engine: its row is
`terminal` 'checkmate' or 'draw' with no score and no best move. Repetition and the move-count
rules depend on a game's history and are never a board's terminal fact.

Otherwise Stockfish evaluates the board at STOCKFISH_DEPTH, the depth the game analyser uses,
and the row stores the score from White's point of view like ply_analysis (centipawns, or a
mate distance in moves) and the engine's best move in SAN. Every write replaces the whole row,
so a row written before migration 010 (no best move) is re-evaluated once and comes out exactly
as a new one would.

Idempotent: a board with a best move or a terminal outcome is never a candidate again. Each
evaluation is committed on its own, so an interrupted run keeps what it did. `workers` engines
run in separate processes (one thread each, as the analyser runs them); the parent keeps the
connection and writes every row. A worker whose engine does not start answers with that and
the run stops, counting what it did not evaluate as failed; nothing a worker raises reaches
the console beyond its class chain.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from contextlib import AbstractContextManager, contextmanager
from typing import Any

import chess
import chess.engine
from psycopg import Connection

from core.chess.board import replay
from core.constants import POSITION_EVALS_PER_RUN, STOCKFISH_DEPTH
from core.review import mistakes
from core.settings import Settings

# (centipawns, mate in moves, best move in SAN), White's point of view; exactly one score is not None.
Score = tuple[int | None, int | None, str | None]
Analyser = Callable[[chess.Board], Score]
Engine = Callable[[], AbstractContextManager[Analyser]]
# (key, the engine's answer or None, the class chain of an exception a worker caught)
Answer = tuple[int, Score | None, str | None]

CHECKMATE = mistakes.CHECKMATE
DRAW = mistakes.DRAW


def _score(info: chess.engine.InfoDict, board: chess.Board) -> Score | None:
    score = info.get("score")
    if score is None:
        return None
    pv = info.get("pv") or []
    best = board.san(pv[0]) if pv else None
    pov = score.white()
    mate = pov.mate()
    if mate is not None:
        return None, mate, best
    return pov.score(), None, best


class Stockfish:
    """The engine as a context manager yielding an Analyser; closed on exit."""

    def __enter__(self) -> Analyser:
        from core.analysis.engine import find_stockfish, open_engine

        engine = open_engine(find_stockfish())
        self._engine = engine

        def analyse(board: chess.Board) -> Score:
            got = _score(engine.analyse(board, chess.engine.Limit(depth=STOCKFISH_DEPTH), game=object()), board)
            if got is None:
                raise RuntimeError("the engine returned no score")
            return got

        return analyse

    def __exit__(self, *_: object) -> None:
        self._engine.quit()


def canonical(board: chess.Board) -> chess.Board:
    """The board as its key sees it: the four key fields, clocks `0 1`, no move history."""
    fields = board.fen().split(" ")[:4]
    return chess.Board(" ".join([*fields, "0", "1"]))


def terminal_of(board: chess.Board) -> str | None:
    """What the board itself says about the game being over, on the canonical board."""
    if board.is_checkmate():
        return CHECKMATE
    if board.is_stalemate() or board.is_insufficient_material():
        return DRAW
    return None


def _rebuild(conn: Connection[Any], row: dict[str, Any]) -> tuple[chess.Board | None, bool]:
    """(the canonical board from the first source that replays to the board's key, else None;
    whether an earlier source did not)."""
    for i, source in enumerate(row["sources"]):
        replayed = replay(list(source["moves"] or []), int(source["ply"]))
        if replayed is None:
            continue
        board = canonical(replayed)
        check = conn.execute("SELECT bq_position_key(%s) = %s AS same", (board.fen(), row["key"])).fetchone()
        if check and check["same"]:
            return board, i > 0
    return None, len(row["sources"]) > 1


_WRITE = """
    INSERT INTO position_evals (board_key, fen, eval_cp, mate_in, best_move, terminal, depth, computed_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (board_key) DO UPDATE SET
        fen = EXCLUDED.fen, eval_cp = EXCLUDED.eval_cp, mate_in = EXCLUDED.mate_in,
        best_move = EXCLUDED.best_move, terminal = EXCLUDED.terminal, depth = EXCLUDED.depth,
        computed_at = EXCLUDED.computed_at
"""

# A worker process's engine, opened once by `_init_worker`, or the class chain of the error that
# kept it from opening.
_worker: dict[str, Any] = {}

# An answer whose label starts with this says the worker's engine never started: the run stops
# there rather than feeding the rest of the work to engines that cannot answer.
NO_ENGINE = "engine did not start: "


def _init_worker(engine: Engine) -> None:
    """Open this worker's engine. A failure is kept, never raised: a pool whose initializer raises
    replaces the worker and tries again for ever, and the child would print the traceback. The
    engine is closed when the worker exits: python-chess drives Stockfish from a non-daemon
    thread, and a worker process waits for its non-daemon threads before it ends, so an engine
    left open would keep the pool's shutdown waiting for ever."""
    from multiprocessing import util

    from core.notify import error_label

    try:
        manager = engine()
        _worker["analyse"] = manager.__enter__()
    except Exception as exc:
        _worker["startup"] = error_label(exc)
        return
    util.Finalize(None, _close_quietly, args=(manager,), exitpriority=10)


def _close_quietly(manager: AbstractContextManager[Analyser]) -> None:
    try:
        manager.__exit__(None, None, None)
    except Exception:  # the engine may already be gone; the worker is ending either way
        pass


def _analyse_in_worker(item: tuple[int, str]) -> Answer:
    """(key, the engine's answer, the class chain of an exception if it raised)."""
    from core.notify import error_label

    key, fen = item
    if "startup" in _worker:
        return key, None, NO_ENGINE + str(_worker["startup"])
    try:
        return key, _worker["analyse"](chess.Board(fen)), None
    except Exception as exc:
        return key, None, error_label(exc)


@contextmanager
def _answers(engine: Engine, work: list[tuple[int, str]], workers: int) -> Generator[Iterator[Answer]]:
    """The engine's answers to `work`, as they come: in this process, or from `workers` processes.
    A worker process that dies ends the pool: every answer still owed comes back failed
    (`BrokenProcessPool`). Leaving the block cancels what was not started and waits for the
    workers to exit."""
    if workers <= 1 or len(work) < 2:
        with engine() as analyse:

            def serial() -> Iterator[Answer]:
                for key, fen in work:
                    yield key, analyse(chess.Board(fen)), None

            yield serial()
        return
    pool = ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(engine,))
    try:
        futures = {pool.submit(_analyse_in_worker, item): item[0] for item in work}

        def pooled() -> Iterator[Answer]:
            for future in as_completed(futures):
                try:
                    yield future.result()
                except BrokenProcessPool:
                    yield futures[future], None, "BrokenProcessPool"

        yield pooled()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def run(
    conn: Connection[Any],
    config: Settings,
    *,
    limit: int | None = POSITION_EVALS_PER_RUN,
    engine: Engine = Stockfish,
    workers: int = 1,
) -> dict[str, Any]:
    """Evaluate up to `limit` pending boards (None, or 0: all of them). Counts only."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be 0 (all) or more")
    pending, rows = mistakes.eval_candidates(
        conn, config.review_position_max_ply, config.review_history_months, limit or None
    )
    conn.commit()
    # `fallbacks`: boards rebuilt only from a later source; an earlier game's stored prefix
    # did not replay to the board, which is worth seeing in the run log without failing it.
    summary: dict[str, Any] = {"pending": pending, "evaluated": 0, "terminal": 0, "failed": 0, "fallbacks": 0}
    if not rows:
        return summary
    work: list[tuple[int, str]] = []
    for row in rows:
        board, fell_back = _rebuild(conn, row)
        summary["fallbacks"] += int(fell_back and board is not None)
        if board is None:
            summary["failed"] += 1
            continue
        terminal = terminal_of(board)
        if terminal is not None:
            conn.execute(_WRITE, (row["key"], board.fen(), None, None, None, terminal, STOCKFISH_DEPTH))
            conn.commit()
            summary["terminal"] += 1
            continue
        work.append((int(row["key"]), board.fen()))
    conn.commit()  # no transaction stays open while the engine thinks
    fens = dict(work)
    failures: list[str] = []
    answered: set[int] = set()
    with _answers(engine, work, workers) as answers:
        for key, got, label in answers:
            answered.add(key)
            # A live board always has a move; an answer without one would leave the row pending
            # for ever, so it is a failure and nothing is written.
            if got is None or got[2] is None or (got[0] is None) == (got[1] is None):
                summary["failed"] += 1
                failures.append(f"{key}: {label or 'no move or no score'}")
                if label and label.startswith(NO_ENGINE):
                    break
                continue
            cp, mate, best = got
            conn.execute(_WRITE, (key, fens[key], cp, mate, best, None, STOCKFISH_DEPTH))
            conn.commit()
            summary["evaluated"] += 1
    unanswered = len(work) - len(answered)
    if unanswered:  # the run stopped: an engine that did not start
        summary["failed"] += unanswered
        failures.append(f"{unanswered} boards not evaluated: the run stopped")
    if failures:
        summary["failures"] = failures[:20]
    return summary
