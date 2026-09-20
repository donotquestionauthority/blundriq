"""Retention. The analysis window (`analysis_game_limit` most recent games) is
the only window: analysis artefacts and repertoire results outside it are
deleted, and the bulk JSON payload (moves, fen_sequence, clocks, ply_analysis)
of a game no owner still has in-window is nulled. The metadata row stays, so
the Games page still lists every game ever imported.

Owners are the player (player_games) and scouted opponents (opponent_views,
Scout); both windows use the same limit.
"""

from __future__ import annotations

from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql, window_cte
from core.constants import PLAYER_ID

_WINDOW = f"WITH {window_cte()} "


def cleanup_analysis_beyond_window(conn: Connection[Any], window: int) -> dict[str, int]:
    """Delete blunders and motif events of aged-out games and reset their depth marker,
    in one transaction, so a later window increase re-analyses them."""
    ids = [
        r["chess_game_id"]
        for r in conn.execute(
            cast(
                LiteralString,
                _WINDOW
                + """
            SELECT pg.chess_game_id FROM player_games pg
            WHERE pg.player_id = %(pid)s
              AND pg.chess_game_id NOT IN (SELECT chess_game_id FROM window_games)
              AND (pg.analyzed_at_depth IS NOT NULL
                   OR EXISTS (SELECT 1 FROM blunders b
                              WHERE b.player_id = pg.player_id AND b.chess_game_id = pg.chess_game_id))
            """,
            ),
            {"pid": PLAYER_ID, "window": window},
        ).fetchall()
    ]
    if not ids:
        return {"games": 0, "blunders": 0, "motif_events": 0}
    with conn.transaction():
        b = conn.execute(
            "DELETE FROM blunders WHERE player_id = %s AND chess_game_id = ANY(%s)", (PLAYER_ID, ids)
        ).rowcount
        m = conn.execute(
            "DELETE FROM player_motif_events WHERE player_id = %s AND chess_game_id = ANY(%s)", (PLAYER_ID, ids)
        ).rowcount
        conn.execute(
            "UPDATE player_games SET analyzed_at_depth = NULL WHERE player_id = %s AND chess_game_id = ANY(%s)",
            (PLAYER_ID, ids),
        )
    return {"games": len(ids), "blunders": b, "motif_events": m}


def cleanup_repertoire_beyond_window(conn: Connection[Any], window: int) -> dict[str, int]:
    ids = [
        r["id"]
        for r in conn.execute(
            cast(
                LiteralString,
                _WINDOW
                + """
            SELECT grr.id FROM game_repertoire_results grr
            WHERE grr.player_id = %(pid)s AND grr.chess_game_id NOT IN (SELECT chess_game_id FROM window_games)
            """,
            ),
            {"pid": PLAYER_ID, "window": window},
        ).fetchall()
    ]
    if not ids:
        return {"results": 0, "lines": 0}
    with conn.transaction():
        lines = conn.execute("DELETE FROM game_result_lines WHERE game_repertoire_result_id = ANY(%s)", (ids,)).rowcount
        results = conn.execute("DELETE FROM game_repertoire_results WHERE id = ANY(%s)", (ids,)).rowcount
    return {"results": results, "lines": lines}


def cleanup_bulk_payload(conn: Connection[Any], window: int) -> int:
    """Null moves/fen_sequence/clocks/ply_analysis on games no owner has in-window."""
    with conn.transaction():
        query = cast(
            LiteralString,
            f"""
            WITH {window_cte()},
            opponent_ranked AS (
                SELECT ov.chess_game_id,
                       ROW_NUMBER() OVER (PARTITION BY ov.opponent_profile_id
                                          ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC) AS rn
                FROM opponent_views ov JOIN chess_games cg ON cg.id = ov.chess_game_id
                WHERE {analysable_sql("cg")}
            ),
            in_window AS (
                SELECT chess_game_id FROM window_games
                UNION
                SELECT chess_game_id FROM opponent_ranked WHERE rn <= %(window)s
            )
            UPDATE chess_games cg
            SET moves = NULL, fen_sequence = NULL, clocks = NULL, ply_analysis = NULL, ply_analysis_depth = NULL
            WHERE (cg.moves IS NOT NULL OR cg.fen_sequence IS NOT NULL OR cg.clocks IS NOT NULL
                   OR cg.ply_analysis IS NOT NULL)
              AND NOT EXISTS (SELECT 1 FROM in_window iw WHERE iw.chess_game_id = cg.id)
            """,
        )  # literal SQL plus the shared window/eligibility fragments; values are bound
        return conn.execute(query, {"pid": PLAYER_ID, "window": window}).rowcount


def cleanup_old_runs(conn: Connection[Any], days: int = 90) -> int:
    with conn.transaction():
        return conn.execute(
            "DELETE FROM pipeline_runs WHERE started_at < now() - make_interval(days => %s)", (days,)
        ).rowcount


def run(conn: Connection[Any], window: int) -> dict[str, Any]:
    """The step. Returns counts for the run log."""
    out: dict[str, Any] = {}
    out["analysis"] = cleanup_analysis_beyond_window(conn, window)
    out["repertoire"] = cleanup_repertoire_beyond_window(conn, window)
    out["payload_nulled"] = cleanup_bulk_payload(conn, window)
    out["old_runs"] = cleanup_old_runs(conn)
    conn.commit()
    return out
