"""The Practice routes over a seeded database."""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.puzzles.lines import fen_sequence

FORK_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1"


def _seed(conn: psycopg.Connection[DictRow]) -> int:
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'me')", (PLAYER_ID,))
    conn.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, url, played_at, variant, time_class, moves, fen_sequence)"
        " VALUES (1, 'lichess', 'g1', 'https://example.test/1', now() - interval '1 day', 'standard', 'rapid',"
        " %s::jsonb, %s::jsonb)",
        (json.dumps(["e4"]), json.dumps([FORK_FEN])),
    )
    conn.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, analyzed_at_depth, opponent_username)"
        " VALUES (%s, 1, 'white', 'lichess', 18, 'opp')",
        (PLAYER_ID,),
    )
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, best_move, best_line, classification, themes)"
        " VALUES (%s, 1, 0, %s, 'Nxe5', 'Nxe5 Nxe5 d4', 'blunder', ARRAY['fork'])",
        (PLAYER_ID, FORK_FEN),
    )
    line = ["Nxe5", "Nxe5", "d4"]
    row = conn.execute(
        "INSERT INTO puzzles (fen, solution_line, solution_fen_sequence, source_types, color, themes, player_id)"
        " VALUES (%s, %s::jsonb, %s::jsonb, ARRAY['blunder'], 'w', ARRAY['fork'], %s) RETURNING id",
        (FORK_FEN, json.dumps(line), json.dumps(fen_sequence(FORK_FEN, line)), PLAYER_ID),
    ).fetchone()
    assert row is not None
    conn.commit()
    return int(row["id"])


@pytest.fixture()
def client(app_env: None, clean: psycopg.Connection[DictRow]) -> tuple[TestClient, int]:
    from api.main import app

    pid = _seed(clean)
    c = TestClient(app)
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c, pid


def test_the_routes_need_a_session(app_env: None) -> None:
    from api.main import app

    c = TestClient(app)
    assert c.get("/practice/puzzles").status_code == 401
    assert c.get("/practice/puzzles/1").status_code == 401
    assert c.post("/practice/puzzles/1/attempt", json={"solved": True}).status_code == 401
    assert c.post("/practice/skip", json={"batch_id": 1, "puzzle_id": 1}).status_code == 401
    assert c.get("/practice/repertoire-scopes").status_code == 401
    assert c.get("/practice/today").status_code == 401


def test_the_queue_serves_a_batch_with_the_page_s_context(client: tuple[TestClient, int]) -> None:
    c, pid = client
    body: dict[str, Any] = c.get("/practice/puzzles").json()
    assert body["total"] == 1 and body["batch_id"] == 1 and body["scope"] == "all"
    assert body["mint_ahead_threshold"] == 4 and body["advance_threshold"] == 1 and body["mastered_count"] == 0
    assert "fork" in body["served_themes"]
    puzzle = body["puzzles"][0]
    assert puzzle["id"] == pid and puzzle["play_batch_id"] == 1 and puzzle["srs"] is None
    assert puzzle["occurrence_count"] == 1 and puzzle["source_breakdown"] == {"blunder": 1}
    assert puzzle["game_links"][0]["url"] == "https://example.test/1"
    assert puzzle["attempt_summary"] == {"total": 0, "solved": 0, "last_attempt_at": None, "streak": 0}
    assert c.get("/practice/puzzles", params={"ptype": "motif"}).json()["total"] == 0
    assert c.get("/practice/puzzles", params={"ptype": "endgame"}).status_code == 422


def test_an_attempt_is_graded_scored_and_replayed(client: tuple[TestClient, int]) -> None:
    c, pid = client
    attempt = {
        "solved": True,
        "moves_played": "Nxe5,d4",
        "attempt_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "presentation_ply": None,
    }
    # A segment on a standard puzzle, or outside the line, is a bad request, not a verdict.
    assert c.post(f"/practice/puzzles/{pid}/attempt", json={**attempt, "presentation_ply": 1}).status_code == 422
    assert c.post(f"/practice/puzzles/{pid}/attempt", json={**attempt, "presentation_ply": -1}).status_code == 422
    first = c.post(f"/practice/puzzles/{pid}/attempt", json=attempt).json()
    assert first["solved"] is True and first["srs"]["level"] == "knight"
    assert first["srs"]["transition"]["outcome"] == "promoted"
    replay = c.post(f"/practice/puzzles/{pid}/attempt", json=attempt).json()
    assert replay["detail"] == "attempt recorded (idempotent)" and replay["srs"]["transition"] is None
    after: dict[str, Any] = c.get("/practice/puzzles", params={"srs": "all"}).json()
    assert after["puzzles"][0]["srs"]["level"] == "knight" and after["puzzles"][0]["attempt_summary"]["streak"] == 1
    assert c.get("/practice/puzzles").json()["total"] == 0, "scheduled for tomorrow, so no longer due"
    assert c.post("/practice/puzzles/999999/attempt", json=attempt).status_code == 404


