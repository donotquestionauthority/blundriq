"""Find a two-move pair in the repertoire: one side moves a piece to a square, the other side
answers, anywhere in a line.

The question is "where does this pattern occur", which neither compare surface asks: Similar
positions keeps boards of the same material near the queried board, and branch compare looks one
half-move back inside one puzzle. A pair is two consecutive plies of an effectively active line in
a book of the player's colour; move 1 is played by the side `first` names (the side to move is read
from the stored FEN, never from the ply's parity, because a chapter may be rooted at a tabiya).

Matching is two steps. The SQL is a superset filter of two clauses. On the stored text: the token
names the target square, or, for the king to a castling square, names the king's home square or
carries an `o`/`0` (the importer keeps any spelling `parse_san` accepts, so the text has no reliable
piece shape and is never read for one). On the stored board after the move: the target square holds
the mover's piece of the searched kind, or, for a pawn to the last rank, a promoted piece (after a
legal move of X to s, s holds X or the promotion; the stored sequence is the importer's replay of
the moves). Neither identifies the piece that moved, so Python decides: `parse_san` on the stored
board (the null move refused), the moving piece's type and its destination, castling read as the
king's destination. A stored move that does not parse is not a match; it is counted.

A row is **in practice** when its line has a visible repertoire puzzle whose solution is still the
line's moves (the gate and the segment are `visibility.repertoire_rows_sql`'s) and both moves lie
in the segment the solver is given, `solution_line[0 .. presentation_ply]`. The database orders the
verified rows (in practice first, then book, chapter and line name under `COLLATE "C"`, then ply
and line id) and returns names, the root board and the moves to the pair for the rows shown only;
`total` and `replies` describe every verified row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, LiteralString, cast

import chess
from psycopg import Connection

from core.constants import MOVE_PAIR_MAX_ROWS, PLAYER_ID
from core.puzzles import visibility

_log = logging.getLogger(__name__)

Row = dict[str, Any]
Colour = Literal["white", "black"]
Mover = Literal["me", "opponent"]

PIECES: dict[str, chess.PieceType] = {
    "K": chess.KING,
    "Q": chess.QUEEN,
    "R": chess.ROOK,
    "B": chess.BISHOP,
    "N": chess.KNIGHT,
    "P": chess.PAWN,
}
SQUARES = frozenset(chess.SQUARE_NAMES)
CASTLE_SQUARES = frozenset({"g1", "c1", "g8", "c8"})


@dataclass(frozen=True)
class Target:
    """A piece and the square it goes to."""

    piece: str
    square: str

    def __post_init__(self) -> None:
        if self.piece not in PIECES or self.square not in SQUARES:
            raise ValueError("not a piece and a square")


def _clause(prefix: str) -> LiteralString:
    """The superset filter on `t<prefix>` (the lower-cased stored token), binding `sq<prefix>`,
    `castle<prefix>` and `home<prefix>`."""
    return cast(
        LiteralString,
        f"(strpos(t{prefix}, %(sq{prefix})s) > 0 OR (%(castle{prefix})s AND (strpos(t{prefix}, %(home{prefix})s) > 0"
        f" OR strpos(t{prefix}, 'o') > 0 OR strpos(t{prefix}, '0') > 0)))",
    )


def _expanded(placement: LiteralString) -> LiteralString:
    """The 64 squares of a FEN placement as text, a8 first: each digit expanded to that many `1`s,
    the ranks joined (`neighbourhood.expand_placement` in SQL, so no function and no migration)."""
    out = placement
    for d in "8765432":
        out = f"replace({out}, '{d}', '{'1' * int(d)}')"
    return f"replace({out}, '/', '')"


def _board_clause(prefix: str, fen_after: LiteralString) -> LiteralString:
    """The target square on the board after the move holds an accepted piece; binds `idx<prefix>`
    and `pieces<prefix>`."""
    placement = "split_part(" + fen_after + ", ' ', 1)"
    return cast(
        LiteralString,
        "substr(" + _expanded(placement) + f", %(idx{prefix})s, 1) = ANY(%(pieces{prefix})s::text[])",
    )


def _accepted(target: Target, mover: str) -> list[str]:
    """The FEN letters, in the mover's case, that may stand on the target square after the move:
    the piece itself, or for a pawn to the mover's last rank any piece it may promote to."""
    last = "8" if mover == "w" else "1"
    letters = ["Q", "R", "B", "N"] if target.piece == "P" and target.square[1] == last else [target.piece]
    return letters if mover == "w" else [x.lower() for x in letters]


