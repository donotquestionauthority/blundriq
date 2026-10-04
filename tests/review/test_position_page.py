"""One position's page: the turning point of each game, the order of the games, what happens next,
and the route's boundaries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core.review import position as pp
from core.settings import Settings
from tests.review.position_helpers import NOW, QGD, SCANDI, Games, key_of, scope


def pa(es_white: list[float]) -> list[dict[str, Any]]:
    """Stored analysis whose evaluations put White's expected score at the given values."""
    import math

    k = 0.00368208
    out: list[dict[str, Any]] = []
    for i, es in enumerate(es_white):
        es = min(max(es, 0.01), 99.99)
        out.append({"ply": i, "eval": round(-math.log(100 / es - 1) / k)})
    return out


def ev(ply: int, cost: float) -> dict[str, Any]:
    return {"anchor_ply": ply, "cost": cost}


# --- the turning point --------------------------------------------------------------------------


def test_an_immediate_mistake_is_the_turning_point() -> None:
    # White to move at ply 6 (Rob is White): his first decision is the position itself.
    assert pp.turning_point(6, "white", [ev(6, 40), ev(8, 5)], None) == (pp.TURN_FOUND, 6, 40.0)
    assert pp.turning_point(6, "white", [ev(6, 40)], None) == (pp.TURN_FOUND, 6, 40.0)
    # The costliest wins; a tie goes to the earlier move.
    assert pp.turning_point(6, "white", [ev(10, 30), ev(8, 30)], None)[1] == 8


def test_when_the_opponent_is_to_move_the_boundary_is_the_next_ply() -> None:
    # Ply 6 has White to move; Rob is Black, so his first decision from the board is ply 7.
    assert pp.turning_point(6, "black", [ev(5, 50), ev(9, 20)], None) == (pp.TURN_FOUND, 9, 20.0)
    assert pp.turning_point(6, "black", [ev(5, 50)], None) == (pp.TURN_NOT_ANALYSED, None, None)


def test_without_an_event_the_largest_drop_over_his_own_moves() -> None:
    # Rob is White; his expected score falls 15 at ply 10 and 30 at ply 11 (the opponent's move).
    curve = [50.0] * 10 + [50.0, 35.0, 65.0, 65.0, 60.0]
    assert pp.turning_point(6, "white", [], pa(curve)) == (pp.TURN_FOUND, 10, 15.0)
    # Nothing ever dropped ten points in one of his moves.
    flat = [50.0] * 8 + [45.0, 45.0, 40.0, 40.0]
    assert pp.turning_point(6, "white", [], pa(flat)) == (pp.TURN_NONE, None, None)
    # Analysed, but the drop came before the board: not a turning point from here.
    early = [50.0, 50.0, 50.0, 50.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0]
    assert pp.turning_point(6, "white", [], pa(early)) == (pp.TURN_NONE, None, None)


def test_move_labels_and_side_to_move() -> None:
    assert pp.move_label(0, "e4") == "1.e4"
    assert pp.move_label(33, "Qb6") == "17…Qb6"
    assert pp.rob_to_move(0, "white") and not pp.rob_to_move(0, "black") and pp.rob_to_move(5, "black")


def test_the_games_are_ordered_playable_losses_first() -> None:
    def g(gid: int, result: str, es: float | None, reviewed: bool = False, days: int = 1) -> dict[str, Any]:
        from datetime import timedelta

        return {
            "chess_game_id": gid,
            "result": result,
            "es_on_arrival": es,
            "reviewed": reviewed,
            "played_at": NOW - timedelta(days=days),
        }

    rows = [
        g(1, "win", 60),
        g(2, "loss", 30),
        g(3, "loss", 45, reviewed=True),
        g(4, "draw", 41, days=5),
        g(5, "loss", None, days=2),
        g(6, "loss", 50, days=3),
    ]
    assert [r["chess_game_id"] for r in pp.order_games(rows)] == [6, 4, 3, 2, 5, 1]


# --- over stored games ----------------------------------------------------------------------


def _page(
    conn: psycopg.Connection[DictRow], moves: Sequence[str], colour: str = "black", **kw: Any
) -> dict[str, Any] | None:
    return pp.position_page(conn, scope(Settings(review_position_min_games=3), **kw), colour, key_of(conn, moves), 1)


