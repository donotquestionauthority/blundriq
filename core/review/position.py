"""One position's page: the player's moves from it (`mistake`, `core.review.mistakes.board_detail`), its
results numbers, what happens next, and the games through it.

A position is (colour, board). Its numbers are the ranking's (`core.review.positions`) under the
same filters, at any game count, so a link stays valid when a filter narrows the position below
the ranking's floor. A board deeper than `review_position_max_ply` (reached from "What happens
next") is read over the whole opening prefix and carries no status.

Every ply here is the game's first occurrence `p` of the board. The player's first decision from the
board is at `p` when they are to move there, else at `p + 1`; a turning point is looked for from that
decision on, inclusive: the costliest review event at or after it, else the largest one-move drop
in their expected score over their own moves from it (the detector's curve), when it is at least
REVIEW_TURN_MIN_DROP. A game with neither has no turning point; nothing is invented.
"""

from __future__ import annotations

from typing import Any, LiteralString, cast

from psycopg import Connection

from core.constants import (
    OPENING_PREFIX_PLIES,
    REVIEW_CHILDREN_MAX,
    REVIEW_PLAYABLE_ES,
    REVIEW_TURN_MIN_DROP,
)
from core.review import mistakes
from core.review.detect import es_curve, position_es
from core.review.positions import (
    Scope,
    build_node,
    card,
    line_rows,
    occurrence_ctes,
    one_node,
)

PAGE_SIZE = 50

TURN_FOUND = "found"
TURN_NONE = "none"
TURN_NOT_ANALYSED = "not_analysed"


def player_to_move(ply: int, colour: str) -> bool:
    """Whether the player is to move in the position after `ply` half-moves of a standard game."""
    return (ply % 2 == 0) == (colour == "white")


def move_label(ply: int, san: str) -> str:
    """`moves[ply]` with its move number: 17.Qb6 for White's move, 17…Qb6 for Black's."""
    number = ply // 2 + 1
    return f"{number}.{san}" if ply % 2 == 0 else f"{number}…{san}"


def turning_point(
    ply: int,
    colour: str,
    events: list[dict[str, Any]],
    ply_analysis: list[Any] | None,
) -> tuple[str, int | None, float | None]:
    """(state, anchor ply, cost in expected-score points) for a game through the board at its
    first-occurrence `ply`. The anchor `a` is the position before the player's move, its cost
    es[a] - es[a + 1]."""
    first = ply if player_to_move(ply, colour) else ply + 1
    later = [e for e in events if int(e["anchor_ply"]) >= first]
    if later:
        best = min(later, key=lambda e: (-float(e["cost"] or 0), int(e["anchor_ply"])))
        return TURN_FOUND, int(best["anchor_ply"]), round(float(best["cost"] or 0), 1)
    if not ply_analysis:
        return TURN_NOT_ANALYSED, None, None
    es = es_curve(ply_analysis, colour == "white")
    best_ply: int | None = None
    best_drop = 0.0
    for a in range(first, len(es) - 1, 2):
        drop = es[a] - es[a + 1]
        if drop > best_drop:
            best_ply, best_drop = a, drop
    if best_ply is None or best_drop < REVIEW_TURN_MIN_DROP:
        return TURN_NONE, None, None
    return TURN_FOUND, best_ply, round(best_drop, 1)


def arrival_es(entry: Any, colour: str) -> float | None:
    """The player's expected score on arrival, from the stored analysis of that position."""
    if not isinstance(entry, dict):
        return None
    analysed = cast(dict[str, Any], entry)
    es = position_es(analysed.get("eval"), analysed.get("mate_in_moves"), colour == "white")
    return round(es, 1) if es is not None else None


def _band(row: dict[str, Any]) -> int:
    """Playable then not won first, then the other non-wins, then wins."""
    if row["result"] == "win":
        return 2
    es = row["es_on_arrival"]
    return 0 if es is not None and es >= REVIEW_PLAYABLE_ES else 1


