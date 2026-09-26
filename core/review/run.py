"""`pipeline review`: retag the whole window and publish, in one transaction under two locks.

Statement order is the contract. The transaction begins with `pg_advisory_xact_lock(LOCK_REVIEW,
1)`, then `matching.lock` (the repertoire lock of docs/decisions/007), and only then reads the
settings, the window and the two contexts; both locks are held to the commit that publishes.
Two runs cannot interleave (the second waits, then reads the newer state), and a toggle or an
import cannot rematch between this run reading a result row and publishing a `pool_key` from
it. Tagging happens between the reads and the writes, serially or in a worker pool that gets
pure `(game_ctx, knobs)` tuples; the parent keeps the connection and the locks, and a worker
never touches the database.

Every game that tags is replaced (events or none). A game the detector cannot tag keeps its
prior rows and is counted `failed`, which fails the run and alerts; games that left the window
lose their rows. A failure anywhere rolls the whole run back and the prior generation stays.
"""

from __future__ import annotations

import time
from multiprocessing import Pool
from typing import Any

from psycopg import Connection

from core import settings as settings_module
from core.constants import LOCK_REVIEW, PLAYER_ID
from core.notify import error_label
from core.repertoire import matching
from core.review import write
from core.review.detect import tag_review_events
from core.review.window import KNOB_FIELDS, GameCtx, contexts
from core.settings import Settings

Tagged = tuple[int, tuple[list[dict[str, Any]], int] | None, str | None]


def lock(conn: Connection[Any]) -> None:
    """The review lock, first; then the repertoire lock. Always this order, on every path."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_REVIEW, PLAYER_ID))
    matching.lock(conn)


def knobs_of(settings: Settings) -> dict[str, int]:
    return {k: int(getattr(settings, k)) for k in KNOB_FIELDS}


def tag_one(task: tuple[GameCtx, dict[str, int]]) -> Tagged:
    """One game, in the parent or a worker: (game id, the detector's answer, the class chain
    of an exception if it raised — never its message)."""
    game_ctx, knobs = task
    gid = int(game_ctx["chess_game_id"])
    try:
        return gid, tag_review_events(game_ctx, knobs), None
    except Exception as exc:
        return gid, None, error_label(exc)


def tag_all(games: list[GameCtx], knobs: dict[str, int], workers: int) -> list[Tagged]:
    tasks = [(g, knobs) for g in games]
    if workers <= 1 or len(tasks) < 2:
        results = [tag_one(t) for t in tasks]
    else:
        with Pool(processes=workers) as pool:
            results = list(pool.imap_unordered(tag_one, tasks, chunksize=8))
    return sorted(results, key=lambda r: r[0])


def run(conn: Connection[Any], *, workers: int = 1, after_read: Any = None) -> dict[str, Any]:
    """The step. The settings row is read after the locks like everything else, so the knobs
    that tag are the knobs of the generation published. `after_read` is a test hook called
    with the window's ids once the reads are done and before anything is written (still
    under both locks). Returns counts for the run log; `failed > 0` fails the run."""
    started = time.monotonic()
    with conn.transaction():
        lock(conn)
        settings = settings_module.load(conn)
        knobs = knobs_of(settings)
        games = contexts(conn, settings.analysis_game_limit)
        ids = [g["chess_game_id"] for g in games]
        if after_read is not None:
            after_read(ids)
        results = tag_all(games, knobs, workers)
        summary: dict[str, Any] = {"games": len(games), "events": 0, "unknown": 0, "failed": 0, "deleted": 0}
        failures: list[str] = []
        for gid, answer, label in results:
            if answer is None:
                summary["failed"] += 1
                failures.append(f"{gid}: {label or 'untaggable'}")
                continue
            events, unknown = answer
            summary["events"] += write.replace_game(conn, gid, events)
            summary["unknown"] += unknown
        summary["deleted"] = write.delete_outside(conn, ids)
        write.record_state(conn, summary["unknown"], len(games))
        if failures:
            summary["failures"] = failures[:20]
    summary["seconds"] = round(time.monotonic() - started, 1)
    return summary
