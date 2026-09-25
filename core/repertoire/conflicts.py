"""Where the repertoire disagrees with itself, and the gate that keeps it from doing so.

One relation, `REP_SIGNATURES`, is the definition of "a line prescribes a move at a position":
every line's (position, move) at the plies where the book's side is to move, the position the
full FEN (counters included) and the move the raw token, exactly as `importing.signatures`
keys the import gate. A line is *effective* when it, its chapter and its book are all on. A
line whose stored spine is not `len(moves) + 1` long contributes nothing, as it contributes
nothing to the import gate.

`listing` is the Conflicts page: positions where lines in any state prescribe two or more
distinct moves; a position is *contested* when two effective lines disagree there (the
positions `read.project_ply` fails closed on). `duplicates` is the page's second section:
identical lines in different chapters. `contested_count` is the Repertoire page's number.
`gate` judges what a toggle is about to switch on with the import's own rule
(`importing.decide`) against the effectively-active repertoire, and names, for each refused
line, the first position that blocked it and the lines holding that position against it.

Everything here is read-only and runs under the caller's `matching.lock`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, LiteralString, cast

from psycopg import Connection

from core.constants import PLAYER_ID
from core.repertoire import annotations, importing

Row = dict[str, Any]
Kind = Literal["books", "chapters", "lines"]

REP_SIGNATURES = """
SELECT rl.id AS line_id, rl.line_name, rl.active AS line_active,
       ch.id AS chapter_id, ch.title AS chapter_title, ch.active AS chapter_active,
       bk.id AS book_id, bk.title AS book_title, bk.active AS book_active, bk.color,
       (rl.active AND ch.active AND bk.active) AS effective,
       s.fen, rl.moves->>((s.ord - 1)::int) AS move
FROM repertoire_lines rl
JOIN chapters ch ON ch.id = rl.chapter_id
JOIN books bk ON bk.id = ch.book_id,
LATERAL jsonb_array_elements_text(rl.fen_sequence) WITH ORDINALITY AS s(fen, ord)
WHERE bk.player_id = %(pid)s
  AND jsonb_array_length(rl.fen_sequence) = jsonb_array_length(rl.moves) + 1
  AND s.ord <= jsonb_array_length(rl.moves)
  AND split_part(s.fen, ' ', 2) = CASE bk.color WHEN 'white' THEN 'w' ELSE 'b' END