def _params(prefix: str, target: Target | None, mover: str) -> dict[str, Any]:
    if target is None:
        return {
            f"sq{prefix}": "",
            f"castle{prefix}": False,
            f"home{prefix}": "",
            f"idx{prefix}": 1,
            f"pieces{prefix}": [],
        }
    castle = target.piece == "K" and target.square in CASTLE_SQUARES
    square = chess.parse_square(target.square)
    return {
        f"sq{prefix}": target.square,
        f"castle{prefix}": castle,
        f"home{prefix}": ("e1" if target.square[1] == "1" else "e8") if castle else "",
        f"idx{prefix}": (7 - chess.square_rank(square)) * 8 + chess.square_file(square) + 1,
        f"pieces{prefix}": _accepted(target, mover),
    }


# One row per kept ply, five narrow columns: names and the root board are read afterwards for the
# rows shown only. Ply i is moves[i] (0-based) from fen_sequence[i]; its reply is moves[i + 1]; the
# boards after them are fen_sequence[i + 1] and [i + 2].
_CANDIDATES_SQL: LiteralString = f"""
SELECT rl.id AS line_id, (m.ord - 1)::int AS i, rl.fen_sequence ->> (m.ord - 1)::int AS fen,
       m.mv AS mv1, rl.moves ->> m.ord::int AS mv2
FROM repertoire_lines rl
JOIN chapters ch ON ch.id = rl.chapter_id AND ch.active
JOIN books bk ON bk.id = ch.book_id AND bk.active AND bk.player_id = %(pid)s
CROSS JOIN LATERAL jsonb_array_elements_text(rl.moves) WITH ORDINALITY AS m(mv, ord)
CROSS JOIN LATERAL (SELECT lower(m.mv) AS t1, lower(rl.moves ->> m.ord::int) AS t2) tok
WHERE rl.active AND bk.color = %(colour)s AND rl.fen_sequence IS NOT NULL
  AND jsonb_array_length(rl.fen_sequence) = jsonb_array_length(rl.moves) + 1
  AND tok.t2 IS NOT NULL AND split_part(rl.fen_sequence ->> (m.ord - 1)::int, ' ', 2) = %(side)s
  AND {_clause("1")} AND {_board_clause("1", "rl.fen_sequence ->> m.ord::int")}
  AND (NOT %(has2)s OR ({_clause("2")} AND {_board_clause("2", "rl.fen_sequence ->> (m.ord + 1)::int")}))
"""

# The verified rows, ordered and cut by the database, with what a shown row needs: names, the root
# board and the stored moves through the pair. `k` is the row's 1-based index into the arrays.
_ORDERED_SQL: LiteralString = """
SELECT top.k, top.line_name, top.chapter_title, top.book_title, rl.fen_sequence ->> 0 AS root_fen,
       ARRAY(SELECT x FROM jsonb_array_elements_text(rl.moves) WITH ORDINALITY AS t(x, o)
             WHERE o <= top.ply + 2 ORDER BY o) AS moves
FROM (
    SELECT w.k, w.line_id, w.ply, w.in_practice, rl.line_name, ch.title AS chapter_title, bk.title AS book_title
    FROM unnest(%(ids)s::int[], %(plies)s::int[], %(flags)s::bool[]) WITH ORDINALITY AS w(line_id, ply, in_practice, k)
    JOIN repertoire_lines rl ON rl.id = w.line_id
    JOIN chapters ch ON ch.id = rl.chapter_id
    JOIN books bk ON bk.id = ch.book_id
    ORDER BY NOT w.in_practice, bk.title COLLATE "C", ch.title COLLATE "C", rl.line_name COLLATE "C", w.ply, w.line_id
    LIMIT %(lim)s
) top
JOIN repertoire_lines rl ON rl.id = top.line_id
ORDER BY NOT top.in_practice, top.book_title COLLATE "C", top.chapter_title COLLATE "C",
         top.line_name COLLATE "C", top.ply, top.line_id
"""


