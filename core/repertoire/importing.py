"""`pipeline import-repertoire FILE --mode update|scratch`: load a repertoire file.

The file is neutral JSON — books of chapters of lines, each line a move list with its notes —
produced outside this repository. Nothing here knows or names where it came from;
`source_*` ids are opaque.

Identity (docs/decisions/007): a book is `(player, source_book_id)`; a chapter is `(book,
source_chapter_id)`, falling back to `(book, title, root_fen)` for a row that has no source
id yet; a line is `(chapter, moves)` — the move tokens exactly as the file spells them,
compared raw, because that is what the existing rows were keyed by; a note is `(line,
fen_norm)`. A row that exists keeps its colour, name and `active` flag; a missing `source_*`
id is filled in and a chapter found by its source id takes the file's title. Lines are never
updated and never deleted:
a line whose moves changed is a new line and the old one is switched off, so its puzzle keeps
its spaced-repetition state instead of being rebuilt or cascaded away.

`update` writes notes onto lines that already exist and creates nothing (it still fills in a
missing source id and applies a chapter's new title). `scratch` also
creates what is missing and, for a book the file declares `complete` — the exporter's word
that nothing was dropped on the way — switches off lines and chapters the file no longer
contains and deletes their course notes the file no longer carries; manual notes are kept,
and a line that is in the file but switched off stays off. A book not declared complete, or
one whose notes this side could not all place (off the line, malformed, blank), is only added
to: a note lost on the way, at either end, must not read as the author removing it. That is
decided per book from the file before anything is written, because the conflict gate below
depends on it: only the lines a replacement will switch off are left out of the repertoire the
new lines must agree with, and a book that is only added to keeps every line as an anchor.

A chapter is found by its source id first, so a renamed chapter keeps its lines, its puzzles
and their progress (the new title is applied); a chapter with no source id yet is adopted by
its title, and a title that already belongs to a chapter with a different source id stops the
import rather than fork the learning history.

A new line is inserted inactive when the repertoire already disagrees with it: at any of its
player-turn positions an effectively-active existing line prescribes a different move, or two
existing active lines already disagree there, or the file itself disagrees there and this line
is not the plurality winner (ties: earliest chapter in the file, then the lexically first
move). Inactive existing lines neither vote nor block. Without this a course's alternatives
would all come in active and `read.project_ply` would fail closed across it.

After a structural change the window is matched again in the same transaction
(`matching.rematch_window`), so no hour passes with the results half missing.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import chess
from psycopg import Connection
from pydantic import BaseModel, Field, ValidationError

from core.constants import PLAYER_ID
from core.notify import OperatorError
from core.repertoire import annotations, matching

Row = dict[str, Any]
Mode = Literal["update", "scratch"]
STANDARD_START = chess.STARTING_FEN


class NoteIn(BaseModel):
    fen_norm: str = Field(min_length=10, max_length=100)
    text: str = Field(max_length=20000)
    author: str | None = Field(default=None, max_length=200)


class LineIn(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    moves: list[str] = Field(min_length=1, max_length=400)
    source_line_id: str | None = Field(default=None, max_length=50)
    is_alternative: bool = False
    annotations: list[NoteIn] = []


class ChapterIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    root_fen: str | None = Field(default=None, max_length=100)
    source_chapter_id: int | None = None
    lines: list[LineIn] = []


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    color: Literal["white", "black"]
    source_book_id: int
    # The exporter's word that every chapter, line and note of the source was extracted; only
    # then may a scratch run treat what is absent from the file as removed.
    complete: bool = False
    chapters: list[ChapterIn] = []


class RepertoireFile(BaseModel):
    books: list[BookIn]


@dataclass
class Prepared:
    """One line of the file, validated: its spine and the position-move signatures at the
    player's turns."""

    book_ix: int
    chapter_ix: int
    line: LineIn
    fens: list[str]
    signatures: list[tuple[str, str]]  # (fen before, move) at the player's turns


