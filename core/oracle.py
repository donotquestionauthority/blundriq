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


# --- puzzles -----------------------------------------------------------------------------

GENERATED_SOURCES = ("blunder", "own_mate", "deviation")

_PUZZLE_FIELDS = (
    "id, canonical_fen, fen, source_types, themes, solution_line, color, active,"
    " is_repertoire, repertoire_line_id, acceptance_map"
)


def generated_puzzles(conn: Connection[Any], *, old_schema: bool = False) -> list[dict[str, Any]]:
    """Active puzzles a generator produced, on either side of the port.

    `old_schema` reads the archived database, where puzzles carry a `puzzle_kind` and the
    player's own rows sit beside a shared tier, and a hand-made puzzle is tagged 'manual'
    rather than 'custom'.
    """
    query: LiteralString
    if old_schema:
        query = (
            f"SELECT {_PUZZLE_FIELDS} FROM puzzles"
            " WHERE player_id = %s AND puzzle_kind = 'line' AND active = TRUE"
            "   AND source_types && %s::text[] AND NOT source_types @> ARRAY['manual']"
            " ORDER BY id"
        )
        params: tuple[Any, ...] = (PLAYER_ID, list(GENERATED_SOURCES))
    else:
        query = (
            f"SELECT {_PUZZLE_FIELDS} FROM puzzles"
            " WHERE active = TRUE AND source_types && %s::text[]"
            "   AND NOT source_types @> ARRAY['custom']"
            " ORDER BY id"
        )
        params = (list(GENERATED_SOURCES),)
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def acceptance_maps(conn: Connection[Any], *, old_schema: bool = False) -> list[dict[str, Any]]:
    """Every stored missed-mate map, active or not. Inactive rows are still graded when a
    mastered puzzle is re-attempted, so they belong in a parity check."""
    owner: LiteralString = " AND player_id = %s AND puzzle_kind = 'line'" if old_schema else ""
    query: LiteralString = (
        "SELECT id, fen, acceptance_map FROM puzzles"
        f" WHERE source_types @> ARRAY['own_mate'] AND acceptance_map IS NOT NULL{owner}"
        " ORDER BY id"
    )
    params: tuple[Any, ...] = (PLAYER_ID,) if old_schema else ()
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def forget_generated_puzzles(conn: Connection[Any]) -> int:
    """Scratch only: delete every generated puzzle so a rerun must produce them afresh.

    This cascades to the attempts and the spaced-repetition rows behind those puzzles, so
    `require_scratch_database` guards it and the caller is a disposable copy.
    """
    require_scratch_database(conn)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM puzzles WHERE source_types && %s::text[] AND NOT source_types @> ARRAY['custom']",
            (list(GENERATED_SOURCES),),
        )
        return cur.rowcount


def require_scratch_database(conn: Connection[Any]) -> None:
    """Refuse to continue unless this database is named as a throwaway.

    Deleting a puzzle takes its spaced-repetition row with it, and that progress is the one
    thing in the database that cannot be rebuilt. A name check is a weak signal, so it
    exists to catch a mistake, not to make the operation safe.
    """
    row = conn.execute("SELECT current_database() AS name").fetchone()
    name = str(row["name"]) if row else ""
    if "scratch" not in name:
        raise RuntimeError(
            f"refusing to rewrite puzzles in '{name}': this deletes puzzles, which cascades to"
            " spaced-repetition progress. Point DATABASE_URL at a disposable copy whose name"
            " contains 'scratch'."
        )


# --- practice --------------------------------------------------------------------------------


