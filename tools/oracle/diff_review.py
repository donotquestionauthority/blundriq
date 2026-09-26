"""Review events: this detector's generation against the old writer's last one.

The old rows are read from the oracle (`core.oracle.old_review_events`); the new ones are
what `pipeline review` wrote to the scratch database, which must have been run first. Per
(game, anchor ply): existence and every stored fact — route, opening-candidate flag, pool
key, cost to 0.01, phase, piece label, book relation — plus the evidence's class, proof
state, secondary kinds and game-priced loss; and the window's UNKNOWN count.

The claim is exact parity for every game in which no position has three to five men without
castling rights: the ladder is identical there, so any difference is a defect in the port.
Games that reach such a position were priced over the board at those positions by the old
detector (docs/decisions/001) and are listed separately, each difference to be read on its
own: an event's cost changing because the expected score at one end was 100/0/50 and is now
a sigmoid value; a faded event appearing or vanishing because the peak moved; a material
event gaining or losing confirmation because the anchor's drop crossed the threshold. A
Chess960 game the old writer reviewed is listed for the record and never fails the check (such a
game is never reviewed here); an analysable game with events on one side only, or missing from
the scratch database, does — the archive's row, never the scratch copy's, says which is which.
The two state rows must both exist and agree on the UNKNOWN total; the window sizes must agree
once the excluded games are taken off the old one.

    pipeline review                                  (DATABASE_URL = the scratch database)
    python tools/oracle/diff_review.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from typing import Any

import chess
from common import oracle, report, scratch

from core import oracle as q
from core.chess.eligibility import is_analysable

SMALL_MEN = (3, 5)


def reaches_small_castling_free(fens: list[str]) -> int | None:
    """The first ply whose position has 3–5 men and no castling rights, else None."""
    for i, fen in enumerate(fens):
        try:
            board = chess.Board(fen)
        except ValueError:
            continue
        men = chess.popcount(board.occupied)
        if SMALL_MEN[0] <= men <= SMALL_MEN[1] and not board.castling_rights:
            return i
    return None


def as_num(v: Any) -> float | None:
    if v is None:
        return None
    return float(v) if isinstance(v, Decimal | int | float | str) else None


def compare_event(key: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    for f in q.REVIEW_EVENT_FIELDS:
        a, b = old[f], new[f]
        if f == "cost":
            an, bn = as_num(a), as_num(b)
            if an is None or bn is None or abs(an - bn) > 0.01:
                diffs.append(f"{key} cost: old {a} new {b}")
            continue
        if a != b:
            diffs.append(f"{key} {f}: old {a} new {b}")
    oe, ne = old["evidence"] or {}, new["evidence"] or {}
    for f in q.REVIEW_EVIDENCE_FIELDS:
        a, b = oe.get(f), ne.get(f)
        if f == "game_priced_loss" and a is not None and b is not None:
            an, bn = as_num(a), as_num(b)
            if an is None or bn is None or abs(an - bn) > 0.01:
                diffs.append(f"{key} evidence.{f}: old {a} new {b}")
        elif a != b:
            diffs.append(f"{key} evidence.{f}: old {a} new {b}")
    return diffs


def main() -> int:
    with oracle() as old, scratch() as new:
        old_rows = q.old_review_events(old)
        old_state = q.old_review_state(old)
        new_rows = q.review_events(new)
        new_state = q.old_review_state(new)  # the same one-row shape
        ids = sorted({int(r["chess_game_id"]) for r in old_rows} | {int(r["chess_game_id"]) for r in new_rows})
        # Eligibility and the bucket a game falls in are read from the ARCHIVE's row: the
        # scratch copy is what is being checked, so it cannot also say which games to check.
        archived = q.game_fens(old, ids)
        present = q.game_fens(new, ids)

    old_by = {(int(r["chess_game_id"]), int(r["anchor_ply"])): r for r in old_rows}
    new_by = {(int(r["chess_game_id"]), int(r["anchor_ply"])): r for r in new_rows}
    old_games = {g for g, _ in old_by}
    new_games = {g for g, _ in new_by}

    def excluded(g: int) -> bool:
        return g in archived and not is_analysable(archived[g][0])

    window: list[str] = []
    never: list[str] = []
    for g in sorted(old_games - new_games):
        if excluded(g):
            never.append(f"{g}: only in old — {archived[g][0]}, never reviewed here")
        elif g not in present:
            window.append(f"{g}: has events in old and is missing from the scratch database")
        else:
            window.append(f"{g}: has events only in old")
    for g in sorted(new_games - old_games):
        window.append(f"{g}: has events only in new" + ("" if g in archived else " and is not in the archive"))
    for g in ids:
        if g in archived and g in present and archived[g][0] != present[g][0]:
            window.append(f"{g}: variant {archived[g][0]} in the archive, {present[g][0]} in the scratch database")

    exact: list[str] = []
    small: list[str] = []
    small_games: set[int] = set()
    compared: set[tuple[int, int]] = set()
    for g in ids:
        if g not in archived or excluded(g):
            continue
        at = reaches_small_castling_free(archived[g][1])
        bucket = small if at is not None else exact
        if at is not None:
            small_games.add(g)
        old_keys = {k for k in old_by if k[0] == g}
        new_keys = {k for k in new_by if k[0] == g}
        compared |= old_keys | new_keys
        for k in sorted(old_keys - new_keys):
            bucket.append(f"{g} ply{k[1]}: only in old ({old_by[k]['base_route']})")
        for k in sorted(new_keys - old_keys):
            bucket.append(f"{g} ply{k[1]}: only in new ({new_by[k]['base_route']})")
        for k in sorted(old_keys & new_keys):
            bucket += compare_event(f"{g} ply{k[1]}", old_by[k], new_by[k])

    # The state row: the UNKNOWN total must agree. A difference has only two legitimate
    # sources, each to be read individually: an excluded game's candidates (in the old total,
    # never in the new) and a small-position game whose cliff probes moved with the pricing.
    state: list[str] = []
    if old_state is None:
        state.append("old: no review_detection_state row")
    if new_state is None:
        state.append("new: no review_detection_state row")
    if old_state is not None and new_state is not None:
        if old_state["unknown_candidates"] != new_state["unknown_candidates"]:
            state.append(
                f"unknown_candidates: old {old_state['unknown_candidates']} new {new_state['unknown_candidates']}"
                f" ({len(never)} excluded games, {len(small_games)} small-position games could account for it)"
            )
        if old_state["window_games"] - len(never) != new_state["window_games"]:
            state.append(
                f"window_games: old {old_state['window_games']} less {len(never)} excluded games"
                f" is not the new {new_state['window_games']}"
            )

    checked = len([k for k in compared if k[0] not in small_games])
    print(f"old: {len(old_rows)} events over {len(old_games)} games; new: {len(new_rows)} over {len(new_games)}")
    print(f"old state: {old_state}; new state: {new_state}")
    print(f"games reaching a 3–5-man castling-free position: {len(small_games)}")
    rc = report("review events — window (analysable games)", window, len(old_games | new_games))
    rc |= report("review events — exact parity (no small castling-free position)", exact, checked)
    rc |= report("review state — UNKNOWN total and window size", state, 2)
    print(f"\n== review events — games the old writer reviewed that are never reviewed here ({len(never)})")
    for d in never:
        print("  " + d)
    print(f"\n== review events — games priced over the board by the old detector ({len(small)} differences)")
    for d in small:
        print("  " + d)
    return rc


if __name__ == "__main__":
    sys.exit(main())