def _parse(board: chess.Board, token: str) -> chess.Move | None:
    try:
        move = board.parse_san(token)
    except ValueError:
        return None
    return move if move else None  # the null move parses; it is not a move


def _matches(board: chess.Board, move: chess.Move, target: Target | None) -> bool:
    """The moving piece and where it lands. On a standard board python-chess encodes castling as
    the king's move to its destination (`e1g1`), whatever spelling it was parsed from."""
    if target is None:
        return True
    return board.piece_type_at(move.from_square) == PIECES[target.piece] and move.to_square == chess.parse_square(
        target.square
    )


def _move_number(board: chess.Board) -> str:
    return f"{board.fullmove_number}." if board.turn == chess.WHITE else f"{board.fullmove_number}…"


def _presentation(conn: Connection[Any], line_ids: list[int], lookahead_plies: int) -> dict[int, int]:
    """The largest served segment end (`presentation_ply`) of the visible puzzles of these lines.
    A puzzle whose solution is no longer its line's moves (the line changed; the generator rebuilds
    it on its next run) is not counted: its plies do not index the line."""
    if not line_ids:
        return {}
    inner = visibility.repertoire_rows_sql(lookahead_plies, collapse=False, lines=True)
    sql = (
        "SELECT v.repertoire_line_id, max(v.presentation_ply) AS ply FROM ("
        + inner
        + ") v JOIN repertoire_lines rl ON rl.id = v.repertoire_line_id AND rl.moves = v.solution_line"
        " WHERE v.repertoire_line_id = ANY(%(ids)s) AND v.presentation_ply IS NOT NULL"
        " GROUP BY v.repertoire_line_id"
    )
    rows = conn.execute(sql, {"pid": PLAYER_ID, "ids": line_ids, "scope_ids": line_ids}).fetchall()
    return {int(r["repertoire_line_id"]): int(r["ply"]) for r in rows}


def _matched(
    conn: Connection[Any], *, colour: Colour, first: Mover, move1: Target, move2: Target | None, lookahead_plies: int
) -> tuple[list[Row], int]:
    """Every verified occurrence, unordered and without names, with its in-practice flag, and the
    count of stored plies the filter kept that did not parse."""
    me = "w" if colour == "white" else "b"
    side = me if first == "me" else ("b" if me == "w" else "w")
    other = "b" if side == "w" else "w"
    params: dict[str, Any] = {
        "pid": PLAYER_ID,
        "colour": colour,
        "side": side,
        "has2": move2 is not None,
        **_params("1", move1, side),
        **_params("2", move2, other),
    }
    candidates = conn.execute(_CANDIDATES_SQL, params).fetchall()
    dropped = 0
    found: list[Row] = []
    for c in candidates:
        try:
            board = chess.Board(str(c["fen"]))
        except ValueError:
            dropped += 1
            continue
        m1 = _parse(board, str(c["mv1"]))
        if m1 is None:
            dropped += 1
            continue
        if not _matches(board, m1, move1):
            continue
        board.push(m1)
        m2 = _parse(board, str(c["mv2"]))
        if m2 is None:
            dropped += 1
            continue
        if not _matches(board, m2, move2):
            continue
        found.append(
            {
                "lineId": int(c["line_id"]),
                "ply": int(c["i"]),
                "san2": board.san(m2),
                "piece2": chess.piece_symbol(board.piece_type_at(m2.from_square) or chess.PAWN).upper(),
                "square2": chess.square_name(m2.to_square),
                "_fen": str(c["fen"]),
                "_moves": (m1, m2),
            }
        )
    segment = _presentation(conn, sorted({r["lineId"] for r in found}), lookahead_plies)
    for r in found:
        end = segment.get(r["lineId"])
        r["inPractice"] = end is not None and r["ply"] + 1 <= end
    if dropped:
        _log.warning("move-pair search: %d stored plies did not parse", dropped)
    return found, dropped


