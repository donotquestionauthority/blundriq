"""The review window and the two context reads (core/review/window.py)."""

from __future__ import annotations

import psycopg
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.review import window
from tests import repertoire_helpers as h
from tests.review.helpers import analysed_game, qh_ctx


def ids(conn: psycopg.Connection[DictRow], limit: int = 100) -> list[int]:
    return [g["chess_game_id"] for g in window.window_games(conn, limit)]


def test_the_window_is_the_analysed_intact_part_of_the_analysis_window(conn: psycopg.Connection[DictRow]) -> None:
    analysed_game(conn, 1, qh_ctx(), days_ago=1)
    analysed_game(conn, 2, qh_ctx(), days_ago=2, depth=None)  # not analysed
    analysed_game(conn, 3, qh_ctx(), days_ago=3)
    conn.execute("UPDATE chess_games SET ply_analysis = NULL WHERE id = 3")  # housekeeping aged it out
    analysed_game(conn, 4, qh_ctx(), days_ago=4)
    conn.execute("UPDATE chess_games SET fen_sequence = NULL WHERE id = 4")
    analysed_game(conn, 5, qh_ctx(), days_ago=5, variant="chess960")  # never, whatever its rows say
    analysed_game(conn, 6, qh_ctx(), days_ago=6)
    assert ids(conn) == [1, 6]  # newest first
    # the window counts analysable games, analysed or not: game 2 takes a slot, game 5 never does
    assert ids(conn, 2) == [1]
    assert ids(conn, 3) == [1]
    assert ids(conn, 5) == [1, 6]
    g = window.window_games(conn, 100)[0]
    assert g["moves"] == qh_ctx()["moves"] and len(g["ply_analysis"]) == len(g["moves"]) + 1
    assert g["analysis_depth"] == 18 and g["player_color"] == "white" and g["opening_eco"] == "C20"
    assert g["repertoire"] is None and g["motif_missed"] == []


def test_the_repertoire_context_is_the_deepest_match_lowest_line_id(conn: psycopg.Connection[DictRow]) -> None:
    analysed_game(conn, 1, qh_ctx())
    analysed_game(conn, 2, qh_ctx())
    h.book(conn, 1, "White", "white")
    h.chapter(conn, 1, 1, "Open")
    fens = h.line(conn, 10, 1, "a", ["e4", "e5", "Nf3"])
    h.line(conn, 11, 1, "b", ["e4", "e5", "Nf3", "Nc6"])
    h.line(conn, 12, 1, "c", ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    rid = h.result(
        conn, 1, book_id=1, chapter_id=1, ply=2, by="me", expected="Nf3", played="Qh5", fen=fens[2], line_ids=[]
    )
    for lid, ply in ((10, 2), (12, 4), (11, 4)):
        conn.execute(
            "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply) VALUES (%s, %s, %s)",
            (rid, lid, ply),
        )
    ctx = window.repertoire_ctx(conn, [1, 2])
    assert ctx[2] is None
    assert ctx[1] == {
        "book_id": 1,
        "chapter_id": 1,
        "line_id": 11,  # matched_ply 4 twice; the lower id wins
        "deviated_at_ply": 2,
        "deviation_by": "me",
        "expected_move": "Nf3",
        "matched_ply": 4,
        "line_len": 4,
    }
    assert window.repertoire_ctx(conn, []) == {}


def test_motif_missed_is_found_false_motif_or_mate_only(conn: psycopg.Connection[DictRow]) -> None:
    analysed_game(conn, 1, qh_ctx())
    rows = [
        (1, 4, "motif", "fork", False, None),
        (1, 6, "mate", "mate", False, 2),
        (1, 2, "motif", "pin", True, None),  # found
        (1, 2, "motif", "skewer", None, None),  # never judged
        (1, 4, "positional", "outpost", False, None),
        (1, 4, "endgame", "rookEndgame", False, None),
    ]
    for gid, ply, metric, theme, found, mim in rows:
        conn.execute(
            "INSERT INTO player_motif_events (player_id, chess_game_id, ply, metric_type, theme, found, mate_in_moves,"
            " player_color) VALUES (%s, %s, %s, %s, %s, %s, %s, 'white')",
            (PLAYER_ID, gid, ply, metric, theme, found, mim),
        )
    got = window.motif_missed(conn, [1, 2])
    assert got[2] == []
    assert got[1] == [
        {"ply": 4, "metric_type": "motif", "theme": "fork", "mate_in_moves": None},
        {"ply": 6, "metric_type": "mate", "theme": "mate", "mate_in_moves": 2},
    ]
    games = window.contexts(conn, 100)
    assert games[0]["motif_missed"] == got[1]
