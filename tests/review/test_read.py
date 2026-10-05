"""The Review page over the scratch database: mistake habits, lost wins, the opening filter on every
section, and Chess960's invisibility."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.review import habits, read
from core.review.filters import ReviewParamError, parse_opening
from core.settings import Settings
from tests.review.position_helpers import ITALIAN, NOW, SCANDI, Games

ROOT = Path(__file__).resolve().parents[2]


def plant(
    conn: psycopg.Connection[DictRow],
    gid: int,
    ply: int,
    route: str,
    *,
    cost: float = 10,
    phase: str | None = "middlegame",
    piece: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO review_events (player_id, chess_game_id, anchor_ply, config_version, base_route, evidence, cost,"
        " phase, piece_label, first_detected_at) VALUES (%s, %s, %s, 2, %s, %s::jsonb, %s, %s, %s, now())",
        (PLAYER_ID, gid, ply, route, json.dumps(evidence or {}), cost, phase, piece),
    )


ANALYSED = [{"ply": i, "eval": 0} for i in range(9)]
MOVES = SCANDI + ["Nf3", "Nf6"]


def analysed(g: Games, **kw: Any) -> int:
    return g.add(MOVES, keep_moves=True, ply_analysis=ANALYSED, **kw)


# --- habits ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "hid", "label"),
    [
        ({"base_route": "lapse_offense", "theme": "fork"}, "missed:fork", "Missed a fork"),
        ({"base_route": "lapse_offense", "theme": "hangingPiece"}, "missed:hangingPiece", "Missed a hanging piece"),
        (
            {"base_route": "lapse_offense", "theme": "discoveredAttack"},
            "missed:discoveredAttack",
            "Missed a discovered attack",
        ),
        ({"base_route": "lapse_offense", "theme": "mate"}, "missed:mate", "Missed mate"),
        ({"base_route": "lapse_offense", "piece_label": "material"}, "missed:material", "Missed winning material"),
        ({"base_route": "lapse_offense"}, "missed:missed_win", "Missed winning material"),
        ({"base_route": "lapse_defense", "phase": "opening"}, "lost:opening", "Lost material in the opening"),
        ({"base_route": "lapse_defense", "phase": None}, "lost:middlegame", "Lost material in the middlegame"),
        ({"base_route": "endgame_technique", "phase": "endgame"}, "lost:endgame", "Lost material in the endgame"),
        ({"base_route": "faded"}, "faded", "Let a winning position fade"),
    ],
)
def test_every_stored_event_has_a_habit(event: dict[str, Any], hid: str, label: str) -> None:
    assert habits.habit_id(event) == hid
    assert habits.habit_label(hid) == label
    habits.check_habit_id(hid)


def test_only_a_theme_practice_serves_gets_a_drill_link() -> None:
    assert habits.practice_theme("missed:fork") == "fork"
    assert habits.practice_theme("missed:hangingPiece") == "hangingPiece"
    assert habits.practice_theme("missed:mate") is None  # the corpus splits mates into mateIn1…
    assert habits.practice_theme("missed:material") is None
    assert habits.practice_theme("lost:opening") is None
    for bad in ("missed:", "lost:queen", "x", "missed:a b", "faded:1"):
        with pytest.raises(ReviewParamError):
            habits.check_habit_id(bad)


def test_rates_points_and_trend(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    old = [analysed(g, days=60 + i) for i in range(30)]
    new = [analysed(g, days=1 + i * 0.5) for i in range(30)]
    for gid in old[:3]:
        plant(clean, gid, 7, "lapse_offense", evidence={"theme": "fork"}, cost=20)
    for gid in new[:9]:
        plant(clean, gid, 7, "lapse_offense", evidence={"theme": "fork"}, cost=20)
    for gid in old[3:5] + new[9:10]:
        plant(clean, gid, 7, "faded", cost=5)  # three events
    for gid in new[10:12]:
        plant(clean, gid, 7, "lapse_defense", phase="opening")  # two: below the floor
    rows, window = habits.habits(clean, Settings(), "all", parse_opening("__all__"))
    assert window == 60
    by = {r["id"]: r for r in rows}
    assert set(by) == {"missed:fork", "faded"}
    fork = by["missed:fork"]
    assert fork["rate_per_100"] == 20.0 and fork["events"] == 12
    assert fork["current_rate_per_100"] > 1.25 * fork["rate_per_100"] and fork["trend"] == "worse"
    assert fork["practice_theme"] == "fork"
    assert rows[0]["id"] == "missed:fork"  # ordered by points a month
    drill = habits.habit_games(clean, Settings(), "all", parse_opening("__all__"), "missed:fork", 1)
    assert drill["total"] == 12
    first = drill["rows"][0]
    assert first["chess_game_id"] == new[0] and first["anchor_move"] == "4…Nf6" and first["cost"] == 20.0


def test_the_trend_thresholds_are_inclusive() -> None:
    assert habits.habit_trend(10.0, 12.5, 30) == "worse"
    assert habits.habit_trend(10.0, 12.4, 30) == "steady"
    assert habits.habit_trend(10.0, 7.5, 30) == "improving"
    assert habits.habit_trend(10.0, 7.6, 30) == "steady"
    assert habits.habit_trend(10.0, 20.0, 29.9) is None


def test_points_a_month_the_time_class_and_the_drill_order(clean: psycopg.Connection[DictRow]) -> None:
    import math

    g = Games(clean)
    ages = [0, 10, 40]
    ids = [analysed(g, days=1 + a) for a in ages]
    for gid in ids:
        plant(clean, gid, 7, "faded", cost=30)
    blitz = analysed(g, days=2, time_class="blitz")
    plant(clean, blitz, 7, "faded", cost=30)
    clean.execute("UPDATE player_games SET reviewed_at = now() WHERE chess_game_id = %s", (ids[0],))
    rows, window = habits.habits(clean, Settings(), "focus", parse_opening("__all__"))
    assert window == 3  # the blitz game is outside the focus
    half_life = Settings().review_recency_half_life_days
    expected = sum(0.5 ** (a / half_life) * 0.3 for a in ages) * 30 * math.log(2) / half_life
    assert rows[0]["points_per_month"] == round(expected, 2)
    assert habits.habits(clean, Settings(), "all", parse_opening("__all__"))[1] == 4
    drill = habits.habit_games(clean, Settings(), "focus", parse_opening("__all__"), "faded", 1)
    assert [r["chess_game_id"] for r in drill["rows"]] == [ids[1], ids[2], ids[0]]  # the reviewed one last


def test_no_trend_is_claimed_on_too_few_weighted_games(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    ids = [analysed(g, days=1 + i) for i in range(5)]
    for gid in ids[:3]:
        plant(clean, gid, 7, "faded")
    rows, _ = habits.habits(clean, Settings(), "all", parse_opening("__all__"))
    assert rows[0]["trend"] is None


# --- lost wins ---------------------------------------------------------------------------------


def ev(gid: int, **over: Any) -> read.Event:
    e: read.Event = {
        "chess_game_id": gid,
        "anchor_ply": 20,
        "base_route": "lapse_defense",
        "evidence": {},
        "cost": 10.0,
        "san": "Qb6",
        "played_at": NOW - timedelta(days=gid),
        "time_class": "rapid",
        "termination": "resignation",
        "url": None,
        "result": "loss",
        "opponent_username": f"opp{gid}",
        "opponent_rating": 1500,
        "reviewed_at": None,
    }
    e.update(over)
    return e


def test_lost_wins_qualification_and_reason() -> None:
    events = [
        ev(1, base_route="faded"),
        ev(2, evidence={"game_peak_es": 70}, result="draw"),
        ev(2, anchor_ply=31, cost=25.0, evidence={"game_peak_es": 70}, result="draw"),
        ev(3, evidence={"game_peak_es": 70}, result="win"),  # won
        ev(4, base_route="faded", termination="timeout"),  # clock-decided
        ev(5, evidence={"game_peak_es": 61}),  # below the faded peak
        ev(6, base_route="lapse_offense"),  # a missed win alone was never winning
        ev(7, evidence={"game_peak_es": "not a number"}),
    ]
    events[0]["reviewed_at"] = NOW
    rows = read.select_lost_wins(events, 62)
    assert [(r["chess_game_id"], r["reviewed"]) for r in rows] == [(2, False), (1, True)]
    assert (rows[0]["peak_es"], rows[0]["anchor_ply"], rows[0]["anchor_move"], rows[0]["cost"]) == (
        70.0,
        31,
        "16…Qb6",
        25.0,
    )


# --- the page ------------------------------------------------------------------------------------


@pytest.fixture()
def corpus(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    """Scandinavian losses as Black (analysed, with events), Italian games as White, and a Chess960
    game carrying a planted event."""
    g = Games(clean)
    for i in range(12):
        gid = g.add(MOVES, family="Scandinavian Defense", keep_moves=True, ply_analysis=ANALYSED, days=1 + i * 2)
        plant(clean, gid, 7, "lapse_defense", phase="opening", evidence={"game_peak_es": 80} if i < 2 else {})
    for i in range(12):
        gid = g.add(
            ITALIAN,
            colour="white",
            family="Italian Game",
            keep_moves=True,
            ply_analysis=ANALYSED,
            days=2 + i * 2,
            result="draw",
        )
        plant(clean, gid, 6, "lapse_offense", evidence={"theme": "pin"})
    c960 = g.add(["e4", "e5"], variant="chess960", ply_analysis=ANALYSED, days=0.5)
    plant(clean, c960, 7, "faded", evidence={"game_peak_es": 99})
    return clean


def test_the_page(corpus: psycopg.Connection[DictRow]) -> None:
    page = read.page(corpus, Settings(), time_class="all")
    assert set(page) == {"mistakes", "positions", "habits", "lost_wins", "filter", "meta"}
    assert page["positions"]["ranked"][0]["line_san"][:2] == ["e4", "d5"]
    assert {h["id"] for h in page["habits"]} == {"lost:opening", "missed:pin"}
    assert page["lost_wins"]["total"] == 2  # the Chess960 game's event is invisible
    # Twelve games each: the tie goes to the family's name.
    assert [o["key"] for o in page["filter"]["openings"]] == ["white:Italian Game", "black:Scandinavian Defense"]
    meta = page["meta"]
    assert (meta["games_counted"], meta["window_games"], meta["history_months"]) == (24, 24, 12)


def test_the_opening_filter_reaches_every_section(corpus: psycopg.Connection[DictRow]) -> None:
    page = read.page(corpus, Settings(), time_class="all", opening="white:Italian Game")
    assert all(c["colour"] == "white" for s in ("ranked", "fixed") for c in page["positions"][s])
    assert [h["id"] for h in page["habits"]] == ["missed:pin"]
    assert page["lost_wins"]["total"] == 0
    assert page["meta"]["window_games"] == 12 and page["meta"]["games_counted"] == 12
    for stale in ("white:Scandinavian Defense", "black:Nonsense", "Italian Game", "green:Italian Game"):
        with pytest.raises(ReviewParamError, match="unknown opening key"):
            read.page(corpus, Settings(), time_class="all", opening=stale)


def test_nothing_at_runtime_names_the_retired_pool_table() -> None:
    """The worklist's rotation table is dropped by a migration; no runtime code may read or write it.
    The migration that drops it and the schema history name it, and are not scanned."""
    offenders = [
        str(path.relative_to(ROOT))
        for top in ("core", "api", "pipeline")
        for path in (ROOT / top).rglob("*.py")
        if "review_pool_state" in path.read_text()
    ]
    assert offenders == []
