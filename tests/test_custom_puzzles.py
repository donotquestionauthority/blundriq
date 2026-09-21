"""Hand-made puzzles (core/puzzles/custom.py) and the routes of the Blunders page."""

from __future__ import annotations

import json
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.puzzles import custom
from core.puzzles.generate import blunder
from core.settings import Settings

FORK = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1"
FORK_LATER = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 6 11"


def _make(conn: psycopg.Connection[DictRow], **kw: Any) -> int:
    args: dict[str, Any] = {
        "fen": FORK,
        "solution_line": ["Nxe5", "Nxe5", "d4"],
        "color": "w",
        "context_tags": ["blunder"],
        "title": "Fork trick",
        "description": "remember d4",
    }
    return custom.create(conn, **{**args, **kw})


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    clean.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'me')", (PLAYER_ID,))
    return clean


def test_a_hand_made_puzzle_is_stored_like_a_generated_one_and_always_tagged_custom(
    db: psycopg.Connection[DictRow],
) -> None:
    pid = _make(db, solution_line=["Nxe5!", "Nxe5", "d4"], context_tags=["blunder", "custom", "lichess_cc0"])
    row = db.execute("SELECT * FROM puzzles WHERE id = %s", (pid,)).fetchone()
    assert row is not None
    assert row["source_types"] == ["blunder", "custom"]  # the request cannot choose its own provenance
    assert row["solution_line"] == ["Nxe5", "Nxe5", "d4"] and len(row["solution_fen_sequence"]) == 4
    assert (row["color"], row["title"], row["description"], row["active"], row["is_repertoire"]) == (
        "w",
        "Fork trick",
        "remember d4",
        True,
        False,
    )


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"fen": "not a fen"}, "six-field"),
        ({"fen": "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w"}, "six-field"),
        ({"fen": "xyz/8/8/8/8/8/8/8 w - - 0 1"}, "valid FEN"),
        ({"fen": "8/8/8/8/8/8/8/8 w - - 0 1"}, "legal chess position"),
        ({"solution_line": []}, "1 to 30"),
        ({"solution_line": ["Nxe5"] * 31}, "1 to 30"),
        ({"solution_line": ["Nxe5", "O-O"]}, "move 2"),
        ({"solution_line": ["Nxe5"], "color": "b"}, "no move for your colour"),
    ],
)
def test_a_puzzle_that_cannot_be_played_is_refused(
    db: psycopg.Connection[DictRow], kw: dict[str, Any], message: str
) -> None:
    with pytest.raises(custom.InvalidPuzzle, match=message):
        _make(db, **kw)
    assert db.execute("SELECT count(*) AS n FROM puzzles").fetchone() == {"n": 0}


def test_a_short_fen_is_refused_because_it_has_no_board_key(db: psycopg.Connection[DictRow]) -> None:
    assert db.execute("SELECT bq_canonical_fen(%s) AS k", (FORK.rsplit(" ", 4)[0],)).fetchone() == {"k": None}
    with pytest.raises(custom.InvalidPuzzle):
        _make(db, fen=FORK.rsplit(" ", 4)[0])
    made = db.execute("SELECT canonical_fen FROM puzzles WHERE id = %s", (_make(db),)).fetchone()
    assert made is not None and made["canonical_fen"] is not None


def test_the_line_may_open_with_the_opponents_move(db: psycopg.Connection[DictRow]) -> None:
    assert _make(db, solution_line=["Nxe5", "Nxe5"], color="b") > 0


def test_a_board_holds_one_active_puzzle_and_removing_frees_it(db: psycopg.Connection[DictRow]) -> None:
    first = _make(db)
    with pytest.raises(custom.BoardTaken):
        _make(db, fen=FORK_LATER)  # same board, other clocks
    db.execute("INSERT INTO puzzle_attempts (player_id, puzzle_id, solved) VALUES (%s, %s, TRUE)", (PLAYER_ID, first))
    assert custom.remove(db, first) is True
    assert custom.remove(db, first) is False  # already gone
    kept = db.execute(
        "SELECT active, (SELECT count(*) FROM puzzle_attempts) AS attempts FROM puzzles WHERE id = %s", (first,)
    ).fetchone()
    assert kept == {"active": False, "attempts": 1}  # history stays; "solved today" does not change after the fact
    assert _make(db, fen=FORK_LATER) != first


