"""Review's positions: boards from the opening prefix every analysable game keeps.

A game contributes each board of its prefix (`chess_games.opening_keys`, the keys of the
positions before and after each of its first OPENING_PREFIX_PLIES moves) once: at the board's
first occurrence in the game, the start position at ply 0 included, so a board the game
returns to (a repetition, a piece that goes out and back) never adds a second row, and the
start position is never a board of its own. Only first occurrences at plies 1 to
`review_position_max_ply` are kept. Every statistic and every ply-based use (the line shown,
the board replayed for its FEN, the edges between boards) reads these rows.

The history is `review_history_months` calendar months back from the newest of Rob's
analysable games (not from now, so a break does not age everything at once). A board is a position when at least
`review_position_min_games` of his games reach it as one colour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.board import replay
from core.chess.eligibility import analysable_sql
from core.constants import (
    PLAYER_ID,
    REVIEW_COST_SHARE,
    REVIEW_FIXED_MAX,
    REVIEW_FIXED_MIN_EFFECTIVE,
    REVIEW_FIXED_MIN_RECENT,
    REVIEW_FORCED_SHARE,
    REVIEW_LEAK_THRESHOLD,
    REVIEW_MONTH_MIN_GAMES,
    REVIEW_RANKED_MAX,
    REVIEW_RECENT_DAYS,
    REVIEW_SHRINK_GAMES,
    REVIEW_STALE_RECENT_GAMES,
)
from core.review.detect import position_es
from core.review.filters import Opening, opening_key, opening_params, opening_sql, time_class_sql
from core.settings import Settings

# How many games a board may be rebuilt from before it counts as failed: one game whose stored
# prefix does not replay must not keep the board unevaluated (and the step failing) for ever.
EVAL_SOURCES = 3


def history_start_sql() -> str:
    """The first instant of the history, as a scalar SQL expression: the newest of Rob's
    analysable games minus %(months)s CALENDAR months, counted on the UTC calendar so the
    session's time zone never moves it (a month back from 31 March is 28 or 29 February, as
    Postgres's interval arithmetic says). NULL when he has no analysable game. Binds %(pid)s
    and %(months)s. The backfill reads the same expression (`history_start`), so it reaches
    exactly the games these statistics count."""
    return f"""(
        (SELECT max(cg_h.played_at) FROM player_games pg_h JOIN chess_games cg_h ON cg_h.id = pg_h.chess_game_id
         WHERE pg_h.player_id = %(pid)s AND {analysable_sql("cg_h")}) AT TIME ZONE 'UTC'
        - make_interval(months => %(months)s)
    ) AT TIME ZONE 'UTC'"""


def history_start(conn: Connection[Any], months: int) -> datetime | None:
    """`history_start_sql()` evaluated: the inclusive lower bound of `played_at` in the history."""
    query = cast(LiteralString, f"SELECT {history_start_sql()} AS start")
    row = conn.execute(query, {"pid": PLAYER_ID, "months": months}).fetchone()
    return row["start"] if row else None


def games_cte() -> str:
    """`games AS (...)`: Rob's analysable games with a prefix inside the history (from
    `history_start_sql()`, inclusive). Binds %(pid)s and %(months)s."""
    return f"""
    games AS (
        SELECT cg.id, pg.player_color, cg.opening_keys, cg.played_at
        FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")} AND cg.opening_keys IS NOT NULL
          AND cg.played_at >= {history_start_sql()}
    )"""


def occurrence_ctes(*, keyed: bool = False, scored: bool = False) -> str:
    """`first` (each board's first occurrence per game, ply 0 included) and `occ` (those at
    plies 1 to %(max_ply)s). Needs `games` before it.

    `keyed`: only the boards in %(keys)s. A board's first occurrence in a game does not depend
    on the game's other boards, so these are the same rows for those boards, read only from the
    games that reach one of them. `scored`: each row carries its game's `s`, `e` and `age` (the
    ranking's `games` has them)."""
    carried = ", g.s, g.e, g.age" if scored else ""
    keys = "WHERE g.opening_keys && %(keys)s::bigint[] AND k.key = ANY(%(keys)s::bigint[])" if keyed else ""
    return f"""
    first AS (
        SELECT DISTINCT ON (g.id, k.key) g.id, g.player_color, k.key, (k.ord - 1)::int AS ply{carried}
        FROM games g CROSS JOIN LATERAL unnest(g.opening_keys) WITH ORDINALITY AS k(key, ord)
        {keys}
        ORDER BY g.id, k.key, k.ord
    ),
    occ AS (SELECT * FROM first WHERE ply BETWEEN 1 AND %(max_ply)s)"""


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


