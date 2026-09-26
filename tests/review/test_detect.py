"""The review detector, on synthetic games: pure python-chess, no engine, no database.

Every case names what it pins. The stored evals are White's point of view, as the analyser
writes them; a fixture that starts from an arbitrary position does so through the Chess960
start-FEN path, which is how the detector builds any non-initial board.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import chess
import pytest

from core.review import detect
from core.review.detect import REVIEW_CONFIG_VERSION, tag_review_events

KNOBS = {
    "review_material_candidate_drop": 2,
    "review_conf_es_drop": 15,
    "review_conf_es_drop_depth12": 20,
    "review_early_k_plies": 8,
    "review_early_ply_cap": 30,
    "review_missed_win_shed": 15,
    "review_faded_peak_es": 62,
    "review_quiesce_max_plies": 6,
}


def make_ctx(
    moves: list[str],
    cps: list[int | None],
    *,
    player_color: str = "white",
    result: str = "loss",
    variant: str = "standard",
    starting_fen: str | None = None,
    best_lines: dict[int, str] | None = None,
    mates: dict[int, int] | None = None,
    termination: str = "checkmate",
    eco: str | None = None,
    family: str | None = None,
    variation: str | None = None,
    repertoire: dict[str, Any] | None = None,
    motif_missed: list[dict[str, Any]] | None = None,
    depth: int = 18,
) -> dict[str, Any]:
    """A full game context: the FEN sequence by replay, `ply_analysis` in the analyser's
    shape, one entry per position."""
    best_lines = best_lines or {}
    mates = mates or {}
    board = chess.Board(fen=starting_fen, chess960=True) if starting_fen else chess.Board()
    fens = [board.fen()]
    for san in moves:
        board.push_san(san)
        fens.append(board.fen())
    n = len(moves) + 1
    assert len(cps) == n, "fixture: cps must be len(moves)+1"
    pa = [
        {
            "ply": i,
            "eval": cps[i],
            "best_move": (str(best_lines[i]).split()[0] if i in best_lines else None),
            "best_line": best_lines.get(i),
            "mate_in_moves": mates.get(i),
        }
        for i in range(n)
    ]
    return {
        "chess_game_id": 1,
        "moves": list(moves),
        "fen_sequence": fens,
        "ply_analysis": pa,
        "player_color": player_color,
        "result": result,
        "termination": termination,
        "variant": variant,
        "starting_fen": starting_fen,
        "opening_eco": eco,
        "canonical_family": family,
        "canonical_variation": variation,
        "analysis_depth": depth,
        "repertoire": repertoire,
        "motif_missed": motif_missed or [],
    }


# The hung queen: Qh5, the attack ignored (a3), Nxh5. The PV at the deficit position ("d4") is
# quiet but leaves exd4 hanging, so the extension cashes one capture before settling.
QH_MOVES = ["e4", "e5", "Qh5", "Nf6", "a3", "Nxh5", "g3", "Nf6"]
QH_CPS: list[int | None] = [20, 20, 30, 30, 50, -900, -900, -900, -900]
QH_LINES = {4: "Qe2", 6: "d4"}


def qh_ctx(cps: Sequence[int | None] | None = None, **over: Any) -> dict[str, Any]:
    kw: dict[str, Any] = dict(best_lines=dict(QH_LINES), eco="C20")
    kw.update(over)
    return make_ctx(QH_MOVES, list(cps if cps is not None else QH_CPS), **kw)


# Back rank: Black (the player) trades the rook into Rxd4 and the PV from there mates.
BR_FEN = "3r2k1/p4ppp/8/8/8/8/6PP/3R2K1 b - - 0 1"
BR_MOVES = ["Rd4", "Rxd4"]
BR_CPS: list[int | None] = [-100, 900, 900]
BR_LINES = {0: "h6", 2: "a5 Rd8#"}

# A capture chain whose compensation (Qxd5) lands at extension ply 2.
CH_FEN = "2q3k1/2p5/N7/3n4/2R5/4n3/8/3Q2K1 w - - 0 1"
CH_MOVES = ["Kh1", "Nxc4"]
CH_CPS: list[int | None] = [100, -400, -400]
CH_LINES = {2: "Kg1"}

# A missed mate in two at +6, in a game the player went on to win.
MM_MOVES = ["d4", "d5", "Nf3", "Nf6", "c4", "e6"]
MM_CPS: list[int | None] = [600] * 7

# A papercut fade: peak over 62, a monotone bleed, no captures, a loss.
F1_MOVES = ["d4", "d5", "Nf3", "Nf6", "e3", "e6", "Bd3", "Bd6", "O-O", "O-O", "Nbd2", "Nbd7"]
F1_CPS: list[int | None] = [140, 80, 80, 20, 20, -40, -40, -100, -100, -160, -160, -220, -220]

# The same shape carrying a confirmed material event (Nxe5 loses a knight for a pawn).
F2_MOVES = ["e4", "e5", "Nf3", "Nc6", "Nxe5", "Nxe5", "d3", "d6", "Be2", "Be7", "O-O", "Nf6", "Be3", "Bd7"]
F2_CPS: list[int | None] = [160, 150, 160, 150, 160, -20, -20, -80, -80, -140, -140, -200, -200, -260, -260]
F2_LINES = {4: "d3", 6: "d4"}

# Queen against rook, four men and no castling rights: Qd4?? Rxd4.
KQKR_FEN = "3rk3/8/8/8/8/8/3Q4/3K4 w - - 0 1"
KQKR_MOVES = ["Qd4", "Rxd4", "Kc1", "Ra4"]
KQKR_CPS: list[int | None] = [900, 0, 0, 0, 0]
KQKR_LINES = {0: "Kc1", 2: "Kc1"}

# A pawn ending, eight men: the king walks in and eats two pawns.
PAWNS_FEN = "8/8/8/8/8/k7/1PPPPP2/6K1 w - - 0 1"
PAWNS_MOVES = ["Kh1", "Kxb2", "Kg1", "Kxc2"]
PAWNS_CPS: list[int | None] = [200, 0, 0, -100, -100]
PAWNS_LINES = {4: "Kh1"}

# A Chess960 start where queenside castling is not the standard king move.
C960_FEN = "qrkbbnnr/pppppppp/8/8/8/8/PPPPPPPP/QRKBBNNR w KQkq - 0 1"
C960_MOVES = ["e4", "e5", "Be2", "Be7", "O-O-O", "O-O-O", "Nf3"]

REP_AT_START = {
    "book_id": 7,
    "chapter_id": 3,
    "line_id": 12,
    "deviated_at_ply": 0,
    "deviation_by": "me",
    "expected_move": "e4",
    "matched_ply": 0,
    "line_len": 10,
}


def f2_ctx(**over: Any) -> dict[str, Any]:
    kw: dict[str, Any] = dict(best_lines=dict(F2_LINES), family="Kings Pawn Game", variation="Center Attack")
    kw.update(over)
    return make_ctx(F2_MOVES, list(F2_CPS), **kw)


def only(out: tuple[list[dict[str, Any]], int] | None) -> dict[str, Any]:
    assert out is not None and out[1] == 0 and len(out[0]) == 1, out
    return out[0][0]


# --- the material proof ---------------------------------------------------------------------


def test_hung_queen_settles_quiet_and_prices_by_the_sigmoid() -> None:
    ev = only(tag_review_events(qh_ctx(), KNOBS))
    assert ev["anchor_ply"] == 4  # the largest drop, not the capture ply
    assert ev["base_route"] == "lapse_defense"
    assert 45 < ev["cost"] < 56
    assert ev["evidence"]["proof_state"] == "settled_quiet"
    det = ev["evidence"]["detectors"][0]
    assert det["extension_plies"] == 1 and det["settled_deficit"] == 10
    assert ev["piece_label"] == "queen"
    assert ev["pool_key"] == "eco:C20" and ev["opening_candidate"] is True
    assert (ev["evidence"]["es_authority_before"], ev["evidence"]["es_authority_after"]) == ("sigmoid", "sigmoid")
    assert ev["anchor_fen"] == qh_ctx()["fen_sequence"][4]
    assert ev["book_relation"] == "no_repertoire"
    assert abs(ev["evidence"]["game_priced_loss"] - ev["cost"]) < 0.01
    json.dumps(ev)  # the evidence is stored as jsonb


def test_a_late_material_event_is_not_an_opening_candidate_but_keeps_its_key() -> None:
    ev = only(tag_review_events(qh_ctx(), dict(KNOBS, review_early_ply_cap=3)))
    assert ev["opening_candidate"] is False and ev["pool_key"] == "eco:C20"


def test_flat_evals_prune_the_candidate_on_the_expected_score_leg() -> None:
    assert tag_review_events(qh_ctx(cps=[20] * 9), KNOBS) == ([], 0)


def test_a_missing_pv_is_unknown_counted_and_never_an_event() -> None:
    lines = dict(QH_LINES)
    del lines[6]
    assert tag_review_events(qh_ctx(best_lines=lines), KNOBS) == ([], 1)


def test_compensation_beyond_the_extension_bound_is_unknown() -> None:
    ctx = make_ctx(CH_MOVES, list(CH_CPS), starting_fen=CH_FEN, variant="chess960", best_lines=dict(CH_LINES))
    assert tag_review_events(ctx, dict(KNOBS, review_quiesce_max_plies=1)) == ([], 1)
    ev = only(tag_review_events(ctx, KNOBS))  # the default bound settles the same candidate
    assert ev["anchor_ply"] == 0 and ev["piece_label"] == "rook"
    assert ev["evidence"]["proof_state"] == "settled_quiet"


def test_settled_compensated_is_no_event() -> None:
    """The PV returns the material: the deficit at the endpoint is below the knob."""
    ctx = make_ctx(CH_MOVES, list(CH_CPS), starting_fen=CH_FEN, variant="chess960", best_lines={2: "Qxd5+ Kh8 Qxc4"})
    assert tag_review_events(ctx, KNOBS) == ([], 0)


def test_a_pv_that_ends_the_game_is_settled_terminal() -> None:
    ctx = make_ctx(
        BR_MOVES, list(BR_CPS), player_color="black", starting_fen=BR_FEN, variant="chess960", best_lines=dict(BR_LINES)
    )
    ev = only(tag_review_events(ctx, KNOBS))
    assert ev["evidence"]["proof_state"] == "settled_terminal"
    assert ev["anchor_ply"] == 0 and ev["piece_label"] == "rook"
    assert ev["cost"] > 15  # Black's point of view: the White-POV eval was flipped


def test_the_forced_clause_probes_a_cliff_with_no_candidate_nearby() -> None:
    """No capture happened in the game, so no material candidate; the cliff after a3 is
    probed through the PV, which proves the queen is lost."""
    ctx = make_ctx(QH_MOVES[:5], [20, 20, 30, 30, 50, -900], best_lines={5: "Nxh5 g3 Nf6"})
    ev = only(tag_review_events(ctx, KNOBS))
    assert ev["anchor_ply"] == 4 and ev["piece_label"] == "forced-loss"
    det = ev["evidence"]["detectors"][0]
    assert det["tag"] == "forced" and det["settled_deficit"] == 9
    assert ev["base_route"] == "lapse_defense"


def test_depth_12_games_use_the_stricter_confirmation() -> None:
    assert only(tag_review_events(f2_ctx(), KNOBS))["base_route"] == "lapse_defense"
    # The same anchor drop (~15.8) is under the depth-12 bar of 20: the material event
    # prunes and, with no sharp event left, the papercut shape fades instead.
    assert only(tag_review_events(f2_ctx(depth=12), KNOBS))["base_route"] == "faded"


# --- merge, missed wins, faded ---------------------------------------------------------------


def test_two_detectors_at_one_anchor_merge_with_missed_mate_first() -> None:
    rep = {**REP_AT_START, "deviated_at_ply": None, "deviation_by": None, "expected_move": None, "matched_ply": 2}
    ctx = qh_ctx(repertoire=rep, motif_missed=[{"ply": 4, "metric_type": "mate", "theme": "mate", "mate_in_moves": 2}])
    ev = only(tag_review_events(ctx, KNOBS))
    assert ev["anchor_ply"] == 4 and ev["base_route"] == "lapse_offense"
    assert ev["evidence"]["kinds"] == ["missed_mate", "material"] and ev["evidence"]["secondary"] == ["material"]
    assert 45 < ev["cost"] < 56  # charged once
    assert ev["pool_key"] == "line:7:3:12" and ev["opening_candidate"] is False
    assert ev["book_relation"] == "post_book"
    e = ev["evidence"]
    assert (e["class"], e["theme"], e["mate_in_moves"]) == ("missed_mate", "mate", 2)
    assert isinstance(e["game_peak_es"], float)
    twice = tag_review_events(ctx, KNOBS)
    assert json.dumps(twice, sort_keys=True) == json.dumps(tag_review_events(ctx, KNOBS), sort_keys=True)


def test_a_missed_mate_in_a_won_game_is_priced_by_the_mate_rung() -> None:
    ctx = make_ctx(
        MM_MOVES,
        list(MM_CPS),
        result="win",
        mates={2: 2},
        motif_missed=[{"ply": 2, "metric_type": "mate", "theme": "mate", "mate_in_moves": 2}],
    )
    ev = only(tag_review_events(ctx, KNOBS))
    assert ev["base_route"] == "lapse_offense"
    assert ev["evidence"]["es_authority_before"] == "mate"
    assert abs(ev["cost"] - (100.0 - ev["evidence"]["es_after"])) < 0.02 and ev["cost"] > 5
    assert ev["evidence"]["proof_state"] == "settled_terminal"
    assert ev["evidence"]["game_peak_es"] == 100.0


def test_an_opponent_ply_in_motif_missed_is_not_evidence() -> None:
    ctx = make_ctx(MM_MOVES, list(MM_CPS), motif_missed=[{"ply": 3, "metric_type": "motif", "theme": "fork"}])
    assert tag_review_events(ctx, KNOBS) == ([], 0)


def test_faded_is_one_game_level_event_gated_by_the_peak() -> None:
    ev = only(tag_review_events(make_ctx(F1_MOVES, list(F1_CPS)), KNOBS))
    assert ev["base_route"] == "faded" and ev["evidence"]["kinds"] == ["faded"]
    assert ev["anchor_ply"] == 4 and ev["evidence"]["proof_state"] is None
    assert ev["evidence"]["detectors"][0]["shape"] == "papercut"
    assert (ev["evidence"]["class"], ev["evidence"]["theme"], ev["evidence"]["mate_in_moves"]) == ("faded", None, None)
    assert ev["evidence"]["game_peak_es"] == 62.61
    assert tag_review_events(make_ctx(F1_MOVES, list(F1_CPS)), dict(KNOBS, review_faded_peak_es=70)) == ([], 0)


def test_a_sharp_event_suppresses_faded_and_the_canonical_pair_keys_the_pool() -> None:
    ev = only(tag_review_events(f2_ctx(), KNOBS))
    assert ev["base_route"] == "lapse_defense" and "faded" not in ev["evidence"]["kinds"]
    assert ev["pool_key"] == "canon:Kings Pawn Game::Center Attack" and ev["piece_label"] == "minor"


def test_a_clean_game_has_no_events() -> None:
    assert tag_review_events(make_ctx(MM_MOVES, list(MM_CPS), result="win"), KNOBS) == ([], 0)


# --- routing --------------------------------------------------------------------------------


def test_endgame_technique_by_men_count_and_by_pawn_ending() -> None:
    small = make_ctx(KQKR_MOVES, list(KQKR_CPS), starting_fen=KQKR_FEN, variant="chess960", best_lines=dict(KQKR_LINES))
    assert only(tag_review_events(small, KNOBS))["phase"] == "opening"  # the heuristic exit is late
    small["repertoire"] = REP_AT_START  # book exit at ply 0: the anchor is in the endgame
    ev = only(tag_review_events(small, KNOBS))
    assert ev["phase"] == "endgame" and ev["base_route"] == "endgame_technique"
    assert ev["book_relation"] == "deviation_before"
    pawns = make_ctx(
        PAWNS_MOVES, list(PAWNS_CPS), starting_fen=PAWNS_FEN, variant="chess960", best_lines=dict(PAWNS_LINES)
    )
    pawns["repertoire"] = REP_AT_START
    ev = only(tag_review_events(pawns, KNOBS))
    assert ev["anchor_ply"] == 0 and ev["base_route"] == "endgame_technique"  # eight men, but a pawn ending


def test_a_small_castling_free_position_is_priced_by_the_sigmoid_alone() -> None:
    """Four men and no castling rights: the old detector consulted a table here; this one
    prices the win-to-nothing slip from the evals (docs/decisions/001)."""
    ctx = make_ctx(KQKR_MOVES, list(KQKR_CPS), starting_fen=KQKR_FEN, variant="chess960", best_lines=dict(KQKR_LINES))
    ev = only(tag_review_events(ctx, KNOBS))
    e = ev["evidence"]
    assert (e["es_authority_before"], e["es_authority_after"]) == ("sigmoid", "sigmoid")
    assert e["es_before"] == 96.49 and e["es_after"] == 50.0 and ev["cost"] == 46.49


def test_no_source_line_names_a_table_over_the_board() -> None:
    root = Path(__file__).resolve().parents[2]
    hits = [
        str(p.relative_to(root))
        for d in ("core", "pipeline")
        for p in (root / d).rglob("*.py")
        if re.search(r"tablebase|syzygy", p.read_text(), re.IGNORECASE)
    ]
    assert hits == []
    assert REVIEW_CONFIG_VERSION == 2


@pytest.mark.parametrize("relation, rep", [
    ("opponent_left", {**REP_AT_START, "deviation_by": "opponent"}),
    ("inside_line", {**REP_AT_START, "deviated_at_ply": None, "deviation_by": None, "matched_ply": 6}),
    ("post_book", {**REP_AT_START, "deviated_at_ply": None, "deviation_by": None, "matched_ply": 2}),
    ("deviation_before", {**REP_AT_START, "deviated_at_ply": 2, "matched_ply": 2}),
])  # fmt: skip
def test_book_relation_at_the_anchor(relation: str, rep: dict[str, Any]) -> None:
    ev = only(tag_review_events(qh_ctx(repertoire=rep), KNOBS))
    assert ev["book_relation"] == relation and ev["pool_key"] == "line:7:3:12"
    # an event at or before book exit + k plies is an opening candidate
    assert ev["opening_candidate"] is True


def test_the_early_cap_follows_the_book_exit() -> None:
    rep = {**REP_AT_START, "deviated_at_ply": 2, "matched_ply": 2}
    assert (
        only(tag_review_events(qh_ctx(repertoire=rep), dict(KNOBS, review_early_k_plies=1)))["opening_candidate"]
        is False
    )


# --- fail closed and authoritative zero -----------------------------------------------------


@pytest.mark.parametrize("termination", detect.CLOCK_DECIDED_TERMINATIONS)
def test_a_clock_decided_game_is_an_authoritative_zero(termination: str) -> None:
    assert tag_review_events(qh_ctx(termination=termination), KNOBS) == ([], 0)


def test_unreliable_input_returns_none() -> None:
    bad = qh_ctx()
    bad["moves"][3] = "Zz9@@"
    assert tag_review_events(bad, KNOBS) is None
    bad = qh_ctx()
    bad["ply_analysis"] = bad["ply_analysis"][:-1]
    assert tag_review_events(bad, KNOBS) is None
    bad = qh_ctx()
    bad["fen_sequence"] = None
    assert tag_review_events(bad, KNOBS) is None
    bad = qh_ctx()
    bad["moves"] = "not json"
    assert tag_review_events(bad, KNOBS) is None


def test_moves_as_json_text_are_accepted() -> None:
    ctx = qh_ctx()
    ctx["moves"] = json.dumps(ctx["moves"])
    assert only(tag_review_events(ctx, KNOBS))["anchor_ply"] == 4


def test_a_chess960_game_replays_from_its_start_position() -> None:
    flat: list[int | None] = [0] * (len(C960_MOVES) + 1)
    ctx = make_ctx(C960_MOVES, flat, starting_fen=C960_FEN, variant="chess960")
    assert tag_review_events(ctx, KNOBS) == ([], 0)
    ctx["variant"] = "standard"  # without the flag the castling move does not parse
    assert tag_review_events(ctx, KNOBS) is None
    ctx["variant"], ctx["starting_fen"] = "chess960", None  # no start position: unbuildable
    assert tag_review_events(ctx, KNOBS) is None


def test_gaps_in_the_evals_are_forward_filled_and_no_evals_price_flat() -> None:
    ctx = qh_ctx()
    ctx["ply_analysis"][7] = None  # a gap after the event changes nothing
    assert only(tag_review_events(ctx, KNOBS))["cost"] == only(tag_review_events(qh_ctx(), KNOBS))["cost"]
    ctx = qh_ctx()
    ctx["ply_analysis"][5] = None  # the position after Nxh5 carries ply 4's eval forward: no drop, pruned
    assert tag_review_events(ctx, KNOBS) == ([], 0)
    assert tag_review_events(qh_ctx(cps=[None] * 9), KNOBS) == ([], 0)


# --- the internals the oracle would otherwise be the only check of ---------------------------

FORCED_FEN = "rnb1kb1r/pppp1ppp/5n2/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR w KQkq - 2 3"  # after e4 e5 Qh5 Nf6


def test_a_leading_eval_gap_takes_the_first_known_value_not_fifty() -> None:
    """The player's first move has no eval: the position before it is priced like the one after,
    so no drop is charged to it. Filled with 50 it would be a 46-point cliff whose PV proves the
    queen lost — a forced event out of nothing."""
    ctx = make_ctx(["a3"], [None, -900], starting_fen=FORCED_FEN, variant="chess960", best_lines={1: "Nxh5 g3 Nf6"})
    assert tag_review_events(ctx, KNOBS) == ([], 0)


def test_the_cliff_threshold_is_twenty_points() -> None:
    lines = {5: "Nxh5 g3 Nf6"}
    over = make_ctx(QH_MOVES[:5], [20, 20, 30, 30, 50, -200], best_lines=lines)  # 54.6 → 32.4
    assert only(tag_review_events(over, KNOBS))["cost"] == 22.21
    under = make_ctx(QH_MOVES[:5], [20, 20, 30, 30, 50, -150], best_lines=lines)  # 54.6 → 36.5
    assert tag_review_events(under, KNOBS) == ([], 0)


def test_mate_in_zero_is_not_a_mate() -> None:
    """`mate_in_moves` 0 carries no sign: the position is priced by the sigmoid."""
    ctx = make_ctx(
        MM_MOVES,
        list(MM_CPS),
        result="win",
        mates={2: 0},
        motif_missed=[{"ply": 2, "metric_type": "mate", "theme": "mate", "mate_in_moves": 2}],
    )
    ev = only(tag_review_events(ctx, KNOBS))
    assert ev["evidence"]["es_authority_before"] == "sigmoid" and ev["cost"] == 0.0


def test_a_rook_for_a_minor_is_the_exchange() -> None:
    ctx = make_ctx(
        ["Rxd7", "Kxd7"],
        [300, -100, -100],
        starting_fen="4k3/3n4/8/8/8/8/P6P/3RK3 w - - 0 1",
        variant="chess960",
        best_lines={2: "Kd2"},
    )
    ev = only(tag_review_events(ctx, KNOBS))
    assert ev["piece_label"] == "exchange" and ev["evidence"]["detectors"][0]["settled_deficit"] >= 2
