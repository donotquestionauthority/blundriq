"""The repository describes the player, not one person: anyone can clone it and run it for
themselves. Code, comments, tests and documentation say "the player" (or "the user" for
setup), never the original author's name. The copyright line in LICENSE is authorship and
is the one place the name belongs; the engine's upstream AUTHORS file lists its own
contributors, whoever they are.

The pattern is assembled at runtime so this file does not trip its own check."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ALLOWED = {"LICENSE", "ui/public/engine/AUTHORS"}
# A token, not a substring: "problem", "robust" and "probe" are fine; identifiers built on the
# name ("x_to_move", or the possessive "xs_moves" with its apostrophe dropped) are not.
NAME = re.compile(r"(?<![a-z])(" + "|".join(["r" + "ob", "r" + "obert", "tavo" + "ularis"]) + r")s?(?![a-z])", re.I)


def _tracked_text_files() -> list[Path]:
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    names = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, check=True, capture_output=True).stdout.split(b"\0")
    out: list[Path] = []
    for raw in names:
        if not raw:
            continue
        rel = raw.decode()
        path = REPO / rel
        if rel in ALLOWED or not path.is_file():
            continue
        if b"\0" in path.read_bytes()[:8192]:  # binary (the engine, its source archive, images)
            continue
        out.append(path)
    return out


def test_no_tracked_file_names_the_author_outside_the_licence() -> None:
    hits: list[str] = []
    for path in _tracked_text_files():
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if NAME.search(line):
                hits.append(f"{path.relative_to(REPO)}:{n}")
    assert not hits, "say 'the player' (or 'the user' for setup) instead: " + ", ".join(hits)


@pytest.mark.parametrize(
    "line,flagged",
    [
        ("the " + "R" + "ob's games", True),
        ("x = " + "r" + "ob_to_move(ply)", True),
        ("def test_carries_" + "r" + "obs_moves():", True),
        ("a problem with robust probes", False),
        ("r" + "obson and probs", False),
        ("the player's games", False),
    ],
)
def test_the_pattern_is_a_token_not_a_substring(line: str, flagged: bool) -> None:
    assert bool(NAME.search(line)) is flagged