def test_only_a_hand_made_puzzle_can_be_removed(db: psycopg.Connection[DictRow]) -> None:
    row = db.execute(
        "INSERT INTO puzzles (fen, solution_line, source_types, color, player_id) VALUES (%s, %s::jsonb, ARRAY['blunder'], 'w', %s) RETURNING id",
        (FORK, json.dumps(["Nxe5"]), PLAYER_ID),
    ).fetchone()
    assert row is not None
    assert custom.remove(db, row["id"]) is False and custom.remove(db, 999) is False
    assert db.execute("SELECT active FROM puzzles WHERE id = %s", (row["id"],)).fetchone() == {"active": True}


def test_the_hourly_generator_leaves_a_created_puzzle_alone(db: psycopg.Connection[DictRow]) -> None:
    for gid in (1, 2, 3):
        db.execute(
            "INSERT INTO chess_games (id, platform, platform_game_id, played_at, variant, time_class, moves, fen_sequence)"
            " VALUES (%s, 'lichess', %s, now(), 'standard', 'rapid', '[\"e4\"]'::jsonb, %s::jsonb)",
            (gid, f"g{gid}", json.dumps([FORK])),
        )
        db.execute(
            "INSERT INTO player_games (player_id, chess_game_id, player_color, source, analyzed_at_depth) VALUES (%s, %s, 'white', 'lichess', 18)",
            (PLAYER_ID, gid),
        )
        db.execute(
            "INSERT INTO blunders (player_id, chess_game_id, ply, fen, best_move, best_line, centipawn_loss, classification, themes)"
            " VALUES (%s, %s, 0, %s, 'Nxe5', 'Nxe5 Nxe5 d4', 300, 'blunder', ARRAY['fork'])",
            (PLAYER_ID, gid, FORK),
        )
    pid = _make(db, solution_line=["d3"])
    stats = blunder.generate(db, Settings())
    assert stats["created"] == 0 and stats["deactivated"] == 0
    assert [dict(r) for r in db.execute("SELECT id, active FROM puzzles").fetchall()] == [{"id": pid, "active": True}]


# --- routes ------------------------------------------------------------------------------------


@pytest.fixture()
def client(app_env: None, db: psycopg.Connection[DictRow]) -> TestClient:
    from api.main import app

    for gid in (1, 2):
        db.execute(
            "INSERT INTO chess_games (id, platform, platform_game_id, url, played_at, variant, time_class, moves, fen_sequence)"
            " VALUES (%s, 'lichess', %s, 'https://example.test/g', now() - interval '1 day', 'standard', 'rapid', '[\"e4\"]'::jsonb, %s::jsonb)",
            (gid, f"g{gid}", json.dumps([FORK])),
        )
        db.execute(
            "INSERT INTO player_games (player_id, chess_game_id, player_color, source, result) VALUES (%s, %s, 'white', 'lichess', 'win')",
            (PLAYER_ID, gid),
        )
        db.execute(
            "INSERT INTO blunders (player_id, chess_game_id, ply, fen, move_played, best_move, centipawn_loss, classification)"
            " VALUES (%s, %s, 4, %s, 'd3', 'Nxe5', 300, 'blunder')",
            (PLAYER_ID, gid, FORK),
        )
    db.commit()
    c = TestClient(app)
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_every_route_needs_a_session(app_env: None) -> None:
    from api.main import app

    c = TestClient(app)
    assert c.get("/blunders").status_code == 401
    assert c.get("/blunders/prompts").status_code == 401
    for path in ("/blunders/dismiss", "/blunders/restore", "/blunders/explain", "/puzzles"):
        assert c.post(path, json={}).status_code == 401, path
    assert c.delete("/puzzles/1").status_code == 401


