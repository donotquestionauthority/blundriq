"""pipeline_runs: one row per step execution, so the Home page and the CLI can
say when each step last ran and whether it succeeded."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from psycopg import Connection

from core.constants import PIPELINE_JOB_TIMEOUT_MINUTES

# The hourly chain, in order (pipeline/cli.py runs exactly these). Home reports on these
# steps only: a hand-run step that failed once would otherwise stay red for ever.
HOURLY_STEPS = (
    "import",
    "match",
    "analyze",
    "generate-puzzles",
    "srs-maintain",
    "import-opponents",
    "review",
    "housekeep",
    "position-evals",
    "review-snapshot",
)

# The step whose success means the chain ran through, for Home's "last successful run". It is
# not the last step: position-evals and review-snapshot run after it, so that a board the
# first cannot rebuild fails only itself, and such a failure shows in the failed list without
# making the import, the analysis and the housekeeping that did run look stale. The snapshot
# runs last because it summarises everything before it, the evaluations included.
CHAIN_THROUGH_STEP = "housekeep"


def start(conn: Connection[Any], step: str) -> int:
    row = conn.execute(
        "INSERT INTO pipeline_runs (step, status) VALUES (%s, 'running') RETURNING id", (step,)
    ).fetchone()
    conn.commit()
    assert row is not None
    return int(row["id"])


def finish(conn: Connection[Any], run_id: int, summary: dict[str, Any]) -> None:
    """`clock_timestamp()`, not `now()`: a step that holds one transaction from its first statement
    to here would otherwise finish when it started, and the order of steps (core/reprocess.py)
    reads these times."""
    conn.execute(
        "UPDATE pipeline_runs SET status = 'ok', finished_at = clock_timestamp(), summary = %s::jsonb WHERE id = %s",
        (json.dumps(summary), run_id),
    )
    conn.commit()


def fail(conn: Connection[Any], run_id: int, error: str) -> None:
    conn.rollback()
    conn.execute(
        "UPDATE pipeline_runs SET status = 'failed', finished_at = clock_timestamp(), error = %s WHERE id = %s",
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


RUN_STATUSES = ("failed", "ok", "running", "incomplete")


def latest_run(conn: Connection[Any]) -> dict[str, Any] | None:
    """The most recent run of the chain, reconstructed from its rows (nothing ties them together
    but time). A run starts at the latest `import` row, the chain's first step, and owns every
    hourly-step row from then on. The chain runs its steps in order and stops at the first failure,
    so it has reached a result in exactly two cases: a row is `failed`, or the last step is `ok`.
    With no result, the run is `running` while its start is younger than the job's timeout
    (whether a row is `running` or the next step has not been inserted yet: a step's row is
    committed on its own connection, and a read between two steps sees neither) and `incomplete`
    once it is older, whatever its rows look like: a `running` row past the timeout is a killed
    job, not a slow step. `pipeline reprocess` writes no `import` row; its rows attach to the
    previous run."""
    rows = conn.execute(
        """
        WITH start AS (SELECT max(started_at) AS at FROM pipeline_runs WHERE step = 'import')
        SELECT r.step, r.status, s.at AS started_at, clock_timestamp() - s.at AS age
        FROM start s JOIN pipeline_runs r ON r.started_at >= s.at
        WHERE r.step = ANY(%s)
        ORDER BY r.started_at, r.id
        """,
        (list(HOURLY_STEPS),),
    ).fetchall()
    if not rows:
        return None
    failed = [r["step"] for r in rows if r["status"] == "failed"]
    failed_step = min(failed, key=HOURLY_STEPS.index) if failed else None
    if failed_step is not None:
        status = "failed"
    elif any(r["step"] == HOURLY_STEPS[-1] and r["status"] == "ok" for r in rows):
        status = "ok"
    elif rows[0]["age"] < timedelta(minutes=PIPELINE_JOB_TIMEOUT_MINUTES):
        status = "running"
    else:
        status = "incomplete"
    return {"started_at": rows[0]["started_at"].isoformat(), "status": status, "failed_step": failed_step}


def hourly_status(conn: Connection[Any]) -> dict[str, Any]:
    """What Home shows: the latest run (`latest_run`), when the chain last ran through (its
    last step succeeded), and every hourly step whose most recent run failed, in chain order."""
    row = conn.execute(
        "SELECT max(finished_at) AS at FROM pipeline_runs WHERE step = %s AND status = 'ok'", (CHAIN_THROUGH_STEP,)
    ).fetchone()
    assert row is not None
    by_step = {r["step"]: r for r in latest(conn)}
    failed = [by_step[s] for s in HOURLY_STEPS if s in by_step and by_step[s]["status"] == "failed"]
    return {
        "last_run": latest_run(conn),
        "last_ok_at": row["at"].isoformat() if row["at"] is not None else None,
        "failed": [{"step": r["step"], "started_at": r["started_at"].isoformat(), "error": r["error"]} for r in failed],
    }
