"""The Repertoire page's reads, and the one write it has: switching a book, chapter or line
on or off.

Nothing here judges a toggle. The old system ran an activation gate that refused to enable a
line disagreeing with another active one; here `project_ply` fails closed with `conflict`
instead, and the toggle is the only remedy the product offers for a deviation that keeps
recurring because a line should not be active (dismissing a deviation was removed for that
reason). After a toggle the games it can affect are matched again in the same transaction,
so the Deviations page reflects it at once; repertoire puzzles follow on the next
`generate-puzzles` (an inactive line's puzzle is switched off with its SRS state kept).
"""

from __future__ import annotations

from typing import Any, Literal

from psycopg import Connection

from core.constants import PLAYER_ID
from core.repertoire import annotations, matching

Row = dict[str, Any]
Kind = Literal["books", "chapters", "lines"]


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


def set_active(conn: Connection[Any], kind: Kind, id: int, active: bool, window: int) -> dict[str, int] | None:
    """Flip one flag and match again the games it can affect. None when nothing matched the
    id; otherwise the rematch counts."""
    with conn.transaction():
        matching.lock(conn)
        cur = conn.execute(_OWNED[kind], {"active": active, "id": id, "pid": PLAYER_ID})  # type: ignore[arg-type]
        if cur.rowcount == 0:
            return None
        line_ids = [int(r["id"]) for r in conn.execute(_LINES_OF[kind], (id,)).fetchall()]  # type: ignore[arg-type]
        touched = matching.games_touched_by(conn, line_ids, activated=active, window=window)
        return matching.rematch_games(conn, touched)
