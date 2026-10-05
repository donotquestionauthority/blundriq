"""Review's opening mistakes: Rob's own decisions in the opening, charged where he made them.

A decision is a board Rob was to move on at ply `p < review_position_max_ply` of a game's stored
opening prefix (`chess_games.opening_keys`), and the board his move led to: `B = keys[p]`,
`A = keys[p + 1]` (0-based plies). One decision is kept per (game, B, A), the lowest ply: the
same move from the same board twice in a game is one decision; a different move on a return to
the board is another. Games are the history under the page's two filters, as for the results
section, but a game needs only its prefix (no result or ratings).

A decision's loss is `max(0, ES(B) - ES(A))` in expected-score points (0-100, Rob's side) from
the stored evaluations (`position_evals`, `board_es`), and it is charged
`max(0, loss - review_mistake_floor_es)`. A decision whose B or A has no usable evaluation is
unknown: it counts in neither the cost nor the sample, and the coverage says so.

A board (colour, B) ranks when Rob moved from it in at least `review_position_min_games`
distinct games and was charged in at least `review_min_costly_games` of them. Its key is the
expected points it gives away a month at the current rate (each decision weighted
`0.5 ** (age / review_recency_half_life_days)`, age before the newest game under the time class).

Status is one ordered decision over the board's visits in time order (`played_at`, game id,
ply), each fine, costly or unknown; Fixed? eligibility is separate from the status shown:

1. no visit in the last REVIEW_RECENT_DAYS days: not reached lately (not ranked);
2. the newest visit unknown: not yet checked;
3. at least REVIEW_FIXED_RUN known-fine visits in a row at the end, after a costly one: Fixed?
   (an unknown visit ends the run);
4. otherwise: still costing you.

A board is in Fixed? when rule 3 holds, whatever its status says.

`pipeline position-evals` evaluates every board B Rob moved from in at least
REVIEW_EVAL_MIN_GAMES games of the whole history (either colour, any time class), and every
board A his moves from them led to, under the same `review_position_max_ply`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import (
    PLAYER_ID,
    REVIEW_EVAL_MIN_GAMES,
    REVIEW_FIXED_MAX,
    REVIEW_FIXED_RUN,
    REVIEW_RANKED_MAX,
    REVIEW_RECENT_DAYS,
    REVIEW_STRIP_VISITS,
)
from core.review.detect import position_es
from core.review.filters import opening_params, opening_sql, time_class_sql
from core.review.positions import Scope, board_of, history_start_sql, parents

STILL = "still_costing"
AWAITING = "not_yet_checked"
FIXED = "fixed"
NOT_REACHED = "not_reached_lately"

FINE = "fine"
COSTLY = "costly"
UNKNOWN = "unknown"

CHECKMATE = "checkmate"
DRAW = "draw"

_LN2 = math.log(2)

# A position page lists Rob's games from a board this many at a time.
MOVE_GAMES_PAGE = 50

# How many games a board may be rebuilt from before it counts as failed: one game whose stored
# prefix does not replay must not keep the board unevaluated (and the step failing) for ever.
EVAL_SOURCES = 3


# --- pricing ----------------------------------------------------------------------------------


def board_es(eval_cp: Any, mate_in: Any, terminal: str | None, rob_is_white: bool, rob_to_move: bool) -> float | None:
    """Rob's expected score (0-100) on a board from its stored row. A terminal board takes its
    outcome: checkmate is 0 for the side to move and 100 for the other, a draw 50. Otherwise
    the detector's pricing (`position_es`): a forced mate is 0 or 100, a score the sigmoid. A
    stored mate of 0 that is not marked terminal, or no row at all, is unknown."""
    if terminal == CHECKMATE:
        return 0.0 if rob_to_move else 100.0
    if terminal == DRAW:
        return 50.0
    if eval_cp is None and not mate_in:
        return None
    return position_es(eval_cp, mate_in, rob_is_white)


def decision_loss(row: dict[str, Any]) -> float | None:
    """A decision's loss (B before Rob's move, A after it), or None when either is unknown."""
    white = row["player_color"] == "white"
    before = board_es(row["b_cp"], row["b_mate"], row["b_terminal"], white, True)
    after = board_es(row["a_cp"], row["a_mate"], row["a_terminal"], white, False)
    if before is None or after is None:
        return None
    return max(0.0, before - after)


# --- reading decisions ------------------------------------------------------------------------


def _games_cte(scope: Scope) -> str:
    """`asof` and `g`: Rob's games in the history under both filters that have a prefix."""
    time_sql = time_class_sql(scope.time_class, scope.config.time_class_focus)
    return f"""
    asof AS (
        SELECT max(cg.played_at) AS t FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")} AND {time_sql}
    ),
    g AS (
        SELECT cg.id, pg.player_color, cg.opening_keys, cg.opening_moves, cg.played_at,
               greatest(0.0, extract(epoch FROM (a.t - cg.played_at)) / 86400.0)::float8 AS age
        FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id CROSS JOIN asof a
        WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")} AND {time_sql} AND {opening_sql(scope.opening)}
          AND cg.played_at >= {history_start_sql()} AND cg.opening_keys IS NOT NULL
    )"""


