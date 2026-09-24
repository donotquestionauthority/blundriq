"""Deviations page: the new ranked patterns against the old route's grouping.

The old query lives in `core.oracle.old_deviation_rows`; its grouping (`api/routes/deviations.py`
in the archive) is reproduced here: dedup on (game_url, ply, expected_move, book, chapter) —
the line join multiplied rows — then group by (color, ply, expected_move, book, chapter),
count, W/L/D, the mode of the played move, the set of games. The new one is
`core.deviations.positions` on the migrated scratch database. Compared per pattern: count,
wins/losses/draws, most common played move (ties by SAN on both sides: the old code's tie was
executor order), the game set, the representative game (the most recent — the old code took
whichever row came first) and its board, and the order of the list (count, then last played).

Run against the migrated copy before anything has matched games again on it: a rematch under
the new matcher reproduces decision 003's known differences (58 expected moves, 8 chapters
across the archive), which change pattern keys and are not what this script measures.

    python tools/oracle/diff_deviations.py
"""

from __future__ import annotations

import sys
from collections import Counter
from typing import Any

from common import oracle, report, scratch

from core import deviations
from core import oracle as q

FOCUS = ("rapid", "classical", "correspondence")
# (window, colour, time classes or None for all, min occurrences)
CASES: list[tuple[int, str | None, tuple[str, ...] | None, int]] = [
    (0, None, None, 1),
    (0, None, None, 2),
    (200, None, FOCUS, 2),
    (500, None, None, 2),
    (500, "white", FOCUS, 1),
    (500, "black", None, 3),
    (1000, None, ("blitz",), 1),
]

Key = tuple[int, int, int, str]


def old_patterns(rows: list[dict[str, Any]], min_occ: int) -> dict[Key, dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    groups: dict[Key, dict[str, Any]] = {}
    for r in rows:
        dk = (r["game_url"], r["ply"], r["expected_move"], r["book_id"], r["chapter_id"])
        if dk in seen:
            continue
        seen.add(dk)
        key: Key = (r["book_id"], r["chapter_id"], r["ply"], r["expected_move"])
        g = groups.setdefault(
            key,
            {"times": 0, "wins": 0, "losses": 0, "draws": 0, "played": [], "games": set(), "last": None, "fens": {}},
        )
        g["times"] += 1
        if r["result"] in ("win", "loss", "draw"):
            g[{"win": "wins", "loss": "losses", "draw": "draws"}[r["result"]]] += 1
        g["played"].append(r["played_move"])
        g["games"].add(r["chess_game_id"])
        g["fens"][r["chess_game_id"]] = " ".join(str(r["deviation_fen"]).split()[:4])
        if r["played_at"] and (g["last"] is None or r["played_at"] > g["last"]):
            g["last"] = r["played_at"]
        stamp = (r["played_at"].timestamp() if r["played_at"] else float("-inf"), r["chess_game_id"])
        if g.get("rep") is None or stamp > g["rep"][0]:
            g["rep"] = (stamp, r["chess_game_id"])
    return {k: g for k, g in groups.items() if g["times"] >= min_occ}


def mode(played: list[str]) -> str | None:
    if not played:
        return None
    c = Counter(played)
    return min(c, key=lambda m: (-c[m], m))


def new_cards(conn: Any, window: int, color: str | None, time_class: str, min_occ: int) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    page = 0
    while True:
        f = deviations.DeviationFilters(min_occ, None, window, time_class, color, 1, False)
        out = deviations.positions(conn, f, "rapid_plus", page)
        cards += out["positions"]
        page += 1
        if page >= out["total_pages"]:
            break
    return cards


def compare(name: str, old: dict[Key, dict[str, Any]], cards: list[dict[str, Any]]) -> list[str]:
    diffs: list[str] = []
    new = {(c["book_id"], c["chapter_id"], c["ply"], c["expected_move"]): c for c in cards}
    for k in old.keys() - new.keys():
        diffs.append(f"{name}: pattern only in old: {k}")
    for k in new.keys() - old.keys():
        diffs.append(f"{name}: pattern only in new: {k}")
    for k in old.keys() & new.keys():
        o, n = old[k], new[k]
        for of, nf in (("times", "count"), ("wins", "wins"), ("losses", "losses"), ("draws", "draws")):
            if o[of] != n[nf]:
                diffs.append(f"{name}: {k} {nf}: old {o[of]} new {n[nf]}")
        if mode(o["played"]) != n["most_common_played"]:
            diffs.append(f"{name}: {k} most_common_played: old {mode(o['played'])} new {n['most_common_played']}")
        if o["games"] != {g["chess_game_id"] for g in n["games"]}:
            diffs.append(f"{name}: {k} game set differs")
        if n["chess_game_id"] != o["rep"][1]:
            diffs.append(f"{name}: {k} representative: old most recent {o['rep'][1]} new {n['chess_game_id']}")
        elif o["fens"][n["chess_game_id"]] != " ".join(str(n["deviation_fen"]).split()[:4]):
            diffs.append(f"{name}: {k} representative board differs")
    order_old = [
        k
        for k, _ in sorted(
            old.items(), key=lambda kv: (-kv[1]["times"], -(kv[1]["last"].timestamp() if kv[1]["last"] else 0))
        )
    ]
    order_new = [k for k in new]
    if [k for k in order_old if k in new] != order_new:
        diffs.append(f"{name}: order differs")
    return diffs


def main() -> int:
    diffs: list[str] = []
    checked = 0
    with oracle() as old, scratch() as new:
        for window, color, classes, min_occ in CASES:
            name = f"last{window} {color or 'both'} {'+'.join(classes) if classes else 'all'} min{min_occ}"
            old_groups = old_patterns(q.old_deviation_rows(old, window, color, classes), min_occ)
            checked += len(old_groups)
            time_class = "all" if classes is None else ("focus" if classes == FOCUS else classes[0])
            diffs += compare(name, old_groups, new_cards(new, window, color, time_class, min_occ))
    return report("deviations page patterns", diffs, checked)


if __name__ == "__main__":
    sys.exit(main())
