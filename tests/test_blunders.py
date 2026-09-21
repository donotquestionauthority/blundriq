"""The Blunders page's ranked list and dismissal (core/blunders.py)."""

from __future__ import annotations

import json
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core import blunders
from core.blunders import BlunderFilters
from core.constants import PLAYER_ID

A = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1"
A_LATER = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 9"  # same board, other clocks
B = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
ALL = ("miss", "blunder", "mistake", "inaccuracy")


def _game(
    conn: psycopg.Connection[DictRow],
    gid: int,
    *,
    days_ago: float = 1,
    time_class: str = "rapid",
    variant: str = "standard",
    color: str = "white",
) -> None:
    conn.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, url, played_at, variant, time_class, moves,"
        " fen_sequence, opening_name, starting_fen)"
        " VALUES (%s, 'lichess', %s, %s, now() - make_interval(secs => %s), %s, %s, %s::jsonb, %s::jsonb, 'Italian Game', %s)",
        (
            gid,
            f"g{gid}",
            f"https://example.test/{gid}",
            days_ago * 86400,
            variant,
            time_class,
            json.dumps(["e4", "e5"]),
            json.dumps([A]),
            A if variant == "chess960" else None,
        ),
    )
    conn.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, result, player_rating)"
        " VALUES (%s, %s, %s, 'lichess', 'loss', 1500)",
        (PLAYER_ID, gid, color),
    )


def _blunder(
    conn: psycopg.Connection[DictRow],
    gid: int,
    fen: str = A,
    *,
    cls: str = "blunder",
    cp: int = 300,
    ply: int = 4,
    played: str = "d3",
) -> None:
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, move_played, best_move, best_line,"
        " post_blunder_line, centipawn_loss, classification, phase)"
        " VALUES (%s, %s, %s, %s, %s, 'Nxe5', 'Nxe5 Nxe5 d4', 'Nf6 O-O', %s, %s, 'opening')",
        (PLAYER_ID, gid, ply, fen, played, cp, cls),
    )


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    clean.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'me')", (PLAYER_ID,))
    return clean


def _list(conn: psycopg.Connection[DictRow], **kw: Any) -> dict[str, Any]:
    f = BlunderFilters(**{"classifications": ALL, "time_class": "all", **kw})
    return blunders.positions(conn, f, "rapid_plus")


def test_a_board_is_counted_in_distinct_games_and_scored_by_each_games_worst_instance(
    db: psycopg.Connection[DictRow],
) -> None:
    _game(db, 1)
    _game(db, 2, days_ago=2)
    _blunder(db, 1, A, cls="mistake", cp=120, ply=4)
    _blunder(db, 1, A_LATER, cls="miss", cp=500, ply=16, played="h3")  # same board, same game: the worse one counts
    _blunder(db, 2, A, cls="blunder", cp=250)
    _game(db, 3)
    _blunder(db, 3, B)  # seen once: below the threshold
    out = _list(db)
    assert out["active_count"] == 1 and out["dismissed_count"] == 0 and out["total_pages"] == 1
    (card,) = out["positions"]
    assert card["count"] == 2 and card["score"] == 8 + 4
    assert card["classifications"] == {"miss": 1, "blunder": 1}
    assert sum(card["classifications"].values()) == card["count"]
    # The example is the most severe occurrence, with that occurrence's own FEN and move.
    assert card["fen"] == A_LATER and card["move_played"] == "h3" and card["ply"] == 16 and card["cp_loss"] == 500
    assert card["chess_game_id"] == 1 and card["moves"] == ["e4", "e5"]
    assert [g["chess_game_id"] for g in card["games"]] == [1, 2]  # newest first, one row per game
    assert card["games"][0]["classification"] == "miss" and card["games"][0]["result"] == "loss"
    assert card["context"] == "Italian Game" and card["book"] is None


def test_filters_narrow_what_is_counted_not_just_what_is_listed(db: psycopg.Connection[DictRow]) -> None:
    for gid, cls in ((1, "blunder"), (2, "blunder"), (3, "inaccuracy")):
        _game(db, gid)
        _blunder(db, gid, cls=cls)
    assert _list(db)["positions"][0]["count"] == 3
    only = _list(db, classifications=("blunder",))["positions"][0]
    assert only["count"] == 2 and len(only["games"]) == 2 and only["classifications"] == {"blunder": 2}
    assert _list(db, min_occurrences=4)["positions"] == []


def test_the_window_is_days_or_the_most_recent_standard_games(db: psycopg.Connection[DictRow]) -> None:
    _game(db, 1, days_ago=1)
    _game(db, 2, days_ago=5)
    _game(db, 3, days_ago=40)
    _game(db, 9, days_ago=0.5, variant="chess960")  # newest of all, and never takes a slot
    for gid in (1, 2, 3):
        _blunder(db, gid)
    assert _list(db)["positions"][0]["count"] == 3
    assert _list(db, since_days=10)["positions"][0]["count"] == 2
    assert _list(db, last_n_games=2)["positions"][0]["count"] == 2
    assert _list(db, last_n_games=2, since_days=1)["positions"][0]["count"] == 2  # the game count wins