def load_file(path: Path) -> RepertoireFile:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RepertoireFile.model_validate(raw)
    except (OSError, ValueError, ValidationError) as exc:
        raise OperatorError(f"repertoire file is not valid: {type(exc).__name__}") from exc


def spine(root_fen: str | None, moves: list[str]) -> list[str]:
    """FEN before each move and after the last, replayed from the chapter's root."""
    try:
        board = chess.Board(root_fen) if root_fen else chess.Board()
    except ValueError as exc:
        raise ValueError("unreadable root FEN") from exc
    fens = [board.fen()]
    for ply, san in enumerate(moves):
        try:
            board.push(board.parse_san(san))
        except ValueError as exc:
            raise ValueError(f"unreadable move at ply {ply}") from exc
        fens.append(board.fen())
    return fens


def signatures(moves: list[str], fens: list[str], color: str) -> list[tuple[str, str]]:
    """(position, move) at every ply where the book's side is to move. The position is the
    full FEN, counters included, as the gate has always keyed it: two lines reaching one board
    with different move counts are not held to one move here (the read side's `project_ply`
    is what reports such a disagreement when a game meets it)."""
    side = "w" if color == "white" else "b"
    return [(fens[i], m) for i, m in enumerate(moves) if fens[i].split(" ")[1] == side]


def prepare(doc: RepertoireFile) -> list[Prepared]:
    """Validate every line of the file; raise OperatorError naming indexes, never text."""
    out: list[Prepared] = []
    for b, book in enumerate(doc.books):
        if book.complete and not any(ch.lines for ch in book.chapters):
            raise OperatorError(f"book {b}: marked complete but carries no lines; it would switch the whole book off")
        for c, chapter in enumerate(book.chapters):
            seen: set[tuple[str, ...]] = set()
            for line in chapter.lines:
                key = tuple(line.moves)
                if key in seen:
                    continue  # duplicate in file: keep the first
                seen.add(key)
                try:
                    fens = spine(chapter.root_fen, line.moves)
                except ValueError as exc:
                    raise OperatorError(f"book {b} chapter {c}: a line does not replay ({exc})") from exc
                out.append(Prepared(b, c, line, fens, signatures(line.moves, fens, book.color)))
    return out


# --- the cohort gate ---------------------------------------------------------------

Decision = Literal["active", "inactive"]


def decide(existing: dict[str, set[str]], dirty: set[str], batch: list[Prepared]) -> list[Decision]:
    """For each prepared line (in file order), whether it may come in active. `existing`
    maps a player-turn position to the moves effectively-active lines already prescribe
    there; `dirty` is the positions where they already disagree."""
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    first_chapter: dict[tuple[str, str], tuple[int, int]] = {}
    for p in batch:
        for fen, move in p.signatures:
            votes[fen][move] += 1
            first_chapter.setdefault((fen, move), (p.book_ix, p.chapter_ix))
    winner: dict[str, str] = {}
    for fen, counter in votes.items():
        if len(counter) <= 1 or existing.get(fen):
            continue
        winner[fen] = min(counter, key=lambda m: (-counter[m], first_chapter[(fen, m)], m))
    decisions: list[Decision] = []
    for p in batch:
        verdict: Decision = "active"
        for fen, move in p.signatures:
            if fen in dirty:
                verdict = "inactive"
                break
            anchors = existing.get(fen)
            if anchors:
                if move not in anchors:
                    verdict = "inactive"
                    break
                continue
            if fen in winner and winner[fen] != move:
                verdict = "inactive"
                break
        decisions.append(verdict)
    return decisions


