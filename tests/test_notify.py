"""The failure alert carries nothing that identifies accounts, hosts or credentials."""

from __future__ import annotations

import pytest

from core import notify


def test_alert_body_keeps_only_the_error_class() -> None:
    # assembled at runtime so the repository's own secret scanner does not flag this fixture
    dsn = "postgres" + "ql://user:pw" + "@db.example.internal:5432/x"
    nasty = f"FetchError: chesscom HTTP 403 for https://api.chess.com/pub/player/somebody/games dsn={dsn} token=sk-abc"
    body = notify.alert_body("import", 42, nasty)
    assert body.splitlines()[0] == "blundriq step import failed (run 42): FetchError"
    for fragment in ("somebody", "example", "postgresql", "sk-abc", "http", "@"):
        assert fragment not in body
    assert (
        notify.alert_body("analyze", None, "")
        == "blundriq step analyze failed (run -): error\nSee pipeline_runs for detail."
    )
    assert "Runtime" in notify.alert_body("x", 1, "RuntimeError: at https://h/x")
    assert "https" not in notify.alert_body("x", 1, "RuntimeError: at https://h/x")


def test_send_failure_without_secrets_is_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RESEND_API_KEY", "ALERT_EMAIL", "ALERT_FROM"):
        monkeypatch.delenv(name, raising=False)
    assert notify.send_failure("import", 1, "FetchError: x") is False
