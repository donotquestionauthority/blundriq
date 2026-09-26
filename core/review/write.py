"""The replace of review events, game by game in one statement, and the run's bookkeeping rows.

Timestamps are the old writer's: `first_detected_at` survives a recompute (discovery time
is kept); `meaning_changed_at` is stamped only when a stored, filter-independent fact —
the route, the opening-candidate flag, the pool key — changed, so a repriced event does not
resurface; a removed event is gone, and its later return is a fresh discovery. The delete
and the insert are one statement, so no reader ever aggregates two generations.
`config_version` is a lineage stamp, never an identity: the replace is unconditional.

Runs inside the caller's transaction (the run's, under both locks); nothing here commits.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg import Connection

from core.constants import PLAYER_ID
from core.review.detect import REVIEW_CONFIG_VERSION

_REPLACE = """
    WITH old AS (
        DELETE FROM review_events WHERE player_id = %(pid)s AND chess_game_id = ANY(%(gids)s::bigint[])
        RETURNING chess_game_id, anchor_ply, base_route, opening_candidate, pool_key, first_detected_at,
                  meaning_changed_at
    )
    INSERT INTO review_events
        (player_id, chess_game_id, anchor_ply, config_version, base_route, opening_candidate, pool_key,
         evidence, cost, phase, piece_label, book_relation, board_key, first_detected_at, meaning_changed_at)
    SELECT %(pid)s, new.chess_game_id, new.anchor_ply, %(cv)s, new.base_route, new.opening_candidate, new.pool_key,
           new.evidence, new.cost, new.phase, new.piece_label, new.book_relation, bq_position_key(new.anchor_fen),
           COALESCE(old.first_detected_at, now()),
           CASE WHEN old.chess_game_id IS NOT NULL
                 AND (old.base_route, old.opening_candidate, COALESCE(old.pool_key, ''))
                     IS DISTINCT FROM (new.base_route, new.opening_candidate, COALESCE(new.pool_key, ''))
                THEN now() ELSE old.meaning_changed_at END
    FROM unnest(%(egids)s::bigint[], %(plies)s::int[], %(routes)s::text[], %(cands)s::boolean[], %(pkeys)s::text[],
                %(evs)s::jsonb[], %(costs)s::numeric[], %(phases)s::text[], %(pieces)s::text[], %(brels)s::text[],
                %(fens)s::text[])
         AS new(chess_game_id, anchor_ply, base_route, opening_candidate, pool_key, evidence, cost, phase,
                piece_label, book_relation, anchor_fen)
    LEFT JOIN old ON old.chess_game_id = new.chess_game_id AND old.anchor_ply = new.anchor_ply
"""


def replace_games(conn: Connection[Any], tagged: dict[int, list[dict[str, Any]]]) -> int:
    """Replace the rows of every game in `tagged` (its events, possibly none) in one statement:
    one round trip for the whole generation, not one per game, since the run holds two locks
    for as long as this takes. A game absent from `tagged` (one the detector could not tag)
    is not touched. Returns the rows written."""
    if not tagged:
        return 0
    gids = sorted(tagged)
    ordered = [(gid, e) for gid in gids for e in tagged[gid]]
    if not ordered:
        conn.execute(
            "DELETE FROM review_events WHERE player_id = %(pid)s AND chess_game_id = ANY(%(gids)s::bigint[])",
            {"pid": PLAYER_ID, "gids": gids},
        )
        return 0
    cur = conn.execute(
        _REPLACE,
        {
            "pid": PLAYER_ID,
            "gids": gids,
            "cv": REVIEW_CONFIG_VERSION,
            "egids": [gid for gid, _ in ordered],
            "plies": [int(e["anchor_ply"]) for _, e in ordered],
            "routes": [e["base_route"] for _, e in ordered],
            "cands": [bool(e["opening_candidate"]) for _, e in ordered],
            "pkeys": [e["pool_key"] for _, e in ordered],
            "evs": [json.dumps(e["evidence"]) for _, e in ordered],
            "costs": [e["cost"] for _, e in ordered],
            "phases": [e["phase"] for _, e in ordered],
            "pieces": [e["piece_label"] for _, e in ordered],
            "brels": [e["book_relation"] for _, e in ordered],
            "fens": [e["anchor_fen"] for _, e in ordered],
        },
    )
    return cur.rowcount


def delete_outside(conn: Connection[Any], keep_ids: list[int]) -> int:
    """Drop the rows of every game not in `keep_ids` (the window, failed games included, so
    a game that could not be tagged keeps its prior rows). Returns the rows deleted."""
    cur = conn.execute(
        "DELETE FROM review_events WHERE player_id = %(pid)s AND chess_game_id <> ALL(%(ids)s::bigint[])",
        {"pid": PLAYER_ID, "ids": keep_ids},
    )
    return cur.rowcount


def record_state(conn: Connection[Any], unknown: int, window_games: int) -> None:
    """The run's honesty count and window size, one row."""
    conn.execute(
        """
        INSERT INTO review_detection_state (player_id, config_version, unknown_candidates, window_games)
        VALUES (%(pid)s, %(cv)s, %(unknown)s, %(games)s)
        ON CONFLICT (player_id) DO UPDATE
        SET config_version = EXCLUDED.config_version, unknown_candidates = EXCLUDED.unknown_candidates,
            window_games = EXCLUDED.window_games, computed_at = now()
        """,
        {"pid": PLAYER_ID, "cv": REVIEW_CONFIG_VERSION, "unknown": unknown, "games": window_games},
    )
