"""Writes for imported games. The only code that inserts into chess_games.

`upsert_game` merges by (platform, platform_game_id) with a NULL → value
ratchet: a later import can fill fields the first left empty, never the
reverse. `upsert_player_game` is insert-or-ignore on (player_id, chess_game_id).
Callers own the transaction.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg import Connection

from core.chess.openings import canonical_opening
from core.constants import PLAYER_ID
from core.ingest.records import GameRecord


def _none_if_empty(value: str | None) -> str | None:
    return None if value == "" else value


def upsert_game(conn: Connection[Any], g: GameRecord) -> int:
    """Insert or merge one chess_games row; returns its id."""
    opening_name = _none_if_empty(g.opening_name)
    opening_eco = _none_if_empty(g.opening_eco)
    family, variation = canonical_opening(opening_name, opening_eco)
    row = conn.execute(
        """
        INSERT INTO chess_games (
            platform, platform_game_id, url, played_at, time_control, opening_name, opening_eco,
            moves, fen_sequence, clocks, termination, variant, starting_fen, time_class,
            canonical_family, canonical_variation
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (platform, platform_game_id) DO UPDATE
        SET url          = COALESCE(chess_games.url, EXCLUDED.url),
            played_at    = COALESCE(chess_games.played_at, EXCLUDED.played_at),
            time_control = COALESCE(chess_games.time_control, EXCLUDED.time_control),
            opening_name = COALESCE(chess_games.opening_name, EXCLUDED.opening_name),
            opening_eco  = COALESCE(chess_games.opening_eco, EXCLUDED.opening_eco),
            moves        = COALESCE(chess_games.moves, EXCLUDED.moves),
            fen_sequence = COALESCE(chess_games.fen_sequence, EXCLUDED.fen_sequence),
            clocks       = COALESCE(chess_games.clocks, EXCLUDED.clocks),
            termination  = COALESCE(chess_games.termination, EXCLUDED.termination),
            variant      = EXCLUDED.variant,
            starting_fen = COALESCE(chess_games.starting_fen, EXCLUDED.starting_fen),
            time_class   = COALESCE(chess_games.time_class, EXCLUDED.time_class)
        RETURNING id, opening_name, opening_eco, canonical_family, canonical_variation
        """,
        (
            g.platform,
            g.platform_game_id,
            _none_if_empty(g.url),
            g.played_at,
            _none_if_empty(g.time_control),
            opening_name,
            opening_eco,
            json.dumps(g.moves),
            json.dumps(g.fen_sequence),
            json.dumps(g.clocks) if g.clocks is not None else None,
            _none_if_empty(g.termination),
            g.variant,
            g.starting_fen,
            _none_if_empty(g.time_class),
            family,
            variation,
        ),
    ).fetchone()
    assert row is not None
    # The merge may have filled opening fields the stored row lacked; keep the
    # canonical pair in step with the merged name/eco.
    want = canonical_opening(row["opening_name"], row["opening_eco"])
    if (row["canonical_family"], row["canonical_variation"]) != want:
        conn.execute(
            "UPDATE chess_games SET canonical_family = %s, canonical_variation = %s WHERE id = %s",
            (want[0], want[1], row["id"]),
        )
    return int(row["id"])


def upsert_player_game(conn: Connection[Any], chess_game_id: int, g: GameRecord) -> bool:
    """True iff a new player_games row was inserted."""
    cur = conn.execute(
        """
        INSERT INTO player_games (player_id, chess_game_id, player_color, opponent_username,
                                  opponent_rating, player_rating, source, result, no_repertoire_match)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)
        ON CONFLICT (player_id, chess_game_id) DO NOTHING
        """,
        (
            PLAYER_ID,
            chess_game_id,
            g.player_color,
            g.opponent_username,
            g.opponent_rating,
            g.player_rating,
            g.platform,
            g.result,
        ),
    )
    return cur.rowcount > 0


def store_game(conn: Connection[Any], g: GameRecord) -> bool:
    """Both writes for one game inside a savepoint; True iff the game is new for the player."""
    with conn.transaction():
        game_id = upsert_game(conn, g)
        return upsert_player_game(conn, game_id, g)