"""

Reason = Literal["anchor", "dirty", "cohort"]


@dataclass(frozen=True)
class Refusal:
    """Why one line may not come on: the first position (in line order) that blocked it,
    the move it plays there, and the lines that hold the position against it."""

    line_id: int
    line_name: str
    chapter_title: str
    fen: str
    move: str
    reason: Reason
    rivals: list[Row]

    def as_row(self) -> Row:
        return asdict(self)


def _line_row(r: Row) -> Row:
    return {
        "line_id": int(r["line_id"]),
        "line_name": r["line_name"],
        "chapter_id": int(r["chapter_id"]),
        "chapter_title": r["chapter_title"],
        "book_id": int(r["book_id"]),
        "book_title": r["book_title"],
        "move": r["move"],
    }


# --- the listing --------------------------------------------------------------------


def listing(conn: Connection[Any]) -> list[Row]:
    """Positions where the player's lines, in any state, prescribe two or more distinct
    moves. Contested positions first, then by total lines descending, then by FEN; moves
    within a position by effective count descending then by move. The conflicted FENs are
    an array the rows are filtered by: a join between the two CTE scans is planned as a
    nested loop over 45k rows per conflicted position (1 s vs 0.2 s on the real data)."""
    rows = conn.execute(
        f"""
        WITH sig AS MATERIALIZED ({REP_SIGNATURES}),
             at AS MATERIALIZED (SELECT fen FROM sig GROUP BY fen HAVING count(DISTINCT move) > 1)
        SELECT sig.* FROM sig WHERE sig.fen = ANY(ARRAY(SELECT fen FROM at))
        ORDER BY fen, move, book_title, chapter_title, line_name, line_id
        """,
        {"pid": PLAYER_ID},
    ).fetchall()
    by_fen: dict[str, dict[str, list[Row]]] = {}
    for r in rows:
        by_fen.setdefault(str(r["fen"]), {}).setdefault(str(r["move"]), []).append(
            {
                **_line_row(r),
                "line_active": bool(r["line_active"]),
                "chapter_active": bool(r["chapter_active"]),
                "book_active": bool(r["book_active"]),
                "effective": bool(r["effective"]),
            }
        )
    out: list[Row] = []
    for fen, by_move in by_fen.items():
        effective_of = {m: sum(1 for ln in lines if ln["effective"]) for m, lines in by_move.items()}
        moves: list[Row] = [{"move": m, "lines": lines} for m, lines in by_move.items()]
        moves.sort(key=lambda g: (-effective_of[str(g["move"])], str(g["move"])))
        active_moves = sum(1 for n in effective_of.values() if n > 0)
        out.append(
            {
                "fen": fen,
                "color": "white" if fen.split(" ")[1] == "w" else "black",
                "contested": active_moves > 1,
                "active_moves": active_moves,
                "moves": moves,
            }
        )
    out.sort(key=lambda p: (not p["contested"], -sum(len(g["lines"]) for g in p["moves"]), p["fen"]))
    return out


def duplicates(conn: Connection[Any]) -> list[Row]:
    """Groups of two or more lines with the same book colour, the same first position and
    the same move list, in different chapters: identical lines that never diverge, so they
    never reach `listing`. Each group carries its `root` (the first position), which with the
    colour and the moves is its identity: the same moves from two starts are two groups.
    Ordered by effective lines descending, then by moves."""
    rows = conn.execute(
        """
        WITH keys AS (
            SELECT bk.color, rl.fen_sequence->>0 AS root, rl.moves
            FROM repertoire_lines rl
            JOIN chapters ch ON ch.id = rl.chapter_id
            JOIN books bk ON bk.id = ch.book_id
            WHERE bk.player_id = %(pid)s
            GROUP BY bk.color, rl.fen_sequence->>0, rl.moves
            HAVING count(DISTINCT rl.chapter_id) > 1
        )
        SELECT rl.id AS line_id, rl.line_name, rl.active AS line_active, rl.moves,
               ch.id AS chapter_id, ch.title AS chapter_title, ch.active AS chapter_active,
               bk.id AS book_id, bk.title AS book_title, bk.active AS book_active, bk.color,
               (rl.active AND ch.active AND bk.active) AS effective,
               k.root
        FROM repertoire_lines rl
        JOIN chapters ch ON ch.id = rl.chapter_id
        JOIN books bk ON bk.id = ch.book_id
        JOIN keys k ON k.color = bk.color AND k.root IS NOT DISTINCT FROM rl.fen_sequence->>0 AND k.moves = rl.moves
        WHERE bk.player_id = %(pid)s
        ORDER BY bk.color DESC, rl.moves::text, bk.title, ch.title, rl.line_name, rl.id
        """,
        {"pid": PLAYER_ID},
    ).fetchall()
    groups: dict[tuple[str, str | None, str], Row] = {}
    for r in rows:
        moves = annotations.strings(r["moves"])
        key = (str(r["color"]), r["root"], " ".join(moves))
        g = groups.setdefault(key, {"color": r["color"], "root": r["root"], "moves": moves, "lines": []})
        g["lines"].append(
            {
                **_line_row({**r, "move": None}),
                "line_active": bool(r["line_active"]),
                "chapter_active": bool(r["chapter_active"]),
                "book_active": bool(r["book_active"]),
                "effective": bool(r["effective"]),
            }
        )
    for g in groups.values():
        for ln in g["lines"]:
            del ln["move"]
    out = list(groups.values())
    out.sort(key=lambda g: (-sum(1 for ln in g["lines"] if ln["effective"]), g["moves"]))
    return out


def contested_count(conn: Connection[Any]) -> int:
    """Positions where two or more effective lines disagree: the Repertoire page's number."""
    row = conn.execute(
        f"""
        SELECT count(*) AS n FROM (
            SELECT fen FROM ({REP_SIGNATURES}) sig WHERE effective GROUP BY fen HAVING count(DISTINCT move) > 1
        ) contested
        """,
        {"pid": PLAYER_ID},
    ).fetchone()
    assert row is not None
    return int(row["n"])


# --- the gate ----------------------------------------------------------------------

_CANDIDATES: dict[str, str] = {
    "lines": "rl.id = %(id)s",
    "chapters": "rl.chapter_id = %(id)s AND rl.active",
    "books": "ch.book_id = %(id)s AND rl.active AND ch.active",
}


