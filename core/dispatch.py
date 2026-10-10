"""Start the hourly workflow from the app: one POST to GitHub's workflow_dispatch endpoint.

GitHub answers 204 with no body and no run id; the run shows up as `pipeline_runs` rows
once the job's setup is done (core/runs.py reads those). Nothing here logs: the repository
name is an identifier (rule 2) and GitHub's reply is never read into a message.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from core.secrets import DispatchSecrets

GITHUB_API = "https://api.github.com"
WORKFLOW_FILE = "pipeline.yml"
REF = "main"
TIMEOUT_SECONDS = 10.0


class DispatchError(RuntimeError):
    """The dispatch did not happen; `detail` is safe to show (a status code, never a body)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def run_pipeline(creds: DispatchSecrets, http: httpx.Client) -> datetime:
    """Ask GitHub to run the workflow on `main`; the time of the accepted request."""
    url = f"{GITHUB_API}/repos/{creds.repo}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        response = http.post(url, json={"ref": REF}, headers=headers)
    except httpx.HTTPError as exc:
        raise DispatchError(502, "GitHub unreachable") from exc
    if response.status_code != 204:
        raise DispatchError(502, f"GitHub refused the dispatch (HTTP {response.status_code})")
    return datetime.now(UTC)
