"""Builders for analysed games in the scratch database, shared by the review tests."""

from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from tests import repertoire_helpers as h
from tests.review.test_detect import KNOBS, QH_MOVES, make_ctx, qh_ctx

__all__ = ["KNOBS", "QH_MOVES", "make_ctx", "qh_ctx"]


def analysed_game(
    conn: psycopg.Connection[DictRow],
    gid: int,
    ctx: dict[str, Any],
    *,
    days_ago: float = 1,
    depth: int | None = 18,
    variant: str = "standard",
    termination: str = "checkmate",
) -> None:
    """A game row carrying the context a detector fixture built: moves, FEN sequence and
    ply analysis as the analyser stores them, analysed at `depth` (None: not analysed)."""
    h.player(conn)
    h.game(conn, gid, list(ctx["moves"]), color=ctx["player_color"], days_ago=days_ago, variant=variant)
    conn.execute(
        "UPDATE chess_games SET fen_sequence = %s::jsonb, ply_analysis = %s::jsonb, ply_analysis_depth = %s,"
        " termination = %s, opening_eco = %s, starting_fen = %s WHERE id = %s",
        (
            json.dumps(ctx["fen_sequence"]),
            json.dumps(ctx["ply_analysis"]) if ctx["ply_analysis"] is not None else None,
            depth,
            termination,
            ctx.get("opening_eco"),
            ctx["fen_sequence"][0] if variant == "chess960" else None,
            gid,
        ),
    )
    conn.execute(
        "UPDATE player_games SET analyzed_at_depth = %s, result = %s WHERE chess_game_id = %s AND player_id = %s",
        (depth, ctx["result"], gid, PLAYER_ID),
    )


def events_of(conn: psycopg.Connection[DictRow], gid: int) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT anchor_ply, base_route, opening_candidate, pool_key, cost, config_version, first_detected_at,"
        " meaning_changed_at, board_key FROM review_events WHERE player_id = %s AND chess_game_id = %s"
        " ORDER BY anchor_ply",
        (PLAYER_ID, gid),
    ).fetchall()


def plant_event(
    conn: psycopg.Connection[DictRow], gid: int, anchor_ply: int, fen: str, *, route: str = "faded"
) -> None:
    conn.execute(
        "INSERT INTO review_events (player_id, chess_game_id, anchor_ply, config_version, base_route, evidence, cost,"
        " board_key, first_detected_at) VALUES (%s, %s, %s, 1, %s, '{}'::jsonb, 1, bq_position_key(%s), now())",
        (PLAYER_ID, gid, anchor_ply, route, fen),
    )
