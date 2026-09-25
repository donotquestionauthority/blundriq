"""The `pipeline` command's boundaries: console output never carries error text
(GitHub Actions logs are public), a step with failed work is a failed run, and
a database that cannot be reached still yields a clean one-line failure."""

from __future__ import annotations

import argparse
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.rows import DictRow

from core import notify
from core.constants import PLAYER_ID
from core.ingest.records import FetchError
from pipeline import cli

HOST_MARKER = "host-marker-7f3a9c"
TOKEN_MARKER = "token-marker-2b8e1d"
# Fixture DSNs are assembled at runtime so the repository's own secret scanner does not flag them.
BAD_DSN = "postgres" + f"ql://user:{TOKEN_MARKER}" + f"@{HOST_MARKER}.invalid:5432/x?connect_timeout=1"


def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int | None, str]]:
    sent: list[tuple[str, int | None, str]] = []
    monkeypatch.setattr(notify, "send_failure", lambda step, run_id, error: sent.append((step, run_id, error)) or True)
    return sent


def test_error_label_is_the_class_chain_only() -> None:
    try:
        try:
            raise ConnectionError(f"dsn={BAD_DSN}")
        except ConnectionError as inner:
            raise FetchError(f"chesscom transport error at https://{HOST_MARKER}/x?token={TOKEN_MARKER}") from inner
    except FetchError as exc:
        label = notify.error_label(exc)
    assert label == "FetchError<-ConnectionError"


def test_cli_failure_output_is_redacted(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = clean
    conn.execute("INSERT INTO players (id, chesscom_username, lichess_username) VALUES (%s, 'me', 'me')", (PLAYER_ID,))
    conn.commit()
    sent = _capture_alerts(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed to reach {HOST_MARKER} with {TOKEN_MARKER}", request=request)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler)))
    code = cli.main(["import", "--platform", "chesscom"])
    out = capsys.readouterr()
    assert code == 1
    assert "import: FAILED (FetchError<-ConnectError)" in out.err
    for marker in (HOST_MARKER, TOKEN_MARKER, "Traceback", "https://"):
        assert marker not in out.out + out.err
    assert sent == []  # a single step invocation does not alert; `run` does
    row = conn.execute("SELECT status, error FROM pipeline_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row and row["status"] == "failed" and HOST_MARKER in row["error"]  # the private row keeps the detail


def test_step_with_failed_work_is_a_failed_run(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = clean
    conn.execute("INSERT INTO players (id) VALUES (%s)", (PLAYER_ID,))  # no usernames: import has nothing to fetch
    conn.commit()
    sent = _capture_alerts(monkeypatch)

    def mixed(_conn: Any, _args: argparse.Namespace) -> dict[str, Any]:
        return {"pending": 3, "analyzed": 2, "failed": 1, "failures": ["42: EngineTerminatedError<-BrokenPipeError"]}

    monkeypatch.setattr(cli, "_step_analyze", mixed)
    code = cli.main(["run"])
    out = capsys.readouterr()
    assert code == 1
    assert "analyze: FAILED (StepFailed)" in out.err
    assert sent == [("analyze", sent[0][1], "StepFailed")] and sent[0][1] is not None
    rows = conn.execute("SELECT step, status, error FROM pipeline_runs ORDER BY id").fetchall()
    assert [(r["step"], r["status"]) for r in rows] == [("import", "ok"), ("match", "ok"), ("analyze", "failed")]
    assert '"analyzed": 2' in rows[-1]["error"]  # the partial summary is kept in the private row
    assert "housekeep" not in [r["step"] for r in rows]  # the chain stopped


def test_unreachable_database_fails_in_one_clean_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", BAD_DSN)
    sent = _capture_alerts(monkeypatch)
    code = cli.main(["run"])
    out = capsys.readouterr()
    assert code == 1
    assert "import: FAILED before it could be recorded (OperationalError)" in out.err
    for marker in (HOST_MARKER, TOKEN_MARKER, "Traceback"):
        assert marker not in out.out + out.err
    assert sent == [("import", None, "OperationalError")]


def test_uncaught_command_error_prints_no_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", BAD_DSN)
    code = cli.main(["db", "version"])
    out = capsys.readouterr()
    assert code == 1 and "pipeline db: FAILED (OperationalError)" in out.err
    assert HOST_MARKER not in out.err and TOKEN_MARKER not in out.err


def test_the_hourly_chain_generates_puzzles_after_analysis(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generation is part of the hour, not a separate thing to remember to run, and it
    runs after analysis because it reads what analysis wrote."""
    conn = clean
    conn.execute("INSERT INTO players (id) VALUES (%s)", (PLAYER_ID,))  # no usernames: nothing to fetch
    conn.commit()
    monkeypatch.setattr(cli, "_step_analyze", lambda _conn, _args: {"pending": 0, "analyzed": 0, "failed": 0})
    assert cli.main(["run"]) == 0
    capsys.readouterr()
    steps = [r["step"] for r in conn.execute("SELECT step FROM pipeline_runs ORDER BY id").fetchall()]
    assert steps == ["import", "match", "analyze", "generate-puzzles", "srs-maintain", "import-opponents", "housekeep"]


def test_generate_puzzles_runs_on_its_own(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = clean
    conn.execute("INSERT INTO players (id) VALUES (%s)", (PLAYER_ID,))
    conn.commit()
    assert cli.main(["generate-puzzles"]) == 0
    out = capsys.readouterr().out
    assert "generate-puzzles" in out and "blunder" in out
    row = conn.execute("SELECT step, status FROM pipeline_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row and (row["step"], row["status"]) == ("generate-puzzles", "ok")


def test_import_corpus_needs_a_csv() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["import-corpus"])


def test_srs_maintain_and_the_funnel_run_on_their_own(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = clean
    conn.execute("INSERT INTO players (id) VALUES (%s)", (PLAYER_ID,))
    conn.commit()
    assert cli.main(["srs-maintain"]) == 0
    assert '"demoted": 0' in capsys.readouterr().out
    row = conn.execute("SELECT step, status FROM pipeline_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row and (row["step"], row["status"]) == ("srs-maintain", "ok")
    assert cli.main(["blunder-funnel"]) == 0
    out = capsys.readouterr().out
    assert "window 500 games, threshold 3, focus rapid_plus" in out
    assert "not already covered by the repertoire" in out
    runs_row = conn.execute("SELECT count(*) AS n FROM pipeline_runs").fetchone()
    assert runs_row and runs_row["n"] == 1, "a report, not a step"


def test_an_operator_error_is_printed_in_full(
    app_env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A precondition the operator can fix is told to them in words; everything else
    stays a class chain."""
    import argparse

    from core.notify import OperatorError

    def _boom(_args: object) -> int:
        raise OperatorError("target table puzzles is not empty; migrate only into a fresh database")

    parser = argparse.ArgumentParser()
    parser.add_argument("group")
    parser.set_defaults(func=_boom)
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    assert cli.main(["migrate"]) == 1
    err = capsys.readouterr().err
    assert "target table puzzles is not empty" in err and "Traceback" not in err
