"""The reprocess batch and its proof. `pipeline reprocess` runs the five required steps in order
with no limits and stops at the first failure, each as its own logged run; `engine-status` is the
acceptance: it fails closed on pending work, on a failed, missing, running or stale step, and
on steps out of order, and a later step's success never hides an earlier step's failure."""

from __future__ import annotations

import argparse
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core import reprocess
from core.constants import PLAYER_ID, STOCKFISH_STAMP
from core.settings import Settings
from pipeline import cli

S = Settings()
STEPS = list(reprocess.REQUIRED_STEPS)


def _player(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'me')", (PLAYER_ID,))
    conn.commit()


def _runs(conn: psycopg.Connection[DictRow], rows: list[tuple[str, str, int]]) -> None:
    """Pipeline runs as (step, status, minutes after the boundary); each takes one minute."""
    conn.execute("DELETE FROM pipeline_runs")
    for step, status, minutes in rows:
        conn.execute(
            """
            INSERT INTO pipeline_runs (step, status, started_at, finished_at)
            SELECT %s, %s, b + make_interval(mins => %s),
                   CASE WHEN %s = 'running' THEN NULL ELSE b + make_interval(mins => %s) END
            FROM (SELECT max(applied_at) AS b FROM schema_version) s
            """,
            (step, status, minutes, status, minutes + 1),
        )
    conn.commit()


def _good() -> list[tuple[str, str, int]]:
    return [(step, "ok", 10 * (i + 1)) for i, step in enumerate(STEPS)]


def _problems(conn: psycopg.Connection[DictRow]) -> list[str]:
    return reprocess.status(conn, S).problems


