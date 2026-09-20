"""pipeline_runs: one row per step execution, so the Home page and the CLI can
say when each step last ran and whether it succeeded."""

from __future__ import annotations

import json
from typing import Any

from psycopg import Connection


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