# Rob's decisions: plies 0 .. max_ply - 1 where he is to move, one per (game, B, A).
_DECISIONS = """
    dec AS (
        SELECT DISTINCT ON (g.id, g.opening_keys[p + 1], g.opening_keys[p + 2])
               g.id, g.player_color, p AS ply, g.opening_keys[p + 1] AS kb, g.opening_keys[p + 2] AS ka,
               g.opening_moves ->> p AS san, g.played_at, g.age
        FROM g CROSS JOIN generate_series(0, %(max_ply)s - 1) AS p
        WHERE (p %% 2 = 0) = (g.player_color = 'white') AND g.opening_keys[p + 2] IS NOT NULL
        ORDER BY g.id, g.opening_keys[p + 1], g.opening_keys[p + 2], p
    ),
    sup AS (SELECT player_color, kb, count(DISTINCT id) AS n FROM dec GROUP BY player_color, kb)"""

# A stored row the pricing can use: terminal, a score, or a non-zero mate.
_KNOWN = "({t}.terminal IS NOT NULL OR {t}.eval_cp IS NOT NULL OR coalesce({t}.mate_in, 0) <> 0)"


def _params(scope: Scope) -> dict[str, Any]:
    return {
        "pid": PLAYER_ID,
        "months": scope.config.review_history_months,
        "max_ply": scope.config.review_position_max_ply,
        "eval_min": REVIEW_EVAL_MIN_GAMES,
        **opening_params(scope.opening),
    }


def decision_rows(
    conn: Connection[Any], scope: Scope, *, min_games: int, colour: str | None = None, key: int | None = None
) -> list[dict[str, Any]]:
    """Every decision on a board Rob moved from in at least `min_games` games under the scope,
    in time order (`played_at`, game id, ply), with both boards' stored rows and the game's
    context for a position page. `colour` and `key` narrow it to one board."""
    one = "AND d.player_color = %(colour)s AND d.kb = %(key)s" if key is not None else ""
    query = cast(
        LiteralString,
        f"""
        WITH {_games_cte(scope)}, {_DECISIONS}
        SELECT d.id, d.player_color, d.ply, d.kb, d.ka, d.san, d.played_at, d.age,
               b.eval_cp AS b_cp, b.mate_in AS b_mate, b.terminal AS b_terminal, b.best_move AS b_best,
               a.eval_cp AS a_cp, a.mate_in AS a_mate, a.terminal AS a_terminal,
               pg.opponent_username, pg.opponent_rating, pg.result, cg.moves IS NOT NULL AS has_moves
        FROM dec d
        JOIN sup s ON s.player_color = d.player_color AND s.kb = d.kb AND s.n >= %(min_games)s
        JOIN chess_games cg ON cg.id = d.id
        JOIN player_games pg ON pg.chess_game_id = d.id AND pg.player_id = %(pid)s
        LEFT JOIN position_evals b ON b.board_key = d.kb
        LEFT JOIN position_evals a ON a.board_key = d.ka
        WHERE TRUE {one}
        ORDER BY d.played_at NULLS LAST, d.id, d.ply
        """,
    )
    params = {**_params(scope), "min_games": min_games, "colour": colour, "key": key}
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def coverage(conn: Connection[Any], scope: Scope) -> dict[str, int]:
    """How many decisions the scope has, how many are on boards the evaluator covers (moved
    from in at least REVIEW_EVAL_MIN_GAMES of these games), and how many of those are evaluated
    before and after."""
    query = cast(
        LiteralString,
        f"""
        WITH {_games_cte(scope)}, {_DECISIONS}
        SELECT count(*) AS decisions,
               count(*) FILTER (WHERE s.n >= %(eval_min)s) AS covered,
               count(*) FILTER (WHERE s.n >= %(eval_min)s AND {_KNOWN.format(t="b")} AND {_KNOWN.format(t="a")})
                 AS evaluated
        FROM dec d JOIN sup s ON s.player_color = d.player_color AND s.kb = d.kb
        LEFT JOIN position_evals b ON b.board_key = d.kb
        LEFT JOIN position_evals a ON a.board_key = d.ka
        """,
    )
    row: dict[str, Any] = dict(conn.execute(query, _params(scope)).fetchone() or {})
    out = {k: int(row.get(k) or 0) for k in ("decisions", "covered", "evaluated")}
    out["eval_min_games"] = REVIEW_EVAL_MIN_GAMES
    return out


