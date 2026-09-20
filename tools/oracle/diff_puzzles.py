#!/usr/bin/env python3
"""Compare the puzzles this system generates against the ones the old database holds.

Two checks.

**Acceptance maps.** Every stored missed-mate map in the *old* database — active or not —
is rebuilt from the current solver and compared. The reference is the archive, which
nothing here writes to, so it holds whatever else runs. A difference means a solver change
that would grade a puzzle differently from the way it was originally solved.

**Generation.** The generated puzzles in the scratch database are deleted, the generators
are run, and what comes back is compared with the old rows at the same board or line —
including each one's acceptance map, so a regenerated map is checked against the archive
rather than against itself.

Deleting a puzzle cascades its spaced-repetition row away, so generation refuses to run
outside a database named as a throwaway. Build one by migrating from the old database; do
not point this at the database that is being practised against.

    python tools/oracle/diff_puzzles.py [--maps] [--generation]

With no flags it runs both, maps first. Exits 1 if anything differs. The settings the
generators read (the two windows and the two occurrence thresholds) decide what qualifies,
so this prints them: a comparison against a different threshold is not a parity result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import oracle, scratch  # noqa: E402

from core import oracle as q  # noqa: E402
from core import settings  # noqa: E402
from core.chess.mate_acceptance import build_acceptance_map  # noqa: E402
from core.puzzles.generate import run as generators  # noqa: E402


def _report(title: str, diffs: list[str], checked: int, noun: str) -> int:
    """Differences, counted by the thing being compared rather than by game."""
    subjects = {d.split(":", 2)[1] if d.startswith(("board:", "line:")) else d.split(":", 1)[0] for d in diffs}
    print(f"\n== {title}: {checked} {noun} checked, {len(subjects)} with {len(diffs)} differences")
    for line in diffs[:200]:
        print("  " + line)
    if len(diffs) > 200:
        print(f"  ... {len(diffs) - 200} more")
    return 1 if diffs else 0


def _key(row: dict[str, Any]) -> str:
    return f"line:{row['repertoire_line_id']}" if row["is_repertoire"] else f"board:{row['canonical_fen']}"


def diff_maps() -> int:
    """Rebuild every archived missed-mate map and compare. Read-only on both databases."""
    diffs: list[str] = []
    with oracle() as old:
        stored = q.acceptance_maps(old, old_schema=True)
    for row in stored:
        original = row["acceptance_map"]
        rebuilt = build_acceptance_map(row["fen"], int(original["n"]))
        if rebuilt is None:
            diffs.append(f"puzzle {row['id']}: the archived map no longer builds at all")
        elif json.dumps(rebuilt, sort_keys=True) != json.dumps(original, sort_keys=True):
            diffs.append(f"puzzle {row['id']}: the rebuilt map differs from the archived one")
    return _report("acceptance maps", diffs, len(stored), "archived maps")


def diff_generation() -> int:
    """Regenerate in the scratch database and compare with the archive."""
    diffs: list[str] = []
    with scratch() as new, oracle() as old:
        q.require_scratch_database(new)
        config = settings.load(new)
        print(
            "generator inputs: blunder window "
            f"{config.blunders_default_last_n_games} / >= {config.blunder_puzzle_min_occurrences} games, "
            f"deviation window {config.deviations_default_last_n_games} / "
            f">= {config.deviation_puzzle_min_occurrences} games, "
            f"mate ceiling {config.missed_mate_max_moves}, time-class focus {config.time_class_focus}"
        )
        before = {_key(r): r for r in q.generated_puzzles(old, old_schema=True)}
        q.forget_generated_puzzles(new)
        generators.generate_all(new, config)
        new.commit()
        after = {_key(r): r for r in q.generated_puzzles(new)}

    for key in sorted(set(before) | set(after)):
        old_row, new_row = before.get(key), after.get(key)
        if old_row is None:
            diffs.append(f"{key}: only the new system generates this puzzle")
            continue
        if new_row is None:
            diffs.append(f"{key}: the new system does not generate this puzzle")
            continue
        for field in ("fen", "color", "solution_line", "themes"):
            if old_row[field] != new_row[field]:
                diffs.append(f"{key}: {field} {old_row[field]!r} -> {new_row[field]!r}")
        if sorted(old_row["source_types"]) != sorted(new_row["source_types"]):
            diffs.append(f"{key}: source_types {old_row['source_types']} -> {new_row['source_types']}")
        if json.dumps(old_row["acceptance_map"], sort_keys=True) != json.dumps(
            new_row["acceptance_map"], sort_keys=True
        ):
            diffs.append(f"{key}: the regenerated acceptance map differs from the archived one")
    return _report("puzzle generation", diffs, len(set(before) | set(after)), "puzzles")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--maps", action="store_true")
    parser.add_argument("--generation", action="store_true")
    args = parser.parse_args()
    run_all = not (args.maps or args.generation)
    status = 0
    if run_all or args.maps:
        status |= diff_maps()
    if run_all or args.generation:
        status |= diff_generation()
    return status


if __name__ == "__main__":
    sys.exit(main())