def existing_index(conn: Connection[Any], exclude: set[int] | None = None) -> tuple[dict[str, set[str]], set[str]]:
    """Player-turn positions of the effectively-active lines → the moves prescribed there,
    and the positions where the active repertoire already disagrees with itself. `exclude`
    leaves out lines that are about to be switched off."""
    index: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute(
        "SELECT rl.moves, rl.fen_sequence, bk.color FROM repertoire_lines rl"
        " JOIN chapters ch ON ch.id = rl.chapter_id JOIN books bk ON bk.id = ch.book_id"
        " WHERE bk.player_id = %s AND rl.active AND ch.active AND bk.active AND NOT (rl.id = ANY(%s))",
        (PLAYER_ID, sorted(exclude or set())),
    ).fetchall():
        moves, fens = annotations.strings(r["moves"]), annotations.strings(r["fen_sequence"])
        if not moves or len(fens) != len(moves) + 1:
            continue
        for fen, move in signatures(moves, fens, str(r["color"])):
            index[fen].add(move)
    return dict(index), {fen for fen, moves in index.items() if len(moves) > 1}


# --- the import -----------------------------------------------------------------


def _counts() -> dict[str, dict[str, int]]:
    return {
        "books": {
            "in_file": 0,
            "created": 0,
            "existing": 0,
            "unknown": 0,
            "title_differs": 0,
            "not_complete": 0,
            "notes_lost": 0,
        },
        "chapters": {"in_file": 0, "created": 0, "existing": 0, "unknown": 0, "renamed": 0, "deactivated": 0},
        "lines": {
            "in_file": 0,
            "inserted": 0,
            "imported_inactive": 0,
            "existing": 0,
            "present_but_inactive": 0,
            "vanished_deactivated": 0,
            "unknown": 0,
        },
        "annotations": {
            "in_file": 0,
            "written": 0,
            "unchanged": 0,
            "skipped_no_line": 0,
            "skipped_off_spine": 0,
            "skipped_preserved": 0,
            "blank": 0,
            "stale_deleted": 0,
        },
    }


class _Rollback(Exception):
    pass


def import_file(
    conn: Connection[Any], path: Path, *, mode: Mode, window: int, preserve_manual: bool = False, dry_run: bool = False
) -> Row:
    """The step. Returns counts only."""
    doc = load_file(path)
    prepared = prepare(doc)
    counts = _counts()
    counts["dirty_anchors"] = 0  # type: ignore[assignment]
    try:
        with conn.transaction():
            matching.lock(conn)
            structural = _write(conn, doc, prepared, counts, mode=mode, preserve_manual=preserve_manual)
            if structural:
                counts["rematch"] = matching.rematch_window(conn, window)
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    counts["dry_run"] = dry_run  # type: ignore[assignment]
    counts["mode"] = mode  # type: ignore[assignment]
    return counts


@dataclass
class _Book:
    ix: int
    spec: BookIn
    book_id: int
    chapter_ids: list[int]
    line_ids: list[int]
    notes: list[tuple[int, str]]
    to_insert: list[tuple[int, int]]  # (chapter_id, prepared index)
    to_note: list[tuple[int, int]]  # (line_id, prepared index) for lines that already exist
    spines: dict[int, list[str]]  # existing line id → its fen_sequence as stored
    replace: bool = False  # a scratch run may retire what the file no longer has


def _judge_notes(book: _Book, prepared: list[Prepared]) -> int:
    """How many of the book's notes this side cannot place, judged from the file and the
    existing lines' spines before anything is written: the same tests the write applies."""
    lost = 0
    for line_id, ix in book.to_note:
        spine_fens = book.spines.get(line_id, prepared[ix].fens)
        lost += sum(not annotations.placeable(spine_fens, n.fen_norm, n.text) for n in prepared[ix].line.annotations)
    for _chapter_id, ix in book.to_insert:
        lost += sum(
            not annotations.placeable(prepared[ix].fens, n.fen_norm, n.text) for n in prepared[ix].line.annotations
        )
    return lost


