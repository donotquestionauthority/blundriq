"""The hourly workflow's wiring, read from the file GitHub runs: the keepalive runs after the
chain whatever happened to it, a failed keepalive sends the application's own alert, and the
secrets reach only the steps that need them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pipeline.yml"
ALERT_SECRETS = {"RESEND_API_KEY", "ALERT_EMAIL", "ALERT_FROM"}


def _job() -> dict[str, Any]:
    doc = yaml.safe_load(WORKFLOW.read_text())
    return doc["jobs"]["run"]


def _steps() -> list[dict[str, Any]]:
    return _job()["steps"]


def _named(name: str) -> tuple[int, dict[str, Any]]:
    for i, step in enumerate(_steps()):
        if step.get("name") == name:
            return i, step
    raise AssertionError(f"no step named {name!r}")


def test_the_keepalive_runs_after_the_chain_whatever_its_outcome() -> None:
    chain_at, _ = _named("Run the chain")
    keep_at, keep = _named("Keep this schedule enabled")
    assert keep_at > chain_at
    assert keep["id"] == "keepalive"
    assert keep["if"] == "always()"
    assert "actions/workflows/pipeline.yml/enable" in keep["run"]
    # A failure must fail the step (that is what triggers the alert), with nothing from the reply.
    assert "exit 1" in keep["run"] and "|| true" not in keep["run"] and ">/dev/null 2>&1" in keep["run"]
    assert set(keep["env"]) == {"GH_TOKEN"}
    assert keep["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_a_failed_keepalive_sends_the_application_alert() -> None:
    keep_at, _ = _named("Keep this schedule enabled")
    alert_at, alert = _named("Alert when the keepalive failed")
    assert alert_at == keep_at + 1
    # always(): the job is already failing when this has to run.
    assert alert["if"] == "always() && steps.keepalive.outcome == 'failure'"
    assert alert["run"].strip() == "pipeline alert keepalive"
    assert set(alert["env"]) == ALERT_SECRETS


def _mentions(value: object, needle: str) -> bool:
    return needle in yaml.safe_dump(value) if value is not None else False


def test_secrets_reach_only_the_chain_and_the_alert() -> None:
    """Every place a step can carry text (env, run, with), the job's and the workflow's env: only
    the chain and the alert step name a secret, and each names exactly its own."""
    doc = yaml.safe_load(WORKFLOW.read_text())
    assert not _mentions(doc.get("env"), "secrets.")
    assert not _mentions(_job().get("env"), "secrets.")
    holders = [i for i, step in enumerate(_steps()) if _mentions(step, "secrets.")]
    chain_at, chain = _named("Run the chain")
    alert_at, alert = _named("Alert when the keepalive failed")
    assert holders == [chain_at, alert_at]
    for step in (chain, alert):
        assert not _mentions(step.get("run"), "secrets.") and not _mentions(step.get("with"), "secrets.")
    assert set(chain["env"]) == ALERT_SECRETS | {"DATABASE_URL"}
    assert set(alert["env"]) == ALERT_SECRETS


def test_only_the_keepalive_holds_the_token() -> None:
    """The job may write to Actions, so the checkout must not leave its token in the git config
    for the steps after it, and only the keepalive step names it."""
    steps = _steps()
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    holders = [s.get("name") for s in steps if _mentions(s, "github.token") or _mentions(s, "GITHUB_TOKEN")]
    assert holders == ["Keep this schedule enabled"]


def test_the_job_may_enable_its_workflow_and_nothing_more() -> None:
    assert _job()["permissions"] == {"contents": "read", "actions": "write"}
    assert yaml.safe_load(WORKFLOW.read_text())["permissions"] == {"contents": "read"}
