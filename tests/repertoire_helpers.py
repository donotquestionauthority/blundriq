"""Builders for repertoire rows and matched games, shared by the repertoire tests."""

from __future__ import annotations

import json
from typing import Any

import chess
import psycopg
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.repertoire.importing import spine

START = chess.STARTING_FEN


def player(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute(
        "INSERT INTO players (id, chesscom_username) VALUES (%s, 'me') ON CONFLICT (id) DO NOTHING", (PLAYER_ID,)
    )


def book(conn: psycopg.Connection[DictRow], id: int, title: str, color: str, *, active: bool = True) -> None:
    conn.execute(
        "INSERT INTO books (id, player_id, title, color, active, source_book_id) VALUES (%s, %s, %s, %s, %s, %s)",
        (id, PLAYER_ID, title, color, active, id),
    )


def chapter(conn: psycopg.Connection[DictRow], id: int, book_id: int, title: str, *, active: bool = True) -> None:
    conn.execute(
        "INSERT INTO chapters (id, book_id, title, active) VALUES (%s, %s, %s, %s)", (id, book_id, title, active)
    )


def line(
    conn: psycopg.Connection[DictRow],
    id: int,
    chapter_id: int,
    name: str,
    moves: list[str],
    *,
    active: bool = True,
    fens: list[str] | None = None,
) -> list[str]:
    fens = fens if fens is not None else spine(None, moves)
    conn.execute(
        "INSERT INTO repertoire_lines (id, chapter_id, line_name, moves, fen_sequence, active)"
        " VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)",
        (id, chapter_id, name, json.dumps(moves), json.dumps(fens), active),
    )
    return fens


def game(
    conn: psycopg.Connection[DictRow],
    gid: int,
    moves: list[str],
    *,
    color: str = "white",
    days_ago: float = 1,
    time_class: str = "rapid",
    variant: str = "standard",
    result: str = "loss",
) -> list[str]:
    fens = spine(None, moves)
    conn.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, url, played_at, variant, time_class, moves,"
        " fen_sequence, starting_fen)"
        " VALUES (%s, 'lichess', %s, %s, now() - make_interval(secs => %s), %s, %s, %s::jsonb, %s::jsonb, %s)",
        (
            gid,
            f"g{gid}",
            f"https://example.test/{gid}",
            days_ago * 86400,
            variant,
            time_class,
            json.dumps(moves),
            json.dumps(fens),
            START if variant == "chess960" else None,
        ),
    )
    conn.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, result, player_rating)"
        " VALUES (%s, %s, %s, 'lichess', %s, 1500)",
        (PLAYER_ID, gid, color, result),
    )
    return fens


def result(
    conn: psycopg.Connection[DictRow],
    gid: int,
    *,
    book_id: int,
    chapter_id: int,
    ply: int,
    by: str,
    expected: str | None,
    played: str | None,
    fen: str,
    line_ids: list[int],
) -> int:
    row: Any = conn.execute(
        "INSERT INTO game_repertoire_results (chess_game_id, player_id, book_id, chapter_id, deviated_at_ply,"
        " deviation_by, expected_move, played_move, deviation_fen) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (gid, PLAYER_ID, book_id, chapter_id, ply, by, expected, played, fen),
    ).fetchone()
    for lid in line_ids:
        conn.execute(
            "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply) VALUES (%s, %s, %s)",
            (row["id"], lid, ply),
        )
    return int(row["id"])