def line_rows(
    conn: Connection[Any], scope: Scope, boards: list[tuple[str, int]]
) -> dict[tuple[str, int], dict[str, Any]]:
    """For each board, the line shown on it: the most frequent move sequence by which Rob's
    decisions reached it (ties to the most recent game), with the position keys after each move
    of the line (the last is the board's), as `positions.line_rows` gives them."""
    if not boards:
        return {}
    query = cast(
        LiteralString,
        f"""
        WITH {_games_cte(scope)}, {_DECISIONS},
        want AS (SELECT * FROM unnest(%(colours)s::text[], %(keys)s::bigint[]) AS w(player_color, kb)),
        seqs AS (
            SELECT d.player_color, d.kb, d.ply, d.id, d.played_at, g.opening_keys[1:d.ply + 1] AS path
            FROM dec d JOIN want w ON w.player_color = d.player_color AND w.kb = d.kb JOIN g ON g.id = d.id
        ),
        counted AS (SELECT *, count(*) OVER (PARTITION BY player_color, kb, path) AS c FROM seqs),
        chosen AS (
            SELECT DISTINCT ON (player_color, kb) player_color, kb, ply, id, path FROM counted
            ORDER BY player_color, kb, c DESC, played_at DESC NULLS LAST, id DESC
        )
        SELECT ch.player_color, ch.kb AS key, ch.ply, ch.path[2:] AS line_keys,
               CASE WHEN ch.ply = 0 THEN '[]'::jsonb
                    ELSE jsonb_path_query_array(cg.opening_moves, '$[0 to $n]', jsonb_build_object('n', ch.ply - 1))
               END AS line
        FROM chosen ch JOIN chess_games cg ON cg.id = ch.id
        """,
    )
    params = {**_params(scope), "colours": [c for c, _ in boards], "keys": [k for _, k in boards]}
    rows = conn.execute(query, params).fetchall()
    return {(r["player_color"], int(r["key"])): dict(r) for r in rows}


# --- a board's numbers ------------------------------------------------------------------------


def state_of(loss: float | None, floor: float) -> str:
    if loss is None:
        return UNKNOWN
    return COSTLY if loss > floor else FINE


def fixed_eligible(states: list[str]) -> bool:
    """At least REVIEW_FIXED_RUN known-fine visits in a row at the end, after a costly one. An
    unknown visit ends the run."""
    run = 0
    for s in reversed(states):
        if s != FINE:
            break
        run += 1
    return run >= REVIEW_FIXED_RUN and COSTLY in states[: len(states) - run]


def status_of(states: list[str], newest_age: float) -> str:
    """The status shown, one ordered decision (the module docstring's rules)."""
    if newest_age > REVIEW_RECENT_DAYS:
        return NOT_REACHED
    if states and states[-1] == UNKNOWN:
        return AWAITING
    return FIXED if fixed_eligible(states) else STILL