def test_the_page_of_a_position(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    g.anchor()
    tail = "Nf3 Nf6 d4 Bf5 Bd2 c6".split()
    analysed = pa([50.0] * 7 + [52.0, 52.0, 70.0] + [70.0] * 3)  # Black (Rob) fine on arrival, then worse
    with_moves = g.add(SCANDI + tail, keep_moves=True, ply_analysis=analysed, days=2)
    clean.execute(
        "INSERT INTO review_events (player_id, chess_game_id, anchor_ply, config_version, base_route, evidence, cost,"
        " first_detected_at) VALUES (1, %s, 3, 2, 'lapse_defense', '{}'::jsonb, 50, now()),"
        " (1, %s, 9, 2, 'lapse_defense', '{}'::jsonb, 18, now())",
        (with_moves, with_moves),
    )
    unanalysed = g.add(SCANDI + ["d4"], keep_moves=True, days=3)
    for i in range(3):
        g.add(SCANDI + ["Nf3"], days=10 + i, result="win")
    page = _page(clean, SCANDI)
    assert page is not None
    node = page["node"]
    assert node["n"] == 5 and node["line_san"] == SCANDI
    assert node["rob_to_move"] is False  # White moves after …Qa5
    assert [(c["san"], c["n"]) for c in page["children"]] == [("Nf3", 4), ("d4", 1)]
    assert page["children"][0]["key"] == str(key_of(clean, SCANDI + ["Nf3"]))
    assert page["older_games"] == 3 and page["games"]["total"] == 2
    rows = {r["chess_game_id"]: r for r in page["games"]["rows"]}
    # The event at ply 3 is before the board; the one at 9 is Rob's first costly decision after it.
    assert (rows[with_moves]["turning_state"], rows[with_moves]["turning_ply"]) == (pp.TURN_FOUND, 9)
    assert rows[with_moves]["turning_move"] == "5…Bf5" and rows[with_moves]["turning_cost"] == 18.0
    assert rows[with_moves]["es_on_arrival"] is not None and rows[with_moves]["es_on_arrival"] > 40
    assert rows[unanalysed]["turning_state"] == pp.TURN_NOT_ANALYSED
    # Below the floor a page still answers: it shows what is there, without a status.
    small = pp.position_page(clean, scope(), "black", key_of(clean, SCANDI), 1)
    assert small is not None and small["node"]["n"] == 5 and small["node"]["status"] is None
    # A board past the ranking's plies is read over the whole prefix.
    deep = SCANDI + tail
    assert _page(clean, deep) is not None
    shallow = pp.position_page(clean, scope(Settings(review_position_max_ply=4)), "black", key_of(clean, SCANDI), 1)
    assert shallow is not None and shallow["node"]["n"] == 5 and shallow["node"]["status"] is None
    # Nobody reaches it as White.
    assert _page(clean, SCANDI, colour="white") is None
    assert _page(clean, QGD) is None


@pytest.fixture()
def client(app_env: None, clean: psycopg.Connection[DictRow]) -> TestClient:
    g = Games(clean)
    for i in range(12):
        g.add(SCANDI, family="Scandinavian Defense", days=1 + i * 3)
    clean.commit()
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_the_position_route(client: TestClient, clean: psycopg.Connection[DictRow]) -> None:
    key = key_of(clean, SCANDI)
    body = client.get(f"/review/positions/black/{key}").json()
    assert body["node"]["key"] == str(key) and body["node"]["n"] == 12
    assert set(body) == {"node", "children", "games", "older_games"}
    assert client.get(f"/review/positions/black/{key}?opening=black:Scandinavian%20Defense").status_code == 200
    assert client.get(f"/review/positions/white/{key}").status_code == 404
    assert client.get("/review/positions/black/-9223372036854775808").status_code == 404  # negative, in range
    for bad in ("12a", "9223372036854775808", "-9223372036854775809", "1" * 20):
        assert client.get(f"/review/positions/black/{bad}").status_code == 422, bad
    assert client.get(f"/review/positions/red/{key}").status_code == 422
    stale = client.get(f"/review/positions/black/{key}?opening=white:Italian%20Game")
    assert stale.status_code == 422 and "unknown opening key" in stale.json()["detail"]


def test_the_fallback_prices_only_robs_moves_from_his_first_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    # Rob is Black and White is to move at ply 6: his decisions are plies 7, 9, ...
    curve = [50.0] * 6 + [
        80.0,
        30.0,
        18.0,
        5.0,
        5.0,
    ]
    monkeypatch.setattr(pp, "es_curve", lambda _pa, _white: curve)
    # es[6]-es[7] = 50 is White's move; es[7]-es[8] = 12 is his; es[8]-es[9] = 13 is White's again.
    assert pp.turning_point(6, "black", [], [{}] * len(curve)) == (pp.TURN_FOUND, 7, 12.0)


def test_a_drop_of_exactly_the_minimum_is_a_turning_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pp, "es_curve", lambda _pa, _white: [50.0] * 6 + [50.0, 40.0, 40.0])
    assert pp.turning_point(6, "white", [], [{}] * 9) == (pp.TURN_FOUND, 6, 10.0)
    monkeypatch.setattr(pp, "es_curve", lambda _pa, _white: [50.0] * 6 + [50.0, 40.1, 40.1])
    assert pp.turning_point(6, "white", [], [{}] * 9)[0] == pp.TURN_NONE


