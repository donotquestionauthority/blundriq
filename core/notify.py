"""One ops email when a pipeline step fails. Best effort, never raises.

The body names the step, the run id and the error class — never usernames,
URLs, DSNs or the raw exception text, so a forwarded alert leaks nothing
(tests/test_notify.py). The pipeline_runs row holds the detail.
"""

from __future__ import annotations

import re

import httpx

from core import secrets

_RESEND = "https://api.resend.com/emails"
_SANITISE = re.compile(r"[^A-Za-z0-9 _.:()-]")


class OperatorError(RuntimeError):
    """A precondition the person running the command can fix themselves. Its message is
    written here, names only tables and steps, and is the one message a CLI may print in
    full; everything else reaches a console as a class chain."""


def error_label(exc: BaseException) -> str:
    """The exception class chain and nothing else: 'FetchError<-ConnectError'. This is the
    only form of an error that may reach a console (Actions logs are public); the text
    goes to pipeline_runs.error, which is private."""
    parts: list[str] = []
    cur: BaseException | None = exc
    while cur is not None and len(parts) < 4:
        parts.append(type(cur).__name__)
        cur = cur.__cause__ or cur.__context__
    return "<-".join(parts)


def error_detail(exc: BaseException) -> str:
    """Class and message of every link in the chain, for the PRIVATE pipeline_runs row only."""
    parts: list[str] = []
    cur: BaseException | None = exc
    while cur is not None and len(parts) < 4:
        parts.append(f"{type(cur).__name__}: {cur}")
        cur = cur.__cause__ or cur.__context__
    return " <- ".join(parts)


def alert_body(step: str, run_id: int | None, error: str) -> str:
    """Only the first token of the error (its class) survives, cleaned of punctuation
    that could carry a URL or DSN fragment."""
    error_class = error.split(":", 1)[0].split(" ", 1)[0]
    error_class = _SANITISE.sub("", error_class)[:60] or "error"
    run = run_id if run_id is not None else "-"
    return f"blundriq step {step} failed (run {run}): {error_class}\nSee pipeline_runs for detail."


def send_failure(step: str, run_id: int | None, error: str) -> bool:
    """True iff Resend accepted the message. Missing secrets or a network error → False."""
    try:
        s = secrets.alerts()
    except secrets.MissingSecret:
        return False
    payload = {
        "from": s.alert_from,
        "to": [s.alert_email],
        "subject": f"[blundriq] {step} failed",
        "text": alert_body(step, run_id, error),
    }
    try:
        r = httpx.post(_RESEND, json=payload, headers={"Authorization": f"Bearer {s.resend_api_key}"}, timeout=10.0)
    except httpx.HTTPError:
        return False
    return r.status_code < 400
