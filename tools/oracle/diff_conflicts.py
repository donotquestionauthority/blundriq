"""Repertoire conflicts: the new listing and the new activation gate against the old ones.

The old `get_player_repertoire_conflicts` (Universe D) and `check_activation_conflicts`
(Universe B) are lifted out of the archived source by their AST with the helpers they call,
and run verbatim over the archive's rows — read once by `core.oracle`, where the reference
SQL lives, and served to the old code through a stand-in connection, since its module cannot
be imported outside the old application. The new `core.repertoire.conflicts.listing` and
`gate` run on the migrated scratch database.

Listing: the set and order of FENs; per FEN the move set and order, each move's line ids and
their effectiveness, `dirty_anchor == contested`, `active_move_count == active_moves`. Gate:
for every line that is not effectively active in the archive, whether the old check blocks
it and, when it does, the first blocking position and the move the line plays there, against
the new gate's refusal. The old gate rebuilds its index per line (~6 min on Rob's data).

    python tools/oracle/diff_conflicts.py --old-src /path/to/old-src   (the extracted archive)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

from common import oracle, report, scratch

from core import oracle as q
from core.repertoire import conflicts


class _Rows:
    """The stand-in for the old connection: the old functions issue one of three reads, told
    apart by their text, and get the archive's rows filtered the way that read would."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.pending: list[dict[str, Any]] = []

    def cursor(self) -> _Rows:
        return self

    def __enter__(self) -> _Rows:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        if "rl.id = %s AND bk.player_id" in sql:  # the candidate line
            self.pending = [r for r in self.rows if r["line_id"] == params[0]]
        elif "rl.active = TRUE" in sql:  # the effectively-active index, minus the excluded
            exclude = set(params[1]) if len(params) > 1 else set()
            self.pending = [
                r
                for r in self.rows
                if r["rl_active"] and r["c_active"] and r["b_active"] and r["line_id"] not in exclude
            ]
        else:  # every line
            self.pending = list(self.rows)

    def fetchall(self) -> list[dict[str, Any]]:
        return self.pending

    def fetchone(self) -> dict[str, Any] | None:
        return self.pending[0] if self.pending else None


def lift(old_src: Path) -> dict[str, Any]:
    """The old listing and gate with their helpers, compiled from the source file alone."""
    tree = ast.parse((old_src / "blundriq-api" / "db" / "repertoire.py").read_text())
    wanted = {
        "_line_signature",
        "_compute_cohort_decisions",
        "_build_effectively_active_index",
        "get_player_repertoire_conflicts",
        "check_activation_conflicts",
    }
    nodes: list[ast.stmt] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    nodes += [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LineNotFoundForPlayer"]
    assert len(nodes) == 6, "old functions not found"
    prelude = ast.parse(
        "import json\nfrom collections import Counter, defaultdict\nSAFE = 'safe'\nBLOCKED = 'blocked'"
    ).body
    module = ast.Module(body=[*prelude, *nodes], type_ignores=[])
    ns: dict[str, Any] = {}
    exec(compile(ast.fix_missing_locations(module), "old-conflicts", "exec"), ns)  # noqa: S102
    return ns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-src", required=True)
    args = ap.parse_args()
    old = lift(Path(args.old_src))
    diffs: list[str] = []
    checked = 0
    with oracle() as old_db, scratch() as new:
        rows = _Rows(q.old_conflict_lines(old_db))
        expected = old["get_player_repertoire_conflicts"](rows, 1)
        got = conflicts.listing(new)
        checked += 1
        if [p["fen"] for p in expected] != [p["fen"] for p in got]:
            diffs.append(f"listing: {len(expected)} old FENs vs {len(got)} new, or a different order")
        for e, g in zip(expected, got, strict=False):
            if e["fen"] != g["fen"]:
                break
            checked += 1
            if e["dirty_anchor"] != g["contested"] or e["active_move_count"] != g["active_moves"]:
                diffs.append(
                    f"{e['fen']}: old dirty={e['dirty_anchor']}/{e['active_move_count']} new {g['contested']}/{g['active_moves']}"
                )
            if [x["next_move"] for x in e["groups"]] != [x["move"] for x in g["moves"]]:
                diffs.append(
                    f"{e['fen']}: moves old {[x['next_move'] for x in e['groups']]} new {[x['move'] for x in g['moves']]}"
                )
                continue
            for eg, gg in zip(e["groups"], g["moves"], strict=True):
                old_lines = {(ln["line_id"], ln["effectively_active"]) for ln in eg["lines"]}
                new_lines = {(ln["line_id"], ln["effective"]) for ln in gg["lines"]}
                if old_lines != new_lines:
                    diffs.append(f"{e['fen']} {eg['next_move']}: lines old {sorted(old_lines)} new {sorted(new_lines)}")
        # The gate, over every line that is not in play.
        blocked = 0
        for r in rows.rows:
            if r["rl_active"] and r["c_active"] and r["b_active"]:
                continue
            checked += 1
            old_conflicts = old["check_activation_conflicts"](rows, 1, r["line_id"])
            refusals = conflicts.gate(new, "lines", int(r["line_id"]))
            old_first = (old_conflicts[0]["fen"], old_conflicts[0]["their_move"]) if old_conflicts else None
            new_first = (refusals[0].fen, refusals[0].move) if refusals else None
            blocked += old_first is not None
            if old_first != new_first:
                diffs.append(f"gate line {r['line_id']}: old {old_first} new {new_first}")
        print(f"{len(expected)} positions listed, {blocked} lines the old gate blocks")
    return report("repertoire conflicts", diffs, checked)


if __name__ == "__main__":
    sys.exit(main())