# --- the ranking ------------------------------------------------------------------------------
#
# Every counted game has a result s (1 / ½ / 0), an Elo expectation e from the two ratings stored
# with it (same platform, so the scales agree), a deficit d = e − s (points below expectation)
# and a weight w = 0.5 ** (age / H), age in days before `as_of` (the newest of Rob's analysable
# games under the time-class filter, so a break does not age everything at once) and H the
# `review_recency_half_life_days` setting. A node is (colour, board): the same board means
# different things to each side. Its numbers are over its first-occurrence rows only, so a
# game counts once however often it returns to the board.

RANKED = "ranked"
FIXED = "fixed"

# The statuses, by the section they put a node in.
STILL_LEAKING = "still_leaking"
NEW_LEAK = "new_leak"
TOO_EARLY = "too_early"
NOT_REACHED = "not_reached_lately"
LOOKS_FIXED = "looks_fixed"
IMPROVING = "improving"
SECTION_OF = {
    STILL_LEAKING: RANKED,
    NEW_LEAK: RANKED,
    TOO_EARLY: RANKED,
    NOT_REACHED: FIXED,
    LOOKS_FIXED: FIXED,
    IMPROVING: FIXED,
}

_LN2 = math.log(2)


def _counted_games_ctes(time_sql: str, opening_sql: str) -> str:
    """`asof`, `hist` (every game of Rob's in the history under both filters) and `games` (those
    the statistics count: a prefix, a result and both ratings). Binds %(pid)s, %(months)s,
    %(half_life)s and the opening filter's parameters."""
    return f"""
    asof AS (
        SELECT max(cg.played_at) AS t FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")} AND {time_sql}
    ),
    hist AS (
        SELECT cg.id, pg.player_color, cg.opening_keys, cg.played_at, pg.result, pg.player_rating, pg.opponent_rating
        FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")} AND {time_sql} AND {opening_sql}
          AND cg.played_at >= {history_start_sql()}
    ),
    games AS (
        SELECT h.id, h.player_color, h.opening_keys, h.played_at,
               CASE h.result WHEN 'win' THEN 1.0 WHEN 'draw' THEN 0.5 ELSE 0.0 END::float8 AS s,
               1.0 / (1.0 + power(10.0::float8, (h.opponent_rating - h.player_rating)::float8 / 400.0)) AS e,
               greatest(0.0, extract(epoch FROM (a.t - h.played_at)) / 86400.0)::float8 AS age
        FROM hist h CROSS JOIN asof a
        WHERE h.opening_keys IS NOT NULL AND h.result IS NOT NULL
          AND h.player_rating IS NOT NULL AND h.opponent_rating IS NOT NULL
    )"""


_OBS = """
    obs AS (
        SELECT id, player_color, key, ply, s, e, e - s AS d, age, power(0.5::float8, age / %(half_life)s) AS w
        FROM occ
    )"""

# Per node: the sums every quantity is built from, its edges (children by first occurrence at
# the next ply) and its 30-day trend buckets (0 = the newest).
_NODE_COLUMNS = """
    count(*) AS n, sum(d) AS sd, sum(w * d) AS swd, sum(w) AS sw, sum(w * w) AS sww,
    sum(s) AS ss, sum(e) AS se, sum(w * s) AS sws, sum(w * e) AS swe,
    count(*) FILTER (WHERE age <= %(recent_days)s) AS recent"""