def test_a_complete_batch_passes(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _runs(clean, _good())
    found = reprocess.status(clean, S)
    assert found.ok and found.engine == STOCKFISH_STAMP and found.since is not None
    assert (found.games_pending, found.boards_pending) == (0, 0)
    assert [s.problem for s in found.steps] == [None] * 5


def test_a_failed_step_is_not_hidden_by_the_steps_after_it(clean: psycopg.Connection[DictRow]) -> None:
    """Analysis finishes, generate-puzzles fails and rolls back, Review, the evaluations and the
    snapshot succeed after it. Every backlog is zero and the last row is
    `review-snapshot: ok`, and the batch is still not complete."""
    _player(clean)
    rows = _good()
    rows[1] = ("generate-puzzles", "failed", 20)
    _runs(clean, rows)
    assert _problems(clean) == ["generate-puzzles: latest run is failed"]
    # the retry, followed by the three steps after it: complete
    _runs(
        clean,
        rows
        + [
            ("generate-puzzles", "ok", 60),
            ("review", "ok", 70),
            ("position-evals", "ok", 80),
            ("review-snapshot", "ok", 90),
        ],
    )
    assert _problems(clean) == []
    # the retry alone, with the later steps left as they were: they ran before it, so they are stale
    _runs(clean, rows + [("generate-puzzles", "ok", 60)])
    assert _problems(clean) == [
        "review: ran before the step before it finished",
        "position-evals: ran before the step before it finished",
        "review-snapshot: ran before the step before it finished",
    ]


def test_missing_running_stale_and_out_of_order_steps_fail_closed(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _runs(clean, _good()[:-1])
    assert _problems(clean) == ["review-snapshot: no run recorded"]
    rows = _good()
    rows[3] = ("position-evals", "running", 40)
    _runs(clean, rows)
    assert _problems(clean) == ["position-evals: latest run is running"]
    rows = _good()
    rows[0] = ("analyze", "ok", -30)  # before the boundary
    _runs(clean, rows)
    assert _problems(clean) == ["analyze: latest run is from before the batch boundary"]
    rows = _good()
    rows[2], rows[3] = ("review", "ok", 40), ("position-evals", "ok", 30)  # evaluations before Review
    _runs(clean, rows)
    assert _problems(clean) == ["position-evals: ran before the step before it finished"]


def test_an_explicit_boundary_overrides_the_schema_one(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _runs(clean, _good())
    since = reprocess.boundary(clean)
    assert since is not None
    later = reprocess.status(clean, S, since + timedelta(hours=1))
    assert [s.problem for s in later.steps] == ["latest run is from before the batch boundary"] * 5


def test_pending_work_fails_closed_with_its_count(clean: psycopg.Connection[DictRow]) -> None:
    """A game the old engine analysed and a board no row covers each fail the gate on their own,
    whatever the step evidence says."""
    import json

    _player(clean)
    _runs(clean, _good())
    conn = clean
    for gid, engine in ((1, "stockfish_0"), (2, STOCKFISH_STAMP)):
        conn.execute(
            "INSERT INTO chess_games (id, platform, platform_game_id, played_at, moves, variant, analysis_engine,"
            " analysis_depth, ply_analysis_depth) VALUES (%s, 'lichess', %s, now(), %s::jsonb, 'standard', %s, 18, 18)",
            (gid, f"g{gid}", json.dumps(["e4", "e5"]), engine),
        )
        conn.execute(
            "INSERT INTO player_games (player_id, chess_game_id, player_color, source, analyzed_at_depth)"
            " VALUES (%s, %s, 'white', 'lichess', 18)",
            (PLAYER_ID, gid),
        )
    conn.commit()
    found = reprocess.status(conn, S)
    assert (found.games_pending, found.boards_pending) == (1, 0) and found.problems == ["1 games pending"]
    conn.execute("UPDATE chess_games SET analysis_engine = %s", (STOCKFISH_STAMP,))
    conn.commit()
    assert _problems(conn) == []


# --- the CLI ---------------------------------------------------------------------------------


def _spy(calls: list[tuple[str, Any, Any, Any]], name: str, fail: bool = False) -> Any:
    def step(_conn: Any, args: argparse.Namespace) -> dict[str, Any]:
        calls.append(
            (name, getattr(args, "limit", "-"), getattr(args, "evals_limit", "-"), getattr(args, "workers", "-"))
        )
        return {"failed": 1 if fail else 0}

    return step


def test_reprocess_runs_the_five_steps_in_order_without_limits_and_stops_at_a_failure(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _player(clean)
    calls: list[tuple[str, Any, Any, Any]] = []
    steps = cli.hourly_steps()
    spied = {name: _spy(calls, name) for name in steps}
    monkeypatch.setattr(cli, "hourly_steps", lambda: spied)
    assert cli.main(["reprocess", "--workers", "8"]) == 0
    capsys.readouterr()
    assert calls == [(name, None, 0, 8) for name in STEPS]
    rows = clean.execute("SELECT step, status FROM pipeline_runs ORDER BY id").fetchall()
    assert [(r["step"], r["status"]) for r in rows] == [(name, "ok") for name in STEPS]
    assert reprocess.status(clean, S).ok

    calls.clear()
    spied["generate-puzzles"] = _spy(calls, "generate-puzzles", fail=True)
    assert cli.main(["reprocess"]) == 1
    err = capsys.readouterr().err
    assert "generate-puzzles: FAILED (StepFailed)" in err
    assert [c[0] for c in calls] == ["analyze", "generate-puzzles"]
    assert reprocess.status(clean, S).problems == [
        "generate-puzzles: latest run is failed",
        "review: ran before the step before it finished",
        "position-evals: ran before the step before it finished",
        "review-snapshot: ran before the step before it finished",
    ]


def test_position_evals_limit_zero_reaches_the_step_as_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runbook's `--limit 0`: the adapter passes None (all) to the step, and no flag means the
    hourly cap. Through the real parser."""
    from core.review import evals

    seen: list[tuple[int | None, int]] = []
    monkeypatch.setattr(evals, "run", lambda _conn, _s, *, limit, workers: seen.append((limit, workers)) or {})
    monkeypatch.setattr(cli.settings, "load", lambda _conn: S)
    parser = cli.build_parser()
    for argv in (["position-evals", "--limit", "0", "--workers", "8"], ["position-evals"]):
        cli._step_position_evals(None, parser.parse_args(argv))  # type: ignore[arg-type]
    assert seen == [(None, 8), (40, 1)]


def test_engine_status_prints_counts_names_and_times_never_error_text(
    clean: psycopg.Connection[DictRow], app_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _player(clean)
    rows = _good()
    rows[1] = ("generate-puzzles", "failed", 20)
    _runs(clean, rows)
    marker = "error-text-marker-5c1e"
    clean.execute("UPDATE pipeline_runs SET error = %s WHERE step = 'generate-puzzles'", (f"boom {marker}",))
    clean.commit()
    assert cli.main(["engine-status"]) == 1
    out = capsys.readouterr()
    assert marker not in out.out + out.err
    assert "generate-puzzles   failed" in out.out and "latest run is failed" in out.out
    assert "NOT complete (1 problems)" in out.err
    _runs(clean, _good())
    assert cli.main(["engine-status"]) == 0
    out = capsys.readouterr()
    assert "engine-status: complete" in out.out and f"engine {STOCKFISH_STAMP}" in out.out
    since = reprocess.boundary(clean)
    assert since is not None
    assert cli.main(["engine-status", "--since", (since + timedelta(days=1)).isoformat()]) == 1
    assert "before the batch boundary" in capsys.readouterr().out
    # a boundary with no offset cannot be compared with the run times: refused in full, as an
    # operator error, not a class chain
    assert cli.main(["engine-status", "--since", "2026-10-08T20:00:00"]) == 1
    assert "--since needs a UTC offset" in capsys.readouterr().err
