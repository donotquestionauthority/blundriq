#!/usr/bin/env python3
"""Rerun the generators against the migrated data and diff against the old rows.

Three comparisons, in order of how much they prove:

1. **Generation.** Wipe the generated puzzles in the scratch database, run all three
   generators, and compare what comes back with the old system's rows at the same
   board or line. Expected identical, apart from the differences this phase chose and
   recorded: a repertoire line whose window now excludes Chess960 games, and any
   puzzle whose supporting rows were dropped in phase 2's migration.

2. **Acceptance maps.** Rebuild every missed-mate map and assert it is byte-identical
   to the migrated one. The builder is deterministic, so a difference here is a port
   error, not drift.

Attempt replay — re-grading every attempt the player ever made and comparing the
verdict — needs the grading module that arrives with serving, and is added then.

Usage, with the two databases described in this directory's README:

    python tools/oracle/diff_puzzles.py [--generation] [--maps]

With no flags it runs both. Exits 1 if anything differs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import oracle, report, scratch  # noqa: E402

from core import settings  # noqa: E402
from core.chess.mate_acceptance import build_acceptance_map  # noqa: E402
from core.constants import PLAYER_ID  # noqa: E402
from core.puzzles.generate import run as generators  # noqa: E402

GENERATED = ("blunder", "own_mate", "deviation")


def _old_puzzles(conn: Any) -> dict[str, dict[str, Any]]:
    """Old generated puzzles, keyed by board (or by line, for repertoire puzzles)."""
    rows = conn.execute(
        "SELECT canonical_fen, fen, source_types, themes, solution_line, color, active,"
        " is_repertoire, repertoire_line_id, acceptance_map"
        " FROM puzzles WHERE player_id = %s AND puzzle_kind = 'line' AND active = TRUE"
        "   AND source_types && %s::text[] AND NOT source_types @> ARRAY['manual']",
        (PLAYER_ID, list(GENERATED)),
    ).fetchall()
    return {_key(r): dict(r) for r in rows}


def _new_puzzles(conn: Any) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT canonical_fen, fen, source_types, themes, solution_line, color, active,"
        " is_repertoire, repertoire_line_id, acceptance_map"
        " FROM puzzles WHERE active = TRUE AND source_types && %s::text[]"
        "   AND NOT source_types @> ARRAY['custom']",
        (list(GENERATED),),
    ).fetchall()
    return {_key(r): dict(r) for r in rows}


def _key(row: Any) -> str:
    return f"line:{row['repertoire_line_id']}" if row["is_repertoire"] else f"board:{row['canonical_fen']}"


def diff_generation() -> int:
    """Regenerate and compare. This DELETES the generated puzzles in `DATABASE_URL` and
    remakes them, and deleting a puzzle cascades its SRS row away, so it must point at a
    scratch copy — never at the database Rob practises against."""
    diffs: list[str] = []
    with scratch() as new, oracle() as old:
        _refuse_unless_scratch(new)
        before = _old_puzzles(old)
        new.execute(
            "DELETE FROM puzzles WHERE source_types && %s::text[] AND NOT source_types @> ARRAY['custom']",
            (list(GENERATED),),
        )
        generators.generate_all(new, settings.load(new))
        new.commit()
        after = _new_puzzles(new)

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
    return report("puzzle generation", diffs, len(set(before) | set(after)))


def _refuse_unless_scratch(conn: Any) -> None:
    """Deleting a puzzle takes its `player_puzzle_state` row with it, so the regeneration
    diff runs only against a database explicitly named as a throwaway."""
    name = conn.execute("SELECT current_database() AS n").fetchone()["n"]
    if "scratch" not in str(name):
        raise SystemExit(
            f"refusing to regenerate in '{name}': this deletes puzzles, which cascades to"
            " SRS progress. Point DATABASE_URL at a scratch copy whose name contains"
            " 'scratch' (see this directory's README)."
        )


def diff_maps() -> int:
    """A stored map and a fresh rebuild must be identical, or a regenerated puzzle would
    grade differently from the one the player solved."""
    diffs: list[str] = []
    with scratch() as new:
        rows = new.execute(
            "SELECT id, fen, acceptance_map FROM puzzles"
            " WHERE source_types @> ARRAY['own_mate'] AND acceptance_map IS NOT NULL"
        ).fetchall()
    for row in rows:
        stored = row["acceptance_map"]
        rebuilt = build_acceptance_map(row["fen"], int(stored["n"]))
        if rebuilt is None:
            diffs.append(f"puzzle {row['id']}: the map no longer builds")
        elif json.dumps(rebuilt, sort_keys=True) != json.dumps(stored, sort_keys=True):
            diffs.append(f"puzzle {row['id']}: the rebuilt map differs from the stored one")
    return report("acceptance maps", diffs, len(rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--generation", action="store_true")
    parser.add_argument("--maps", action="store_true")
    args = parser.parse_args()
    chosen = [args.generation, args.maps]
    run_all = not any(chosen)
    status = 0
    if run_all or args.generation:
        status |= diff_generation()
    if run_all or args.maps:
        status |= diff_maps()
    return status


if __name__ == "__main__":
    sys.exit(main())