def order_games(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bands, then unreviewed first, then the most recent."""

    def recency(r: dict[str, Any]) -> tuple[float, int]:
        played = r["played_at"]
        return (-(played.timestamp() if played else 0.0), -int(r["chess_game_id"]))

    return sorted(rows, key=lambda r: (_band(r), 1 if r["reviewed"] else 0, *recency(r)))


def _children(conn: Connection[Any], scope: Scope, colour: str, key: int, max_ply: int) -> list[dict[str, Any]]:
    """The moves played from the board, most played first. A move is `linkable` when the board it
    leads to is a position of its own somewhere in these games: first reached after at least one
    move. The one board that never is, the start, is still listed (games do return to it) but
    has no page; every other child's first occurrence is at ply 1 or later, so its page exists."""
    query = cast(
        LiteralString,
        f"""
        WITH {scope.games_ctes()}, {occurrence_ctes(keyed=True)}
        SELECT cg.opening_moves ->> o.ply AS san, cg.opening_keys[o.ply + 2] AS child, o.ply,
               bool_or(array_position(cg.opening_keys, cg.opening_keys[o.ply + 2]) > 1) AS linkable,
               count(*) AS n, avg(g.s) AS score, avg(g.e) AS expected
        FROM occ o JOIN games g ON g.id = o.id JOIN chess_games cg ON cg.id = o.id
        WHERE o.player_color = %(colour)s AND jsonb_array_length(cg.opening_moves) > o.ply
          AND cardinality(cg.opening_keys) >= o.ply + 2
        GROUP BY 1, 2, 3
        """,
    )
    rows = conn.execute(query, {**scope.params(max_ply), "colour": colour, "keys": [key]}).fetchall()
    # A board can be reached at different plies; a move from it is one child however reached.
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    for r in rows:
        k = (r["san"], int(r["child"]))
        m = merged.setdefault(
            k, {"san": r["san"], "key": str(r["child"]), "linkable": False, "n": 0, "s": 0.0, "e": 0.0}
        )
        m["linkable"] = m["linkable"] or bool(r["linkable"])
        m["n"] += int(r["n"])
        m["s"] += float(r["score"]) * int(r["n"])
        m["e"] += float(r["expected"]) * int(r["n"])
    out = [
        {
            "san": m["san"],
            "key": m["key"],
            "linkable": m["linkable"],
            "n": m["n"],
            "score": round(m["s"] / m["n"], 4),
            "expected": round(m["e"] / m["n"], 4),
        }
        for m in merged.values()
    ]
    out.sort(key=lambda c: (-c["n"], c["san"]))
    return out[:REVIEW_CHILDREN_MAX]


def _game_rows(conn: Connection[Any], scope: Scope, colour: str, key: int, max_ply: int) -> list[dict[str, Any]]:
    """Every counted game through the board whose moves are still stored, with what the order
    and the page need: the arrival evaluation and the game's review events."""
    query = cast(
        LiteralString,
        f"""
        WITH {scope.games_ctes()}, {occurrence_ctes(keyed=True)}
        SELECT cg.id AS chess_game_id, o.ply, cg.played_at, cg.time_class, pg.opponent_username,
               pg.opponent_rating, pg.result, pg.reviewed_at IS NOT NULL AS reviewed,
               cg.ply_analysis -> o.ply AS arrival,
               (SELECT coalesce(jsonb_agg(jsonb_build_object('anchor_ply', re.anchor_ply, 'cost', re.cost)),
                                '[]'::jsonb)
                  FROM review_events re WHERE re.player_id = %(pid)s AND re.chess_game_id = cg.id) AS events
        FROM occ o JOIN games g ON g.id = o.id JOIN chess_games cg ON cg.id = o.id
        JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
        WHERE o.player_color = %(colour)s AND cg.moves IS NOT NULL
        """,
    )
    rows = conn.execute(query, {**scope.params(max_ply), "colour": colour, "keys": [key]}).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        row["es_on_arrival"] = arrival_es(row.pop("arrival"), colour)
        out.append(row)
    return out


def _analysis(conn: Connection[Any], ids: list[int]) -> dict[int, dict[str, Any]]:
    rows = conn.execute("SELECT id, moves, ply_analysis FROM chess_games WHERE id = ANY(%s)", (ids,)).fetchall()
    return {int(r["id"]): dict(r) for r in rows}


def _with_turning_point(row: dict[str, Any], colour: str, stored: dict[str, Any] | None) -> dict[str, Any]:
    moves: list[Any] = (stored or {}).get("moves") or []
    analysis: list[Any] | None = (stored or {}).get("ply_analysis")
    events: list[dict[str, Any]] = row.pop("events") or []
    state, anchor, cost = turning_point(int(row["ply"]), colour, events, analysis)
    played = row["played_at"]
    return {
        **row,
        "played_at": played.isoformat() if played else None,
        "turning_state": state,
        "turning_ply": anchor,
        "turning_move": move_label(anchor, str(moves[anchor])) if anchor is not None and anchor < len(moves) else None,
        "turning_cost": cost,
    }


def position_page(conn: Connection[Any], scope: Scope, colour: str, key: int, page: int) -> dict[str, Any] | None:
    """The page for (colour, board), or None when no counted game reaches it."""
    config = scope.config
    max_ply = config.review_position_max_ply
    mistake = mistakes.board_detail(conn, scope, colour, key)
    row = one_node(conn, scope, colour, key, max_ply)
    ranked_depth = row is not None
    if row is None:
        max_ply = OPENING_PREFIX_PLIES
        row = one_node(conn, scope, colour, key, max_ply)
        if row is None:
            if mistake is None:
                return None
            # Only the start (ply 0) is a board the player moves from that no game first reaches later:
            # the page is its mistakes alone.
            empty: dict[str, Any] = {"rows": [], "total": 0, "page": page, "page_size": PAGE_SIZE, "total_pages": 1}
            return {"node": None, "children": [], "games": empty, "older_games": 0, "mistake": mistake}
    node = build_node(row, config, with_status=ranked_depth and int(row["n"]) >= scope.results_min_games)
    line = line_rows(conn, scope, [(colour, key)], max_ply).get((colour, key))
    node_card = card(node, line, None)
    ply = int(line["ply"]) if line else 0
    games = order_games(_game_rows(conn, scope, colour, key, max_ply))
    total = len(games)
    start = (page - 1) * PAGE_SIZE
    shown = games[start : start + PAGE_SIZE]
    stored = _analysis(conn, [int(r["chess_game_id"]) for r in shown])
    return {
        "node": {**node_card, "player_to_move": player_to_move(ply, colour), "ply": ply},
        "children": _children(conn, scope, colour, key, max_ply),
        "games": {
            "rows": [_with_turning_point(r, colour, stored.get(int(r["chess_game_id"]))) for r in shown],
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "total_pages": max(1, -(-total // PAGE_SIZE)),
        },
        "older_games": node.n - total,
        "mistake": mistake,
    }
