"""Blunders page: the new ranked list against the old system's ranking query.

The old query lives in `core.oracle.old_blunder_ranking` with the archived weights frozen
beside it; the new one is `core.blunders.positions` on the migrated scratch database. Compared
per board: distinct-game count, score, classification map, and the order of the active list.

    python tools/oracle/diff_blunders.py
"""

from __future__ import annotations

import sys
from typing import Any

from common import oracle, report, scratch

from core import blunders
from core import oracle as q

CASES: list[tuple[tuple[str, ...], int, int]] = [
    (("miss", "blunder", "mistake"), 2, 0),
    (("miss", "blunder", "mistake", "inaccuracy"), 2, 0),
    (("miss", "blunder", "mistake"), 2, 500),
    (("miss", "blunder", "mistake", "inaccuracy"), 3, 200),
    (("blunder",), 1, 100),
]


def board_of(fen: str) -> str:
    return " ".join(fen.split()[:4])


def new_cards(conn: Any, classes: tuple[str, ...], min_occ: int, window: int) -> list[dict[str, Any]]:
    """Every card of both views, page by page (a dismissed board is still a ranked board)."""
    cards: list[dict[str, Any]] = []
    for shown in (False, True):
        page = 0
        while True:
            f = blunders.BlunderFilters(classes, min_occ, None, window, "all", shown)
            out = blunders.positions(conn, f, "rapid_plus", page)
            cards += out["positions"]
            page += 1
            if page >= out["total_pages"]:
                break
    return cards


def compare(name: str, old_rows: list[dict[str, Any]], cards: list[dict[str, Any]]) -> list[str]:
    diffs: list[str] = []
    old_by = {board_of(r["canonical_fen"]): r for r in old_rows}
    new_by = {board_of(c["fen"]): c for c in cards}
    for board in old_by.keys() - new_by.keys():
        diffs.append(f"{name}: board only in old: {board}")
    for board in new_by.keys() - old_by.keys():
        diffs.append(f"{name}: board only in new: {board}")
    for board in old_by.keys() & new_by.keys():
        for field in ("count", "score", "classifications"):
            if old_by[board][field] != new_by[board][field]:
                diffs.append(f"{name}: {board} {field}: old {old_by[board][field]} new {new_by[board][field]}")
    active = [board_of(c["fen"]) for c in cards if not c["dismissed"]]
    expected = [b for b in (board_of(r["canonical_fen"]) for r in old_rows) if b in set(active)]
    if active != expected:
        diffs.append(f"{name}: order differs")
    return diffs


def main() -> int:
    diffs: list[str] = []
    checked = 0
    with oracle() as old, scratch() as new:
        for classes, min_occ, window in CASES:
            name = f"{'+'.join(classes)} min{min_occ} last{window}"
            old_rows = q.old_blunder_ranking(old, classes, min_occ, window)
            checked += len(old_rows)
            diffs += compare(name, old_rows, new_cards(new, classes, min_occ, window))
    return report("blunders page ranking", diffs, checked)


if __name__ == "__main__":
    sys.exit(main())
