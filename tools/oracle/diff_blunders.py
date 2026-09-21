"""Blunders page: the new ranked list against the old system's ranking query.

The old query is reproduced here from the archive's `db/blunders.py::_ranked_sql` and run
on the old database; the new one is `core.blunders.positions` on the migrated scratch
database. Compared per board: distinct-game count, score, classification map, and the
order of the list. Two deliberate differences are neutralised so that everything else must
match exactly: Chess960 games are excluded on the old side too (their blunder rows are not
migrated), and the old window (most recent N of *all* games) is replaced by the new one
(most recent N standard games).

    python tools/oracle/diff_blunders.py
"""

from __future__ import annotations

import sys
from typing import Any

from common import oracle, report, scratch

from core import blunders
from core.constants import BLUNDER_SCORE_WEIGHTS

_WEIGHTS = " ".join(f"WHEN b.classification = '{k}' THEN {v}" for k, v in BLUNDER_SCORE_WEIGHTS.items())

OLD_SQL = f"""
WITH per_game AS (
    SELECT DISTINCT ON (b.player_id, b.canonical_fen, b.chess_game_id)
        b.canonical_fen, b.classification, cg.played_at, (CASE {_WEIGHTS} ELSE 0 END) AS rep_score
    FROM blunders b
    JOIN player_games pg ON pg.chess_game_id = b.chess_game_id AND pg.player_id = b.player_id
    JOIN chess_games cg ON cg.id = b.chess_game_id
    WHERE b.player_id = 1 AND b.classification = ANY(%(cls)s) AND cg.variant = 'standard'
      AND (%(window)s = 0 OR b.chess_game_id IN (
            SELECT pg2.chess_game_id FROM player_games pg2 JOIN chess_games cg2 ON cg2.id = pg2.chess_game_id
            WHERE pg2.player_id = 1 AND cg2.variant = 'standard'
            ORDER BY cg2.played_at DESC NULLS LAST, cg2.id DESC LIMIT %(window)s))
    ORDER BY b.player_id, b.canonical_fen, b.chess_game_id, b.centipawn_loss DESC NULLS LAST, b.ply, b.id
)
SELECT canonical_fen, count(*) AS count, sum(rep_score) AS score, max(played_at) AS last_played,
       (SELECT jsonb_object_agg(classification, n) FROM (
            SELECT classification, count(*) AS n FROM per_game x WHERE x.canonical_fen = p.canonical_fen GROUP BY 1) c
       ) AS classifications
FROM per_game p GROUP BY canonical_fen HAVING count(*) >= %(min_occ)s
ORDER BY score DESC, last_played DESC NULLS LAST, canonical_fen
"""

CASES: list[tuple[tuple[str, ...], int, int]] = [
    (("miss", "blunder", "mistake"), 2, 0),
    (("miss", "blunder", "mistake", "inaccuracy"), 2, 0),
    (("miss", "blunder", "mistake"), 2, 500),
    (("miss", "blunder", "mistake", "inaccuracy"), 3, 200),
    (("blunder",), 1, 100),
]


def _board(fen: str) -> str:
    return " ".join(fen.split()[:4])


def main() -> int:
    diffs: list[str] = []
    checked = 0
    with oracle() as old, scratch() as new:
        for classes, min_occ, window in CASES:
            name = f"{'+'.join(classes)} min{min_occ} last{window}"
            old_rows = old.execute(OLD_SQL, {"cls": list(classes), "window": window, "min_occ": min_occ}).fetchall()
            new_cards: list[dict[str, Any]] = []
            for shown in (False, True):  # a dismissed board is still a ranked board
                page = 0
                while True:
                    f = blunders.BlunderFilters(classes, min_occ, None, window, "all", shown)
                    out = blunders.positions(new, f, "rapid_plus", page)
                    new_cards += out["positions"]
                    page += 1
                    if page >= out["total_pages"]:
                        break
            old_by = {_board(r["canonical_fen"]): r for r in old_rows}
            new_by = {_board(c["fen"]): c for c in new_cards}
            checked += len(old_by)
            for board in old_by.keys() - new_by.keys():
                diffs.append(f"{name}: board only in old: {board}")
            for board in new_by.keys() - old_by.keys():
                diffs.append(f"{name}: board only in new: {board}")
            for board in old_by.keys() & new_by.keys():
                o, n = old_by[board], new_by[board]
                for field in ("count", "score", "classifications"):
                    if o[field] != n[field]:
                        diffs.append(f"{name}: {board} {field}: old {o[field]} new {n[field]}")
            active = [_board(c["fen"]) for c in new_cards if not c["dismissed"]]
            expected = [b for b in (_board(r["canonical_fen"]) for r in old_rows) if b in set(active)]
            if active != expected:
                diffs.append(f"{name}: order differs")
    return report("blunders page ranking", diffs, checked)


if __name__ == "__main__":
    sys.exit(main())
