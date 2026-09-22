"""The Home page (core/home.py) and the NEW predicate it shares with Blunders."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core import blunders, home, runs
from core.blunders import BlunderFilters
from core.constants import PLAYER_ID
from core.settings import Settings
from tests.test_blunders import A, B, _blunder, _game
from tests.test_practice import _puzzle

ALL = ("miss", "blunder", "mistake", "inaccuracy")


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    clean.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'me')", (PLAYER_ID,))
    return clean


def _analyzed(conn: psycopg.Connection[DictRow], gid: int, days_ago: float) -> None:
    """When the game's analysis landed — what dates a board's newness (import time never does)."""
    conn.execute(
        "UPDATE chess_games SET analysis_status = 'completed', analyzed_at = now() - make_interval(secs => %s)"
        " WHERE id = %s",
        (days_ago * 86400, gid),
    )


def _attempt(conn: psycopg.Connection[DictRow], puzzle_id: int, *, solved: bool = True, days_ago: float = 0) -> None:
    conn.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_at)"
        " VALUES (%s, %s, %s, now() - make_interval(secs => %s))",
        (puzzle_id, PLAYER_ID, solved, days_ago * 86400),
    )


def _seen(conn: psycopg.Connection[DictRow], at: str | None) -> None:
    conn.execute("UPDATE players SET blunders_seen_at = %s::timestamptz WHERE id = %s", (at, PLAYER_ID))


# --- the marker: moved by the page, read by Home -------------------------------


def test_the_marker_is_null_until_the_list_is_first_looked_at(db: psycopg.Connection[DictRow]) -> None:
    assert blunders.seen_at(db) is None
    at = blunders.mark_seen(db)
    assert blunders.seen_at(db) == at


def test_home_never_moves_the_marker(db: psycopg.Connection[DictRow]) -> None:
    _seen(db, "2026-09-01T12:00:00Z")
    for _ in range(2):
        assert home.page(db, Settings())["since"] == "2026-09-01T12:00:00+00:00"
    assert blunders.seen_at(db) == datetime(2026, 9, 1, 12, tzinfo=UTC)


def test_the_marker_needs_the_players_row(clean: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(RuntimeError):
        blunders.seen_at(clean)
    with pytest.raises(RuntimeError):
        blunders.mark_seen(clean)


# --- new blunders: one predicate for the chip and the count ---------------------


def _list(conn: psycopg.Connection[DictRow], **kw: Any) -> list[dict[str, Any]]:
    f = BlunderFilters(**{"classifications": ALL, "time_class": "all", **kw})
    return blunders.positions(conn, f, "rapid_plus")["positions"]


def test_a_board_is_new_when_it_crossed_the_threshold_after_the_boundary(db: psycopg.Connection[DictRow]) -> None:
    since = datetime.now(UTC) - timedelta(days=3)
    # A: two games analysed before the boundary, one after — already met min 2: not new.
    # B: one before, one after — crossed after the boundary: new, and listed first.
    for gid, fen, days in ((1, A, 10), (2, A, 8), (3, A, 1), (4, B, 8), (5, B, 1)):
        _game(db, gid, days_ago=days)
        _blunder(db, gid, fen)
        _analyzed(db, gid, days)
    listed = _list(db, new_since=since)
    assert [(p["fen"], p["is_new"], p["count"]) for p in listed] == [(B, True, 2), (A, False, 3)]
    assert [p["fen"] for p in _list(db)] == [A, B]  # no boundary: nothing is new, score order
    assert all(p["is_new"] is False for p in _list(db))
    assert {p["fen"]: p["is_new"] for p in _list(db, new_since=since, min_occurrences=3)} == {A: True}

    f = BlunderFilters(classifications=ALL, time_class="all", new_since=since)
    assert blunders.new_count(db, f, "rapid_plus") == 1
    assert blunders.positions(db, f, "rapid_plus")["new_count"] == 1
    blunders.dismiss(db, B)
    assert blunders.new_count(db, f, "rapid_plus") == 0  # dismissed boards are not nudged about
    dismissed = blunders.positions(db, replace(f, show_dismissed=True), "rapid_plus")
    assert dismissed["new_count"] == 0 and [p["is_new"] for p in dismissed["positions"]] == [False]
    assert blunders.new_count(db, BlunderFilters(classifications=ALL, time_class="all"), "rapid_plus") == 0


def test_newness_is_dated_by_analysis_not_import(db: psycopg.Connection[DictRow]) -> None:
    """A game imported before the boundary but analysed after it is news: its blunders did
    not exist when Rob last looked."""
    since = datetime.now(UTC) - timedelta(days=3)
    for gid, days in ((1, 10), (2, 5)):
        _game(db, gid, days_ago=days)  # both imported (created_at) before the boundary
        _blunder(db, gid, A)
    _analyzed(db, 1, 9)
    _analyzed(db, 2, 1)  # analysed after the boundary
    assert [p["is_new"] for p in _list(db, new_since=since)] == [True]
    db.execute("UPDATE chess_games SET analyzed_at = NULL WHERE id = 2")  # not analysed at all: not seen either
    assert [p["is_new"] for p in _list(db, new_since=since)] == [True]


def test_default_filters_are_the_pages_opening_filters() -> None:
    f = blunders.default_filters(Settings())
    assert f == BlunderFilters(("blunder", "miss", "mistake"), 2, 20, 0, "focus", False, None)
    g = blunders.default_filters(Settings(blunders_default_filter_mode="games", blunders_default_last_n_games=100))
    assert g.since_days is None and g.last_n_games == 100
    # The same fallback as ui/src/blunders.ts: unknown classes dropped, an empty list means the three.
    assert blunders.default_filters(Settings(blunders_default_classifications=["miss", "x"])).classifications == (
        "miss",
    )
    assert blunders.default_filters(Settings(blunders_default_classifications=[])).classifications == (
        "miss",
        "blunder",
        "mistake",
    )


# --- streaks ------------------------------------------------------------------


def test_streak_counts_back_from_today_or_from_yesterday() -> None:
    today = date(2026, 9, 22)
    d = lambda n: today - timedelta(days=n)  # noqa: E731
    assert home.streak({d(0): 10, d(1): 12, d(2): 10, d(3): 3}, 10, today) == 3
    assert home.streak({d(1): 12, d(2): 10}, 10, today) == 2  # today unfinished: not broken
    assert home.streak({d(0): 4, d(1): 12}, 10, today) == 1
    assert home.streak({d(2): 12}, 10, today) == 0  # yesterday missed
    assert home.streak({}, 10, today) == 0
    assert home.streak({d(0): 5}, 0, today) == 0  # no target, no streak


def test_the_week_starts_on_monday() -> None:
    days = {date(2026, 9, 20): 1, date(2026, 9, 21): 2, date(2026, 9, 22): 4, date(2026, 9, 23): 8}
    assert home.week_total(days, date(2026, 9, 22)) == 6  # Tuesday: Monday + Tuesday, not Sunday, not tomorrow
    assert home.week_total(days, date(2026, 9, 21)) == 2  # Monday: itself only
    assert home.week_total(days, date(2026, 9, 27)) == 14  # Sunday closes the week


# --- the page -----------------------------------------------------------------


def test_the_page_counts_todays_puzzles_and_games_in_the_configured_timezone(db: psycopg.Connection[DictRow]) -> None:
    config = Settings(timezone="UTC", daily_puzzle_target=2, daily_game_target=1)
    p1, p2 = _puzzle(db), _puzzle(db, fen=B, line=["e4"])
    _attempt(db, p1)
    _attempt(db, p1)  # a retry of the same puzzle counts once
    _attempt(db, p2, solved=False)
    _attempt(db, p2, days_ago=1)
    _attempt(db, p1, days_ago=1)
    _attempt(db, p2, days_ago=1)
    _game(db, 1, days_ago=0)
    _game(db, 2, days_ago=0, variant="chess960")  # played, so it counts
    _game(db, 3, days_ago=1)
    _game(db, 4, days_ago=40)
    page = home.page(db, config)
    assert page["since"] is None and page["timezone"] == "UTC"
    assert page["puzzles"] == {"due": 2, "solved_today": 1, "target": 2, "streak": 1}
    week = 2 + (1 if date.fromisoformat(page["today"]).weekday() >= 1 else 0)
    assert page["games"] == {"today": 2, "week": week, "target": 1, "streak": 2}
    assert page["new_blunders"] == 0


def test_days_are_bucketed_in_the_configured_timezone(db: psycopg.Connection[DictRow]) -> None:
    """09:30 UTC on the 22nd is still the 21st in Honolulu and already the 22nd in Auckland."""
    p = _puzzle(db)
    db.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_at) VALUES (%s, %s, TRUE, '2026-09-22T09:30:00Z')",
        (p, PLAYER_ID),
    )
    _game(db, 1)
    db.execute("UPDATE chess_games SET played_at = '2026-09-22T09:30:00Z' WHERE id = 1")
    for tz, day in (
        ("Pacific/Honolulu", date(2026, 9, 21)),
        ("UTC", date(2026, 9, 22)),
        ("Pacific/Auckland", date(2026, 9, 22)),
    ):
        assert home._per_day(db, home._PUZZLE_DAYS, tz) == {day: 1}, tz
        assert home._per_day(db, home._GAME_DAYS, tz) == {day: 1}, tz


def test_the_page_reports_new_blunders_until_the_list_is_looked_at(db: psycopg.Connection[DictRow]) -> None:
    for gid, days in ((1, 10), (2, 1), (3, 1)):
        _game(db, gid, days_ago=days)
        _blunder(db, gid, A, cls="blunder")
        _analyzed(db, gid, days)
    config = Settings(blunders_default_window_days=30)
    assert home.page(db, config)["new_blunders"] == 0  # never looked: nothing to compare against
    _seen(db, (datetime.now(UTC) - timedelta(days=3)).isoformat())
    page = home.page(db, config)
    assert page["since"] is not None and page["new_blunders"] == 1  # A crossed 2 with games 2 and 3
    assert home.page(db, config)["new_blunders"] == 1  # still waiting: Home does not acknowledge
    blunders.mark_seen(db)  # the page showed the list
    assert home.page(db, config)["new_blunders"] == 0
    _game(db, 4, days_ago=0)
    _blunder(db, 4, B)
    _game(db, 5, days_ago=0)
    _blunder(db, 5, B)
    for gid in (4, 5):
        _analyzed(db, gid, -0.001)  # analysed after the look
    assert home.page(db, config)["new_blunders"] == 1


def test_the_page_shows_the_last_full_run_and_the_hourly_steps_that_failed(db: psycopg.Connection[DictRow]) -> None:
    assert home.page(db, Settings())["pipeline"] == {"last_ok_at": None, "failed": []}
    rid = runs.start(db, "import")
    runs.finish(db, rid, {"games": 1})
    assert home.page(db, Settings())["pipeline"]["last_ok_at"] is None  # the chain did not run through
    rid = runs.start(db, "housekeep")
    runs.finish(db, rid, {})
    through = home.page(db, Settings())["pipeline"]["last_ok_at"]
    assert through is not None
    rid = runs.start(db, "analyze")
    runs.fail(db, rid, "worker exploded")
    rid = runs.start(db, "match")
    runs.fail(db, rid, "old")
    rid = runs.start(db, "match")
    runs.finish(db, rid, {})  # a later success clears the step
    rid = runs.start(db, "import-corpus")
    runs.fail(db, rid, "by hand, on the Mac")  # not an hourly step: never a red line
    p = home.page(db, Settings())["pipeline"]
    assert p["last_ok_at"] == through
    assert [(f["step"], f["error"]) for f in p["failed"]] == [("analyze", "worker exploded")]
    assert all(f["started_at"] for f in p["failed"])


def test_the_cli_runs_exactly_the_hourly_steps() -> None:
    from pipeline.cli import hourly_steps

    assert tuple(hourly_steps()) == runs.HOURLY_STEPS


# --- the routes ---------------------------------------------------------------


@pytest.fixture()
def client(app_env: None, db: psycopg.Connection[DictRow]) -> TestClient:
    for gid, days in ((1, 10), (2, 1)):
        _game(db, gid, days_ago=days)
        _blunder(db, gid, A)
        _analyzed(db, gid, days)
    db.commit()
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_home_requires_login(app_env: None) -> None:
    from api.main import create_app

    assert TestClient(create_app()).get("/home").status_code == 401


def test_the_page_opens_on_the_marker_and_moves_it_once_shown(
    client: TestClient, db: psycopg.Connection[DictRow]
) -> None:
    assert client.get("/blunders/seen").json() == {"seen_at": None}
    first = client.get("/home").json()
    assert first["since"] is None and first["new_blunders"] == 0
    db.execute("UPDATE players SET blunders_seen_at = now() - interval '3 days' WHERE id = %s", (PLAYER_ID,))
    db.commit()
    seen = client.get("/blunders/seen").json()["seen_at"]
    second = client.get("/home").json()
    assert second["since"] == seen and second["new_blunders"] == 1
    r = client.get("/blunders", params={"time_class": "all", "new_since": seen, "classifications": ["blunder"]})
    assert r.status_code == 200 and [p["is_new"] for p in r.json()["positions"]] == [True]
    assert r.json()["new_count"] == 1
    r = client.get("/blunders", params={"time_class": "all", "classifications": ["blunder"]})
    assert [p["is_new"] for p in r.json()["positions"]] == [False] and r.json()["new_count"] == 0
    assert client.get("/blunders", params={"new_since": "yesterday"}).status_code == 422
    assert client.get("/blunders", params={"new_since": "2026-09-20T14:00:00"}).status_code == 422  # naive
    marked = client.post("/blunders/seen").json()["seen_at"]
    assert marked > seen and client.get("/blunders/seen").json()["seen_at"] == marked
    assert client.get("/home").json()["new_blunders"] == 0
    assert json.dumps(second)  # serialisable