def test_the_list_route_defaults_to_the_settings_and_validates_its_filters(client: TestClient) -> None:
    body = client.get("/blunders").json()  # no classifications given: the setting's default set
    assert body["active_count"] == 1 and body["positions"][0]["count"] == 2 and body["page_size"] == 50
    assert client.get("/blunders", params={"classifications": ["inaccuracy"]}).json()["positions"] == []
    assert client.get("/blunders", params={"classifications": ["nonsense"]}).status_code == 422
    assert client.get("/blunders", params={"time_class": "hyper"}).status_code == 422
    assert client.get("/blunders", params={"min_occurrences": 0}).status_code == 422


def test_dismiss_and_restore_round_trip_and_refuse_a_non_position(client: TestClient) -> None:
    assert client.post("/blunders/dismiss", json={"fen": FORK_LATER}).status_code == 200
    assert client.get("/blunders").json()["active_count"] == 0
    assert client.get("/blunders", params={"show_dismissed": True}).json()["positions"][0]["dismissed"] is True
    assert client.post("/blunders/restore", json={"fen": FORK}).status_code == 200
    assert client.get("/blunders").json()["active_count"] == 1
    assert client.post("/blunders/dismiss", json={"fen": "'; DROP TABLE blunders; --"}).status_code == 422
    assert (
        client.post("/blunders/dismiss", json={"fen": FORK.rsplit(" ", 4)[0]}).status_code == 422
    )  # short: no board key
    assert client.get("/blunders").json()["active_count"] == 1


def test_create_and_remove_a_puzzle_over_http(client: TestClient) -> None:
    body = {
        "fen": FORK,
        "solution_line": ["Nxe5", "Nxe5", "d4"],
        "color": "w",
        "source_types": ["blunder"],
        "title": " Fork ",
    }
    made = client.post("/puzzles", json=body)
    assert made.status_code == 200 and made.json()["visible"] is True
    pid = made.json()["id"]
    assert client.get(f"/practice/puzzles/{pid}").json()["source_types"] == ["blunder", "custom"]
    assert client.post("/puzzles", json=body).status_code == 409
    assert client.post("/puzzles", json={**body, "solution_line": ["Qh5"]}).status_code == 422
    assert client.post("/puzzles", json={**body, "source_types": ["custom"]}).status_code == 422
    assert client.delete(f"/puzzles/{pid}").status_code == 200
    assert client.delete(f"/puzzles/{pid}").status_code == 404
    assert client.get(f"/practice/puzzles/{pid}").status_code == 404


def test_a_puzzle_the_dismissed_board_hides_says_so(client: TestClient) -> None:
    client.post("/blunders/dismiss", json={"fen": FORK})
    made = client.post("/puzzles", json={"fen": FORK, "solution_line": ["Nxe5"], "color": "w"})
    assert made.status_code == 200 and made.json()["visible"] is False


def test_explain_over_http_maps_refusals_to_statuses(client: TestClient) -> None:
    labels = client.get("/blunders/prompts").json()["prompts"]
    assert labels[0]["key"] == "a" and {p["key"] for p in labels} == {"a", "b", "c"}
    dry = client.post("/blunders/explain", json={"chess_game_id": 1, "ply": 4, "prompt_key": "a", "dry_run": True})
    assert (
        dry.status_code == 200
        and "d3" in dry.json()["rendered_prompt"]
        and dry.json()["thinking"] == {"type": "disabled"}
    )
    assert client.post("/blunders/explain", json={"chess_game_id": 1, "ply": 5, "prompt_key": "a"}).status_code == 404
    assert client.post("/blunders/explain", json={"chess_game_id": 1, "ply": 4, "prompt_key": "zz"}).status_code == 404