def solved_attempts(conn: Connection[Any]) -> list[dict[str, Any]]:
    """Every attempt recorded as solved, with what it was graded against. A replay through
    the current grader must agree with every one of them."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT a.id, a.puzzle_id, a.moves_played, a.attempt_at, p.fen, p.solution_line, p.color,"
            " p.acceptance_map, p.source_types, p.is_repertoire, p.updated_at AS puzzle_updated_at"
            " FROM puzzle_attempts a JOIN puzzles p ON p.id = a.puzzle_id"
            " WHERE a.player_id = %s AND a.solved = TRUE AND a.moves_played IS NOT NULL ORDER BY a.id",
            (PLAYER_ID,),
        ).fetchall()
    ]


def old_visible_ids(conn: Connection[Any], lookahead_plies: int) -> set[int]:
    """The puzzles the old system would show the player, computed on the archived schema
    with the old rules: active line puzzles he owns or that his sources reach, not
    contradicted by the repertoire, not redundant with it, not dismissed; repertoire
    puzzles with three deviations, one per presented position."""
    n = int(lookahead_plies)
    standard = conn.execute(
        """
        WITH rep_steps AS (
            SELECT CASE bk.color WHEN 'white' THEN 'w' ELSE 'b' END AS book_color, step.fen AS position_fen,
                   rl.fen_sequence->>(step.ord::int) AS next_fen
            FROM repertoire_lines rl JOIN chapters ch ON ch.id = rl.chapter_id JOIN books bk ON bk.id = ch.book_id,
            LATERAL jsonb_array_elements_text(rl.fen_sequence) WITH ORDINALITY AS step(fen, ord)
            WHERE bk.player_id = %(pid)s AND bk.active AND ch.active AND rl.active
              AND step.ord < jsonb_array_length(rl.fen_sequence)
        ),
        steps AS (
            SELECT p.id AS puzzle_id, p.color, step.fen AS position_fen,
                   p.solution_fen_sequence->>(step.ord::int) AS next_fen
            FROM puzzles p, LATERAL jsonb_array_elements_text(p.solution_fen_sequence) WITH ORDINALITY AS step(fen, ord)
            WHERE p.active AND p.is_repertoire = FALSE AND p.puzzle_kind = 'line'
              AND step.ord < jsonb_array_length(p.solution_fen_sequence) AND split_part(step.fen, ' ', 2) = p.color
        ),
        conflicted AS (
            SELECT DISTINCT s.puzzle_id FROM steps s JOIN rep_steps r
              ON r.position_fen = s.position_fen AND r.book_color = s.color
            WHERE r.next_fen IS NOT NULL AND s.next_fen IS NOT NULL AND r.next_fen <> s.next_fen
        ),
        sources AS (
            SELECT canonical_fen FROM blunders WHERE player_id = %(pid)s
            UNION SELECT canonical_fen FROM game_repertoire_results
            WHERE player_id = %(pid)s AND deviation_by = 'me' AND deviated_at_ply IS NOT NULL
        )
        SELECT p.id FROM puzzles p
        WHERE p.active AND p.is_repertoire = FALSE AND p.puzzle_kind = 'line'
          AND (p.player_id = %(pid)s
               OR (p.player_id IS NULL AND p.canonical_fen IN (SELECT canonical_fen FROM sources)))
          AND NOT (p.source_types @> ARRAY['own_mate'] AND p.acceptance_map IS NULL)
          AND NOT EXISTS (SELECT 1 FROM conflicted c WHERE c.puzzle_id = p.id)
          AND NOT (p.source_types @> ARRAY['blunder'] AND EXISTS (
                SELECT 1 FROM rep_steps r WHERE r.position_fen = p.fen AND r.book_color = p.color))
          AND NOT EXISTS (SELECT 1 FROM dismissed_blunder_fens d
                          WHERE d.player_id = %(pid)s AND d.canonical_fen = p.canonical_fen)
        """,
        {"pid": PLAYER_ID},
    ).fetchall()
    repertoire = conn.execute(
        cast(
            LiteralString,
            f"""
        WITH line_stats AS (
            SELECT grl.line_id, count(DISTINCT grr.chess_game_id) AS event_count,
                   max(grr.deviated_at_ply) AS furthest_ply
            FROM game_result_lines grl
            JOIN game_repertoire_results grr ON grr.id = grl.game_repertoire_result_id
            WHERE grr.player_id = %(pid)s AND grr.deviation_by = 'me' AND grr.deviated_at_ply IS NOT NULL
            GROUP BY grl.line_id HAVING count(DISTINCT grr.chess_game_id) >= 3
        ),
        candidates AS (
            SELECT p.id, ls.event_count,
                   p.solution_fen_sequence->>LEAST(ls.furthest_ply + {n},
                       (jsonb_array_length(p.solution_line) - 1)
                         - ((jsonb_array_length(p.solution_line) - 1 - ls.furthest_ply) %% 2))::int AS presentation_fen
            FROM puzzles p
            JOIN line_stats ls ON ls.line_id = p.repertoire_line_id
            JOIN repertoire_lines rl ON rl.id = p.repertoire_line_id
            JOIN chapters ch ON ch.id = rl.chapter_id JOIN books bk ON bk.id = ch.book_id
            WHERE p.active AND p.is_repertoire = TRUE AND p.player_id = %(pid)s
              AND bk.player_id = %(pid)s AND rl.active AND ch.active AND bk.active
        )
        SELECT DISTINCT ON (presentation_fen) id FROM candidates ORDER BY presentation_fen, event_count DESC, id
        """,
        ),
        {"pid": PLAYER_ID},
    ).fetchall()
    return {int(r["id"]) for r in standard} | {int(r["id"]) for r in repertoire}


def old_due_ids(conn: Connection[Any], visible: set[int]) -> set[int]:
    """Of the visible set, those the old system counted as due right now."""
    if not visible:
        return set()
    rows = conn.execute(
        "SELECT puzzle_id, level, next_show_at FROM player_puzzle_state WHERE player_id = %s AND puzzle_id = ANY(%s)",
        (PLAYER_ID, sorted(visible)),
    ).fetchall()
    now = conn.execute("SELECT NOW() AS now").fetchone()
    assert now is not None
    state = {int(r["puzzle_id"]): r for r in rows}
    return {
        pid
        for pid in visible
        if pid not in state or (state[pid]["level"] != "king" and state[pid]["next_show_at"] <= now["now"])
    }