def test_skip_outcomes_map_to_status_codes(client: tuple[TestClient, int]) -> None:
    c, pid = client
    c.get("/practice/puzzles")
    body = {"ptype": "all", "subtype": None, "batch_id": 1, "puzzle_id": pid}
    assert c.post("/practice/skip", json=body).json() == {"status": "DEFERRED"}
    assert c.post("/practice/skip", json=body).json() == {"status": "ALREADY_CONSUMED"}
    miss = c.post("/practice/skip", json={**body, "batch_id": 7})
    assert miss.status_code == 409 and miss.json()["detail"] == {"status": "STATE_MISS"}


def test_the_deep_link_returns_the_solver_payload_or_404(client: tuple[TestClient, int]) -> None:
    c, pid = client
    payload = c.get(f"/practice/puzzles/{pid}").json()
    assert set(payload) == {
        "id",
        "fen",
        "solution_line",
        "color",
        "acceptance_map",
        "source_types",
        "themes",
        "is_repertoire",
        "presentation_ply",
    }
    assert c.get("/practice/puzzles/999999").status_code == 404


def test_the_retired_view_lists_mastered_puzzles(
    client: tuple[TestClient, int], clean: psycopg.Connection[DictRow]
) -> None:
    c, pid = client
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level, next_show_at) VALUES (%s, %s, 'king', '9999-12-31')",
        (PLAYER_ID, pid),
    )
    clean.commit()
    body = c.get("/practice/puzzles", params={"srs": "retired"}).json()
    assert [p["id"] for p in body["puzzles"]] == [pid] and body["mastered_count"] == 1
    assert c.get("/practice/puzzles").json()["total"] == 0


@pytest.mark.parametrize("subtype", ["chapter:", "x:1", "0", "line:01", "-1", "1.5", "book:99999999999", "line:1:2"])
def test_a_malformed_repertoire_subtype_is_refused_with_nothing_to_match(
    client: tuple[TestClient, int], subtype: str
) -> None:
    """Refused before any row is read: the seeded database has no repertoire at all."""
    c, pid = client
    for view in ("due", "all", "retired"):
        r = c.get("/practice/puzzles", params={"ptype": "repertoire", "subtype": subtype, "srs": view})
        assert r.status_code == 422, (view, r.text)
    skip = c.post("/practice/skip", json={"ptype": "repertoire", "subtype": subtype, "batch_id": 1, "puzzle_id": pid})
    assert skip.status_code == 422


def test_the_repertoire_scopes_route_answers_an_empty_repertoire(client: tuple[TestClient, int]) -> None:
    c, _ = client
    assert c.get("/practice/repertoire-scopes").json() == {"books": []}
    # A theme SubType is still free text for the other types.
    assert c.get("/practice/puzzles", params={"ptype": "motif", "subtype": "chapter:"}).status_code == 200


def test_the_list_routes_and_the_scopes_route_serve_a_scope_s_own_puzzle(
    client: tuple[TestClient, int], clean: psycopg.Connection[DictRow]
) -> None:
    """Over HTTP: a chapter whose puzzle loses the one-per-position choice to another chapter's
    on the same board still lists and serves its own, and the tree counts what each scope lists."""
    from tests.test_practice import _shared_board

    c, _ = client
    ids = _shared_board(clean, first_game=100)  # the seed holds game 1
    clean.commit()
    for view in ("all", "due"):
        listed = c.get("/practice/puzzles", params={"ptype": "repertoire", "subtype": "chapter:1", "srs": view}).json()
        assert [p["id"] for p in listed["puzzles"]] == [ids["p1"]], view
    listed = c.get("/practice/puzzles", params={"ptype": "repertoire", "subtype": "book:1", "srs": "all"}).json()
    assert [p["id"] for p in listed["puzzles"]] == [ids["p2"]]
    unfiltered = c.get("/practice/puzzles", params={"ptype": "repertoire", "srs": "all"}).json()
    assert [p["id"] for p in unfiltered["puzzles"]] == [ids["p3"]]
    tree = c.get("/practice/repertoire-scopes").json()["books"]
    assert [(b["id"], b["count"], [(ch["id"], ch["count"]) for ch in b["chapters"]]) for b in tree] == [
        (1, 1, [(1, 1), (2, 1)]),
        (2, 1, [(3, 1)]),
    ]
    assert [line["id"] for line in tree[0]["chapters"][0]["lines"]] == [1]


def test_today_counts_the_attempts_the_attempt_route_recorded(client: tuple[TestClient, int]) -> None:
    c, pid = client
    before = c.get("/practice/today")
    assert before.status_code == 200
    body = before.json()
    assert set(body) == {"date", "solved", "tried", "target", "next_day_at", "now"}
    assert (body["solved"], body["tried"]) == (0, 0)
    for solved, moves in ((False, "Qh5"), (True, "Nxe5,d4")):
        r = c.post(
            f"/practice/puzzles/{pid}/attempt",
            json={"solved": solved, "moves_played": moves, "attempt_id": str(uuid.uuid4())},
        )
        assert r.status_code == 200, r.text
    after = c.get("/practice/today").json()
    assert (after["solved"], after["tried"]) == (1, 1)
