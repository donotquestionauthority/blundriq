"""Prove the scanner rules in .gitleaks.toml fire on synthetic examples and that the
allowlists admit only what they are meant to. Skips when gitleaks is not installed
(CI installs 8.24.3; the hooks require any version >= 8.24). Probe values are
high-entropy like real secrets because newer gitleaks applies an entropy floor."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / ".gitleaks.toml"

# (rule id, parts joined at runtime). Each probe is split so that this file itself never
# contains a matching literal — the hooks scan this file too.
MUST_FLAG: list[tuple[str, tuple[str, ...]]] = [
    ("postgres-dsn", ("postgresql://", "review:fake-secret@localhost:5432/db")),
    ("postgres-dsn", ("postgres://", "user:pw@", "db.example.internal:5432/app")),
    ("supabase-host", ("https://abcdefghijklmnopqrst.", "supabase.co/rest/v1")),
    ("supabase-access-token", ("sbp_", "9f3c1e7a2b4d8e6f0a5c7b9d1e3f2a4c6b8d0e2f")),
    ("render-host", ("https://blundriq-api-xyz1.", "onrender.com")),
    ("render-service-id", ("RENDER_SERVICE_ID=srv-", "c123456789abcdefghij")),
    ("vercel-id", ("VERCEL_PROJECT_ID=prj_", "1234567890abcdefghijklmn")),
    ("aws-host-or-arn", ("https://sqs.us-east-1.", "amazonaws.com/123456789012/queue")),
    ("aws-host-or-arn", ("arn:", "aws:iam::", "123456789012", ":role/x")),
    ("aws-account-id", ("AWS_ACCOUNT_ID=", "123456789012")),
    ("resend-key", ("re_", "Qz7pLm2XvK9tRw4bNc8dHy3f")),
    ("anthropic-key", ("sk-", "ant-api03-Qz7pLm2XvK9tRw4bNc8dHy3fJs6gAe1uBo5iTx0kMr")),
    ("openai-key", ("sk-", "proj-Hx4kQ9vT2mLp7cWz1nRb8yFj5sGd3aEu6oIq0tYv")),
    (
        "jwt-token",
        (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            ".eyJpc3MiOiJzdXBhYmFzZSIsInJvbGUiOiJhbm9uIiwiaWF0IjoxNzAwMDAwMDAwfQ",
            ".Qz7pLm2XvK9tRw4bNc8dHy3fJs6gAe1uBo5iTx0kMrZq",
        ),
    ),
    ("bcrypt-hash", ("$2b$12$", "C6UzMDM.H6dfI/f/IKcEeO4Zp0MnjJyIm1WBNTUqXqmIWy7NiT1Ne")),
    ("email-address", ("someone@", "gmail.com")),
    ("email-address", ("review@", "example.com.invalid")),
    ("course-vendor", ("imported from chess", "able")),
]
# text that must NOT be reported (the allowlists)
MUST_PASS = [
    "postgresql://postgres:ci@localhost:5432/blundriq_test",
    "postgresql://postgres:ci@localhost:5432/blundriq_install",
    "postgresql://localhost/blundriq_test",
    "Co-Authored-By: Claude <noreply@anthropic.com>",
    "contact@example.com",
    "a twelve digit number in prose: 123456789012 games",
]

gitleaks = shutil.which("gitleaks")
pytestmark = pytest.mark.skipif(gitleaks is None, reason="gitleaks not installed")


def _findings(tmp_path: Path, text: str) -> set[str]:
    probe = tmp_path / "probe.txt"
    probe.write_text(text + "\n")
    report = tmp_path / "report.json"
    assert gitleaks is not None
    subprocess.run(
        [
            gitleaks,
            "dir",
            str(probe),
            "--config",
            str(CONFIG),
            "--no-banner",
            "--exit-code",
            "0",
            "--report-format",
            "json",
            "--report-path",
            str(report),
        ],
        check=True,
        capture_output=True,
    )
    if not report.exists() or report.stat().st_size == 0:
        return set()
    return {f["RuleID"] for f in json.loads(report.read_text() or "[]")}


@pytest.mark.parametrize(("rule", "parts"), MUST_FLAG, ids=[f"{r}:{i}" for i, (r, _) in enumerate(MUST_FLAG)])
def test_rule_fires(tmp_path: Path, rule: str, parts: tuple[str, ...]) -> None:
    assert rule in _findings(tmp_path, "".join(parts))


@pytest.mark.parametrize("text", MUST_PASS)
def test_allowlisted_text_passes(tmp_path: Path, text: str) -> None:
    assert _findings(tmp_path, text) == set()
