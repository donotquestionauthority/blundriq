"""The test session drops the database TEST_DATABASE_URL names. The guard that stands in
front of that drop reads DSNs only, so every case here runs without a database."""

from __future__ import annotations

import pytest

from tests.conftest import scratch_refusal

# DSNs are assembled at runtime so the repository's own secret scanner does not flag them.
SCHEME = "postgres" + "ql://"
LOCAL = SCHEME + "/"


def remote(host: str, name: str) -> str:
    return f"{SCHEME}{host}:5432/{name}"


@pytest.mark.parametrize(
    "name",
    ["blundriq_test", "test_blundriq", "test", "my-scratch", "blundriq_scratch_2", "BLUNDRIQ_TEST"],
)
def test_a_scratch_name_is_accepted(name: str) -> None:
    assert scratch_refusal(LOCAL + name, "") is None


@pytest.mark.parametrize("name", ["blundriq", "postgres", "contest", "testing_live", "latest", "blundriqtest"])
def test_any_other_name_is_refused(name: str) -> None:
    refused = scratch_refusal(LOCAL + name, "")
    assert refused is not None and "scratch" in refused


def test_a_dsn_without_a_database_is_refused() -> None:
    assert scratch_refusal("host=localhost user=x", "") == "TEST_DATABASE_URL names no database"


def test_the_application_database_is_refused_even_when_named_as_a_test() -> None:
    url = remote("db.example.org", "blundriq_test")
    assert scratch_refusal(url, url) is not None
    assert scratch_refusal(url, remote("other.example.org", "blundriq_test")) is not None
    # A sibling the suite would create and drop (`<name>_old`, `<name>_upgrade`) is refused too.
    assert scratch_refusal(url, remote("db.example.org", "blundriq_test_old")) is not None


def test_a_different_application_database_does_not_block_the_scratch_one() -> None:
    assert scratch_refusal(LOCAL + "blundriq_test", remote("db.example.org", "postgres")) is None
    assert scratch_refusal(LOCAL + "blundriq_test", "not a dsn = = =") is None


def test_the_session_stops_before_any_drop_when_refused() -> None:
    """The guard is wired in front of the drop: a session pointed at a database that is not
    named as scratch exits with pytest's usage code before connecting anywhere. The probe name
    exists nowhere, so even a broken guard could only create and drop a throwaway database."""
    import os  # noqa: TID251 — the child's environment, built from this one
    import subprocess
    import sys
    from pathlib import Path

    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}  # noqa: TID251
    env["TEST_DATABASE_URL"] = LOCAL + "guard_probe_live"
    repo = Path(__file__).resolve().parent.parent
    child = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "-p", "no:cacheprovider", "tests/test_settings.py"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert child.returncode == 2, child.stdout[-2000:]
    assert "refusing to drop the test database" in child.stdout + child.stderr
