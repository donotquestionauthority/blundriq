"""Reads and resets used by tools/oracle to compare this pipeline with the old
database's rows. Every query about games goes through the eligibility rule in
core/chess/eligibility.py; the tools only orchestrate and diff.

The "oracle" is a local restore of the old database (ORACLE_DATABASE_URL); the
"scratch" database is a fresh `pipeline db init` + `pipeline migrate` of it
(DATABASE_URL), which these resets modify.
"""

from __future__ import annotations

from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import PLAYER_ID, STOCKFISH_DEPTH

RESULT_FIELDS = (
    "book_id",
    "chapter_id",
    "deviated_at_ply",
    "deviation_by",
    "expected_move",
    "played_move",
    "deviation_fen",
)
BLUNDER_FIELDS = (
    "ply",
    "move_played",
    "best_move",
    "best_line",
    "post_blunder_line",
    "centipawn_loss",
    "classification",
    "phase",
    "themes",
)
EVENT_FIELDS = ("ply", "metric_type", "theme", "found", "mate_in_moves", "cp_loss")

_ANALYSED_GAME = cast(
    LiteralString,
    f"""
    SELECT cg.id FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
    WHERE pg.player_id = %(pid)s AND pg.analyzed_at_depth = %(depth)s AND {analysable_sql("cg")}
      AND cg.moves IS NOT NULL AND cg.ply_analysis IS NOT NULL
""",
)


def analysed_game_ids(conn: Connection[Any]) -> list[int]:
    """Games with a complete stored analysis at the working depth, by id."""
    rows = conn.execute(_ANALYSED_GAME + " ORDER BY cg.id", {"pid": PLAYER_ID, "depth": STOCKFISH_DEPTH}).fetchall()
    return [int(r["id"]) for r in rows]


# The named edge cases of the fixed comparison sample: (predicate over aliases cg/pg, ordering).
_EDGE_CASES: dict[str, tuple[LiteralString, LiteralString]] = {
    "checkmate_win": ("cg.termination = 'checkmate' AND pg.result = 'win'", "cg.id"),
    "promotion": ("cg.moves::text LIKE '%%=Q%%'", "cg.id"),
    "clean": (
        "NOT EXISTS (SELECT 1 FROM blunders b WHERE b.player_id = pg.player_id AND b.chess_game_id = cg.id)",
        "cg.id",
    ),
    "missed_mate": (
        "EXISTS (SELECT 1 FROM player_motif_events e WHERE e.player_id = pg.player_id AND e.chess_game_id = cg.id"
        " AND e.metric_type = 'mate' AND e.found IS FALSE)",
        "cg.id",
    ),
    "longest": ("TRUE", "jsonb_array_length(cg.moves) DESC"),
    "opponent_deviated": (
        "EXISTS (SELECT 1 FROM game_repertoire_results g WHERE g.player_id = pg.player_id"
        " AND g.chess_game_id = cg.id AND g.deviation_by = 'opponent')",
        "cg.id",
    ),
    "followed_line": (
        "EXISTS (SELECT 1 FROM game_repertoire_results g WHERE g.player_id = pg.player_id"
        " AND g.chess_game_id = cg.id AND g.deviation_by = 'none')",
        "cg.id",
    ),
}


def edge_case_games(conn: Connection[Any]) -> dict[str, int]:
    """One analysed game per named edge case (a case with no game is left out)."""
    out: dict[str, int] = {}
    for name, (predicate, order) in _EDGE_CASES.items():
        row = conn.execute(
            _ANALYSED_GAME + f" AND ({predicate}) ORDER BY {order} LIMIT 1",
            {"pid": PLAYER_ID, "depth": STOCKFISH_DEPTH},
        ).fetchone()
        if row:
            out[name] = int(row["id"])
    return out


def games_for_replay(conn: Connection[Any], ids: list[int]) -> list[dict[str, Any]]:
    """Analysed games with their stored per-ply series, for the engine-free replay."""
    return conn.execute(
        cast(
            LiteralString,
            f"""
            SELECT cg.id, cg.moves, cg.ply_analysis, cg.opening_eco, cg.variant, cg.starting_fen, pg.player_color
            FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
            WHERE pg.player_id = %(pid)s AND pg.analyzed_at_depth = %(depth)s AND {analysable_sql("cg")}
              AND cg.moves IS NOT NULL AND cg.ply_analysis IS NOT NULL AND cg.id = ANY(%(ids)s) ORDER BY cg.id
            """,
        ),
        {"pid": PLAYER_ID, "depth": STOCKFISH_DEPTH, "ids": ids},
    ).fetchall()


