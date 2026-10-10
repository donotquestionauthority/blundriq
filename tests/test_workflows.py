"""The hourly workflow's wiring, read from the file GitHub runs: the keepalive runs after the
chain whatever happened to it and leaves a manual pause alone, a failed keepalive sends the
application's own alert, the secrets reach only the steps that need them, and both workflows
download the engine the constants name."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from typing import Any

import yaml

from core.constants import PIPELINE_JOB_TIMEOUT_MINUTES, STOCKFISH_VERSION

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pipeline.yml"
CI = WORKFLOW.with_name("ci.yml")
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


# What a stub `gh` answers, per outcome: the workflow's state, and whether the enable PUT works.
_STUB = r"""#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$*" in
  *-X\ PUT*) [ "$GH_PUT_OK" = 1 ] && exit 0 || exit 1 ;;
  *--jq\ .state*) [ -n "$GH_STATE" ] && { echo "$GH_STATE"; exit 0; } || exit 1 ;;
esac
exit 2
"""


def _keepalive(tmp_path: Path, state: str, put_ok: bool) -> tuple[int, str, list[str]]:
    """Run the checked-in keepalive shell with a stub gh: (exit code, stdout, the gh calls)."""
    _, keep = _named("Keep this schedule enabled")
    gh = tmp_path / "gh"
    gh.write_text(_STUB)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log"
    log.write_text("")
    script = keep["run"].replace("${{ github.repository }}", "owner/repo")
    # A fixed environment: the stub first on PATH, the shells after it, nothing inherited.
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "GH_LOG": str(log),
        "GH_STATE": state,
        "GH_PUT_OK": "1" if put_ok else "0",
    }
    done = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)
    return done.returncode, done.stdout, log.read_text().splitlines()


def test_the_keepalive_enables_a_workflow_github_disabled_and_leaves_a_manual_pause_alone(tmp_path: Path) -> None:
    """A workflow switched off by inactivity is enabled; one the operator disabled by hand (a
    reprocess that must not overlap the chain) stays disabled, and the step still succeeds; an
    `active` one is enabled again (a no-op on GitHub's side). A state the step cannot read, or an
    enable that fails, fails the step with its one fixed line."""
    code, out, calls = _keepalive(tmp_path, "disabled_inactivity", put_ok=True)
    assert code == 0 and out == "" and ["-X PUT" in c for c in calls] == [False, True]
    code, out, calls = _keepalive(tmp_path, "disabled_manually", put_ok=True)
    assert code == 0 and out == "keepalive: paused by operator\n" and len(calls) == 1
    code, out, calls = _keepalive(tmp_path, "active", put_ok=True)
    assert code == 0 and len(calls) == 2
    code, out, _ = _keepalive(tmp_path, "", put_ok=True)
    assert code == 1 and out == "keepalive: FAILED\n"
    code, out, _ = _keepalive(tmp_path, "active", put_ok=False)
    assert code == 1 and out == "keepalive: FAILED\n"


def test_both_workflows_download_the_engine_the_constants_name() -> None:
    """The pipeline refuses any other version at run time; the download must name the same one,
    or every hourly analysis fails. CI installs it too, so the real-engine tests run there."""
    for path in (WORKFLOW, CI):
        doc = yaml.safe_load(path.read_text())
        runs = [s.get("run", "") for job in doc["jobs"].values() for s in job["steps"]]
        downloads = [r for r in runs if "official-stockfish/Stockfish/releases/download/" in r]
        assert len(downloads) == 1, path.name
        assert f"/releases/download/sf_{STOCKFISH_VERSION}/" in downloads[0], path.name
        assert "/usr/local/bin/stockfish" in downloads[0]


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


def test_the_jobs_timeout_is_the_constant_home_reads() -> None:
    """core/runs.py declares a run without a result over once its import is this old."""
    assert _job()["timeout-minutes"] == PIPELINE_JOB_TIMEOUT_MINUTES


def test_the_job_may_enable_its_workflow_and_nothing_more() -> None:
    assert _job()["permissions"] == {"contents": "read", "actions": "write"}
    assert yaml.safe_load(WORKFLOW.read_text())["permissions"] == {"contents": "read"}
