"""Similar positions (core/repertoire/neighbourhood.py) and branch compare (branch_compare.py),
and their two routes."""

from __future__ import annotations

import json
from typing import Any

import chess
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.repertoire import branch_compare as bc
from core.repertoire import neighbourhood as nb
from core.repertoire.importing import spine
from tests import repertoire_helpers as h

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6", "d4"]
TWO_KNIGHTS = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "d3", "Bc5", "c3"]
SCOTCH = ["e4", "e5", "Nf3", "Nc6", "d4", "exd4", "Nxd4"]


def fen_after(moves: list[str]) -> str:
    return spine(None, moves)[len(moves)]


# --- the metric ------------------------------------------------------------------------------


def test_expand_placement_is_64_squares_rank_8_first() -> None:
    p = nb.expand_placement(chess.STARTING_FEN)
    assert len(p) == 64 and p[:8] == "rnbqkbnr" and p[-8:] == "RNBQKBNR" and p[16:48] == "." * 32
    with pytest.raises(ValueError):
        nb.expand_placement("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP w")
    with pytest.raises(ValueError):
        nb.expand_placement("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNRR w")
    with pytest.raises(ValueError):
        nb.expand_placement("")


def test_square_name_indexes_from_a8() -> None:
    assert (
        nb.square_name(0) == "a8"
        and nb.square_name(7) == "h8"
        and nb.square_name(56) == "a1"
        and nb.square_name(63) == "h1"
    )
    with pytest.raises(ValueError):
        nb.square_name(64)


def test_distance_move_is_2_castling_4_en_passant_3() -> None:
    start = nb.expand_placement(chess.STARTING_FEN)
    assert nb.placement_distance(start, start) == 0
    assert nb.placement_distance(start, nb.expand_placement(fen_after(["e4"]))) == 2
    castled = fen_after(["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O"])
    before = fen_after(["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"])
    assert nb.placement_distance(nb.expand_placement(before), nb.expand_placement(castled)) == 4
    assert nb.looks_like_castling(nb.expand_placement(before), nb.expand_placement(castled))
    ep_before = fen_after(["e4", "a6", "e5", "d5"])
    ep_after = fen_after(["e4", "a6", "e5", "d5", "exd6"])
    assert nb.placement_distance(nb.expand_placement(ep_before), nb.expand_placement(ep_after)) == 3
    # The cutoff is an early exit that returns a lower bound, never the distance.
    assert nb.placement_distance(start, nb.expand_placement(castled), cutoff=2) == 3
    with pytest.raises(ValueError):
        nb.placement_distance(start[:-1], start)


def test_diff_squares_names_query_then_neighbour_occupant() -> None:
    d = nb.diff_squares(nb.expand_placement(chess.STARTING_FEN), nb.expand_placement(fen_after(["e4"])))
    assert d == [{"square": "e4", "from": None, "to": "P"}, {"square": "e2", "from": "P", "to": None}]


def test_castling_rights_and_castle_shape() -> None:
    assert nb.castling_rights("x w KQ -") == frozenset("KQ") and nb.castling_rights("x w -") == frozenset()
    assert nb.castling_rights("x") == frozenset()
    start = nb.expand_placement(chess.STARTING_FEN)
    # Two pieces moved without a king involved is not a castle shape.
    assert not nb.looks_like_castling(start, nb.expand_placement(fen_after(["e4", "e5"])))