def candidates(conn: Connection[Any], kind: Kind, id: int) -> list[importing.Prepared]:
    """The lines a toggle of `kind`/`id` would bring into play, as the import gate takes
    them: a line → itself; a chapter → its lines that are on; a book → its lines that are on
    under chapters that are on. `(book_ix, chapter_ix)` is the chapter's ordinal in the
    Repertoire page's own order, so a tie inside a course goes to the chapter the course
    lists first, as it does on import."""
    query = cast(
        LiteralString,
        f"""
        SELECT rl.id AS line_id, rl.line_name, rl.moves, rl.fen_sequence, bk.color,
               dense_rank() OVER (ORDER BY ch.source_chapter_id NULLS LAST, ch.title, ch.id) - 1 AS chapter_ix
        FROM repertoire_lines rl
        JOIN chapters ch ON ch.id = rl.chapter_id
        JOIN books bk ON bk.id = ch.book_id
        WHERE bk.player_id = %(pid)s AND {_CANDIDATES[kind]}
        ORDER BY chapter_ix, rl.id
        """,
    )  # the fragment is one of three literals keyed by a Literal type; values are bound
    rows = conn.execute(query, {"pid": PLAYER_ID, "id": id}).fetchall()
    out: list[importing.Prepared] = []
    for r in rows:
        moves, fens = annotations.strings(r["moves"]), annotations.strings(r["fen_sequence"])
        if not moves or len(fens) != len(moves) + 1:
            continue
        line = importing.LineIn(name=str(r["line_name"]), moves=moves)
        sigs = importing.signatures(moves, fens, str(r["color"]))
        out.append(importing.Prepared(0, int(r["chapter_ix"]), line, fens, sigs, int(r["line_id"])))
    return out


def _holders(conn: Connection[Any], fen: str, move: str) -> list[Row]:
    """The effective lines at `fen` prescribing something other than `move`."""
    rows = conn.execute(
        f"SELECT * FROM ({REP_SIGNATURES}) sig WHERE effective AND fen = %(fen)s AND move <> %(move)s"
        " ORDER BY move, book_title, chapter_title, line_name, line_id",
        {"pid": PLAYER_ID, "fen": fen, "move": move},
    ).fetchall()
    return [_line_row(r) for r in rows]


def gate(conn: Connection[Any], kind: Kind, id: int, *, batch: list[importing.Prepared] | None = None) -> list[Refusal]:
    """What the toggle may not switch on. The candidates are judged by `importing.decide`
    against every effectively-active line; nothing is excluded from that index because a
    candidate is, by the caller's no-op check, not effectively active itself. A batch of one
    has no cohort, so a single line is refused only for `anchor` or `dirty`. `batch` lets a
    caller that already read the candidates pass them in."""
    if batch is None:
        batch = candidates(conn, kind, id)
    if not batch:
        return []
    existing, dirty = importing.existing_index(conn)
    verdicts = importing.decide(existing, dirty, batch)
    meta = _metas(conn, [p.line_id for p in batch if p.line_id is not None])
    out: list[Refusal] = []
    accepted = {p.line_id for p, v in zip(batch, verdicts, strict=True) if v.active}
    for p, v in zip(batch, verdicts, strict=True):
        if v.active:
            continue
        assert v.fen is not None and v.move is not None and v.reason is not None and p.line_id is not None
        if v.reason == "cohort":
            # The fellow candidates that carried the winning move and are coming on. A winner
            # refused at another position still voted (the import's rule, kept on purpose) but
            # does not hold the position, so it is not named.
            winner = v.rival
            assert winner is not None
            rivals = [
                {**meta[q.line_id], "move": winner}
                for q in batch
                if q.line_id is not None and q.line_id in accepted and (v.fen, winner) in q.signatures
            ]
        else:
            rivals = _holders(conn, v.fen, v.move)
        out.append(Refusal(p.line_id, p.line.name, meta[p.line_id]["chapter_title"], v.fen, v.move, v.reason, rivals))
    return out


def _metas(conn: Connection[Any], line_ids: list[int]) -> dict[int, Row]:
    """The line shape (without a move) for these lines, in one read."""
    rows = conn.execute(
        "SELECT rl.id AS line_id, rl.line_name, ch.id AS chapter_id, ch.title AS chapter_title,"
        " bk.id AS book_id, bk.title AS book_title FROM repertoire_lines rl"
        " JOIN chapters ch ON ch.id = rl.chapter_id JOIN books bk ON bk.id = ch.book_id WHERE rl.id = ANY(%s)",
        (line_ids,),
    ).fetchall()
    out: dict[int, Row] = {}
    for r in rows:
        row = _line_row({**r, "move": None})
        del row["move"]
        out[int(r["line_id"])] = row
    return out