def _by_game(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(int(r["chess_game_id"]), []).append(dict(r))
    return out


def match_results(conn: Connection[Any], ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT g.chess_game_id, g.book_id, g.chapter_id, g.deviated_at_ply, g.deviation_by, g.expected_move,
               g.played_move, g.deviation_fen,
               (SELECT array_agg(l.line_id ORDER BY l.line_id) FROM game_result_lines l
                 WHERE l.game_repertoire_result_id = g.id) AS lines
        FROM game_repertoire_results g WHERE g.player_id = %s AND g.chess_game_id = ANY(%s)
        """,
        (PLAYER_ID, ids),
    ).fetchall()
    return _by_game(rows)


def no_match_flags(conn: Connection[Any], ids: list[int]) -> dict[int, bool]:
    rows = conn.execute(
        "SELECT chess_game_id, no_repertoire_match FROM player_games WHERE player_id = %s AND chess_game_id = ANY(%s)",
        (PLAYER_ID, ids),
    ).fetchall()
    return {int(r["chess_game_id"]): bool(r["no_repertoire_match"]) for r in rows}


def decided_game_ids(conn: Connection[Any]) -> list[int]:
    """Games with a match decision either way (a result row or the no-match flag)."""
    rows = conn.execute(
        """
        SELECT chess_game_id FROM player_games pg WHERE pg.player_id = %s AND (pg.no_repertoire_match OR EXISTS
            (SELECT 1 FROM game_repertoire_results g
              WHERE g.player_id = pg.player_id AND g.chess_game_id = pg.chess_game_id))
        ORDER BY chess_game_id
        """,
        (PLAYER_ID,),
    ).fetchall()
    return [int(r["chess_game_id"]) for r in rows]


def forget_match_decisions(conn: Connection[Any]) -> None:
    """Scratch only: every game becomes an unmatched candidate again."""
    with conn.transaction():
        conn.execute("DELETE FROM game_result_lines")
        conn.execute("DELETE FROM game_repertoire_results WHERE player_id = %s", (PLAYER_ID,))
        conn.execute("UPDATE player_games SET no_repertoire_match = FALSE WHERE player_id = %s", (PLAYER_ID,))


def blunders(conn: Connection[Any], ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT chess_game_id, ply, move_played, best_move, best_line, post_blunder_line, centipawn_loss, classification,
               phase, themes
        FROM blunders WHERE player_id = %s AND chess_game_id = ANY(%s) ORDER BY chess_game_id, ply
        """,
        (PLAYER_ID, ids),
    ).fetchall()
    return _by_game(rows)


def motif_events(conn: Connection[Any], ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT chess_game_id, ply, metric_type, theme, found, mate_in_moves, cp_loss FROM player_motif_events
        WHERE player_id = %s AND chess_game_id = ANY(%s) AND metric_type <> 'endgame'
        ORDER BY chess_game_id, ply, metric_type, theme
        """,
        (PLAYER_ID, ids),
    ).fetchall()
    return _by_game(rows)


def forget_analysis(conn: Connection[Any], ids: list[int]) -> None:
    """Scratch only: remove these games' analysis so a rerun must produce every row afresh."""
    with conn.transaction():
        conn.execute("DELETE FROM blunders WHERE player_id = %s AND chess_game_id = ANY(%s)", (PLAYER_ID, ids))
        conn.execute(
            "DELETE FROM player_motif_events WHERE player_id = %s AND chess_game_id = ANY(%s)", (PLAYER_ID, ids)
        )
        conn.execute(
            "UPDATE player_games SET analyzed_at_depth = NULL WHERE player_id = %s AND chess_game_id = ANY(%s)",
            (PLAYER_ID, ids),
        )
        conn.execute(
            "UPDATE chess_games SET analysis_depth = NULL, ply_analysis = NULL, ply_analysis_depth = NULL,"
            " analysis_status = 'unanalyzed' WHERE id = ANY(%s)",
            (ids,),
        )


def freshly_analysed(conn: Connection[Any], ids: list[int]) -> list[int]:
    """Of these games, the ones now carrying a complete analysis at the working depth."""
    rows = conn.execute(
        """
        SELECT pg.chess_game_id FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %s AND pg.chess_game_id = ANY(%s) AND pg.analyzed_at_depth = %s
          AND cg.ply_analysis_depth = %s AND cg.ply_analysis IS NOT NULL
        ORDER BY pg.chess_game_id
        """,
        (PLAYER_ID, ids, STOCKFISH_DEPTH, STOCKFISH_DEPTH),
    ).fetchall()
    return [int(r["chess_game_id"]) for r in rows]
