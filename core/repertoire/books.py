"""The Repertoire page's reads, and the one write it has: switching a book, chapter or line
on or off.

Switching something on is gated. A line, or the lines a chapter or book would bring into
play, must agree with every line that is already effectively active at each position where
the book's side is to move — the import's own rule (`importing.decide`, applied by
`conflicts.gate`). A refused line is not switched on (`Refused`, nothing written); a chapter
or book still comes on, with the lines the gate refused switched off in the same transaction
and reported as `held_back`, so a container never brings a contested position with it. A
target that is already on is a no-op — nothing is gated, written or rematched, because a line
that is on is an anchor, never a candidate — and a flip under a container that is off changes
nothing the matcher can see, so it is not gated either; the gate runs when the container
comes on. Switching off is never gated: it is the one remedy for a deviation that keeps
recurring because a line should not be active (dismissing a deviation was removed for that
reason). After a toggle the games it can affect are matched again in the same transaction,
so the Deviations page reflects it at once; repertoire puzzles follow on the next
`generate-puzzles` (an inactive line's puzzle is switched off with its SRS state kept).
"""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from core.constants import PLAYER_ID
from core.repertoire import annotations, conflicts, matching
from core.repertoire.conflicts import Kind, Refusal

Row = dict[str, Any]


class Refused(Exception):
    """A line the gate would not switch on. Nothing was written."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.reason)
        self.refusal = refusal


def books(conn: Connection[Any]) -> list[Row]:
    """Every book with its line counts. `active_lines` counts the line flag alone, a display
    count; whether a line is in play also depends on its chapter and book."""
    rows = conn.execute(
        """
        SELECT bk.id AS book_id, bk.title, bk.color, bk.active, bk.source_url, bk.source_author, bk.source_title,
               count(rl.id) AS total_lines, count(rl.id) FILTER (WHERE rl.active) AS active_lines
        FROM books bk
        LEFT JOIN chapters ch ON ch.book_id = bk.id
        LEFT JOIN repertoire_lines rl ON rl.chapter_id = ch.id
        WHERE bk.player_id = %s
        GROUP BY bk.id
        ORDER BY bk.color DESC, bk.title, bk.id
        """,
        (PLAYER_ID,),
    ).fetchall()
    return [
        {
            "book_id": r["book_id"],
            "title": r["title"],
            "color": r["color"],
            "active": bool(r["active"]),
            "source_url": r["source_url"],
            "source_author": r["source_author"],
            "source_title": r["source_title"],
            "total_lines": int(r["total_lines"]),
            "active_lines": int(r["active_lines"]),
        }
        for r in rows
    ]


def sections(conn: Connection[Any], book_id: int) -> list[Row] | None:
    """A book's chapters and their lines in the course's own order (chapters by source id,
    lines by insertion), or None when the book is not the player's."""
    owned = conn.execute("SELECT 1 FROM books WHERE id = %s AND player_id = %s", (book_id, PLAYER_ID)).fetchone()
    if owned is None:
        return None
    rows = conn.execute(
        """
        SELECT ch.id AS chapter_id, ch.title, ch.active AS chapter_active, ch.root_fen,
               rl.id AS line_id, rl.line_name, rl.moves, rl.active AS line_active, rl.is_alternative
        FROM chapters ch
        LEFT JOIN repertoire_lines rl ON rl.chapter_id = ch.id
        WHERE ch.book_id = %s
        ORDER BY ch.source_chapter_id NULLS LAST, ch.title, ch.id, rl.id
        """,
        (book_id,),
    ).fetchall()
    out: list[Row] = []
    lines_of: dict[int, list[Row]] = {}
    for r in rows:
        cid = int(r["chapter_id"])
        if cid not in lines_of:
            lines_of[cid] = []
            out.append(
                {
                    "chapter_id": r["chapter_id"],
                    "title": r["title"],
                    "active": bool(r["chapter_active"]),
                    "root_fen": r["root_fen"],
                    "lines": lines_of[cid],
                }
            )
        if r["line_id"] is not None:
            lines_of[cid].append(
                {
                    "id": r["line_id"],
                    "name": r["line_name"],
                    "moves": annotations.strings(r["moves"]),
                    "active": bool(r["line_active"]),
                    "is_alternative": bool(r["is_alternative"]),
                }
            )
    return out


