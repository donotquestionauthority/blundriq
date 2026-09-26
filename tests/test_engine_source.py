"""The record of what the pinned corresponding-source archive contains.

`ui/public/engine/stockfish-source.tar.gz` is the GPL Corresponding Source for the engine the
browser runs. Its SHA-256 is pinned by `ui/src/engine/engineAssets.test.ts`; that pin is the gate
(a byte changed anywhere inside is a different archive and fails CI). This file is what the pin
stands for: expanded, the archive carries exactly two `email-address` findings under the project's
scanner rules, both upstream attribution (the `author` field of `nmrugg-stockfish.js/package.json`
and the MIT header of the bundled `examples/js/chess.min.js`), the one exception CLAUDE.md rule 2
admits. A new archive is a new content review and a new pin — never a re-run that happens to pass.

Findings are asserted by exact identity — count, path, rule, and a digest of each matched text —
so nothing here has to spell an address out. Skips when gitleaks is not installed (CI installs
8.24.3, whose `dir` command exists).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / ".gitleaks.toml"
ARCHIVE = REPO / "ui" / "public" / "engine" / "stockfish-source.tar.gz"
ROOT = "stockfish-corresponding-source"

# (path inside the archive, rule id, SHA-256 of the matched text), sorted by path.
EXPECTED: list[tuple[str, str, str]] = [
    (
        f"{ROOT}/nmrugg-stockfish.js/examples/js/chess.min.js",
        "email-address",
        "e4d4d8b567e27508dc42eee2886af54a1c00a5895111c2ca5979cb1b9e2efb08",
    ),
    (
        f"{ROOT}/nmrugg-stockfish.js/package.json",
        "email-address",
        "a5f771da6e3840056b330d6ee245a2f8aeae2e1b389082a0acf09cca18ab8240",
    ),
]

gitleaks = shutil.which("gitleaks")
pytestmark = pytest.mark.skipif(gitleaks is None, reason="gitleaks not installed")


def _skipping_data_filter(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
    """`tarfile`'s `data` filter, but a member it refuses is skipped rather than fatal: the archive
    holds an `examples/src -> ../src` symlink whose target resolves outside the member's own
    directory, and the filter raises on it."""
    try:
        return tarfile.data_filter(member, dest)
    except tarfile.FilterError:
        return None


def _expand(into: Path) -> Path:
    with tarfile.open(ARCHIVE) as tar:
        tar.extractall(into, filter=_skipping_data_filter)
    return into


def test_pinned_archive_carries_exactly_the_two_upstream_attribution_findings(tmp_path: Path) -> None:
    tree = _expand(tmp_path / "src")
    assert (tree / ROOT).is_dir()
    report = tmp_path / "report.json"
    assert gitleaks is not None
    subprocess.run(
        [
            gitleaks,
            "dir",
            str(tree),
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
    raw = json.loads(report.read_text() or "[]") if report.exists() else []
    found = sorted(
        (
            Path(f["File"]).relative_to(tree).as_posix(),
            f["RuleID"],
            hashlib.sha256(f["Secret"].encode()).hexdigest(),
        )
        for f in raw
    )
    # Multiplicity included: an address added beside an existing one is a third finding.
    assert len(raw) == len(EXPECTED)
    assert found == EXPECTED
