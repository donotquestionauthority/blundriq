"""A full reprocess — every game and every board under the current engine — and the proof it ran.

`pipeline reprocess` runs REQUIRED_STEPS in this order, each as its own logged step, and stops
at the first failure; rerunning it is the resume (finished games and boards are not redone).
`pipeline engine-status` is read-only and fails closed: it passes only when nothing is pending
under the current engine (`core.analysis.run.worklist`, `core.review.mistakes.eval_candidates`)
AND the latest run of every required step is `ok`, started after the batch boundary, and in
order (each step started after the one before it finished). A step that failed and rolled back
is not hidden by later steps that succeeded, a step rerun out of order invalidates what came
after it, and a missing or still-running step is not evidence. The boundary defaults to the
latest schema change (`schema_version.applied_at`): a reprocess follows the migration that
made it necessary, on the rehearsal copy and in production alike.

Nothing here prints: the CLI reports labels, counts, step names and timestamps, never a failed
run's stored error text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg import Connection

from core.analysis.run import worklist
from core.constants import STOCKFISH_STAMP
from core.review.mistakes import eval_candidates
from core.settings import Settings

REQUIRED_STEPS = ("analyze", "generate-puzzles", "review", "position-evals", "review-snapshot")


@dataclass(frozen=True)
class StepEvidence:
    step: str
    status: str | None  # None: no run recorded
    started_at: datetime | None
    finished_at: datetime | None
    problem: str | None  # None when this step's evidence passes


@dataclass(frozen=True)
class Status:
    engine: str
    since: datetime | None
    games_pending: int
    boards_pending: int
    steps: list[StepEvidence] = field(default_factory=lambda: [])

    @property
    def problems(self) -> list[str]:
        out: list[str] = []
        if self.since is None:
            out.append("no batch boundary: schema_version has no applied_at")
        if self.games_pending:
            out.append(f"{self.games_pending} games pending")
        if self.boards_pending:
            out.append(f"{self.boards_pending} boards pending")
        out.extend(f"{s.step}: {s.problem}" for s in self.steps if s.problem)
        return out

    @property
    def ok(self) -> bool:
        return not self.problems


def boundary(conn: Connection[Any]) -> datetime | None:
    row = conn.execute("SELECT max(applied_at) AS at FROM schema_version").fetchone()
    return row["at"] if row else None


def status(conn: Connection[Any], config: Settings, since: datetime | None = None) -> Status:
    since = since or boundary(conn)
    games = len(worklist(conn, config.analysis_game_limit))
    boards, _ = eval_candidates(conn, config.review_position_max_ply, config.review_history_months, 1)
    latest = {
        r["step"]: r
        for r in conn.execute(
            """
            SELECT DISTINCT ON (step) step, status, started_at, finished_at
            FROM pipeline_runs WHERE step = ANY(%s) ORDER BY step, started_at DESC
            """,
            (list(REQUIRED_STEPS),),
        ).fetchall()
    }
    steps: list[StepEvidence] = []
    previous_finished: datetime | None = None
    for name in REQUIRED_STEPS:
        row = latest.get(name)
        problem: str | None = None
        if row is None:
            problem = "no run recorded"
        elif row["status"] != "ok":
            problem = f"latest run is {row['status']}"
        elif since is not None and row["started_at"] < since:
            problem = "latest run is from before the batch boundary"
        elif previous_finished is not None and row["started_at"] < previous_finished:
            problem = "ran before the step before it finished"
        if problem is None and row is not None and row["finished_at"] is not None:
            previous_finished = row["finished_at"]  # only a valid step is the reference for the next
        steps.append(
            StepEvidence(
                name,
                row["status"] if row else None,
                row["started_at"] if row else None,
                row["finished_at"] if row else None,
                problem,
            )
        )
    return Status(STOCKFISH_STAMP, since, games, boards, steps)