def test_a_chess960_blunder_row_is_never_listed(db: psycopg.Connection[DictRow]) -> None:
    for gid in (1, 2):
        _game(db, gid, variant="chess960")
        _blunder(db, gid)
    assert _list(db)["positions"] == []


def test_time_class_filters_inside_the_window(db: psycopg.Connection[DictRow]) -> None:
    _game(db, 1, days_ago=1, time_class="blitz")
    _game(db, 2, days_ago=2, time_class="blitz")
    _game(db, 3, days_ago=3, time_class="rapid")
    _game(db, 4, days_ago=4, time_class="correspondence")
    for gid in (1, 2, 3, 4):
        _blunder(db, gid)
    count = lambda **kw: [p["count"] for p in _list(db, min_occurrences=1, **kw)["positions"]]  # noqa: E731
    assert count(time_class="all") == [4]
    assert count(time_class="focus") == [2]  # rapid_plus: rapid and slower
    assert count(time_class="blitz") == [2]
    assert count(time_class="classical") == [1]
    assert count(time_class="bullet") == []
    # Two most recent games are both blitz: the window is taken first, then filtered.
    assert count(time_class="focus", last_n_games=2) == []
    assert [
        p["count"] for p in blunders.positions(db, BlunderFilters(ALL, 1, time_class="focus"), "all")["positions"]
    ] == [4]
    with pytest.raises(ValueError):
        _list(db, time_class="x' OR TRUE --")


def test_dismissal_is_by_board_and_both_views_report_both_counts(db: psycopg.Connection[DictRow]) -> None:
    for gid in (1, 2):
        _game(db, gid)
        _blunder(db, gid, A)
        _blunder(db, gid, B, ply=2)
    blunders.dismiss(db, A_LATER)  # any occurrence's FEN names the board
    blunders.dismiss(db, A)  # twice is a no-op
    active = _list(db)
    assert [p["fen"] for p in active["positions"]] == [B] and active["positions"][0]["dismissed"] is False
    assert (active["active_count"], active["dismissed_count"]) == (1, 1)
    hidden = _list(db, show_dismissed=True)
    assert [p["fen"] for p in hidden["positions"]] == [A] and hidden["positions"][0]["dismissed"] is True
    assert (hidden["active_count"], hidden["dismissed_count"]) == (1, 1)
    blunders.restore(db, A_LATER)
    assert _list(db)["active_count"] == 2 and _list(db, show_dismissed=True)["positions"] == []


def test_a_page_past_the_end_is_empty_and_still_carries_the_counts(db: psycopg.Connection[DictRow]) -> None:
    for gid in (1, 2):
        _game(db, gid)
        _blunder(db, gid)
    out = blunders.positions(db, BlunderFilters(ALL, time_class="all"), "rapid_plus", page=7)
    assert out["positions"] == [] and out["active_count"] == 1 and out["page"] == 7
    assert _list(db, classifications=())["active_count"] == 0


def test_order_is_score_then_recency(db: psycopg.Connection[DictRow]) -> None:
    _game(db, 1, days_ago=9)
    _game(db, 2, days_ago=8)
    _game(db, 3, days_ago=1)
    _blunder(db, 1, A, cls="mistake")
    _blunder(db, 2, A, cls="mistake")
    _blunder(db, 2, B, cls="mistake", ply=2)
    _blunder(db, 3, B, cls="mistake", ply=2)
    C = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    _blunder(db, 1, C, cls="miss", ply=1)
    _blunder(db, 2, C, cls="miss", ply=1)
    assert [p["fen"] for p in _list(db)["positions"]] == [C, B, A]


def test_repertoire_context_is_named_only_while_the_game_was_still_in_the_line(db: psycopg.Connection[DictRow]) -> None:
    db.execute(
        "INSERT INTO books (id, player_id, title, color) VALUES (1, %s, 'Course: The Italian', 'white')", (PLAYER_ID,)
    )
    db.execute("INSERT INTO chapters (id, book_id, title) VALUES (1, 1, '3) Main line')")
    db.execute(
        "INSERT INTO repertoire_lines (id, chapter_id, line_name, moves, fen_sequence) VALUES (1, 1, 'Line #1', %s::jsonb, %s::jsonb)",
        (json.dumps(["e4"]), json.dumps([A])),
    )
    for gid, deviated_at, by in ((1, 6, "me"), (2, 2, "opponent")):
        _game(db, gid, days_ago=gid)
        _blunder(db, gid, ply=4)
        rid = db.execute(
            "INSERT INTO game_repertoire_results (player_id, chess_game_id, book_id, chapter_id, deviated_at_ply, deviation_by)"
            " VALUES (%s, %s, 1, 1, %s, %s) RETURNING id",
            (PLAYER_ID, gid, deviated_at, by),
        ).fetchone()
        assert rid is not None
        db.execute(
            "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply) VALUES (%s, 1, 4)",
            (rid["id"],),
        )
    (card,) = _list(db)["positions"]
    assert (
        card["book"] == "Course: The Italian"
        and card["chapter"] == "3) Main line"
        and card["line_names"] == ["Line #1"]
    )
    assert card["context"] == "The Italian (3) Main line)"
    in_line, left_earlier = card["games"]
    assert in_line["in_repertoire"] and in_line["deviated_by_me"]
    assert not left_earlier["in_repertoire"] and not left_earlier["deviated_by_me"]
