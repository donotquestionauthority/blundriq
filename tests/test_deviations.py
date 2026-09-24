"""The Deviations page's ranked patterns and the seen set (core/deviations.py)."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core import deviations, home, settings
from core.deviations import DeviationFilters
from tests import repertoire_helpers as h

MAIN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3"]


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    h.player(clean)
    h.book(clean, 1, "Italian", "white")
    h.chapter(clean, 1, 1, "Giuoco")
    h.line(clean, 1, 1, "Main", MAIN)
    h.line(clean, 2, 1, "Main too", MAIN + ["d6"])
    return clean


def _dev(conn: psycopg.Connection[DictRow], gid: int, played: str, *, days_ago: float = 1, **kw: Any) -> None:
    moves = MAIN[:4] + [played]
    fens = h.game(conn, gid, moves, days_ago=days_ago, **{k: v for k, v in kw.items() if k != "line_ids"})
    h.result(
        conn,
        gid,
        book_id=1,
        chapter_id=1,
        ply=4,
        by="me",
        expected="Bc4",
        played=played,
        fen=fens[4],
        line_ids=kw.get("line_ids", [1, 2]),
    )


def _list(conn: psycopg.Connection[DictRow], page: int = 0, **kw: Any) -> dict[str, Any]:
    return deviations.positions(conn, DeviationFilters(**{"time_class": "all", **kw}), "rapid_plus", page)


def test_a_pattern_is_counted_in_distinct_games_whatever_lines_tie(db: psycopg.Connection[DictRow]) -> None:
    _dev(db, 1, "d4", days_ago=3)
    _dev(db, 2, "d3", days_ago=2)
    _dev(db, 3, "d3", days_ago=1, result="win")
    out = _list(db)
    assert out["total"] == 1
    card = out["positions"][0]
    assert (card["count"], card["wins"], card["losses"], card["win_pct"]) == (3, 1, 2, 33)
    assert (card["book"], card["chapter"], card["ply"], card["expected_move"]) == ("Italian", "Giuoco", 4, "Bc4")
    assert card["most_common_played"] == "d3"
    assert card["chess_game_id"] == 3 and card["deviation_fen"] == h.spine(None, MAIN)[4]
    assert [g["chess_game_id"] for g in card["games"]] == [3, 2, 1]
    assert card["line_names"] == ["Main", "Main too"]
    assert card["rep_expected_move"] == "Bc4" and [r["line_name"] for r in card["rep_lines"]] == ["Main", "Main too"]


def test_the_threshold_window_and_time_class_apply(db: psycopg.Connection[DictRow]) -> None:
    _dev(db, 1, "d4", days_ago=30, time_class="blitz")
    _dev(db, 2, "d4", days_ago=1)
    assert _list(db, min_occurrences=3)["total"] == 0
    assert _list(db, since_days=7)["total"] == 0
    assert _list(db, last_n_games=1)["total"] == 0
    assert _list(db, time_class="rapid")["total"] == 0
    assert _list(db, time_class="focus")["total"] == 0 and _list(db)["total"] == 1
    assert _list(db, color="black")["total"] == 0 and _list(db, color="white")["total"] == 1
    assert _list(db, min_ply=5)["total"] == 0


def test_a_chess960_game_never_takes_a_window_slot_nor_counts(db: psycopg.Connection[DictRow]) -> None:
    _dev(db, 1, "d4", days_ago=3)
    _dev(db, 2, "d4", days_ago=2)
    _dev(db, 3, "d4", days_ago=1, variant="chess960")
    assert _list(db)["positions"][0]["count"] == 2
    assert _list(db, last_n_games=2)["positions"][0]["count"] == 2


def test_new_is_a_pattern_the_list_has_never_shown(db: psycopg.Connection[DictRow]) -> None:
    _dev(db, 1, "d4", days_ago=2)
    _dev(db, 2, "d4", days_ago=1)
    first = _list(db, mark_new=False)
    assert first["new_count"] == 0 and not first["positions"][0]["is_new"]
    assert first["to_acknowledge"] == [[1, 1, 4, "Bc4"]]
    deviations.mark_seen(db, [(1, 1, 4, "Bc4")])
    assert deviations.seen_at(db) is not None
    seen = _list(db, mark_new=True)
    assert seen["new_count"] == 0 and not seen["positions"][0]["is_new"] and seen["to_acknowledge"] == []
    # A second pattern arrives: new until shown, then acknowledged only once rendered.
    h.line(db, 3, 1, "Other chapter line", ["e4", "e5", "Nf3", "Nf6", "Nxe5"])
    for gid, days in ((3, 0.5), (4, 0.25)):
        fens = h.game(db, gid, ["e4", "e5", "Nf3", "Nf6", "d4"], days_ago=days)
        h.result(
            db, gid, book_id=1, chapter_id=1, ply=4, by="me", expected="Nxe5", played="d4", fen=fens[4], line_ids=[3]
        )
    out = _list(db, mark_new=True)
    assert out["new_count"] == 1 and [c["is_new"] for c in out["positions"]] == [True, False]
    assert out["to_acknowledge"] == [[1, 1, 4, "Nxe5"]]
    config = settings.Settings()
    assert deviations.new_count(db, deviations.default_filters(config, mark_new=True), "all") == 1
    deviations.mark_seen(db, [(1, 1, 4, "Nxe5"), (9, 9, 9, "made up")])  # a key that exists nowhere is ignored
    assert _list(db, mark_new=True)["new_count"] == 0


def test_home_reports_new_deviations_from_the_same_predicate(db: psycopg.Connection[DictRow]) -> None:
    _dev(db, 1, "d4", days_ago=2)
    _dev(db, 2, "d4", days_ago=1)
    config = settings.Settings()
    assert home.page(db, config)["new_deviations"] == 0  # never looked: nothing is news
    deviations.mark_seen(db, [])
    assert home.page(db, config)["new_deviations"] == 1
    assert home.page(db, config)["deviations_since"] is not None


def test_pages_are_stable_and_a_page_past_the_end_keeps_the_counts(db: psycopg.Connection[DictRow]) -> None:
    for i in range(1, 4):
        _dev(db, i, "d4", days_ago=i)
    out = _list(db, page=3)
    assert out["positions"] == [] and out["total"] == 1 and out["total_pages"] == 1


def test_default_filters_follow_the_settings_row() -> None:
    config = settings.Settings(deviations_default_filter_mode="games", deviations_default_last_n_games=250)
    f = deviations.default_filters(config)
    assert (f.last_n_games, f.since_days, f.min_occurrences, f.min_ply, f.time_class) == (250, None, 2, 1, "focus")
    f = deviations.default_filters(settings.Settings())
    assert (f.last_n_games, f.since_days) == (0, 20)
