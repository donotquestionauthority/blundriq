"""Review's positions: boards from the opening prefix every analysable game keeps.

A game contributes each board of its prefix (`chess_games.opening_keys`, the keys of the
positions before and after each of its first OPENING_PREFIX_PLIES moves) once: at the board's
first occurrence in the game, the start position at ply 0 included, so a board the game
returns to (a repetition, a piece that goes out and back) never adds a second row, and the
start position is never a board of its own. Only first occurrences at plies 1 to
`review_position_max_ply` are kept. Every statistic and every ply-based use (the line shown,
the board replayed for its FEN, the edges between boards) reads these rows.

The history is `review_history_months` back from the newest of Rob's analysable games (not
from now, so a break does not age everything at once). A board is a position when at least
`review_position_min_games` of his games reach it as one colour.
"""

from __future__ import annotations

from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import PLAYER_ID
from core.settings import Settings

# How many games a board may be rebuilt from before it counts as failed: one game whose stored
# prefix does not replay must not keep the board unevaluated (and the step failing) for ever.
EVAL_SOURCES = 3


def games_cte() -> str:
    """`games AS (...)`: Rob's analysable games with a prefix inside the history. Binds
    %(pid)s and %(months)s."""
    return f"""
    games AS (
        SELECT cg.id, pg.player_color, cg.opening_keys, cg.played_at
        FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")} AND cg.opening_keys IS NOT NULL
          AND cg.played_at >= (
              SELECT max(cg2.played_at) FROM player_games pg2 JOIN chess_games cg2 ON cg2.id = pg2.chess_game_id
              WHERE pg2.player_id = %(pid)s AND {analysable_sql("cg2")}
          ) - make_interval(months => %(months)s)
    )"""


def occurrence_ctes() -> str:
    """`first` (each board's first occurrence per game, ply 0 included) and `occ` (those at
    plies 1 to %(max_ply)s). Needs `games` before it."""
    return """
    first AS (
        SELECT DISTINCT ON (g.id, k.key) g.id, g.player_color, k.key, (k.ord - 1)::int AS ply
        FROM games g CROSS JOIN LATERAL unnest(g.opening_keys) WITH ORDINALITY AS k(key, ord)
        ORDER BY g.id, k.key, k.ord
    ),
    occ AS (SELECT id, player_color, key, ply FROM first WHERE ply BETWEEN 1 AND %(max_ply)s)"""


def _params(config: Settings) -> dict[str, Any]:
    return {
        "pid": PLAYER_ID,
        "months": config.review_history_months,
        "max_ply": config.review_position_max_ply,
        "min_games": config.review_position_min_games,
    }


def eval_candidates(conn: Connection[Any], config: Settings, limit: int | None) -> tuple[int, list[dict[str, Any]]]:
    """(how many positions still lack an evaluation, the first `limit` of them, or all when
    `limit` is None). A position is a board some colour's games reach at least min_games times;
    the most-played go first, then the lower key. Each row carries the board's sources: up to
    EVAL_SOURCES games reaching it, lowest id first, each with its first-occurrence ply of the
    board and its prefix moves, from which `core.review.evals` replays the board."""
    query = cast(
        LiteralString,
        f"""
        WITH {games_cte()}, {occurrence_ctes()},
        positions AS (
            SELECT key, max(n) AS n FROM (
                SELECT player_color, key, count(*) AS n FROM occ GROUP BY player_color, key
                HAVING count(*) >= %(min_games)s
            ) per_colour GROUP BY key
        ),
        pending AS (
            SELECT p.key, p.n FROM positions p
            WHERE NOT EXISTS (SELECT 1 FROM position_evals pe WHERE pe.board_key = p.key)
        ),
        chosen AS (SELECT key, n FROM pending ORDER BY n DESC, key LIMIT %(limit)s),
        src AS (
            SELECT o.key, o.id, o.ply, row_number() OVER (PARTITION BY o.key ORDER BY o.id) AS rn
            FROM occ o JOIN chosen c ON c.key = o.key
        )
        SELECT c.key, c.n, (SELECT count(*) FROM pending) AS pending_total,
               jsonb_agg(jsonb_build_object('ply', s.ply, 'moves', cg.opening_moves) ORDER BY s.rn) AS sources
        FROM chosen c
        JOIN src s ON s.key = c.key AND s.rn <= %(sources)s
        JOIN chess_games cg ON cg.id = s.id
        GROUP BY c.key, c.n
        ORDER BY c.n DESC, c.key
        """,
    )
    rows = conn.execute(query, {**_params(config), "limit": limit, "sources": EVAL_SOURCES}).fetchall()
    if not rows:  # LIMIT NULL is no limit, so an empty answer means nothing is pending
        return 0, []
    return int(rows[0]["pending_total"]), [dict(r) for r in rows]