_NODE_DETAIL = """
    edges AS (
        SELECT player_color, key, jsonb_agg(jsonb_build_array(dst::text, c)) AS edges FROM (
            SELECT a.player_color, a.key, b.key AS dst, count(*) AS c
            FROM occ a JOIN occ b ON b.id = a.id AND b.ply = a.ply + 1
            JOIN nodes nd ON nd.player_color = a.player_color AND nd.key = a.key
            GROUP BY a.player_color, a.key, b.key
        ) per_edge GROUP BY player_color, key
    ),
    buckets AS (
        SELECT player_color, key, jsonb_agg(jsonb_build_array(b, c, sd)) AS buckets FROM (
            SELECT o.player_color, o.key, floor(o.age / 30.0)::int AS b, count(*) AS c, sum(o.d) AS sd
            FROM obs o JOIN nodes nd ON nd.player_color = o.player_color AND nd.key = o.key
            WHERE o.age < 30.0 * %(months)s
            GROUP BY o.player_color, o.key, floor(o.age / 30.0)::int
        ) per_bucket GROUP BY player_color, key
    )
    SELECT nd.*, pe.eval_cp, pe.mate_in, e.edges, bk.buckets
    FROM nodes nd
    LEFT JOIN edges e ON e.player_color = nd.player_color AND e.key = nd.key
    LEFT JOIN buckets bk ON bk.player_color = nd.player_color AND bk.key = nd.key
    LEFT JOIN position_evals pe ON pe.board_key = nd.key"""


@dataclass
class Scope:
    """What one Review request counts: the settings and both filters."""

    config: Settings
    time_class: str
    opening: Opening

    def params(self, max_ply: int | None = None) -> dict[str, Any]:
        return {
            "pid": PLAYER_ID,
            "months": self.config.review_history_months,
            "max_ply": max_ply if max_ply is not None else self.config.review_position_max_ply,
            "min_games": self.config.review_position_min_games,
            "half_life": float(self.config.review_recency_half_life_days),
            "recent_days": REVIEW_RECENT_DAYS,
            **opening_params(self.opening),
        }

    def games_ctes(self) -> str:
        return _counted_games_ctes(
            time_class_sql(self.time_class, self.config.time_class_focus), opening_sql(self.opening)
        )


def node_rows(conn: Connection[Any], scope: Scope) -> list[dict[str, Any]]:
    """Every node at least `review_position_min_games` games reach, with its sums, edges,
    trend buckets and evaluation."""
    query = cast(
        LiteralString,
        f"""
        WITH {scope.games_ctes()}, {occurrence_ctes(scored=True)}, {_OBS},
        nodes AS (
            SELECT player_color, key, {_NODE_COLUMNS} FROM obs GROUP BY player_color, key
            HAVING count(*) >= %(min_games)s
        ),
        {_NODE_DETAIL}
        """,
    )
    return [dict(r) for r in conn.execute(query, scope.params()).fetchall()]


def one_node(conn: Connection[Any], scope: Scope, colour: str, key: int, max_ply: int) -> dict[str, Any] | None:
    """One node's sums and trend buckets as `node_rows` gives them (no edges), at any game
    count; None when no counted game reaches the board as `colour` within plies 1 to `max_ply`."""
    query = cast(
        LiteralString,
        f"""
        WITH {scope.games_ctes()}, {occurrence_ctes(keyed=True, scored=True)}, {_OBS}
        SELECT player_color, key, {_NODE_COLUMNS},
               (SELECT eval_cp FROM position_evals WHERE board_key = %(key)s) AS eval_cp,
               (SELECT mate_in FROM position_evals WHERE board_key = %(key)s) AS mate_in,
               (SELECT coalesce(jsonb_agg(jsonb_build_array(b, c, sd)), '[]'::jsonb) FROM (
                    SELECT floor(o.age / 30.0)::int AS b, count(*) AS c, sum(o.d) AS sd FROM obs o
                    WHERE o.player_color = %(colour)s AND o.age < 30.0 * %(months)s
                    GROUP BY floor(o.age / 30.0)::int) per_bucket) AS buckets
        FROM obs WHERE player_color = %(colour)s
        GROUP BY player_color, key
        """,
    )
    params = {**scope.params(max_ply), "colour": colour, "key": key, "keys": [key]}
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def meta(conn: Connection[Any], scope: Scope) -> dict[str, Any]:
    """`as_of`, and how many games of the history the statistics count or leave out (no prefix
    recorded yet, no result, a rating missing on either side)."""
    query = cast(
        LiteralString,
        f"""
        WITH {scope.games_ctes()}
        SELECT (SELECT t FROM asof) AS as_of,
               (SELECT count(*) FROM games) AS games_counted,
               (SELECT count(*) FROM hist WHERE opening_keys IS NULL) AS games_without_prefix,
               (SELECT count(*) FROM hist WHERE opening_keys IS NOT NULL
                  AND (result IS NULL OR player_rating IS NULL OR opponent_rating IS NULL)) AS games_without_ratings
        """,
    )
    row = conn.execute(query, scope.params()).fetchone()
    return dict(row) if row else {}


