"""The Home page (core/home.py) and the NEW predicate it shares with Blunders."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
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


def _attempt(conn: psycopg.Connection[DictRow], puzzle_id: int, *, solved: bool = True, days_ago: float = 0) -> None:
    conn.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_at)"
        " VALUES (%s, %s, %s, now() - make_interval(secs => %s))",
        (puzzle_id, PLAYER_ID, solved, days_ago * 86400),
    )


# --- new: a board the list has never shown ------------------------------------


def _list(conn: psycopg.Connection[DictRow], **kw: Any) -> dict[str, Any]:
    f = BlunderFilters(**{"classifications": ALL, "time_class": "all", "mark_new": True, **kw})
    return blunders.positions(conn, f, "rapid_plus")


def _key(fen: str) -> str:
    """A board key, as the seen table and `to_acknowledge` carry it."""
    return " ".join(fen.split()[:4]) + " 0 1"


def _new(conn: psycopg.Connection[DictRow], **kw: Any) -> int:
    f = BlunderFilters(**{"classifications": ALL, "time_class": "all", "mark_new": True, **kw})
    return blunders.new_count(conn, f, "rapid_plus")


def test_the_marker_needs_the_players_row(clean: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(RuntimeError):
        blunders.seen_at(clean)
    with pytest.raises(RuntimeError):
        blunders.mark_seen(clean, [])


def test_a_board_is_new_until_the_page_has_shown_it(db: psycopg.Connection[DictRow]) -> None:
    for gid, fen in ((1, A), (2, A), (3, A), (4, B), (5, B)):
        _game(db, gid, days_ago=gid)
        _blunder(db, gid, fen)
    listed = _list(db)
    assert [(p["fen"], p["is_new"]) for p in listed["positions"]] == [(A, True), (B, True)]
    assert sorted(listed["to_acknowledge"]) == sorted([_key(A), _key(B)]) and _new(db) == 2
    blunders.mark_seen(db, [A])  # the page showed A (and, say, B was on a page never opened)
    listed = _list(db)
    assert [(p["fen"], p["is_new"]) for p in listed["positions"]] == [(A, False), (B, True)]  # score order holds
    assert listed["to_acknowledge"] == [_key(B)] and listed["new_count"] == 1 and _new(db) == 1
    blunders.mark_seen(db, [_key(B)])
    assert _new(db) == 0 and all(not p["is_new"] for p in _list(db)["positions"])
    assert blunders.seen_at(db) is not None


def test_only_the_boards_the_page_delivered_are_acknowledged(db: psycopg.Connection[DictRow]) -> None:
    """A board that crossed the threshold after the list was read is not consumed by
    acknowledging that list, however the two requests interleave."""
    for gid in (1, 2):
        _game(db, gid, days_ago=gid)
        _blunder(db, gid, A)
    _game(db, 3, days_ago=3)
    _blunder(db, 3, B)  # B seen once: not yet recurring
    delivered = _list(db)["to_acknowledge"]
    assert delivered == [A]
    _game(db, 4, days_ago=0)
    _blunder(db, 4, B)  # B crosses after the read, before the acknowledgement
    blunders.mark_seen(db, delivered)
    assert _new(db) == 1 and [p["fen"] for p in _list(db)["positions"] if p["is_new"]] == [B]


def test_the_first_look_marks_nothing_and_acknowledges_everything_listed(db: psycopg.Connection[DictRow]) -> None:
    """However the history arrived (upgrade, archive copy), the first look declares it
    known; nothing can stay new for lack of a timestamp."""
    for gid, fen in ((1, A), (2, A), (3, B), (4, B)):
        _game(db, gid, days_ago=gid)
        _blunder(db, gid, fen)
    before = _list(db, mark_new=False)  # what the route sends before the first look
    assert all(not p["is_new"] for p in before["positions"]) and _new(db, mark_new=False) == 0
    assert sorted(before["to_acknowledge"]) == sorted([_key(A), _key(B)])  # every active board, any page
    blunders.mark_seen(db, before["to_acknowledge"])
    assert _new(db) == 0
    for gid in (5, 6):  # found after the first look: news
        _game(db, gid, days_ago=0)
        _blunder(db, gid, "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1")
    assert _new(db) == 1


def test_pages_are_stable_under_acknowledgement(db: psycopg.Connection[DictRow]) -> None:
    """Acknowledging page 1 must not move an unseen board ahead of page 2's offset: forward
    paging with an acknowledgement after each page reaches every board exactly once."""
    import chess

    fens: list[str] = []
    root = chess.Board()
    for first in root.legal_moves:
        b1 = root.copy()
        b1.push(first)
        for reply in b1.legal_moves:
            b2 = b1.copy()
            b2.push(reply)
            fens.append(b2.fen())
            if len(fens) == blunders.PAGE_SIZE + 3:
                break
        if len(fens) == blunders.PAGE_SIZE + 3:
            break
    gid = 0
    for fen in fens:  # every board recurs in two games
        for _ in range(2):
            gid += 1
            _game(db, gid, days_ago=1)
            _blunder(db, gid, fen)
    blunders.mark_seen(db, [])  # a first look at an empty list: from now on boards are marked
    assert _new(db) == len(fens)
    seen: list[str] = []
    page = 0
    while True:
        r = blunders.positions(db, BlunderFilters(ALL, time_class="all", mark_new=True), "rapid_plus", page)
        assert all(p["is_new"] for p in r["positions"])
        seen += [p["fen"] for p in r["positions"]]
        blunders.mark_seen(db, r["to_acknowledge"])
        if page >= r["total_pages"] - 1:
            break
        page += 1
    assert len(seen) == len(set(seen)) == len(fens)
    assert _new(db) == 0


def test_a_dismissed_board_is_neither_new_nor_acknowledged(db: psycopg.Connection[DictRow]) -> None:
    for gid in (1, 2):
        _game(db, gid, days_ago=gid)
        _blunder(db, gid, A)
    blunders.dismiss(db, A)
    assert _new(db) == 0 and _list(db)["to_acknowledge"] == []
    dismissed = _list(db, show_dismissed=True)
    assert [p["is_new"] for p in dismissed["positions"]] == [False] and dismissed["to_acknowledge"] == []
    assert _list(db, mark_new=False)["to_acknowledge"] == []  # nor on a first look
    blunders.restore(db, A)
    assert _new(db) == 1


def test_acknowledging_twice_is_a_no_op(db: psycopg.Connection[DictRow]) -> None:
    blunders.mark_seen(db, [A, A])
    blunders.mark_seen(db, [A])
    row = db.execute("SELECT count(*) AS n FROM seen_blunder_boards").fetchone()
    assert row is not None and row["n"] == 1


def test_default_filters_are_the_pages_opening_filters() -> None:
    f = blunders.default_filters(Settings())
    assert f == BlunderFilters(("blunder", "miss", "mistake"), 2, 20, 0, "focus", False, False)
    assert blunders.default_filters(Settings(), mark_new=True).mark_new is True
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


def test_the_page_reports_new_blunders_until_the_list_has_shown_them(db: psycopg.Connection[DictRow]) -> None:
    for gid in (1, 2, 3):
        _game(db, gid, days_ago=gid)
        _blunder(db, gid, A, cls="blunder")
    config = Settings(blunders_default_window_days=30)
    page = home.page(db, config)
    assert page["since"] is None and page["new_blunders"] == 0  # never looked: nothing is news yet
    blunders.mark_seen(db, [])  # the first look, of an empty list under some other filter
    page = home.page(db, config)
    assert page["since"] is not None and page["new_blunders"] == 1
    assert home.page(db, config)["new_blunders"] == 1  # still waiting: Home does not acknowledge
    blunders.mark_seen(db, [A])  # the page showed it
    assert home.page(db, config)["new_blunders"] == 0
    assert datetime.fromisoformat(home.page(db, config)["since"]) == blunders.seen_at(db)


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
    db.commit()
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_home_requires_login(app_env: None) -> None:
    from api.main import create_app

    assert TestClient(create_app()).get("/home").status_code == 401


def test_the_route_marks_new_only_after_a_first_look_and_acknowledges_what_it_sent(
    client: TestClient, db: psycopg.Connection[DictRow]
) -> None:
    assert client.get("/home").json()["new_blunders"] == 0
    r = client.get("/blunders", params={"time_class": "all", "classifications": ["blunder"]})
    assert [p["is_new"] for p in r.json()["positions"]] == [False]  # before the first look
    assert r.json()["to_acknowledge"] == [A]  # ... which acknowledges everything listed
    assert client.post("/blunders/seen", json={"boards": r.json()["to_acknowledge"]}).status_code == 200
    assert client.get("/home").json()["new_blunders"] == 0
    _game(db, 3, days_ago=0)
    _blunder(db, 3, B)
    _game(db, 4, days_ago=0)
    _blunder(db, 4, B)
    db.commit()
    home_ = client.get("/home").json()
    assert home_["new_blunders"] == 1 and home_["since"] is not None
    r = client.get("/blunders", params={"time_class": "all", "classifications": ["blunder"]})
    assert [(p["fen"], p["is_new"]) for p in r.json()["positions"]] == [(B, True), (A, False)]
    assert r.json()["to_acknowledge"] == [_key(B)]
    assert client.post("/blunders/seen", json={"boards": ["x" * 101]}).status_code == 422
    assert client.post("/blunders/seen", json={"boards": r.json()["to_acknowledge"]}).status_code == 200
    assert client.get("/home").json()["new_blunders"] == 0
    assert json.dumps(home_)  # serialisable
