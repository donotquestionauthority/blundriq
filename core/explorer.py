"""The Opening Explorer: one board, reached by a path of moves from the start, read for one colour.

What the board says comes from two places. The repertoire (`core.repertoire.read.board_moves`):
at the player's turn the move or moves it prescribes, at the opponent's the replies it prepares.
The player's games (`moves_from`): every analysable game of theirs as that colour, under the
time class, inside `explorer_history_months` back from the newest game, matched to a line or
not, counted once at its first occurrence of the board in the stored opening prefix
(`chess_games.opening_keys`, the boards at plies 0 to OPENING_PREFIX_PLIES). From that
occurrence, the move the game played next, the board it led to, and the result: a row per move,
grouped by the board it leads to, plus the board's own row.

A game's score is 1, ½ or 0; its expectation is the Elo expectation from the two ratings stored
with it, which are on one platform's scale. `vs_expectation` is the mean of score minus
expectation over the games that have both ratings; a game without them still counts in games,
wins, draws and losses. Board keys are the 64-bit hash, not re-verified against board text, as
Review's position page reads them.

`arrival` judges the path's last move when it was the player's: whether it left a non-empty
prescribed set at the board before it. It belongs to the path, so any answer for the path says
the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, LiteralString, cast

import chess
from psycopg import Connection

from core import settings
from core.chess.eligibility import analysable_sql
from core.constants import EXPLORER_MAX_PLIES, OPENING_PREFIX_PLIES, PLAYER_ID
from core.repertoire import read
from core.review.filters import time_class_sql
from core.review.positions import history_start_sql

Row = dict[str, Any]

PRESCRIBING = (read.STATUS_MATCH, read.STATUS_AGREE, read.STATUS_CONFLICT)


class ExplorerPathError(ValueError):
    """A path that is not a legal sequence of moves from the start: 422."""


@dataclass(frozen=True)
class Scope:
    """What one read counts: `explorer_history_months`, the time class, and `time_class_focus`
    (what `focus` means)."""

    months: int
    time_class: str
    focus: str


def load_scope(conn: Connection[Any], time_class: str) -> Scope:
    """The scope from the settings row, reading only the two settings it needs."""
    values = settings.load_fields(conn, "explorer_history_months", "time_class_focus")
    return Scope(values["explorer_history_months"], time_class, values["time_class_focus"])


def replay(tokens: list[str]) -> list[tuple[chess.Board, chess.Move]]:
    """Each move of the path with the board it is played from. Raises ExplorerPathError at the
    first token that is not a legal move there (python-chess parses `--` as the null move)."""
    if len(tokens) > EXPLORER_MAX_PLIES:
        raise ExplorerPathError(f"a path has at most {EXPLORER_MAX_PLIES} moves")
    board = chess.Board()
    out: list[tuple[chess.Board, chess.Move]] = []
    for i, token in enumerate(tokens):
        try:
            move = board.parse_san(token)
        except ValueError as exc:
            raise ExplorerPathError(f"move {i + 1} is not legal") from exc
        if not move:
            raise ExplorerPathError(f"move {i + 1} is not legal")
        out.append((board.copy(stack=False), move))
        board.push(move)
    return out


def board_after(path: list[tuple[chess.Board, chess.Move]]) -> chess.Board:
    if not path:
        return chess.Board()
    before, move = path[-1]
    after = before.copy(stack=False)
    after.push(move)
    return after


# The games, each one's first occurrence of the board in its prefix, and from there: the board's
# own row (`kind` 'node') and one row per board the next stored move leads to ('move'). A first
# occurrence at the prefix's last board has no stored next move, nor has a game ending there.
_STATS_SQL = """
WITH board AS MATERIALIZED (SELECT bq_position_key(%(fen)s) AS key),
games AS MATERIALIZED (
    SELECT cg.id, cg.opening_keys, cg.opening_moves, pg.result,
           CASE pg.result WHEN 'win' THEN 1.0 WHEN 'draw' THEN 0.5 ELSE 0.0 END::float8 AS s,
           CASE WHEN pg.player_rating IS NOT NULL AND pg.opponent_rating IS NOT NULL THEN
               1.0 / (1.0 + power(10.0::float8, (pg.opponent_rating - pg.player_rating)::float8 / 400.0))
           END AS e
    FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
    WHERE pg.player_id = %(pid)s AND {analysable} AND pg.player_color = %(colour)s AND {time_class}
      AND cg.opening_keys IS NOT NULL AND pg.result IS NOT NULL
      AND cg.opening_keys @> ARRAY[(SELECT key FROM board)]
      AND cg.played_at >= {history_start}
),
first AS (
    SELECT DISTINCT ON (g.id) g.id, (k.ord - 1)::int AS ply
    FROM games g CROSS JOIN LATERAL unnest(g.opening_keys) WITH ORDINALITY AS k(key, ord)
    WHERE k.key = (SELECT key FROM board)
    ORDER BY g.id, k.ord
),
visits AS (
    SELECT g.result, g.s, g.e,
           CASE WHEN f.ply < %(prefix)s AND jsonb_array_length(g.opening_moves) > f.ply
                     AND cardinality(g.opening_keys) >= f.ply + 2
                THEN g.opening_keys[f.ply + 2] END AS child,
           g.opening_moves ->> f.ply AS san
    FROM first f JOIN games g ON g.id = f.id
)
SELECT 'node' AS kind, NULL::bigint AS child, NULL::text AS san, count(*) AS n,
       count(*) FILTER (WHERE result = 'win') AS w, count(*) FILTER (WHERE result = 'draw') AS d,
       avg(s) AS score, count(e) AS n_rated, avg(s - e) AS vs_expectation, count(child) AS continued
