"""Lines the player adds by hand, and the one way any line leaves: `add_manual` and `delete`.

A manual line (`repertoire_lines.origin = 'manual'`) is a repertoire line like any other once it
is in: matched against the games, reported, puzzled, toggled, gated. What is different is how it
arrives and what the import may do to it. It arrives from a gap card on the line report with its
moves from the start position, always whole: a chapter rooted at a tabiya is never a destination,
because a line stored from the root would be a puzzle that starts there, and the point of a manual
line is to memorise the opening from move 1. A line that fits no chapter of the player's goes into
the colour's **catch-all book** (`CATCH_ALL_TITLE`, no `source_book_id`, so the import never sees
it), under a chapter named for the opening family, both created the first time they are needed.
The import never retires a manual line nor a chapter that holds one (`importing._retire_absent`).

Every write here holds the repertoire lock (`matching.lock`) to its commit and matches the games it
can affect again inside the same transaction, as a toggle does, so no result row cites a line that
is gone and the report shows a new line's games on its next read.

A new manual line is judged by the import's own gate (`importing.decide`) against the effectively
active repertoire; a refusal is raised, never written as an inactive row the player would then
have to find. Identity is the import's, `(chapter, moves)`: the same moves in the chapter, in any
state, are a duplicate; so is a line that is a strict prefix of an active line of the same book,
which already teaches further.

`delete` is the player's act, offered for every line. Decision 007's "never deleted" is the
import's rule and stays the import's rule. The schema cascades the line's puzzle and, through it,
its spaced-repetition state and attempts, the result rows citing the line, and its notes.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import chess
from psycopg import Connection

from core.chess.san import normalize_san
from core.chess.san import parse as parse_san
from core.constants import CATCH_ALL_TITLE, PLAYER_ID, REPERTOIRE_LINE_MAX_PLIES
from core.puzzles import stats
from core.repertoire import annotations, conflicts, importing, matching

Row = dict[str, Any]
Colour = Literal["white", "black"]

CATCH_ALL_FAMILY_FALLBACK = "Unnamed opening"
MAX_LINE_NAME = 120


class InvalidLine(ValueError):
    """The moves or the name cannot be a line; the message is for the player."""


class NoChapter(LookupError):
    """No such chapter of the player's, or not one a manual line may go into."""


class Duplicate(Exception):
    """The chapter (or the book) already teaches this: an identical line, in any state, or an
    active line this one is a strict prefix of."""

    def __init__(self, line_id: int, line_name: str, active: bool, kind: Literal["identical", "prefix"]) -> None:
        super().__init__(kind)
        self.line_id, self.line_name, self.active, self.kind = line_id, line_name, active, kind

    def as_row(self) -> Row:
        return {"line_id": self.line_id, "line_name": self.line_name, "active": self.active, "kind": self.kind}


class Refused(Exception):
    """The gate would not let the line in; `refusal` names the position and the holders."""

    def __init__(self, refusal: conflicts.Refusal) -> None:
        super().__init__(refusal.reason)
        self.refusal = refusal


def _replay(moves: list[str], colour: Colour) -> tuple[list[str], list[str]]:
    """The moves re-rendered as the server's SAN from the start position, and the spine. The
    line must have 2..REPERTOIRE_LINE_MAX_PLIES plies and end on the player's move."""
    if not 2 <= len(moves) <= REPERTOIRE_LINE_MAX_PLIES:
        raise InvalidLine(f"a line has 2 to {REPERTOIRE_LINE_MAX_PLIES} plies")
    board = chess.Board()
    fens = [board.fen()]
    out: list[str] = []
    for ply, token in enumerate(moves):
        move = parse_san(board, normalize_san(str(token)))
        if not move:
            raise InvalidLine(f"move {ply + 1} does not play")
        out.append(board.san(move))
        board.push(move)
        fens.append(board.fen())
    side = "w" if colour == "white" else "b"
    if fens[-2].split(" ")[1] != side:
        raise InvalidLine("a line ends on your move")
    return out, fens


def _chapter(conn: Connection[Any], chapter_id: int) -> Row:
    row = conn.execute(
        "SELECT ch.id, ch.root_fen, ch.active AS chapter_active, bk.id AS book_id, bk.color, bk.active AS book_active"
        " FROM chapters ch JOIN books bk ON bk.id = ch.book_id WHERE ch.id = %s AND bk.player_id = %s",
        (chapter_id, PLAYER_ID),
    ).fetchone()
    if row is None:
        raise NoChapter("no such chapter")
    if not row["chapter_active"] or not row["book_active"]:
        raise NoChapter("that chapter is switched off; switch it on first")
    if row["root_fen"] is not None:
        raise NoChapter("a chapter rooted at a position after the start cannot take a line from move 1")
    return dict(row)


def catch_all_chapter(conn: Connection[Any], colour: Colour, family: str | None) -> int:
    """The id of the catch-all chapter for this colour and family, creating the book and the
    chapter when they do not exist yet. A catch-all that was switched off stays off: `_chapter`
    refuses it like any other, and only the toggle (its gate, its rematch) brings it back. Under
    the caller's repertoire lock."""
    title = (family or "").strip() or CATCH_ALL_FAMILY_FALLBACK
    book = conn.execute(
        "SELECT id FROM books WHERE player_id = %s AND source_book_id IS NULL AND title = %s AND color = %s",
        (PLAYER_ID, CATCH_ALL_TITLE, colour),
    ).fetchone()
    if book is None:
        book = conn.execute(
            "INSERT INTO books (player_id, source_book_id, title, color, active) VALUES (%s, NULL, %s, %s, TRUE)"
            " RETURNING id",
            (PLAYER_ID, CATCH_ALL_TITLE, colour),
        ).fetchone()
        assert book is not None
    chapter = conn.execute(
        "SELECT id FROM chapters WHERE book_id = %s AND title = %s AND root_fen IS NULL", (int(book["id"]), title)
    ).fetchone()
    if chapter is None:
        chapter = conn.execute(
            "INSERT INTO chapters (book_id, title, active, root_fen) VALUES (%s, %s, TRUE, NULL) RETURNING id",
            (int(book["id"]), title),
        ).fetchone()
        assert chapter is not None
    return int(chapter["id"])