@dataclass
class Board:
    colour: str
    key: int
    visits: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    # Filled by `measure`.
    games: int = 0
    costly_games: int = 0
    known: int = 0
    costly: int = 0
    per_month: float = 0.0
    per_month_12: float = 0.0
    per_visit: float = 0.0
    states: list[str] = field(default_factory=list[str])
    status: str | None = None
    fixed: bool = False
    best_move: str | None = None
    terminal: str | None = None
    last_costly: datetime | None = None


def measure(board: Board, floor: float, half_life: float, months: int) -> Board:
    """Every number of a board from its visits (already in time order)."""
    games: set[int] = set()
    costly_games: set[int] = set()
    sw = swc = total = 0.0
    states: list[str] = []
    for v in board.visits:
        games.add(int(v["id"]))
        loss = v["loss"]
        state = state_of(loss, floor)
        states.append(state)
        if state == UNKNOWN:
            continue
        charged = max(0.0, float(loss) - floor)
        w = 0.5 ** (float(v["age"]) / half_life)
        board.known += 1
        sw += w
        swc += w * charged
        total += charged
        if state == COSTLY:
            board.costly += 1
            costly_games.add(int(v["id"]))
            board.last_costly = v["played_at"]
    first = board.visits[0] if board.visits else {}
    board.best_move = first.get("b_best")
    board.terminal = first.get("b_terminal")
    board.games = len(games)
    board.costly_games = len(costly_games)
    board.per_month = swc / 100 * 30 * _LN2 / half_life
    board.per_month_12 = total / 100 / months
    board.per_visit = swc / sw / 100 if sw > 0 else 0.0
    board.states = states
    newest = min((float(v["age"]) for v in board.visits), default=math.inf)
    board.status = status_of(states, newest)
    board.fixed = fixed_eligible(states)
    return board


def boards_of(rows: list[dict[str, Any]], scope: Scope) -> list[Board]:
    """The rows grouped by board, each measured, in first-seen order."""
    config = scope.config
    floor = float(config.review_mistake_floor_es)
    by: dict[tuple[str, int], Board] = {}
    for r in rows:
        r["loss"] = decision_loss(r)
        k = (str(r["player_color"]), int(r["kb"]))
        by.setdefault(k, Board(k[0], k[1])).visits.append(r)
    half_life = float(config.review_recency_half_life_days)
    return [measure(b, floor, half_life, config.review_history_months) for b in by.values()]


def qualifies(board: Board, scope: Scope) -> bool:
    return (
        board.games >= scope.config.review_position_min_games
        and board.costly_games >= scope.config.review_min_costly_games
    )


def moves_played(board: Board) -> list[dict[str, Any]]:
    """The moves Rob played from the board: times, how many were evaluated, their mean loss,
    how many were costly, and when he last played each. Most played first."""
    out: dict[str, dict[str, Any]] = {}
    for v, state in zip(board.visits, board.states, strict=True):
        san = str(v["san"])
        m = out.setdefault(
            san,
            {"san": san, "n": 0, "evaluated": 0, "loss_sum": 0.0, "costly": 0, "last_played": None, "mates": False},
        )
        m["n"] += 1
        if v["a_terminal"] == CHECKMATE:
            m["mates"] = True
        if state != UNKNOWN:
            m["evaluated"] += 1
            m["loss_sum"] += float(v["loss"])
        if state == COSTLY:
            m["costly"] += 1
        m["last_played"] = v["played_at"]
    rows: list[dict[str, Any]] = []
    for m in out.values():
        rows.append(
            {
                "san": m["san"],
                "n": m["n"],
                "evaluated": m["evaluated"],
                "mean_loss": round(m["loss_sum"] / m["evaluated"], 1) if m["evaluated"] else None,
                "costly": m["costly"],
                "mates": m["mates"],
                "last_played": m["last_played"].isoformat() if m["last_played"] else None,
            }
        )
    rows.sort(key=lambda r: (-r["n"], r["san"]))
    return rows


