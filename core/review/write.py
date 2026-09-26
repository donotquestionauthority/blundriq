"""The per-game replace of review events, and the run's bookkeeping rows.

Timestamps are the old writer's: `first_detected_at` survives a recompute (discovery time
is kept); `meaning_changed_at` is stamped only when a stored, filter-independent fact —
the route, the opening-candidate flag, the pool key — changed, so a repriced event does not
resurface; a removed event is gone, and its later return is a fresh discovery. The delete
and the insert are one statement, so no reader ever aggregates two generations of a game.
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
        DELETE FROM review_events WHERE player_id = %(pid)s AND chess_game_id = %(gid)s
        RETURNING anchor_ply, base_route, opening_candidate, pool_key, first_detected_at, meaning_changed_at
    )
    INSERT INTO review_events
        (player_id, chess_game_id, anchor_ply, config_version, base_route, opening_candidate, pool_key,
         evidence, cost, phase, piece_label, book_relation, board_key, first_detected_at, meaning_changed_at)
    SELECT %(pid)s, %(gid)s, new.anchor_ply, %(cv)s, new.base_route, new.opening_candidate, new.pool_key,
           new.evidence, new.cost, new.phase, new.piece_label, new.book_relation, bq_position_key(new.anchor_fen),
           COALESCE(old.first_detected_at, now()),
           CASE WHEN old.anchor_ply IS NOT NULL
                 AND (old.base_route, old.opening_candidate, COALESCE(old.pool_key, ''))
                     IS DISTINCT FROM (new.base_route, new.opening_candidate, COALESCE(new.pool_key, ''))
                THEN now() ELSE old.meaning_changed_at END
    FROM unnest(%(plies)s::int[], %(routes)s::text[], %(cands)s::boolean[], %(pkeys)s::text[], %(evs)s::jsonb[],
                %(costs)s::numeric[], %(phases)s::text[], %(pieces)s::text[], %(brels)s::text[], %(fens)s::text[])
         AS new(anchor_ply, base_route, opening_candidate, pool_key, evidence, cost, phase, piece_label,
                book_relation, anchor_fen)
    LEFT JOIN old ON old.anchor_ply = new.anchor_ply
"""


def replace_game(conn: Connection[Any], game_id: int, events: list[dict[str, Any]]) -> int:
    """Replace one game's rows with `events` (possibly none). Returns the rows written."""
    if not events:
        conn.execute(
            "DELETE FROM review_events WHERE player_id = %(pid)s AND chess_game_id = %(gid)s",
            {"pid": PLAYER_ID, "gid": game_id},
        )
        return 0
    cur = conn.execute(
        _REPLACE,
        {
            "pid": PLAYER_ID,
            "gid": game_id,
            "cv": REVIEW_CONFIG_VERSION,
            "plies": [int(e["anchor_ply"]) for e in events],
            "routes": [e["base_route"] for e in events],
            "cands": [bool(e["opening_candidate"]) for e in events],
            "pkeys": [e["pool_key"] for e in events],
            "evs": [json.dumps(e["evidence"]) for e in events],
            "costs": [e["cost"] for e in events],
            "phases": [e["phase"] for e in events],
            "pieces": [e["piece_label"] for e in events],
            "brels": [e["book_relation"] for e in events],
            "fens": [e["anchor_fen"] for e in events],
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