def _duplicates(conn: Connection[Any], chapter: Row, moves: list[str]) -> None:
    """Both sides compared as normalised SAN (no check, mate or annotation marks): the stored
    moves and the new line's, which `_replay` renders with python-chess's `+`/`#`. An identical
    line in the same chapter is a duplicate in any state; a longer line is a prefix holder only
    while it is effective (its chapter on too; the book is the destination's, so on)."""
    rows = conn.execute(
        "SELECT rl.id, rl.line_name, rl.moves, rl.active, rl.chapter_id, ch.active AS chapter_active"
        " FROM repertoire_lines rl JOIN chapters ch ON ch.id = rl.chapter_id"
        " WHERE ch.book_id = %s AND ch.root_fen IS NULL",
        (int(chapter["book_id"]),),
    ).fetchall()
    mine = [normalize_san(m) for m in moves]
    for r in rows:
        theirs = [normalize_san(m) for m in annotations.strings(r["moves"])]
        if int(r["chapter_id"]) == int(chapter["id"]) and theirs == mine:
            raise Duplicate(int(r["id"]), str(r["line_name"]), bool(r["active"]), "identical")
        effective = bool(r["active"]) and bool(r["chapter_active"])
        if effective and len(theirs) > len(mine) and theirs[: len(mine)] == mine:
            raise Duplicate(int(r["id"]), str(r["line_name"]), True, "prefix")


def add_manual(
    conn: Connection[Any],
    *,
    moves: list[str],
    line_name: str,
    window: int,
    chapter_id: int | None = None,
    catch_all: tuple[Colour, str | None] | None = None,
) -> Row:
    """Insert one manual line from the start position and match the window games it can
    reach. Exactly one of `chapter_id` or `catch_all=(colour, family)` names the destination.
    Returns `{id, chapter_id, book_id, rematch}`. The caller commits."""
    name = " ".join(line_name.split())
    if not 1 <= len(name) <= MAX_LINE_NAME:
        raise InvalidLine(f"a line name has 1 to {MAX_LINE_NAME} characters")
    if (chapter_id is None) == (catch_all is None):
        raise InvalidLine("one destination: a chapter or the catch-all")
    with conn.transaction():
        matching.lock(conn)
        if chapter_id is None:
            assert catch_all is not None
            chapter_id = catch_all_chapter(conn, catch_all[0], catch_all[1])
        chapter = _chapter(conn, chapter_id)
        colour: Colour = "white" if str(chapter["color"]) == "white" else "black"
        sans, fens = _replay(moves, colour)
        _duplicates(conn, chapter, sans)
        prepared = importing.Prepared(
            0, 0, importing.LineIn(name=name, moves=sans), fens, importing.signatures(sans, fens, colour)
        )
        existing, dirty = importing.existing_index(conn)
        (verdict,) = importing.decide(existing, dirty, [prepared])
        if not verdict.active:
            assert verdict.fen is not None and verdict.move is not None and verdict.reason is not None
            rivals = conflicts.holders(conn, verdict.fen, verdict.move)
            raise Refused(conflicts.Refusal(0, name, "", verdict.fen, verdict.move, verdict.reason, rivals))
        row = conn.execute(
            "INSERT INTO repertoire_lines"
            " (chapter_id, line_name, moves, fen_sequence, active, is_alternative, source_line_id, origin)"
            " VALUES (%s, %s, %s, %s, TRUE, FALSE, NULL, 'manual') RETURNING id",
            (int(chapter["id"]), name, json.dumps(sans), json.dumps(fens)),
        ).fetchone()
        assert row is not None
        line_id = int(row["id"])
        touched = matching.games_touched_by(conn, [line_id], activated=True, window=window)
        rematch = matching.rematch_games(conn, touched)
    return {"id": line_id, "chapter_id": int(chapter["id"]), "book_id": int(chapter["book_id"]), "rematch": rematch}


def delete(conn: Connection[Any], line_id: int) -> Row | None:
    """Delete one line of the player's, whatever its origin, with everything the schema hangs
    on it, and match again the games whose result cited it. None when there is no such line;
    otherwise `{loads, rematch}` with the number of solver loads the puzzle had. The caller
    commits."""
    with conn.transaction():
        matching.lock(conn)
        row = conn.execute(
            "SELECT rl.id FROM repertoire_lines rl JOIN chapters ch ON ch.id = rl.chapter_id"
            " JOIN books bk ON bk.id = ch.book_id WHERE rl.id = %(id)s AND bk.player_id = %(pid)s",
            {"id": line_id, "pid": PLAYER_ID},
        ).fetchone()
        if row is None:
            return None
        loads = stats.by_line(conn, [line_id]).get(line_id, stats.empty())["loads"]
        touched = matching.games_touched_by(conn, [line_id], activated=False, window=0)
        conn.execute("DELETE FROM repertoire_lines WHERE id = %s", (line_id,))
        rematch = matching.rematch_games(conn, touched)
    return {"loads": loads, "rematch": rematch}