def card(board: Board, line: dict[str, Any] | None, parent_key: int | None) -> dict[str, Any]:
    """One board as the page and the API carry it; keys as decimal strings."""
    raw: list[Any] = line["line"] if line else []
    moves = [str(m) for m in raw]
    fen, last = board_of(moves)
    return {
        "colour": board.colour,
        "key": str(board.key),
        "line_san": moves,
        "fen": fen,
        "last_move": last,
        "games": board.games,
        "costly_games": board.costly_games,
        "decisions": len(board.visits),
        "evaluated": board.known,
        "costly": board.costly,
        "per_month": round(board.per_month, 3),
        "per_month_12": round(board.per_month_12, 3),
        "per_visit": round(board.per_visit, 3),
        "status": board.status,
        "fixed": board.fixed,
        "strip": board.states[-REVIEW_STRIP_VISITS:],
        "moves": moves_played(board),
        "best_move": board.best_move,
        "terminal": board.terminal,
        "last_costly": board.last_costly.isoformat() if board.last_costly else None,
        "parent_key": str(parent_key) if parent_key is not None else None,
    }


# --- the section ------------------------------------------------------------------------------


def section(conn: Connection[Any], scope: Scope) -> dict[str, Any]:
    """`ranked` (Opening mistakes to work on), `fixed` (Fixed?) and `coverage`."""
    rows = decision_rows(conn, scope, min_games=scope.config.review_position_min_games)
    boards = [b for b in boards_of(rows, scope) if qualifies(b, scope)]
    ranked = sorted(
        (b for b in boards if b.status in (STILL, AWAITING)),
        key=lambda b: (-b.per_month, b.colour, b.key),
    )[:REVIEW_RANKED_MAX]
    fixed = sorted((b for b in boards if b.fixed), key=lambda b: (-b.per_month_12, b.colour, b.key))[:REVIEW_FIXED_MAX]
    shown = ranked + [b for b in fixed if b not in ranked]
    lines = line_rows(conn, scope, [(b.colour, b.key) for b in shown])
    up = parents(cast(Any, shown), lines)
    return {
        "ranked": [card(b, lines.get((b.colour, b.key)), up.get((b.colour, b.key))) for b in ranked],
        "fixed": [card(b, lines.get((b.colour, b.key)), up.get((b.colour, b.key))) for b in fixed],
        "coverage": coverage(conn, scope),
    }


def board_detail(conn: Connection[Any], scope: Scope, colour: str, key: int) -> dict[str, Any] | None:
    """A position page's mistakes block: the board's numbers and moves at any game count; None
    when Rob never moved from it under the scope. Its games are `move_games`, a page at a time."""
    rows = decision_rows(conn, scope, min_games=1, colour=colour, key=key)
    if not rows:
        return None
    board = boards_of(rows, scope)[0]
    out = card(board, line_rows(conn, scope, [(colour, key)]).get((colour, key)), None)
    del out["parent_key"]
    out["ranked"] = qualifies(board, scope)
    return out