def test_exactly_playable_on_arrival_is_the_first_band() -> None:
    rows = [
        {"chess_game_id": 1, "result": "loss", "es_on_arrival": 39.9, "reviewed": False, "played_at": NOW},
        {
            "chess_game_id": 2,
            "result": "loss",
            "es_on_arrival": 40.0,
            "reviewed": False,
            "played_at": NOW - timedelta(days=1),
        },
    ]
    assert [r["chess_game_id"] for r in pp.order_games(rows)] == [2, 1]


def test_a_board_both_colours_reach_is_two_positions(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    ruy = "e4 e5 Nf3 Nc6 Bb5".split()
    for i in range(3):
        g.add(ruy, colour="white", keep_moves=True, days=1 + i)
    g.add(ruy[:4] + ["Bc4"], colour="black", keep_moves=True, days=5)
    white = _page(clean, ruy[:4], colour="white")
    black = _page(clean, ruy[:4], colour="black")
    assert white is not None and black is not None
    assert (white["node"]["n"], white["games"]["total"], [c["san"] for c in white["children"]]) == (3, 3, ["Bb5"])
    assert (black["node"]["n"], black["games"]["total"], [c["san"] for c in black["children"]]) == (1, 1, ["Bc4"])


def test_one_move_from_a_board_reached_at_different_plies_is_one_row(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    for i in range(2):
        g.add(["e4", "e5"], colour="white", days=1 + i)
        g.add("Nf3 Nf6 Ng1 Ng8 e4 e5".split(), colour="white", days=3 + i)
    page = _page(clean, ["e4"], colour="white")
    assert page is not None and [(c["san"], c["n"]) for c in page["children"]] == [("e5", 4)]


def test_a_board_read_past_the_ranking_plies_has_no_status(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    for i in range(12):
        g.add(SCANDI, days=1 + i)  # all lost: a status would be a leak
    deep = pp.position_page(
        clean,
        scope(Settings(review_position_max_ply=4, review_position_min_games=3)),
        "black",
        key_of(clean, SCANDI),
        1,
    )
    assert deep is not None and deep["node"]["n"] == 12 and deep["node"]["status"] is None
    ranked = pp.position_page(clean, scope(Settings(review_position_min_games=3)), "black", key_of(clean, SCANDI), 1)
    assert ranked is not None and ranked["node"]["status"] is not None


def test_every_linked_move_has_a_page_and_a_return_to_the_start_has_none(clean: psycopg.Connection[DictRow]) -> None:
    """Games that go back to the starting position: the move is listed, but the start is never a
    position of its own (it is first reached at ply 0), so it gets no link. Every move that is
    linked opens a page under the same filters."""
    g = Games(clean)
    knights = "Nf3 Nf6 Ng1 Ng8 e4 e5".split()
    for day in range(1, 13):
        g.add(knights, colour="white", days=day)
    for day in range(1, 4):
        g.add(knights[:3] + ["Nc6"], colour="white", days=day)
    sc = scope()
    start = key_of(clean, [])
    assert pp.position_page(clean, sc, "white", start, 1) is None
    for depth in range(1, len(knights) + 1):
        if key_of(clean, knights[:depth]) == start:
            continue
        page = pp.position_page(clean, sc, "white", key_of(clean, knights[:depth]), 1)
        assert page is not None
        for child in page["children"]:
            target = pp.position_page(clean, sc, "white", int(child["key"]), 1)
            assert child["linkable"] == (target is not None), (knights[:depth], child["san"])
    after_ng1 = pp.position_page(clean, sc, "white", key_of(clean, knights[:3]), 1)
    assert after_ng1 is not None
    assert [(c["san"], c["n"], c["linkable"]) for c in after_ng1["children"]] == [("Ng8", 12, False), ("Nc6", 3, True)]