def test_parse_arriving_is_canonical_and_fails_closed() -> None:
    a = nb.parse_arriving(fen_after(["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]), "0-0")
    assert a == {"san": "O-O", "from": "e1", "to": "g1", "promotion": None, "is_castling": True, "is_en_passant": False}
    assert nb.parse_arriving(chess.STARTING_FEN, "e5") is None
    assert nb.parse_arriving(None, "e4") is None and nb.parse_arriving(chess.STARTING_FEN, None) is None
    assert nb.parse_arriving("not a fen", "e4") is None
    promo = nb.parse_arriving("8/P6k/8/8/8/8/8/K7 w - - 0 1", "a8=N")
    assert promo is not None and promo["promotion"] == "n"


# --- verify and expansion, driven directly -----------------------------------------------


def _row(moves: list[str], sigs: list[str | None], **over: Any) -> dict[str, Any]:
    fens = spine(None, moves)
    return {
        "line_id": 1,
        "line_name": "L",
        "moves": moves,
        "fen_sequence": fens,
        "is_alternative": False,
        "chapter_title": "C",
        "book_title": "B",
        "material_sigs": sigs,
        **over,
    }


def test_a_hash_match_with_a_different_signature_never_becomes_a_carrier() -> None:
    query = fen_after(["e4", "e5"])
    row = _row(["e4", "e5", "Nf3"], [None, None, "SIG", "SIG"])
    assert [c["line_ply"] for c in nb.expand_and_verify([row], query, "SIG", 4)] == [2]
    # Same rows, a signature that differs on every ply: nothing survives step 2.
    assert nb.expand_and_verify([row], query, "OTHER", 4) == []
    # A NULL signature (hash-unmatched element) is never equal to a real one.
    assert nb.expand_and_verify([_row(["e4", "e5", "Nf3"], [None, None, None, None])], query, "SIG", 4) == []


def test_ply_0_and_the_other_side_to_move_are_not_nodes() -> None:
    query = fen_after(["e4", "e5"])
    row = _row(["e4", "e5"], ["SIG", "SIG", "SIG"])
    carriers = nb.expand_and_verify([row], query, "SIG", 8)
    assert [c["line_ply"] for c in carriers] == [2]  # ply 0 (no arriving move) and ply 1 (Black to move) are out


def test_a_row_breaking_the_length_invariant_is_skipped_not_repaired() -> None:
    query = fen_after(["e4", "e5"])
    row = _row(["e4", "e5"], ["SIG", "SIG"])  # sigs shorter than fens
    assert nb.expand_and_verify([row], query, "SIG", 8) == []
    row = _row(["e4", "e5"], ["SIG", "SIG", "SIG"], fen_sequence=spine(None, ["e4", "e5"])[:2])
    assert nb.expand_and_verify([row], query, "SIG", 8) == []


def test_an_unparseable_arriving_move_keeps_the_node_out_of_the_corpus() -> None:
    query = fen_after(["e4", "e5"])
    row = {**_row(["e4", "e5"], ["SIG", "SIG", "SIG"]), "moves": ["e4", "Qxe5"]}
    assert nb.expand_and_verify([row], query, "SIG", 8) == []


def test_prep_groups_orders_status_then_move_and_attributes_unreadable_tokens() -> None:
    fen = fen_after(["e4", "e5"])
    base = {"book": "B", "chapter": "C", "fen": fen, "arriving": {"san": "e5"}, "is_alternative": False, "line_ply": 2}
    carriers = [
        {**base, "line_name": "z", "line_id": 3, "expected_move": "Nf3"},
        {**base, "line_name": "y", "line_id": 2, "expected_move": None},
        {**base, "line_name": "x", "line_id": 1, "expected_move": "Ke2??"},  # illegal here: unreadable
        {**base, "line_name": "w", "line_id": 4, "expected_move": "Bc4"},
        {**base, "line_name": "v", "line_id": 5, "expected_move": "Ng1-f3"},  # a spelling of Nf3
    ]
    groups, divergent = nb.prep_groups(fen, carriers, "Nf3")
    assert [(g["prep_status"], g["prep_move"], g["prep_raw_token"]) for g in groups] == [
        ("move", "Bc4", None),
        ("move", "Nf3", None),
        ("end_of_line", None, None),
        ("unreadable", None, "Ke2??"),
    ]
    assert divergent
    nf3 = groups[1]
    assert nf3["is_queried_move"] and nf3["carried_by_line_count"] == 2 and nf3["line_name"] == "v"
    assert not groups[0]["is_queried_move"] and not groups[3]["is_queried_move"]


def test_prep_groups_same_line_twice_is_not_divergent() -> None:
    fen = fen_after(["e4", "e5"])
    base = {
        "book": "B",
        "chapter": "C",
        "line_name": "L",
        "line_id": 1,
        "fen": fen,
        "arriving": {},
        "is_alternative": False,
    }
    groups, divergent = nb.prep_groups(
        fen, [{**base, "line_ply": 2, "expected_move": "Nf3"}, {**base, "line_ply": 8, "expected_move": "Bc4"}]
    )
    assert len(groups) == 2 and not divergent


# --- the search on a database ---------------------------------------------------------------


@pytest.fixture()
def rep(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    h.player(clean)
    h.book(clean, 1, "Italian", "white")
    h.chapter(clean, 1, 1, "Giuoco")
    h.line(clean, 1, 1, "Main", ITALIAN)
    h.line(clean, 2, 1, "Two Knights", TWO_KNIGHTS)
    h.book(clean, 2, "Scotch", "white")
    h.chapter(clean, 2, 2, "Mainline")
    h.line(clean, 3, 2, "Scotch main", SCOTCH)
    h.book(clean, 3, "As Black", "black")
    h.chapter(clean, 3, 3, "Open games")
    h.line(clean, 4, 3, "Italian as Black", ITALIAN[:6])
    clean.commit()
    return clean


def similar(conn: psycopg.Connection[DictRow], fen: str, move: str | None = None, **kw: Any) -> dict[str, Any]:
    return nb.similar_positions(
        conn, fen, move, max_distance=kw.get("max_distance", 4), max_positions=kw.get("max_positions", 12)
    )


def test_the_query_board_itself_is_a_neighbour_at_distance_0(rep: psycopg.Connection[DictRow]) -> None:
    r = similar(rep, fen_after(ITALIAN[:6]))
    assert r["neighbours"][0]["distance"] == 0 and r["neighbours"][0]["same_material"]
    groups = r["neighbours"][0]["groups"]
    assert [g["prep_move"] for g in groups] == ["c3"] and groups[0]["book_title"] == "Italian"
    assert groups[0]["arriving"]["san"] == "Bc5" and not r["neighbours"][0]["board_prep_divergent"]
    # The Black book holds the same board with Black to move — a different node, not this one.
    assert all(g["book_title"] != "As Black" for n in r["neighbours"] for g in n["groups"])


def test_only_the_query_colours_books_contribute(rep: psycopg.Connection[DictRow]) -> None:
    # Black to move after 1.e4 e5 2.Nf3 Nc6 3.Bc4: the White books hold this board (ply 5, Black to move) but
    # a White book's node there is the *opponent's* turn; only the Black book answers.
    r = similar(rep, fen_after(ITALIAN[:5]))
    books = {g["book_title"] for n in r["neighbours"] for g in n["groups"]}
    assert books == {"As Black"}


def test_distance_orders_and_a_transposition_is_one_board(rep: psycopg.Connection[DictRow]) -> None:
    # After 3...Bc5 the query; 3...Nf6 (Two Knights) has a different piece developed: four squares differ.
    r = similar(rep, fen_after(ITALIAN[:6]))
    dists = [n["distance"] for n in r["neighbours"]]
    assert dists == sorted(dists) and dists[0] == 0 and 4 in dists and 2 not in dists
    two_knights = next(n for n in r["neighbours"] if n["distance"] == 4 and n["groups"][0]["prep_move"] == "d3")
    assert {d["square"] for d in two_knights["diff_squares"]} == {"c5", "f8", "f6", "g8"}
    assert [g["prep_move"] for g in two_knights["groups"]] == ["d3"]
    assert two_knights["groups"][0]["arriving"]["san"] == "Nf6"


def test_material_must_match_exactly_whatever_the_distance(rep: psycopg.Connection[DictRow]) -> None:
    # The Scotch after 4...exd4: a pawn is gone, so no board of the Italian lines (all 32 pieces) can appear,
    # however close the placement.
    r = similar(rep, fen_after(SCOTCH[:6]), max_distance=8)
    assert {g["book_title"] for n in r["neighbours"] for g in n["groups"]} == {"Scotch"}
    assert all(n["same_material"] for n in r["neighbours"])


def test_the_queried_move_is_flagged_and_must_be_canonical(rep: psycopg.Connection[DictRow]) -> None:
    r = similar(rep, fen_after(ITALIAN[:6]), "c3")
    assert r["query"]["move"] == "c3" and r["neighbours"][0]["groups"][0]["is_queried_move"]
    # The flag is per neighbour group: the Two Knights board (a neighbour) prescribes d3, so asking about d3
    # flags that group and not the query board's own c3.
    r = similar(rep, fen_after(ITALIAN[:6]), "d3")
    flagged = [(n["distance"], g["prep_move"]) for n in r["neighbours"] for g in n["groups"] if g["is_queried_move"]]
    assert flagged == [(4, "d3")]
    r = similar(rep, fen_after(ITALIAN[:6]), "Nc3")
    assert not any(g["is_queried_move"] for n in r["neighbours"] for g in n["groups"])


def test_the_cap_counts_boards_and_keeps_every_group(rep: psycopg.Connection[DictRow]) -> None:
    full = similar(rep, fen_after(ITALIAN[:6]), max_distance=8)
    assert len(full["neighbours"]) > 2 and not full["truncated"]
    capped = similar(rep, fen_after(ITALIAN[:6]), max_distance=8, max_positions=2)
    assert capped["truncated"] and capped["positions_omitted"] == len(full["neighbours"]) - 2
    assert capped["neighbours"] == full["neighbours"][:2]


def test_inactive_lines_chapters_and_books_are_not_in_the_corpus(rep: psycopg.Connection[DictRow]) -> None:
    fen = fen_after(ITALIAN[:6])
    assert similar(rep, fen)["neighbours"][0]["distance"] == 0
    rep.execute("UPDATE repertoire_lines SET active = false WHERE id = 1")
    assert all(n["distance"] > 0 for n in similar(rep, fen)["neighbours"])
    rep.execute("UPDATE repertoire_lines SET active = true WHERE id = 1")
    rep.execute("UPDATE chapters SET active = false WHERE id = 1")
    assert all(g["book_title"] != "Italian" for n in similar(rep, fen)["neighbours"] for g in n["groups"])
    rep.execute("UPDATE chapters SET active = true WHERE id = 1")
    rep.execute("UPDATE books SET active = false WHERE id = 1")
    assert all(g["book_title"] != "Italian" for n in similar(rep, fen)["neighbours"] for g in n["groups"])
    rep.rollback()


def test_end_of_line_and_divergence_across_lines(rep: psycopg.Connection[DictRow]) -> None:
    # A third line in the Italian chapter that stops at 3...Bc5 and a fourth that plays 4.b4 there.
    h.line(rep, 5, 1, "Short", ITALIAN[:6])
    h.line(rep, 6, 1, "Evans", ITALIAN[:6] + ["b4"])
    board = similar(rep, fen_after(ITALIAN[:6]))["neighbours"][0]
    assert [(g["prep_status"], g["prep_move"]) for g in board["groups"]] == [
        ("move", "b4"),
        ("move", "c3"),
        ("end_of_line", None),
    ]
    assert board["board_prep_divergent"]
    assert board["groups"][2]["line_name"] == "Short"
    rep.rollback()


def test_a_corrupt_stored_token_is_shown_as_unreadable_and_attributed(rep: psycopg.Connection[DictRow]) -> None:
    fens = spine(None, ITALIAN[:7])
    h.line(rep, 7, 1, "Corrupt", ITALIAN[:6] + ["Qxh7"], fens=fens)
    board = similar(rep, fen_after(ITALIAN[:6]))["neighbours"][0]
    bad = [g for g in board["groups"] if g["prep_status"] == "unreadable"]
    assert len(bad) == 1 and bad[0]["prep_raw_token"] == "Qxh7" and bad[0]["line_name"] == "Corrupt"
    assert not board["board_prep_divergent"]  # an unreadable token is not a known move to disagree with
    assert [g["prep_move"] for g in board["groups"] if g["prep_status"] == "move"] == ["c3"]
    rep.rollback()


def test_castling_delta_and_castle_shape(rep: psycopg.Connection[DictRow]) -> None:
    h.line(rep, 8, 1, "Castled", ITALIAN[:6] + ["O-O", "d6", "c3"])
    r = similar(rep, fen_after(ITALIAN[:6] + ["O-O", "d6"]), max_distance=8)
    shapes = {n["distance"]: n for n in r["neighbours"]}
    assert 0 in shapes and shapes[0]["castling_delta"] == []
    # 3...Bc5 (uncastled, Black to move) has the same material and White's rights differ, but it is White's move
    # there: not a node of this query. The castled query against the line's own uncastled continuation is what
    # distance 6 / castle shape describes.
    castle_shapes = [n for n in r["neighbours"] if n["is_castle_shape"]]
    assert all(set(n["castling_delta"]) >= {"K", "Q"} for n in castle_shapes)
    rep.rollback()


def test_query_material_rejects_a_fen_with_no_signature(rep: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(ValueError):
        nb.query_material(rep, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR x KQkq - 0 1")
    with pytest.raises(ValueError):
        similar(rep, chess.STARTING_FEN, max_distance=9)
    with pytest.raises(ValueError):
        similar(rep, chess.STARTING_FEN, max_positions=0)


# --- branch compare ---------------------------------------------------------------------


def blunder(
    conn: psycopg.Connection[DictRow], gid: int, ply: int, fen: str, played: str, best: str | None, cp: int | None
) -> None:
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, move_played, best_move, centipawn_loss, classification)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 'blunder')",
        (PLAYER_ID, gid, ply, fen, played, best, cp),
    )


PARENT = fen_after(ITALIAN[:5])  # after 3.Bc4, Black to move
CURRENT = fen_after(ITALIAN[:6])  # 3...Bc5
ARRIVING = nb.parse_arriving(PARENT, "Bc5")
assert ARRIVING is not None


def compare(conn: psycopg.Connection[DictRow], fen: str = CURRENT, pre: str = PARENT, **kw: Any) -> dict[str, Any]:
    arriving = nb.parse_arriving(pre, kw.pop("san", "Bc5"))
    assert arriving is not None
    return bc.branch_compare(
        conn, fen, pre, arriving=arriving, book_color=kw.pop("book_color", "white"), max_boards=kw.pop("max_boards", 12)
    )


def test_repertoire_branches_at_the_parent_with_current_popped_out(rep: psycopg.Connection[DictRow]) -> None:
    r = compare(rep)
    assert r["current"]["child_fen"] == CURRENT and r["current"]["opponent_move"]["san"] == "Bc5"
    cur = r["current"]["sources"]
    assert cur["repertoire"]["reply_san"] == "c3" and cur["repertoire"]["reply_squares"] == {"from": "c2", "to": "c3"}
    assert cur["repertoire"]["line_count"] == 1 and cur["blunders"] is None and cur["scout"] is None
    assert [b["opponent_move"]["san"] for b in r["branches"]] == ["Nf6"]
    alt = r["branches"][0]
    assert alt["child_fen"] == fen_after(TWO_KNIGHTS[:6]) and alt["sources"]["repertoire"]["reply_san"] == "d3"
    assert [g["prep_move"] for g in alt["sources"]["repertoire"]["groups"]] == ["d3"]
    assert not r["truncated"] and r["boards_omitted"] == 0 and r["query"]["book_color"] == "white"


def test_current_always_exists_even_when_nothing_covers_it(rep: psycopg.Connection[DictRow]) -> None:
    pre = fen_after(SCOTCH[:5])  # after 3.d4, Black to move; the repertoire only knows 3...exd4
    fen = fen_after(SCOTCH[:5] + ["d6"])
    r = compare(rep, fen, pre, san="d6")
    assert r["current"] == {
        "child_fen": fen,
        "opponent_move": nb.parse_arriving(pre, "d6"),
        "sources": {"repertoire": None, "blunders": None, "scout": None},
    }
    assert [b["opponent_move"]["san"] for b in r["branches"]] == ["exd4"]


def test_a_line_ending_at_the_parent_branches_nothing_and_a_terminal_child_is_end_of_line(
    rep: psycopg.Connection[DictRow],
) -> None:
    h.line(rep, 5, 1, "Stops at parent", ITALIAN[:5])
    h.line(rep, 6, 1, "Stops at child", ITALIAN[:5] + ["d6"])
    r = compare(rep)
    sans = [b["opponent_move"]["san"] for b in r["branches"]]
    assert sans == ["Nf6", "d6"]  # both one line; tie broken by board text
    d6 = r["branches"][1]["sources"]["repertoire"]
    assert d6["end_of_line"] and d6["reply_san"] is None and d6["groups"][0]["prep_status"] == "end_of_line"
    rep.rollback()


def test_divergent_lines_give_no_reply_arrow(rep: psycopg.Connection[DictRow]) -> None:
    h.line(rep, 6, 1, "Evans", ITALIAN[:6] + ["b4"])
    cur = compare(rep)["current"]["sources"]["repertoire"]
    assert cur["board_prep_divergent"] and cur["reply_san"] is None and cur["reply_squares"] is None
    assert cur["line_count"] == 2 and [g["prep_move"] for g in cur["groups"]] == ["b4", "c3"]
    rep.rollback()


def test_blunders_leg_worst_first_distinct_games_and_repertoire_wins(rep: psycopg.Connection[DictRow]) -> None:
    # Two games where Black played 3...h6 (not in the repertoire) and I blundered with 4.Nxe5?; a third game
    # where Black played 3...Bc5 (in the repertoire) and I blundered: repertoire wins on that board.
    h6 = ITALIAN[:5] + ["h6"]
    for gid, cp in ((1, 150), (2, 400)):
        fens = h.game(rep, gid, h6 + ["Nxe5"])
        blunder(rep, gid, 6, fens[6], "Nxe5", "d3", cp)
    fens = h.game(rep, 3, ITALIAN[:6] + ["Nxe5"])
    blunder(rep, 3, 6, fens[6], "Nxe5", "c3", 300)
    r = compare(rep)
    assert r["current"]["sources"]["blunders"] is None and r["current"]["sources"]["repertoire"] is not None
    by_san = {b["opponent_move"]["san"]: b for b in r["branches"]}
    assert list(by_san) == ["Nf6", "h6"]  # repertoire tier before blunder tier
    blu = by_san["h6"]["sources"]["blunders"]
    assert by_san["h6"]["sources"]["repertoire"] is None
    assert blu["games"] == 2 and blu["worst"]["centipawn_loss"] == 400
    assert blu["worst"]["move_played_san"] == "Nxe5" and blu["worst"]["move_played_squares"] == {
        "from": "f3",
        "to": "e5",
    }
    assert blu["worst"]["best_move_san"] == "d3" and blu["worst"]["best_move_squares"] == {"from": "d2", "to": "d3"}
    rep.rollback()


def test_blunders_leg_positive_control_and_chess960_exclusion(rep: psycopg.Connection[DictRow]) -> None:
    h6 = ITALIAN[:5] + ["h6"]
    fens = h.game(rep, 1, h6 + ["Nxe5"])
    blunder(rep, 1, 6, fens[6], "Nxe5", "d3", 200)
    assert [b["opponent_move"]["san"] for b in compare(rep)["branches"]] == ["Nf6", "h6"]
    # The same rows on a Chess960 game count for nothing (core.chess.eligibility).
    rep.execute("UPDATE chess_games SET variant = 'chess960', starting_fen = fen_sequence->>0 WHERE id = 1")
    assert [b["opponent_move"]["san"] for b in compare(rep)["branches"]] == ["Nf6"]
    rep.rollback()


def test_an_aged_out_game_drops_out_and_a_blunder_at_the_wrong_ply_is_not_a_branch(
    rep: psycopg.Connection[DictRow],
) -> None:
    h6 = ITALIAN[:5] + ["h6"]
    fens = h.game(rep, 1, h6 + ["Nxe5"])
    blunder(rep, 1, 6, fens[6], "Nxe5", "d3", 200)
    # A blunder two plies later has a different parent: not an option at this parent.
    fens2 = h.game(rep, 2, h6 + ["d3", "d6", "Nxe5"])
    blunder(rep, 2, 8, fens2[8], "Nxe5", "c3", 300)
    assert [b["opponent_move"]["san"] for b in compare(rep)["branches"]] == ["Nf6", "h6"]
    rep.execute("UPDATE chess_games SET fen_sequence = NULL, moves = NULL WHERE id = 1")
    assert [b["opponent_move"]["san"] for b in compare(rep)["branches"]] == ["Nf6"]
    rep.rollback()


def test_a_blunder_whose_move_does_not_parse_counts_but_is_not_the_representative(
    rep: psycopg.Connection[DictRow],
) -> None:
    h6 = ITALIAN[:5] + ["h6"]
    fens = h.game(rep, 1, h6 + ["Nxe5"])
    blunder(rep, 1, 6, fens[6], "Qxh7", "d3", 900)  # corrupt token, worst by cp
    fens = h.game(rep, 2, h6 + ["Nxe5"])
    blunder(rep, 2, 6, fens[6], "Nxe5", None, 200)
    blu = compare(rep)["branches"][-1]["sources"]["blunders"]
    assert blu["games"] == 2 and blu["worst"]["centipawn_loss"] == 200 and blu["worst"]["best_move_san"] is None
    rep.rollback()


def test_the_cap_counts_alternatives_and_never_current(rep: psycopg.Connection[DictRow]) -> None:
    h6 = ITALIAN[:5] + ["h6"]
    fens = h.game(rep, 1, h6 + ["Nxe5"])
    blunder(rep, 1, 6, fens[6], "Nxe5", "d3", 200)
    r = compare(rep, max_boards=1)
    assert r["current"]["opponent_move"]["san"] == "Bc5" and len(r["branches"]) == 1
    assert r["truncated"] and r["boards_omitted"] == 1
    rep.rollback()


def test_merge_orders_tiers_then_size_then_board_text() -> None:
    def rep_src(n: int) -> dict[str, Any]:
        return {"child_fen": "r", "arriving": {}, "source": {"line_count": n}}

    def blu_src(cp: int | None) -> dict[str, Any]:
        return {"child_fen": "b", "arriving": {}, "source": {"worst": {"centipawn_loss": cp}}}

    def sco_src(n: int) -> dict[str, Any]:
        return {"child_fen": "s", "arriving": {}, "source": {"total_games": n}}

    current, ordered = bc.merge_branches(
        "cur",
        {"r1": rep_src(1), "r3": rep_src(3), "both": rep_src(1), "cur": rep_src(1)},
        {"b_low": blu_src(50), "b_high": blu_src(500), "b_none": blu_src(None), "both": blu_src(999)},
        {"s2": sco_src(2), "both": sco_src(7)},
    )
    assert current is not None and current["sources"]["repertoire"] == {"line_count": 1}
    keys = [(b["sources"]["repertoire"], b["sources"]["blunders"], b["sources"]["scout"]) for b in ordered]
    assert keys == [
        ({"line_count": 3}, None, None),
        (
            {"line_count": 1},
            None,
            {"total_games": 7},
        ),  # 'both': repertoire wins, scout survives, board text after r1? no: 'both' < 'r1'
        ({"line_count": 1}, None, None),
        (None, {"worst": {"centipawn_loss": 500}}, None),
        (None, {"worst": {"centipawn_loss": 50}}, None),
        (None, {"worst": {"centipawn_loss": None}}, None),
        (None, None, {"total_games": 2}),
    ]


def test_branch_compare_rejects_a_defaulted_colour_and_a_bad_cap(rep: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(ValueError):
        compare(rep, book_color="by_turn")
    with pytest.raises(ValueError):
        compare(rep, max_boards=0)


# --- the routes ---------------------------------------------------------------------------


@pytest.fixture()
def client(app_env: None, rep: psycopg.Connection[DictRow]) -> TestClient:
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_routes_need_login(app_env: None) -> None:
    from api.main import create_app

    c = TestClient(create_app())
    assert c.get("/repertoire/similar", params={"fen": chess.STARTING_FEN}).status_code == 401
    assert c.get("/repertoire/branch-compare", params={"fen": CURRENT, "pre_fen": PARENT}).status_code == 401


def test_similar_route_validates_and_reserialises(client: TestClient) -> None:
    r = client.get("/repertoire/similar", params={"fen": fen_after(ITALIAN[:6]), "move": "c2c3"})
    assert r.status_code == 200 and r.json()["query"]["move"] == "c3" and r.json()["query"]["max_distance"] == 4
    assert r.json()["neighbours"][0]["distance"] == 0
    # A client FEN with a phantom en-passant square (chess.js after a double push) is handed to the search
    # as python-chess's own serialisation (the branch-compare test below is where the field matters).
    after_c3_d5 = fen_after(ITALIAN[:7] + ["Nf6", "d4"])
    assert " - " in after_c3_d5 and after_c3_d5.split(" ")[1] == "b"
    h_line = client.get("/repertoire/similar", params={"fen": after_c3_d5}).json()
    assert h_line["neighbours"] == []  # no Black book covers this; White's lines are the other colour here
    white_query = fen_after(ITALIAN[:6] + ["c3", "d5"])  # ...d5 double push, White to move
    phantom = white_query.replace(" - ", " d6 ")
    assert client.get("/repertoire/similar", params={"fen": phantom}).json()["query"]["fen"] == white_query
    # Four-field FENs are accepted and serialised.
    four = " ".join(fen_after(ITALIAN[:6]).split(" ")[:4])
    assert client.get("/repertoire/similar", params={"fen": four}).json()["query"]["fen"] == four + " 0 1"
    assert client.get("/repertoire/similar", params={"fen": "not a fen"}).status_code == 400
    assert client.get("/repertoire/similar", params={"fen": fen_after(ITALIAN[:6]), "move": "Kd3"}).status_code == 400
    assert (
        client.get("/repertoire/similar", params={"fen": fen_after(ITALIAN[:6]), "max_distance": 2}).json()["query"][
            "max_distance"
        ]
        == 2
    )
    assert (
        client.get("/repertoire/similar", params={"fen": fen_after(ITALIAN[:6]), "max_distance": 5}).status_code == 400
    )
    assert (
        client.get("/repertoire/similar", params={"fen": fen_after(ITALIAN[:6]), "max_distance": 0}).status_code == 422
    )
    assert (
        client.get("/repertoire/similar", params={"fen": fen_after(ITALIAN[:6]), "max_distance": "2.5"}).status_code
        == 422
    )


def test_similar_route_reads_the_settings(client: TestClient, rep: psycopg.Connection[DictRow]) -> None:
    from core import settings

    s = settings.Settings(similar_max_distance=8, similar_max_positions=1)
    settings.save(rep, s)
    r = client.get("/repertoire/similar", params={"fen": fen_after(ITALIAN[:6])}).json()
    assert r["query"] == {"fen": fen_after(ITALIAN[:6]), "move": None, "max_distance": 8, "max_positions": 1}
    assert len(r["neighbours"]) == 1 and r["truncated"]


def test_branch_compare_route_validates_the_pair(client: TestClient) -> None:
    r = client.get("/repertoire/branch-compare", params={"fen": CURRENT, "pre_fen": PARENT})
    assert r.status_code == 200 and r.json()["current"]["opponent_move"]["san"] == "Bc5"
    assert r.json()["query"]["max_boards"] == 12 and [b["opponent_move"]["san"] for b in r.json()["branches"]] == [
        "Nf6"
    ]
    same_side = client.get("/repertoire/branch-compare", params={"fen": CURRENT, "pre_fen": fen_after(ITALIAN[:4])})
    assert same_side.status_code == 400
    two_apart = client.get("/repertoire/branch-compare", params={"fen": fen_after(ITALIAN[:7]), "pre_fen": PARENT})
    assert two_apart.status_code == 400
    assert client.get("/repertoire/branch-compare", params={"fen": "x", "pre_fen": PARENT}).status_code == 400
    assert client.get("/repertoire/branch-compare", params={"fen": CURRENT}).status_code == 422
    # A phantom en-passant square on either input is harmless: the pair is joined on the board.
    pre = fen_after(ITALIAN[:6] + ["c3"])  # Black to move
    child = fen_after(ITALIAN[:6] + ["c3", "d5"])  # ...d5, White to move, python-chess writes '-'
    phantom = child.replace(" - ", " d6 ")
    r = client.get("/repertoire/branch-compare", params={"fen": phantom, "pre_fen": pre})
    assert (
        r.status_code == 200
        and r.json()["query"]["fen"] == child
        and r.json()["current"]["opponent_move"]["san"] == "d5"
    )


def test_branch_compare_reads_the_setting(client: TestClient, rep: psycopg.Connection[DictRow]) -> None:
    from core import settings

    settings.save(rep, settings.Settings(branch_compare_max_boards=1))
    h.line(rep, 6, 1, "Stops at child", ITALIAN[:5] + ["d6"])
    rep.commit()
    r = client.get("/repertoire/branch-compare", params={"fen": CURRENT, "pre_fen": PARENT}).json()
    assert r["query"]["max_boards"] == 1 and len(r["branches"]) == 1 and r["boards_omitted"] == 1


def test_settings_ceiling_matches_the_module() -> None:
    from core import settings

    assert settings.schema()["properties"]["similar_max_distance"]["maximum"] == nb.MAX_DISTANCE_CEILING


def test_json_round_trip_of_a_response(rep: psycopg.Connection[DictRow]) -> None:
    json.dumps(similar(rep, fen_after(ITALIAN[:6])))
    json.dumps(compare(rep))


# --- edge cases: null moves, malformed FENs, ply 0, repetitions, corrupt rows, ordering ----------


def test_null_moves_are_not_moves_anywhere() -> None:
    # python-chess parses '--', 'Z0' and '0000' as the null move without complaint.
    assert nb.parse_arriving(chess.STARTING_FEN, "--") is None and nb.parse_arriving(chess.STARTING_FEN, "Z0") is None
    assert bc._reply_squares(chess.STARTING_FEN, "0000") == (None, None)
    from core.repertoire.read import canonicalise

    with pytest.raises(ValueError):
        canonicalise(chess.STARTING_FEN, [{"expected_move": "--"}])
    fen = fen_after(["e4", "e5"])
    base = {
        "book": "B",
        "chapter": "C",
        "line_name": "L",
        "fen": fen,
        "arriving": {},
        "is_alternative": False,
        "line_ply": 2,
    }
    groups, divergent = nb.prep_groups(
        fen, [{**base, "line_id": 1, "expected_move": "--"}, {**base, "line_id": 2, "expected_move": "Nf3"}]
    )
    assert [(g["prep_status"], g["prep_move"], g["prep_raw_token"]) for g in groups] == [
        ("move", "Nf3", None),
        ("unreadable", None, "--"),
    ]
    assert not divergent


def test_null_move_routes_are_400(client: TestClient) -> None:
    for move in ("--", "Z0", "0000"):
        assert (
            client.get("/repertoire/similar", params={"fen": fen_after(ITALIAN[:6]), "move": move}).status_code == 400
        )


def test_short_fens_and_bad_castling_are_400(client: TestClient) -> None:
    placement_only = fen_after(ITALIAN[:6]).split(" ")[0]
    assert client.get("/repertoire/similar", params={"fen": placement_only}).status_code == 400
    assert client.get("/repertoire/similar", params={"fen": placement_only + " w"}).status_code == 400
    assert (
        client.get("/repertoire/branch-compare", params={"fen": placement_only, "pre_fen": PARENT}).status_code == 400
    )
    # A Chess960 start with its X-FEN castling string: python-chess would strip the rights and carry on.
    assert (
        client.get(
            "/repertoire/similar", params={"fen": "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w GCgc - 0 1"}
        ).status_code
        == 400
    )
    # Rights the placement cannot support are a malformed FEN too.
    assert (
        client.get(
            "/repertoire/similar", params={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQ1BNR w KQkq - 0 1"}
        ).status_code
        == 400
    )


def test_ply_0_is_never_a_node_even_when_it_would_parse() -> None:
    # The start position is the query, White to move, distance 0, signature equal; and the row is built so
    # that the wrap-around parse of moves[-1] against fens[-1] succeeds. Only the range keeps ply 0 out.
    query = chess.STARTING_FEN
    fens = spine(None, ["e4", "e5", "Nf3", "Nc6"])
    row = _row(["e4", "e5", "Nf3", "Nc6"], ["SIG"] * 5)
    row["fen_sequence"] = [fens[0], fens[1], fens[2], fens[3], fens[3]]
    assert nb.parse_arriving(fens[3], "Nc6") is not None
    carriers = nb.expand_and_verify([row], query, "SIG", 8)
    assert 0 not in {c["line_ply"] for c in carriers}


def test_a_null_element_in_moves_is_a_terminal_not_a_token() -> None:
    query = fen_after(["e4", "e5"])
    row = _row(["e4", "e5"], ["SIG", "SIG", "SIG"])
    row["moves"] = ["e4", "e5"]
    row["fen_sequence"] = spine(None, ["e4", "e5"])
    row["moves"] = ["e4", "e5", None][:2]
    carriers = nb.expand_and_verify([row], query, "SIG", 8)
    assert carriers[0]["expected_move"] is None
    row3 = _row(["e4", "e5", "Nf3"], ["SIG", "SIG", "SIG", "SIG"])
    row3["moves"] = ["e4", "e5", None]
    carriers = nb.expand_and_verify([row3], query, "SIG", 8)
    assert [c["expected_move"] for c in carriers if c["line_ply"] == 2] == [None]


def test_line_counts_are_distinct_lines_not_occurrences(rep: psycopg.Connection[DictRow]) -> None:
    # One line reaching the same board twice (a repetition) is one line in every count, and its two
    # occurrences at a board are not a disagreement.
    rep_moves = ITALIAN[:6] + ["Nc3", "Nf6", "Nb1", "Ng8", "Nc3", "Nf6"]
    h.line(rep, 9, 1, "Repeats", rep_moves)  # White's book: the board after 3...Bc5 at plies 6 and 10
    h.line(rep, 10, 3, "Repeats as Black", rep_moves)  # Black's book: the board after 4.Nc3 at plies 7 and 11
    board = similar(rep, fen_after(ITALIAN[:6]))["neighbours"][0]
    nc3 = next(g for g in board["groups"] if g["prep_move"] == "Nc3")
    assert nc3["carried_by_line_count"] == 1 and board["board_prep_divergent"]  # c3 (line 1) vs Nc3 (line 9)
    pre = fen_after(rep_moves[:6])
    child = fen_after(rep_moves[:7])
    cur = compare(rep, child, pre, san="Nc3", book_color="black")["current"]["sources"]["repertoire"]
    assert cur["line_count"] == 1 and cur["reply_san"] == "Nf6" and not cur["board_prep_divergent"]
    assert cur["groups"][0]["carried_by_line_count"] == 1
    rep.rollback()


def test_the_board_representative_is_the_tie_break_minimum(rep: psycopg.Connection[DictRow]) -> None:
    # Two lines reach the same board with different counters; the representative (and so the wire FEN) is
    # the tie-break minimum: book, chapter, line name.
    fens = spine(None, ITALIAN[:6])
    other = [*fens[:6], fens[6].replace(" 4 4", " 0 9")]
    h.line(rep, 9, 1, "A first", ITALIAN[:6], fens=other)
    board = similar(rep, fen_after(ITALIAN[:6]))["neighbours"][0]
    assert board["fen"].endswith(" 0 9") and board["groups"][-1]["line_name"] == "A first"
    # Among boards at one distance the order is the representative's tie-break too.
    h.line(rep, 10, 2, "Scotch-ish", ITALIAN[:5] + ["Nf6", "d3"])  # book "Scotch" sorts after "Italian"
    r = similar(rep, fen_after(ITALIAN[:6]))
    at4 = [n for n in r["neighbours"] if n["distance"] == 4]
    reps = [n["groups"][0]["book_title"] for n in at4]
    assert reps == sorted(reps)
    rep.rollback()


def test_leg_b_verifies_the_child_too(rep: psycopg.Connection[DictRow]) -> None:
    h6 = ITALIAN[:5] + ["h6"]
    fens = h.game(rep, 1, h6 + ["Nxe5"])
    # A blunder row whose own FEN is not the game's position at its ply (a corrupt row): the parent half of the
    # verify alone would admit it.
    blunder(rep, 1, 6, fens[5], "Nxe5", "d3", 200)
    assert [b["opponent_move"]["san"] for b in compare(rep)["branches"]] == ["Nf6"]
    rep.execute("DELETE FROM blunders")
    blunder(rep, 1, 6, fens[6], "Nxe5", "d3", 200)
    assert [b["opponent_move"]["san"] for b in compare(rep)["branches"]] == ["Nf6", "h6"]
    rep.rollback()


def test_leg_b_null_loss_sorts_last_and_games_are_distinct(rep: psycopg.Connection[DictRow]) -> None:
    # A game can reach the same (parent, child) pair twice by repetition; two rows, one game. The parent
    # here (after 3...h6 4.Nc3) is outside the repertoire so the blunder source survives the merge.
    walk = ITALIAN[:5] + ["h6", "Nc3", "Nf6", "Nb1", "Ng8", "Nc3", "Nf6", "Nxe5"]
    fens = h.game(rep, 1, walk)
    blunder(rep, 1, 8, fens[8], "Nb1", "d3", 30)
    blunder(rep, 1, 12, fens[12], "Nxe5", "d3", 100)
    fens2 = h.game(rep, 2, walk[:8] + ["Nxe5"])
    blunder(rep, 2, 8, fens2[8], "Nxe5", None, None)
    pre, child = fens[7], fens[8]
    r = compare(rep, child, pre, san="Nf6", book_color="white")
    blu = r["current"]["sources"]["blunders"]
    assert r["current"]["sources"]["repertoire"] is None and blu is not None
    assert blu["games"] == 2 and blu["worst"]["centipawn_loss"] == 100 and blu["worst"]["move_played_san"] == "Nxe5"
    rep.rollback()


def test_branch_line_ply_is_the_childs_and_current_is_the_query_fen(rep: psycopg.Connection[DictRow]) -> None:
    r = compare(rep)
    assert r["current"]["sources"]["repertoire"]["groups"][0]["line_ply"] == 6
    # A query FEN with other counters than the stored occurrence: the pinned board is the query as given.
    odd = CURRENT.replace(" 4 4", " 0 9")
    r = compare(rep, odd)
    assert r["current"]["child_fen"] == odd and r["current"]["sources"]["repertoire"]["reply_san"] == "c3"


def test_end_of_line_is_only_when_no_move_group(rep: psycopg.Connection[DictRow]) -> None:
    h.line(rep, 5, 1, "Short", ITALIAN[:6])
    cur = compare(rep)["current"]["sources"]["repertoire"]
    assert (
        not cur["end_of_line"]
        and cur["reply_san"] == "c3"
        and [g["prep_status"] for g in cur["groups"]] == ["move", "end_of_line"]
    )
    rep.rollback()


def test_a_patterns_most_common_move_can_be_illegal_on_its_displayed_board(
    client: TestClient, rep: psycopg.Connection[DictRow]
) -> None:
    """A deviation pattern spans boards: its board is the latest game's and its most common played move
    an aggregate over the pattern, so the two need not fit. The route rejects the pair; the card sends
    the move only when it is legal on the board (Deviations.test.tsx covers the card)."""
    from core import deviations, settings

    scandi = ["e4", "d5", "Nf3", "Nc6"]
    for gid, moves, played, days in (
        (11, scandi + ["exd5"], "exd5", 3),
        (12, scandi + ["exd5"], "exd5", 2),
        (13, ITALIAN[:4] + ["d4"], "d4", 1),
    ):
        fens = h.game(rep, gid, moves, days_ago=days)
        h.result(
            rep, gid, book_id=1, chapter_id=1, ply=4, by="me", expected="Bc4", played=played, fen=fens[4], line_ids=[1]
        )
    rep.commit()
    from dataclasses import replace

    f = replace(deviations.default_filters(settings.Settings()), time_class="all")
    card = deviations.positions(rep, f, "all")["positions"][0]
    assert (
        card["count"] == 3 and card["most_common_played"] == "exd5" and card["deviation_fen"] == fen_after(ITALIAN[:4])
    )
    assert (
        client.get(
            "/repertoire/similar", params={"fen": card["deviation_fen"], "move": card["most_common_played"]}
        ).status_code
        == 400
    )
    assert client.get("/repertoire/similar", params={"fen": card["deviation_fen"]}).status_code == 200