FROM visits
UNION ALL
SELECT 'move', child, mode() WITHIN GROUP (ORDER BY san), count(*),
       count(*) FILTER (WHERE result = 'win'), count(*) FILTER (WHERE result = 'draw'),
       avg(s), count(e), avg(s - e), NULL
FROM visits WHERE child IS NOT NULL GROUP BY child
"""


def _numbers(r: Row) -> Row:
    n = int(r["n"])
    w, d = int(r["w"]), int(r["d"])
    n_rated = int(r["n_rated"])
    return {
        "n": n,
        "w": w,
        "d": d,
        "l": n - w - d,
        "score": round(float(r["score"]), 4) if n else None,
        "n_rated": n_rated,
        "vs_expectation": round(float(r["vs_expectation"]), 4) if n_rated else None,
    }


def _canonical(board: chess.Board, san: str) -> str:
    """The stored SAN as the one spelling the repertoire's moves use; kept as stored when it
    does not parse here (the key, not the text, is what grouped it)."""
    try:
        move = board.parse_san(san)
    except ValueError:
        return san
    return board.san(move) if move else san


def moves_from(conn: Connection[Any], scope: Scope, colour: str, board: chess.Board) -> tuple[Row, list[Row]]:
    """(the board's own numbers with `continued`, one row per move played from it)."""
    query = cast(
        LiteralString,
        _STATS_SQL.format(
            analysable=analysable_sql("cg"),
            time_class=time_class_sql(scope.time_class, scope.focus),
            history_start=history_start_sql(),
        ),
    )
    params = {
        "pid": PLAYER_ID,
        "fen": board.fen(),
        "colour": colour,
        "months": scope.months,
        "prefix": OPENING_PREFIX_PLIES,
    }
    node: Row = {}
    children: list[Row] = []
    for r in conn.execute(query, params).fetchall():
        if r["kind"] == "node":
            node = {**_numbers(r), "continued": int(r["continued"])}
        else:
            children.append({"san": _canonical(board, str(r["san"])), **_numbers(r)})
    return node, children


def prescribed(entry: Row) -> list[str]:
    """The moves a repertoire reading prescribes to the player: none unless it is a match,
    an agreement or a conflict."""
    return [str(m["san"]) for m in entry["moves"]] if entry["status"] in PRESCRIBING else []


def judge(before: chess.Board, move: chess.Move, entry: Row) -> Row:
    """`arrival` for `move` played from `before`, given the repertoire's reading there:
    `off_book` exactly when it prescribes a non-empty set without the move (compared as parsed
    moves), with that set as `book_moves`."""
    book = prescribed(entry)
    parsed = {before.parse_san(san) for san in book}
    return {"off_book": bool(book) and move not in parsed, "book_moves": book}


def arrival(conn: Connection[Any], colour: str, path: list[tuple[chess.Board, chess.Move]]) -> Row | None:
    """Whether the path's last move left the book: None when there is no last move or it was the
    opponent's, else `judge` at the board before it."""
    if not path:
        return None
    before, move = path[-1]
    if before.turn != (colour == "white"):
        return None
    return judge(before, move, read.board_moves(conn, before.fen(), colour))


def answer(
    scope: Scope,
    colour: str,
    board: chess.Board,
    ply: int,
    repertoire: Row,
    stats: tuple[Row, list[Row]] | None,
    arrived: Row | None,
) -> Row:
    """The answer for a board from its reads: `repertoire` from `read.board_moves`, `stats` from
    `moves_from` (None past the prefix) and `arrived` from `arrival`."""
    node, rows = (stats[0], [dict(r) for r in stats[1]]) if stats is not None else (None, [])
    book = {str(m["san"]) for m in repertoire["moves"]}
    seen = {str(r["san"]) for r in rows}
    for m in repertoire["moves"]:
        if str(m["san"]) not in seen:
            rows.append(
                {"san": m["san"], "n": 0, "w": 0, "d": 0, "l": 0, "score": None, "n_rated": 0, "vs_expectation": None}
            )
    for r in rows:
        r["in_repertoire"] = str(r["san"]) in book
    # Most played first, then the repertoire's, then by SAN in byte order.
    rows.sort(key=lambda r: (-int(r["n"]), not r["in_repertoire"], str(r["san"]).encode()))
    return {
        "colour": colour,
        "fen": board.fen(),
        "ply": ply,
        "player_to_move": board.turn == (colour == "white"),
        "repertoire": {
            "status": repertoire["status"],
            "moves": [
                {
                    "san": m["san"],
                    "book": m["book"],
                    "chapter": m["chapter"],
                    "line_name": m["line_name"],
                    "line_id": m["line_id"],
                    "line_ply": m["line_ply"],
                    "lines": m["lines"],
                }
                for m in repertoire["moves"]
            ],
        },
        "node": node,
        "moves": rows,
        "arrival": arrived,
        "lines_through": repertoire["lines_through"],
        "months": scope.months,
        "time_class": scope.time_class,
        "beyond_prefix": ply >= OPENING_PREFIX_PLIES,
    }


def position(conn: Connection[Any], scope: Scope, colour: str, tokens: list[str]) -> Row:
    """The Opening Explorer's answer for the board the path reaches."""
    path = replay(tokens)
    board = board_after(path)
    ply = len(path)
    repertoire = read.board_moves(conn, board.fen(), colour)
    stats = moves_from(conn, scope, colour, board) if ply < OPENING_PREFIX_PLIES else None
    return answer(scope, colour, board, ply, repertoire, stats, arrival(conn, colour, path))


def lines(conn: Connection[Any], colour: str, tokens: list[str], *, offset: int, limit: int) -> Row:
    """The lines holding the board the path reaches, one page."""
    board = board_after(replay(tokens))
    page = read.board_lines(conn, board.fen(), colour, offset=offset, limit=limit)
    return {**page, "offset": offset, "page_size": limit}
