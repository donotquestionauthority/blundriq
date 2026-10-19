"""`pipeline review`: retag the whole window and publish, in one transaction under two locks.

Statement order is the contract. The transaction begins with `pg_advisory_xact_lock(LOCK_REVIEW,
1)`, then `matching.lock` (the repertoire lock of docs/decisions/007), and only then reads the
settings, the window and the two contexts; both locks are held to the commit that publishes.
Two runs cannot interleave (the second waits, then reads the newer state), and a toggle or an
import cannot rematch between this run reading a result row and publishing a `pool_key` from
it. Tagging happens between the reads and the writes, serially or in a worker pool that gets
pure `(game_ctx, knobs)` tuples; the parent keeps the connection and the locks, and a worker
never touches the database.

Every game that tags is replaced (events or none); games that left the window lose their
rows. A game the detector cannot tag is counted `failed`, and a failed run publishes nothing:
the transaction is left before any write, the prior generation stays whole, and the run row
is red with the counts of what would have been written (`failed > 0` fails the step and alerts).

A run whose inputs are the published generation's inputs skips the read. The fingerprint
(`window.fingerprint`: the window's rows digested on the server, the repertoire and motif
context, the knobs, the schema version, the code stamp) is computed after the locks and
compared with the proof the state row carries; equal means the generation on the table is
what this run would publish, and nothing is read or written. It is computed before the read
it stands in for, in the same READ COMMITTED transaction: a write committing between the two
statements is seen by the read and tagged while the fingerprint recorded describes the inputs
before it, so the next run tags once more and records the right one — one extra full run,
never a generation that stands stale.
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
from core.review import stamp, window, write
from core.review.detect import tag_review_events
from core.review.window import KNOB_FIELDS, GameCtx, contexts
from core.settings import Settings


class _Failed(Exception):
    """Raised inside the publishing transaction to leave it unwritten; carries the summary."""

    def __init__(self, summary: dict[str, Any]) -> None:
        super().__init__("review: failed games")
        self.summary = summary


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
    under both locks). Returns counts for the run log; `failed > 0` fails the run;
    `skipped` means the published generation already matches the inputs."""
    started = time.monotonic()
    try:
        with conn.transaction():
            lock(conn)
            settings = settings_module.load(conn)
            knobs = knobs_of(settings)
            row = window.fingerprint_row(conn, settings.analysis_game_limit)
            fp = window.fingerprint(row, knobs, settings.analysis_game_limit, stamp.code_stamp())
            if row.get("proven") == fp:
                return {"skipped": True, "games": int(row["games"]), "failed": 0, "seconds": _seconds(started)}
            games = contexts(conn, settings.analysis_game_limit)
            ids = [g["chess_game_id"] for g in games]
            if after_read is not None:
                after_read(ids)
            results = tag_all(games, knobs, workers)
            summary: dict[str, Any] = {"games": len(games), "events": 0, "unknown": 0, "failed": 0, "deleted": 0}
            failures: list[str] = []
            tagged: dict[int, list[dict[str, Any]]] = {}
            for gid, answer, label in results:
                if answer is None:
                    summary["failed"] += 1
                    failures.append(f"{gid}: {label or 'untaggable'}")
                    continue
                tagged[gid], unknown = answer
                summary["unknown"] += unknown
            if failures:
                summary["failures"] = failures[:20]
                summary["events"] = sum(len(events) for events in tagged.values())
                summary["deleted"] = write.count_outside(conn, ids)
                raise _Failed(summary)
            summary["events"] = write.replace_games(conn, tagged)
            summary["deleted"] = write.delete_outside(conn, ids)
            write.record_state(conn, summary["unknown"], len(games), fp)
    except _Failed as failed:
        summary = failed.summary
    summary["seconds"] = _seconds(started)
    return summary


def _seconds(started: float) -> float:
    return round(time.monotonic() - started, 1)
