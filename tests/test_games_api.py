"""GET /games and friends over a seeded database: filters, summary, paging, CSV."""

from __future__ import annotations

import json

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core.constants import PLAYER_ID


@pytest.fixture()
def client(app_env: None, clean: psycopg.Connection[DictRow]) -> TestClient:
    conn = clean
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'p')", (PLAYER_ID,))
    conn.execute("INSERT INTO books (id, title, color, player_id) VALUES (1, 'Scandi', 'black', %s)", (PLAYER_ID,))
    conn.execute("INSERT INTO chapters (id, book_id, title) VALUES (1, 1, 'Main')")
    conn.execute(
        "INSERT INTO repertoire_lines (id, chapter_id, line_name, moves) VALUES (1, 1, 'Qa5 line', '[\"e4\"]'::jsonb)"
    )
    games = [
        (1, "lichess", "black", "win", 2, "standard"),
        (2, "chesscom", "white", "loss", 40, "standard"),
        (3, "chesscom", "black", "draw", 90, "chess960"),
    ]
    for gid, platform, color, result, days_ago, variant in games:
        start = "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1" if variant == "chess960" else None
        conn.execute(
            "INSERT INTO chess_games (id, platform, platform_game_id, url, played_at, moves, variant, starting_fen, opening_name,"
            " analysis_status) VALUES (%s, %s, %s, %s, now() - make_interval(days => %s), %s::jsonb, %s, %s, 'Scandinavian', 'completed')",
            (
                gid,
                platform,
                f"g{gid}",
                f"https://example.test/{gid}",
                days_ago,
                json.dumps(["e4", "d5"]),
                variant,
                start,
            ),
        )
        conn.execute(
            "INSERT INTO player_games (player_id, chess_game_id, player_color, source, result, opponent_username, analyzed_at_depth)"
            " VALUES (%s, %s, %s, %s, %s, %s, 18)",
            (PLAYER_ID, gid, color, platform, result, f"opp{gid}"),
        )
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, classification) VALUES (%s, 1, 1, 'x', 'miss'), (%s, 1, 3, 'y', 'blunder')",
        (PLAYER_ID, PLAYER_ID),
    )
    row = conn.execute(
        "INSERT INTO game_repertoire_results (player_id, chess_game_id, book_id, chapter_id, deviated_at_ply, deviation_by,"
        " expected_move, played_move) VALUES (%s, 1, 1, 1, 3, 'me', 'Qxd5', 'Nf6') RETURNING id",
        (PLAYER_ID,),
    ).fetchone()
    assert row
    conn.execute(
        "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply) VALUES (%s, 1, 3)",
        (row["id"],),
    )
    conn.commit()
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_games_require_login(app_env: None) -> None:
    from api.main import create_app

    assert TestClient(create_app()).get("/games").status_code == 401


def test_list_summary_and_filters(client: TestClient) -> None:
    body = client.get("/games").json()
    assert [g["id"] for g in body["games"]] == [1, 2, 3]  # newest first
    assert body["summary"] == {"total": 3, "wins": 1, "losses": 1, "draws": 1, "win_pct": 33, "pages": 1}
    g1 = body["games"][0]
    assert g1["book_title"] == "Scandi" and g1["line_name"] == "Qa5 line" and g1["deviation_by"] == "me"
    assert (g1["issue_count"], g1["miss_count"], g1["blunder_count"]) == (2, 1, 1) and g1["analyzed"] is True
    assert g1["moves"] == ["e4", "d5"] and g1["played_at"].endswith("+00:00") or "T" in g1["played_at"]

    assert [g["id"] for g in client.get("/games", params={"since_days": 60}).json()["games"]] == [1, 2]
    assert [g["id"] for g in client.get("/games", params={"last_n_games": 1, "since_days": 1}).json()["games"]] == [1]
    assert [g["id"] for g in client.get("/games", params={"variant": "chess960"}).json()["games"]] == [3]
    assert [g["id"] for g in client.get("/games", params={"deviation": "no_match"}).json()["games"]] == [2, 3]
    assert [g["id"] for g in client.get("/games", params={"deviation": "me", "book": "Scandi"}).json()["games"]] == [1]
    assert [g["id"] for g in client.get("/games", params={"opponent": "PP2"}).json()["games"]] == [2]
    assert [
        g["id"] for g in client.get("/games", params={"platform": "chesscom", "result": "loss"}).json()["games"]
    ] == [2]
    assert client.get("/games", params={"color": "purple"}).status_code == 422


def test_filters_opponents_and_csv(client: TestClient) -> None:
    f = client.get("/games/filters").json()
    assert [b["title"] for b in f["books"]] == ["Scandi"] and [c["title"] for c in f["chapters"]] == ["Main"]
    assert client.get("/games/opponents", params={"q": "opp"}).json() == {"opponents": ["opp1", "opp2", "opp3"]}
    csv_text = client.get("/games/export.csv", params={"result": "win"}).text
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("date,platform,variant,color,result,opponent") and len(lines) == 2
    assert ",lichess,standard,black,win,opp1," in lines[1] and lines[1].endswith(",yes,https://example.test/1")