def _resolve_chapter(
    conn: Connection[Any], book_id: int, chapter: ChapterIn, counts: dict[str, Any], *, create: bool, where: str
) -> int | None:
    """The chapter's id: by its source id first (a rename is then a label change and is
    applied), else by (title, root_fen) — adopting a row that has no source id yet, refusing
    one that has a different id — else created (scratch) or reported unknown (update)."""
    title = chapter.title.strip()
    if chapter.source_chapter_id is not None:
        row = conn.execute(
            "SELECT id, title FROM chapters WHERE book_id = %s AND source_chapter_id = %s",
            (book_id, chapter.source_chapter_id),
        ).fetchone()
        if row is not None:
            if str(row["title"]) != title:
                clash = conn.execute(
                    "SELECT 1 FROM chapters WHERE book_id = %s AND title = %s AND root_fen IS NOT DISTINCT FROM %s"
                    " AND id <> %s",
                    (book_id, title, chapter.root_fen, row["id"]),
                ).fetchone()
                if clash is not None:
                    raise OperatorError(f"{where}: its new title already belongs to another chapter of the book")
                conn.execute("UPDATE chapters SET title = %s WHERE id = %s", (title, row["id"]))
                counts["chapters"]["renamed"] += 1
            counts["chapters"]["existing"] += 1
            return int(row["id"])
    row = conn.execute(
        "SELECT id, source_chapter_id FROM chapters WHERE book_id = %s AND title = %s"
        " AND root_fen IS NOT DISTINCT FROM %s ORDER BY id LIMIT 1",
        (book_id, title, chapter.root_fen),
    ).fetchone()
    if row is not None:
        if chapter.source_chapter_id is not None:
            if row["source_chapter_id"] is None:
                conn.execute(
                    "UPDATE chapters SET source_chapter_id = %s WHERE id = %s", (chapter.source_chapter_id, row["id"])
                )
            elif int(row["source_chapter_id"]) != chapter.source_chapter_id:
                raise OperatorError(f"{where}: a chapter with this title already carries another source id")
        counts["chapters"]["existing"] += 1
        return int(row["id"])
    if not create:
        counts["chapters"]["unknown"] += 1
        return None
    row = conn.execute(
        "INSERT INTO chapters (book_id, title, root_fen, source_chapter_id, active) VALUES (%s, %s, %s, %s, TRUE)"
        " RETURNING id",
        (book_id, title, chapter.root_fen, chapter.source_chapter_id),
    ).fetchone()
    assert row is not None
    counts["chapters"]["created"] += 1
    return int(row["id"])