def _display(row: Row) -> Row:
    """A verified row as the response carries it: the pair's label and boards, built only for the
    rows returned (`_fen` is the board before move 1, `_moves` the two parsed moves)."""
    m1, m2 = row["_moves"]
    board = chess.Board(row["_fen"])
    number1, san1 = _move_number(board), board.san(m1)
    board.push(m1)
    fen_before2 = board.fen()
    number2 = _move_number(board) if board.turn == chess.WHITE else ""
    board.push(m2)
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    out.update(
        label=f"{number1}{san1} {number2}{row['san2']}",
        san1=san1,
        move2Uci=m2.uci(),
        fenBefore2=fen_before2,
        fenAfter=board.fen(),
    )
    return out


def _ordered(conn: Connection[Any], rows: list[Row], limit: int | None) -> list[Row]:
    """The rows in the response's order, at most `limit` (None: all), each with its names, root
    board and `movesToPair` (the stored moves through the pair, as the server's SAN)."""
    if not rows:
        return []
    params = {
        "ids": [r["lineId"] for r in rows],
        "plies": [r["ply"] for r in rows],
        "flags": [r["inPractice"] for r in rows],
        "lim": limit,
    }
    out: list[Row] = []
    for o in conn.execute(_ORDERED_SQL, params).fetchall():
        r = _display(rows[int(o["k"]) - 1])
        r.update(book=str(o["book_title"]), chapter=str(o["chapter_title"]), line=str(o["line_name"]))
        r["rootFen"] = str(o["root_fen"])
        r["movesToPair"] = _moves_to_pair(r["rootFen"], [str(x) for x in o["moves"]])
        out.append(r)
    return out


def verified_rows(
    conn: Connection[Any], *, colour: Colour, first: Mover, move1: Target, move2: Target | None, lookahead_plies: int
) -> tuple[list[Row], int]:
    """Every verified row in the response's order, before the cap, and the count of stored plies
    the filter kept that did not parse. The uncapped surface the parity checks compare (the tests
    and the acceptance script); the route calls `search`."""
    found, dropped = _matched(
        conn, colour=colour, first=first, move1=move1, move2=move2, lookahead_plies=lookahead_plies
    )
    return _ordered(conn, found, None), dropped


def _replies(rows: list[Row]) -> list[Row]:
    """Move 2 grouped as the search reads it, piece and destination: `Nbd7` and `Nfd7` are one
    entry, so its count is what searching it returns."""
    acc: dict[tuple[str, str], Row] = {}
    for r in rows:
        e = acc.setdefault(
            (r["piece2"], r["square2"]),
            {"piece": r["piece2"], "square": r["square2"], "sans": [], "count": 0, "inPractice": 0},
        )
        if r["san2"] not in e["sans"]:
            e["sans"].append(r["san2"])
        e["count"] += 1
        e["inPractice"] += 1 if r["inPractice"] else 0
    for e in acc.values():
        e["sans"].sort()
    return sorted(acc.values(), key=lambda e: (-e["inPractice"], -e["count"], e["sans"][0]))


def _moves_to_pair(root_fen: str, tokens: list[str]) -> list[str]:
    """The stored moves from the line's root, re-rendered as the server's SAN."""
    board = chess.Board(root_fen)
    out: list[str] = []
    for token in tokens:
        move = _parse(board, token)
        if move is None:
            break
        out.append(board.san(move))
        board.push(move)
    return out


def search(
    conn: Connection[Any], *, colour: Colour, first: Mover, move1: Target, move2: Target | None, lookahead_plies: int
) -> Row:
    """The rows (at most `MOVE_PAIR_MAX_ROWS`), the replies summary when move 2 is open, `total`
    and `truncated`."""
    found, _ = _matched(conn, colour=colour, first=first, move1=move1, move2=move2, lookahead_plies=lookahead_plies)
    return {
        "rows": _ordered(conn, found, MOVE_PAIR_MAX_ROWS),
        "replies": _replies(found) if move2 is None else None,
        "total": len(found),
        "truncated": len(found) > MOVE_PAIR_MAX_ROWS,
    }
