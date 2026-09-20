"""Retention: artefacts outside the analysis window go, the depth marker resets
with them, and the bulk payload is nulled only when no owner has the game in-window."""

from __future__ import annotations

import json

import psycopg
from psycopg.rows import DictRow

from core import housekeeping
from core.constants import PLAYER_ID


def _seed(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'p')", (PLAYER_ID,))
    conn.execute("INSERT INTO books (id, title, color, player_id) VALUES (1, 'B', 'white', %s)", (PLAYER_ID,))
    conn.execute("INSERT INTO chapters (id, book_id, title) VALUES (1, 1, 'C')")
    conn.execute(
        "INSERT INTO repertoire_lines (id, chapter_id, line_name, moves) VALUES (1, 1, 'L', '[\"e4\"]'::jsonb)"
    )
    for gid in range(1, 5):  # 1 newest … 4 oldest
        conn.execute(
            "INSERT INTO chess_games (id, platform, platform_game_id, played_at, moves, fen_sequence, ply_analysis)"
            " VALUES (%s, 'lichess', %s, now() - make_interval(mins => %s), '[\"e4\"]'::jsonb, %s::jsonb, '[]'::jsonb)",
            (gid, f"g{gid}", gid, json.dumps(["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"])),
        )
        conn.execute(
            "INSERT INTO player_games (player_id, chess_game_id, player_color, source, analyzed_at_depth)"
            " VALUES (%s, %s, 'white', 'lichess', 18)",
            (PLAYER_ID, gid),
        )
        conn.execute(
            "INSERT INTO blunders (player_id, chess_game_id, ply, fen) VALUES (%s, %s, 0, 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1')",
            (PLAYER_ID, gid),
        )
        conn.execute(
            "INSERT INTO player_motif_events (player_id, chess_game_id, ply, metric_type, theme, player_color)"
            " VALUES (%s, %s, 0, 'motif', 'fork', 'white')",
            (PLAYER_ID, gid),
        )
        row = conn.execute(
            "INSERT INTO game_repertoire_results (player_id, chess_game_id, book_id, chapter_id, deviated_at_ply, deviation_by)"
            " VALUES (%s, %s, 1, 1, 1, 'me') RETURNING id",
            (PLAYER_ID, gid),
        ).fetchone()
        assert row
        conn.execute(
            "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply) VALUES (%s, 1, 1)",
            (row["id"],),
        )
    # game 4 is also a scouted opponent's newest game → its payload must survive
    conn.execute("INSERT INTO opponent_profiles (id, player_id, name) VALUES (1, %s, 'opp')", (PLAYER_ID,))
    conn.execute(
        "INSERT INTO opponent_views (opponent_profile_id, chess_game_id, source_type, played_as) VALUES (1, 4, 'lichess', 'black')"
    )
    conn.commit()


def test_run_keeps_the_window_and_owners_payload(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _seed(conn)
    out = housekeeping.run(conn, window=2)
    assert out["analysis"] == {"games": 2, "blunders": 2, "motif_events": 2}
    assert out["repertoire"] == {"results": 2, "lines": 2}
    assert out["payload_nulled"] == 1  # game 3 only; game 4 is in the opponent's window
    kept = conn.execute("SELECT chess_game_id FROM blunders ORDER BY 1").fetchall()
    assert [r["chess_game_id"] for r in kept] == [1, 2]
    depth = conn.execute("SELECT chess_game_id, analyzed_at_depth FROM player_games ORDER BY 1").fetchall()
    assert [(r["chess_game_id"], r["analyzed_at_depth"]) for r in depth] == [(1, 18), (2, 18), (3, None), (4, None)]
    payload = conn.execute("SELECT id, moves IS NULL AS gone FROM chess_games ORDER BY id").fetchall()
    assert [(r["id"], r["gone"]) for r in payload] == [(1, False), (2, False), (3, True), (4, False)]
    # rerun is a no-op
    again = housekeeping.run(conn, window=2)
    assert again["analysis"]["games"] == 0 and again["repertoire"]["results"] == 0 and again["payload_nulled"] == 0


def test_chess960_does_not_evict_standard_games(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _seed(conn)
    conn.execute("UPDATE chess_games SET variant = 'chess960', starting_fen = fen_sequence->>0 WHERE id = 1")  # newest
    conn.commit()
    out = housekeeping.run(conn, window=2)
    # window = games 2 and 3 (standard); game 1 is history: its payload goes, game 3 stays loaded
    assert out["analysis"]["games"] == 2  # games 1 and 4
    payload = conn.execute("SELECT id, moves IS NULL AS gone FROM chess_games ORDER BY id").fetchall()
    assert [(r["id"], r["gone"]) for r in payload] == [(1, True), (2, False), (3, False), (4, False)]