_LINES_OF: dict[str, str] = {
    "books": "SELECT rl.id FROM repertoire_lines rl JOIN chapters ch ON ch.id = rl.chapter_id WHERE ch.book_id = %s",
    "chapters": "SELECT rl.id FROM repertoire_lines rl WHERE rl.chapter_id = %s",
    "lines": "SELECT rl.id FROM repertoire_lines rl WHERE rl.id = %s",
}

_OWNED: dict[str, str] = {
    "books": "UPDATE books SET active = %(active)s WHERE id = %(id)s AND player_id = %(pid)s",
    "chapters": (
        "UPDATE chapters ch SET active = %(active)s FROM books bk"
        " WHERE ch.id = %(id)s AND bk.id = ch.book_id AND bk.player_id = %(pid)s"
    ),
    "lines": (
        "UPDATE repertoire_lines rl SET active = %(active)s FROM chapters ch, books bk"
        " WHERE rl.id = %(id)s AND ch.id = rl.chapter_id AND bk.id = ch.book_id AND bk.player_id = %(pid)s"
    ),
}


_STATE: dict[str, str] = {
    "books": (
        "SELECT bk.active AS target, TRUE AS parents FROM books bk WHERE bk.id = %(id)s AND bk.player_id = %(pid)s"
    ),
    "chapters": (
        "SELECT ch.active AS target, bk.active AS parents FROM chapters ch JOIN books bk ON bk.id = ch.book_id"
        " WHERE ch.id = %(id)s AND bk.player_id = %(pid)s"
    ),
    "lines": (
        "SELECT rl.active AS target, (ch.active AND bk.active) AS parents FROM repertoire_lines rl"
        " JOIN chapters ch ON ch.id = rl.chapter_id JOIN books bk ON bk.id = ch.book_id"
        " WHERE rl.id = %(id)s AND bk.player_id = %(pid)s"
    ),
}

_NO_REMATCH = {"candidates": 0, "matched": 0, "no_match": 0, "lines": 0}


def set_active(conn: Connection[Any], kind: Kind, id: int, active: bool, window: int) -> Row | None:
    """Flip one flag and match again the games it can affect. None when nothing matched the
    id; otherwise `{rematch, held_back}` — the rematch counts and the lines a container
    coming on could not bring with it. Raises `Refused` for a line the gate refuses."""
    with conn.transaction():
        matching.lock(conn)
        refused: list[Refusal] = []
        line_ids: list[int] | None = None
        if active:
            state = conn.execute(_STATE[kind], {"id": id, "pid": PLAYER_ID}).fetchone()  # type: ignore[arg-type]
            if state is None:
                return None
            if state["target"]:
                return {"rematch": dict(_NO_REMATCH), "held_back": []}
            if state["parents"]:
                batch = conflicts.candidates(conn, kind, id)
                refused = conflicts.gate(conn, kind, id, batch=batch)
                if kind == "lines" and refused:
                    raise Refused(refused[0])
                held_ids = {r.line_id for r in refused}
                line_ids = [p.line_id for p in batch if p.line_id is not None and p.line_id not in held_ids]
        cur = conn.execute(_OWNED[kind], {"active": active, "id": id, "pid": PLAYER_ID})  # type: ignore[arg-type]
        if cur.rowcount == 0:
            return None
        if refused:
            held = [r.line_id for r in refused]
            conn.execute("UPDATE repertoire_lines SET active = FALSE WHERE id = ANY(%s)", (held,))
        if line_ids is None:
            line_ids = [int(r["id"]) for r in conn.execute(_LINES_OF[kind], (id,)).fetchall()]  # type: ignore[arg-type]
        touched = matching.games_touched_by(conn, line_ids, activated=active, window=window)
        return {"rematch": matching.rematch_games(conn, touched), "held_back": [r.as_row() for r in refused]}