def opening_options(conn: Connection[Any], config: Settings, time_class: str) -> list[dict[str, Any]]:
    """The opening filter's choices: one per (colour, family) with at least
    `review_position_min_games` games in the history under the time class, most played first."""
    query = cast(
        LiteralString,
        f"""
        SELECT pg.player_color, cg.canonical_family, count(*) AS games
        FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")}
          AND {time_class_sql(time_class, config.time_class_focus)}
          AND cg.played_at >= {history_start_sql()}
        GROUP BY pg.player_color, cg.canonical_family
        HAVING count(*) >= %(min_games)s
        ORDER BY count(*) DESC, cg.canonical_family NULLS LAST, pg.player_color
        """,
    )
    rows = conn.execute(
        query,
        {"pid": PLAYER_ID, "months": config.review_history_months, "min_games": config.review_position_min_games},
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        family = r["canonical_family"]
        side = "White" if r["player_color"] == "white" else "Black"
        out.append(
            {
                "key": opening_key(r["player_color"], family),
                "label": f"{family if family is not None else 'Unclassified openings'} · {side}",
                "games": int(r["games"]),
            }
        )
    return out


def line_rows(
    conn: Connection[Any], scope: Scope, nodes: list[tuple[str, int]], max_ply: int | None = None
) -> dict[tuple[str, int], dict[str, Any]]:
    """For each requested node, the line shown on it: the most frequent move sequence among its
    games up to each game's first occurrence of the board, ties to the most recent game, with
    that game's position keys after each move of the line (the last is the node's). Two games
    share a move sequence exactly when they share the positions along it, so the sequences are
    grouped by the prefix's position keys and only the chosen game's moves are read."""
    if not nodes:
        return {}
    query = cast(
        LiteralString,
        f"""
        WITH {scope.games_ctes()}, {occurrence_ctes(keyed=True)},
        want AS (SELECT * FROM unnest(%(colours)s::text[], %(keys)s::bigint[]) AS w(player_color, key)),
        seqs AS (
            SELECT o.player_color, o.key, o.ply, o.id, g.played_at, g.opening_keys[1:o.ply + 1] AS path
            FROM occ o JOIN want w ON w.player_color = o.player_color AND w.key = o.key
            JOIN games g ON g.id = o.id
        ),
        counted AS (SELECT *, count(*) OVER (PARTITION BY player_color, key, path) AS c FROM seqs),
        chosen AS (
            SELECT DISTINCT ON (player_color, key) player_color, key, ply, id, path FROM counted
            ORDER BY player_color, key, c DESC, played_at DESC NULLS LAST, id DESC
        )
        SELECT ch.player_color, ch.key, ch.ply, ch.path[2:] AS line_keys,
               jsonb_path_query_array(cg.opening_moves, '$[0 to $n]', jsonb_build_object('n', ch.ply - 1)) AS line
        FROM chosen ch JOIN chess_games cg ON cg.id = ch.id
        """,
    )
    rows = conn.execute(
        query,
        {**scope.params(max_ply), "colours": [c for c, _ in nodes], "keys": [k for _, k in nodes]},
    ).fetchall()
    return {(r["player_color"], int(r["key"])): dict(r) for r in rows}


# --- numbers and status ----------------------------------------------------------------------


@dataclass
class Node:
    colour: str
    key: int
    n: int
    long_deficit: float  # L: Σd / n, shrunk
    current_deficit: float  # C: Σwd / Σw, shrunk by the effective sample
    n_eff: float
    leak_per_month: float
    recent: int
    score: float
    expected: float
    current_score: float
    current_expected: float
    status: str | None
    edges: list[tuple[int, int]]  # (child key, games)
    trend: list[float | None]
    eval_cp: int | None
    mate_in: int | None

    @property
    def section(self) -> str | None:
        return SECTION_OF.get(self.status) if self.status else None

    def section_score(self) -> float:
        """What a section ranks by: the leak per month in Ranked, points lost over the history in Fixed?."""
        return self.leak_per_month if self.section == RANKED else self.long_deficit * self.n


def status_of(long_deficit: float, current_deficit: float, recent: int, n_eff: float) -> str | None:
    """The one ordered decision (the first rule that matches wins). Staleness comes first: an
    ageing position keeps its deficits and only its leak per month shrinks, so without that rule
    a position Rob no longer reaches would keep any status for ever."""
    t = REVIEW_LEAK_THRESHOLD
    lk, ck = long_deficit, current_deficit
    if recent < REVIEW_STALE_RECENT_GAMES:
        return NOT_REACHED if (lk > t or ck > t) else None
    if ck > t and lk > t:
        return STILL_LEAKING
    if ck > t:
        return NEW_LEAK
    if lk > t and ck <= 0 and recent >= REVIEW_FIXED_MIN_RECENT and n_eff >= REVIEW_FIXED_MIN_EFFECTIVE:
        return LOOKS_FIXED
    if lk > t and ck > 0:
        return TOO_EARLY
    if lk > t:
        return IMPROVING
    return None


def build_node(row: dict[str, Any], config: Settings, *, with_status: bool = True) -> Node:
    n = int(row["n"])
    sw, sww = float(row["sw"]), float(row["sww"])
    k = REVIEW_SHRINK_GAMES
    long_deficit = float(row["sd"]) / (n + k)
    n_eff = sw * sw / sww if sww > 0 else 0.0
    raw_current = float(row["swd"]) / sw if sw > 0 else 0.0
    current_deficit = raw_current * n_eff / (n_eff + k)
    half_life = config.review_recency_half_life_days
    leak = current_deficit * sw * 30 * _LN2 / half_life
    recent = int(row["recent"])
    months = config.review_history_months
    trend: list[float | None] = [None] * months
    buckets: list[list[Any]] = row.get("buckets") or []
    for b, c, sd in buckets:
        if 0 <= b < months and c >= REVIEW_MONTH_MIN_GAMES:
            trend[months - 1 - b] = round(100 * float(sd) / c, 1)
    status = status_of(long_deficit, current_deficit, recent, n_eff) if with_status else None
    edges: list[list[Any]] = row.get("edges") or []
    return Node(
        colour=row["player_color"],
        key=int(row["key"]),
        n=n,
        long_deficit=long_deficit,
        current_deficit=current_deficit,
        n_eff=n_eff,
        leak_per_month=leak,
        recent=recent,
        score=float(row["ss"]) / n,
        expected=float(row["se"]) / n,
        current_score=float(row["sws"]) / sw if sw > 0 else 0.0,
        current_expected=float(row["swe"]) / sw if sw > 0 else 0.0,
        status=status,
        edges=[(int(e[0]), int(e[1])) for e in edges],
        trend=trend,
        eval_cp=row.get("eval_cp"),
        mate_in=row.get("mate_in"),
    )


# --- carry-down: the position that carries the leak, shown once --------------------------------


def dominant_child(node: Node) -> tuple[int, int] | None:
    """(key, games) of the most frequent edge target, ties to the lower key (signed order)."""
    if not node.edges:
        return None
    return min(node.edges, key=lambda e: (-e[1], e[0]))


def walk(start: Node, nodes: dict[tuple[str, int], Node]) -> Node:
    """From a node with a status, step to its dominant child while the child is a node in the
    same section that either nearly every game reaches (forced) or that carries most of the
    section score; stop on a revisit. Each step adds a new node to `visited`, so it ends within
    as many steps as there are nodes."""
    section = start.section
    visited = {start.key}
    cur = start
    for _ in range(len(nodes)):
        dom = dominant_child(cur)
        if dom is None:
            return cur
        child_key, games = dom
        child = nodes.get((cur.colour, child_key))
        if child_key in visited or child is None or child.section != section:
            return cur
        forced = games >= REVIEW_FORCED_SHARE * cur.n
        here = cur.section_score()
        carries = here > 0 and child.section_score() >= REVIEW_COST_SHARE * here
        if not (forced or carries):
            return cur
        visited.add(child_key)
        cur = child
    return cur


def select_cards(nodes: dict[tuple[str, int], Node]) -> dict[str, list[Node]]:
    """Each section's cards: every node with a status walks down; walks that end on the same
    node give one card; the section's cap applies after that."""
    ends: dict[str, dict[tuple[str, int], Node]] = {RANKED: {}, FIXED: {}}
    for node in nodes.values():
        if node.section is None:
            continue
        if node.section == RANKED and node.leak_per_month <= 0:
            continue
        end = walk(node, nodes)
        ends[node.section][(end.colour, end.key)] = end
    ranked = sorted(ends[RANKED].values(), key=lambda x: (-x.leak_per_month, x.colour, x.key))
    ranked = [x for x in ranked if x.leak_per_month > 0][:REVIEW_RANKED_MAX]
    fixed = sorted(ends[FIXED].values(), key=lambda x: (-x.section_score(), x.colour, x.key))[:REVIEW_FIXED_MAX]
    return {RANKED: ranked, FIXED: fixed}


def parents(cards: list[Node], lines: dict[tuple[str, int], dict[str, Any]]) -> dict[tuple[str, int], int]:
    """Each card's breadcrumb: the deepest other card of the same colour whose board appears
    earlier in this card's line and whose own line is shorter. Every parent has a strictly
    shorter line than its child, so a chain of parents always ends."""
    out: dict[tuple[str, int], int] = {}
    for card in cards:
        mine = lines.get((card.colour, card.key))
        if not mine:
            continue
        line_keys: list[Any] = mine["line_keys"] or []
        keys = [int(k) for k in line_keys]
        best: tuple[int, int] | None = None  # (index in this line, key)
        for other in cards:
            if other is card or other.colour != card.colour:
                continue
            theirs = lines.get((other.colour, other.key))
            if not theirs or len(theirs["line"]) >= len(mine["line"]):
                continue
            idx = next((i for i, k in enumerate(keys[:-1]) if k == other.key), None)
            if idx is not None and (best is None or idx > best[0]):
                best = (idx, other.key)
        if best is not None:
            out[(card.colour, card.key)] = best[1]
    return out


def board_of(line: list[str]) -> tuple[str | None, str | None]:
    """(FEN, last move in UCI) after the line, from the standard start; (None, None) when a stored
    move does not replay."""
    board = replay(line, len(line))
    if board is None:
        return None, None
    last = board.peek().uci() if board.move_stack else None
    return board.fen(), last


def es_at(node: Node) -> float | None:
    """Rob's expected score at the board (0-100) from its stored evaluation, priced as the
    detector prices a position; None until the board is evaluated."""
    if node.eval_cp is None and node.mate_in is None:
        return None
    es = position_es(node.eval_cp, node.mate_in, node.colour == "white")
    return round(es, 1) if es is not None else None


def card(node: Node, line: dict[str, Any] | None, parent_key: int | None) -> dict[str, Any]:
    """One position as the page and the API carry it. Keys are decimal strings: a 64-bit board
    key does not survive a JavaScript number."""
    raw: list[Any] = line["line"] if line else []
    moves = [str(m) for m in raw]
    fen, last = board_of(moves)
    return {
        "colour": node.colour,
        "key": str(node.key),
        "line_san": moves,
        "fen": fen,
        "last_move": last,
        "n": node.n,
        "score": round(node.score, 4),
        "expected": round(node.expected, 4),
        "current_score": round(node.current_score, 4),
        "current_expected": round(node.current_expected, 4),
        "long_deficit": round(node.long_deficit, 4),
        "current_deficit": round(node.current_deficit, 4),
        "leak_per_month": round(node.leak_per_month, 2),
        "recent_games": node.recent,
        "status": node.status,
        "es_at_node": es_at(node),
        "trend": node.trend,
        "parent_key": str(parent_key) if parent_key is not None else None,
    }


def ranked_positions(conn: Connection[Any], scope: Scope) -> dict[str, list[dict[str, Any]]]:
    """The two position sections of the page: `ranked` (what is costing points now) and `fixed`."""
    nodes = {(n.colour, n.key): n for n in (build_node(r, scope.config) for r in node_rows(conn, scope))}
    chosen = select_cards(nodes)
    shown = chosen[RANKED] + chosen[FIXED]
    lines = line_rows(conn, scope, [(n.colour, n.key) for n in shown])
    up = parents(shown, lines)
    return {
        section: [card(n, lines.get((n.colour, n.key)), up.get((n.colour, n.key))) for n in cards]
        for section, cards in chosen.items()
    }