def _write(
    conn: Connection[Any],
    doc: RepertoireFile,
    prepared: list[Prepared],
    counts: dict[str, Any],
    *,
    mode: Mode,
    preserve_manual: bool,
) -> bool:
    scratch = mode == "scratch"
    by_position = {(p.book_ix, p.chapter_ix, tuple(p.line.moves)): i for i, p in enumerate(prepared)}
    resolved: list[_Book] = []

    # Pass 1: books, chapters and existing lines, and what the file wants inserted.
    for b, spec in enumerate(doc.books):
        counts["books"]["in_file"] += 1
        row = conn.execute(
            "SELECT id, title FROM books WHERE player_id = %s AND source_book_id = %s", (PLAYER_ID, spec.source_book_id)
        ).fetchone()
        if row is None:
            if not scratch:
                counts["books"]["unknown"] += 1
                continue
            row = conn.execute(
                "INSERT INTO books (player_id, source_book_id, title, color, active) VALUES (%s, %s, %s, %s, TRUE)"
                " RETURNING id, title",
                (PLAYER_ID, spec.source_book_id, spec.title.strip(), spec.color),
            ).fetchone()
            assert row is not None
            counts["books"]["created"] += 1
        else:
            counts["books"]["existing"] += 1
            if str(row["title"]).strip() != spec.title.strip():
                counts["books"]["title_differs"] += 1
        book = _Book(b, spec, int(row["id"]), [], [], [], [], [], {})
        resolved.append(book)
        for c, chapter in enumerate(spec.chapters):
            counts["chapters"]["in_file"] += 1
            chapter_id = _resolve_chapter(
                conn, book.book_id, chapter, counts, create=scratch, where=f"book {b} chapter {c}"
            )
            if chapter_id is None:
                counts["lines"]["unknown"] += len(chapter.lines)
                counts["annotations"]["skipped_no_line"] += sum(len(ln.annotations) for ln in chapter.lines)
                continue
            book.chapter_ids.append(chapter_id)
            # Every line of the chapter in one read, keyed by its moves.
            known: dict[tuple[str, ...], tuple[int, bool, Any]] = {}
            for r in conn.execute(
                "SELECT id, moves, fen_sequence, active, source_line_id FROM repertoire_lines WHERE chapter_id = %s",
                (chapter_id,),
            ).fetchall():
                known[tuple(annotations.strings(r["moves"]))] = (int(r["id"]), bool(r["active"]), r["source_line_id"])
                book.spines[int(r["id"])] = annotations.strings(r["fen_sequence"])
            fill_ids: list[tuple[int, str]] = []
            for line in chapter.lines:
                ix = by_position.get((b, c, tuple(line.moves)))
                if ix is None or prepared[ix].line is not line:
                    continue  # a duplicate in the file; the first copy was kept
                counts["lines"]["in_file"] += 1
                counts["annotations"]["in_file"] += len(line.annotations)
                found = known.get(tuple(line.moves))
                if found is None:
                    if scratch:
                        book.to_insert.append((chapter_id, ix))
                    else:
                        counts["lines"]["unknown"] += 1
                        counts["annotations"]["skipped_no_line"] += len(line.annotations)
                    continue
                line_id, active, source_line_id = found
                counts["lines"]["existing"] += 1
                if not active:
                    counts["lines"]["present_but_inactive"] += 1
                if line.source_line_id is not None and source_line_id is None:
                    fill_ids.append((line_id, line.source_line_id))
                book.line_ids.append(line_id)
                book.to_note.append((line_id, ix))
            if fill_ids:
                conn.execute(
                    "UPDATE repertoire_lines rl SET source_line_id = f.sid"
                    " FROM jsonb_to_recordset(%s) AS f(id int, sid text)"
                    " WHERE rl.id = f.id AND rl.source_line_id IS NULL",
                    (json.dumps([{"id": i, "sid": sid} for i, sid in fill_ids]),),
                )

    # Whether a book is replaced or only added to is settled here, before anything is written:
    # a scratch run replaces a book the file declares complete and whose notes this side can
    # all place. A note it cannot place (off its line, malformed, blank) is a loss on the way,
    # and a book that lost anything keeps every line it has. Only the lines a replacement will
    # switch off are left out of the repertoire the new lines must agree with (an old line
    # prescribing e4 must not block its own replacement's d4); a book that is only added to
    # keeps its lines as anchors, so a new line that disagrees with them comes in switched off.
    for book in resolved:
        if not scratch:
            continue
        if not book.spec.complete:
            counts["books"]["not_complete"] += 1
        elif _judge_notes(book, prepared):
            counts["books"]["notes_lost"] += 1
        else:
            book.replace = True
    vanishing: set[int] = _vanishing(conn, [book for book in resolved if book.replace])
    existing, dirty = existing_index(conn, exclude=vanishing)
    counts["dirty_anchors"] = len(dirty)
    batch = [prepared[ix] for book in resolved for _, ix in book.to_insert]
    decisions = dict(
        zip([ix for book in resolved for _, ix in book.to_insert], decide(existing, dirty, batch), strict=True)
    )

    # Pass 2: insert, write the notes, retire.
    structural = counts["books"]["created"] > 0 or counts["chapters"]["created"] > 0
    for book in resolved:
        for chapter_id, ix in book.to_insert:
            p = prepared[ix]
            active = decisions[ix] == "active"
            lrow = conn.execute(
                "INSERT INTO repertoire_lines"
                " (chapter_id, line_name, moves, fen_sequence, active, is_alternative, source_line_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    chapter_id,
                    p.line.name.strip(),
                    json.dumps(p.line.moves),
                    json.dumps(p.fens),
                    active,
                    p.line.is_alternative,
                    p.line.source_line_id,
                ),
            ).fetchone()
            assert lrow is not None
            counts["lines"]["inserted"] += 1
            if not active:
                counts["lines"]["imported_inactive"] += 1
            structural = True
            book.line_ids.append(int(lrow["id"]))
            book.to_note.append((int(lrow["id"]), ix))
        notes: list[dict[str, Any]] = []
        for line_id, ix in book.to_note:
            line = prepared[ix].line
            for n in line.annotations:
                notes.append(
                    {
                        "line_id": line_id,
                        "fen_norm": n.fen_norm,
                        "text": n.text,
                        "author": n.author,
                        "book_title": book.spec.title.strip(),
                        "source_ref": str(book.spec.source_book_id),
                    }
                )
                try:
                    book.notes.append((line_id, annotations.normalize_fen(n.fen_norm)))
                except ValueError:
                    continue
        # The book's notes are written before anything of the book is retired, and the write
        # must agree with the judgement the gate was built on: a book judged replaceable whose
        # write still lost a note would have excluded lines it then keeps, so it stops the run.
        written = annotations.upsert_many(conn, notes, source="course", preserve_manual=preserve_manual)
        for k, v in written.items():
            counts["annotations"][k] += v
        lost = written["skipped_off_spine"] + written["blank"] + written["skipped_no_line"]
        if book.replace:
            if lost:
                raise OperatorError(f"book {book.ix}: a note judged placeable was not written")
            structural |= _retire_absent(conn, book, counts)
    return structural


