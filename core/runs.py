"""pipeline_runs: one row per step execution, so the Home page and the CLI can
say when each step last ran and whether it succeeded."""

from __future__ import annotations

import json
from typing import Any

from psycopg import Connection

# The hourly chain, in order (pipeline/cli.py runs exactly these). Home reports on these
# steps only: a hand-run step that failed once would otherwise stay red for ever.
HOURLY_STEPS = ("import", "match", "analyze", "generate-puzzles", "srs-maintain", "import-opponents", "housekeep")


def start(conn: Connection[Any], step: str) -> int:
    row = conn.execute(
        "INSERT INTO pipeline_runs (step, status) VALUES (%s, 'running') RETURNING id", (step,)
    ).fetchone()
    conn.commit()
    assert row is not None
    return int(row["id"])


def finish(conn: Connection[Any], run_id: int, summary: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE pipeline_runs SET status = 'ok', finished_at = now(), summary = %s::jsonb WHERE id = %s",
        (json.dumps(summary), run_id),
    )
    conn.commit()


def fail(conn: Connection[Any], run_id: int, error: str) -> None:
    conn.rollback()
    conn.execute(
        "UPDATE pipeline_runs SET status = 'failed', finished_at = now(), error = %s WHERE id = %s",
        (error[:2000], run_id),
    )
    conn.commit()


def latest(conn: Connection[Any]) -> list[dict[str, Any]]:
    """The most recent run of every step."""
    return conn.execute(
        """
        SELECT DISTINCT ON (step) step, status, started_at, finished_at, summary, error
        FROM pipeline_runs ORDER BY step, started_at DESC
        """
    ).fetchall()


def hourly_status(conn: Connection[Any]) -> dict[str, Any]:
    """What Home shows: when the chain last ran through (its last step succeeded), and every
    hourly step whose most recent run failed, in chain order."""
    row = conn.execute(
        "SELECT max(finished_at) AS at FROM pipeline_runs WHERE step = %s AND status = 'ok'", (HOURLY_STEPS[-1],)
    ).fetchone()
    assert row is not None
    by_step = {r["step"]: r for r in latest(conn)}
    failed = [by_step[s] for s in HOURLY_STEPS if s in by_step and by_step[s]["status"] == "failed"]
    return {
        "last_ok_at": row["at"].isoformat() if row["at"] is not None else None,
        "failed": [{"step": r["step"], "started_at": r["started_at"].isoformat(), "error": r["error"]} for r in failed],
    }