def move_games(
    conn: Connection[Any], scope: Scope, colour: str, key: int, *, move: str | None, page: int
) -> dict[str, Any] | None:
    """Rob's games from the board, newest first, MOVE_GAMES_PAGE a page: every visit where he
    played `move` (fine, costly or not checked yet), or with no `move` every costly visit. Each row
    keeps the ply he played it at; a game whose moves are no longer stored has `has_moves` false.
    None when Rob never moved from the board under the scope."""
    rows = decision_rows(conn, scope, min_games=1, colour=colour, key=key)
    if not rows:
        return None
    board = boards_of(rows, scope)[0]
    chosen = [
        (v, st)
        for v, st in zip(board.visits, board.states, strict=True)
        if (st == COSTLY if move is None else v["san"] == move)
    ]
    chosen.reverse()
    total = len(chosen)
    start = (page - 1) * MOVE_GAMES_PAGE
    return {
        "move": move,
        "rows": [
            {
                "chess_game_id": int(v["id"]),
                "ply": int(v["ply"]),
                "san": v["san"],
                "state": st,
                "loss": round(float(v["loss"]), 1) if v["loss"] is not None else None,
                "played_at": v["played_at"].isoformat() if v["played_at"] else None,
                "opponent_username": v["opponent_username"],
                "opponent_rating": v["opponent_rating"],
                "result": v["result"],
                "has_moves": bool(v["has_moves"]),
            }
            for v, st in chosen[start : start + MOVE_GAMES_PAGE]
        ],
        "total": total,
        "page": page,
        "page_size": MOVE_GAMES_PAGE,
        "total_pages": max(1, -(-total // MOVE_GAMES_PAGE)),
    }


# --- what the evaluator evaluates -------------------------------------------------------------


def eval_candidates(
    conn: Connection[Any], max_ply: int, months: int, limit: int | None
) -> tuple[int, list[dict[str, Any]]]:
    """(how many boards are pending, the first `limit` of them, or all when `limit` is None).
    The boards: every board B Rob moved from in at least REVIEW_EVAL_MIN_GAMES distinct games of
    the history (either colour, every time class) at a ply below `max_ply`, and every board A
    one of those moves led to. Pending: no row, or a row with neither a best move nor a terminal
    outcome (rows written before migration 010). Most-played first (games through the board),
    then the lower key. Each row carries up to EVAL_SOURCES games through the board, lowest id
    first, each with the board's ply in it and its prefix moves, from which `core.review.evals`
    rebuilds the board."""
    query = cast(
        LiteralString,
        f"""
        WITH g AS (
            SELECT cg.id, pg.player_color, cg.opening_keys
            FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
            WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")} AND cg.opening_keys IS NOT NULL
              AND cg.played_at >= {history_start_sql()}
        ),
        dec AS (
            SELECT g.id, g.player_color, p AS ply, g.opening_keys[p + 1] AS kb, g.opening_keys[p + 2] AS ka
            FROM g CROSS JOIN generate_series(0, %(max_ply)s - 1) AS p
            WHERE (p %% 2 = 0) = (g.player_color = 'white') AND g.opening_keys[p + 2] IS NOT NULL
        ),
        sup AS (
            SELECT player_color, kb FROM dec GROUP BY player_color, kb HAVING count(DISTINCT id) >= %(eval_min)s
        ),
        occ AS (
            SELECT d.id, d.kb AS key, d.ply FROM dec d JOIN sup s USING (player_color, kb)
            UNION ALL
            SELECT d.id, d.ka AS key, d.ply + 1 FROM dec d JOIN sup s USING (player_color, kb)
        ),
        boards AS (SELECT key, count(DISTINCT id) AS n FROM occ GROUP BY key),
        pending AS (
            SELECT b.key, b.n FROM boards b
            WHERE NOT EXISTS (
                SELECT 1 FROM position_evals pe
                WHERE pe.board_key = b.key AND (pe.best_move IS NOT NULL OR pe.terminal IS NOT NULL)
            )
        ),
        chosen AS (SELECT key, n FROM pending ORDER BY n DESC, key LIMIT %(limit)s),
        src AS (
            SELECT o.key, o.id, min(o.ply) AS ply FROM occ o JOIN chosen c ON c.key = o.key GROUP BY o.key, o.id
        ),
        ranked_src AS (SELECT *, row_number() OVER (PARTITION BY key ORDER BY id) AS rn FROM src)
        SELECT c.key, c.n, (SELECT count(*) FROM pending) AS pending_total,
               jsonb_agg(jsonb_build_object('ply', s.ply, 'moves', cg.opening_moves) ORDER BY s.rn) AS sources
        FROM chosen c
        JOIN ranked_src s ON s.key = c.key AND s.rn <= %(sources)s
        JOIN chess_games cg ON cg.id = s.id
        GROUP BY c.key, c.n
        ORDER BY c.n DESC, c.key
        """,
    )
    params = {
        "pid": PLAYER_ID,
        "months": months,
        "max_ply": max_ply,
        "eval_min": REVIEW_EVAL_MIN_GAMES,
        "limit": limit,
        "sources": EVAL_SOURCES,
    }
    rows = conn.execute(query, params).fetchall()
    if not rows:  # LIMIT NULL is no limit, so an empty answer means nothing is pending
        return 0, []
    return int(rows[0]["pending_total"]), [dict(r) for r in rows]