def _vanishing(conn: Connection[Any], replaced: list[_Book]) -> set[int]:
    """Lines of the books being replaced that the file no longer contains (including every
    line of a chapter the file no longer contains): exactly what `_retire_absent` switches off."""
    out: set[int] = set()
    for book in replaced:
        rows = conn.execute(
            "SELECT rl.id FROM repertoire_lines rl JOIN chapters ch ON ch.id = rl.chapter_id"
            " WHERE ch.book_id = %s AND rl.active AND NOT (rl.id = ANY(%s))",
            (book.book_id, book.line_ids),
        ).fetchall()
        out |= {int(r["id"]) for r in rows}
    return out


def _retire_absent(conn: Connection[Any], book: _Book, counts: dict[str, Any]) -> bool:
    """Switch off what the file no longer has, and drop the course notes it no longer
    carries. Only for a book the file declares complete; nothing is deleted but notes."""
    changed = False
    cur = conn.execute(
        "UPDATE repertoire_lines rl SET active = FALSE FROM chapters ch"
        " WHERE rl.chapter_id = ch.id AND ch.book_id = %s AND rl.active AND NOT (rl.id = ANY(%s))",
        (book.book_id, book.line_ids),
    )
    counts["lines"]["vanished_deactivated"] += cur.rowcount
    changed |= cur.rowcount > 0
    cur = conn.execute(
        "UPDATE chapters SET active = FALSE WHERE book_id = %s AND active AND NOT (id = ANY(%s))",
        (book.book_id, book.chapter_ids),
    )
    counts["chapters"]["deactivated"] += cur.rowcount
    changed |= cur.rowcount > 0
    cur = conn.execute(
        """
        DELETE FROM repertoire_annotations ra
        USING repertoire_lines rl, chapters ch
        WHERE ra.line_id = rl.id AND rl.chapter_id = ch.id AND ch.book_id = %(book)s
          AND ra.player_id = %(pid)s AND ra.source = 'course'
          AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(%(keep)s) k
                          WHERE (k->>0)::int = ra.line_id AND k->>1 = ra.fen_norm)
        """,
        {"book": book.book_id, "pid": PLAYER_ID, "keep": json.dumps([[lid, fn] for lid, fn in book.notes])},
    )
    counts["annotations"]["stale_deleted"] += cur.rowcount
    return changed
