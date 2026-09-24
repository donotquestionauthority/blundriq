"""Position notes on repertoire lines, and the walk-through that projects them onto a line.

A note is keyed by `fen_norm`, the first four FEN fields (placement, side, castling, en
passant — the Python twin of SQL `bq_canonical_fen`, which returns NULL where this raises),
computed here and never by a caller. It is about the move played *from* that position.

Two namespaces, chosen by the caller's `line_id` and never inferred from the FEN:
**attached** notes belong to one line (`(player_id, line_id, fen_norm)`; the note must sit on
that line's spine; the line's book and chapter are derived here, not trusted from the caller),
and **unattached** notes belong to a bare position (`(player_id, fen_norm) WHERE line_id IS
NULL`, the note editor on any card). `get` reads only the unattached one: line notes are
reached only through `line_with_notes`, and its wall is the book, so a note written for one
opening never surfaces in another.

`line_with_notes` is ownership-gated, not active-gated: a retired line stays readable.
`project` decides which note, if any, each ply shows. A candidate is eligible at spine index
`i` when its `fen_norm` is that position's and its own line reaches the position *and makes the
same move from it* (`moves[:i+1]`), so a sibling that diverges here does not lend its note; at
the root only the line's own notes and its chapter's count (a course preamble once projected
onto all twenty chapters of a 1.e4 course). Among the eligible, the line's own note wins, then
the chapter's, then the most recently updated, then the highest id: locality beats freshness.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, cast

from psycopg import Connection

from core.constants import PLAYER_ID

Row = dict[str, Any]

_PGN_COMMAND_RE = re.compile(r"\[%[^\]]*\]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_FEN_REF_RE = re.compile(r"@@StartFEN@@.*?@@EndFEN@@", re.DOTALL)
_FEN_REF_ORPHAN_RE = re.compile(r"@@(?:Start|End)FEN@@")
_WS_RE = re.compile(r"\s+")

SOURCES = ("course", "manual")


def normalize_fen(fen: str) -> str:
    """The first four FEN fields. ValueError under four: a malformed FEN never becomes a key."""
    parts = fen.strip().split()
    if len(parts) < 4:
        raise ValueError(f"expected at least four FEN fields, got {len(parts)}")
    return " ".join(parts[:4])


def placeable(spine: list[str], fen_norm: str, text: str) -> bool:
    """Would `upsert_many` write this note onto a line with this spine? The same three tests
    it applies — non-blank text, a FEN of at least four fields, a position on the spine — so
    an importer can judge a file's notes before it has written anything."""
    if not clean_text(text):
        return False
    try:
        key = normalize_fen(fen_norm)
    except ValueError:
        return False
    for f in spine:
        try:
            if normalize_fen(f) == key:
                return True
        except ValueError:
            continue  # a malformed stored position places nothing
    return False


def clean_text(text: str) -> str:
    """Strip position references, PGN command tokens and HTML, collapse whitespace. The
    `@@SANStart@@…@@SANEnd@@` move references stay: the walk-through makes them clickable."""
    if not text:
        return ""
    cleaned = _FEN_REF_RE.sub(" ", text)
    cleaned = _FEN_REF_ORPHAN_RE.sub(" ", cleaned)
    cleaned = _PGN_COMMAND_RE.sub(" ", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    return _WS_RE.sub(" ", cleaned).strip()


def get(conn: Connection[Any], fen: str) -> Row | None:
    """The unattached note at this position, or None."""
    row = conn.execute(
        "SELECT text, source, author, book_title, updated_at FROM repertoire_annotations"
        " WHERE player_id = %s AND fen_norm = %s AND line_id IS NULL",
        (PLAYER_ID, normalize_fen(fen)),
    ).fetchone()
    return dict(row) if row else None


def _line_context(conn: Connection[Any], line_id: int) -> tuple[int, int, set[str]] | None:
    row = conn.execute(
        "SELECT rl.chapter_id, ch.book_id, rl.fen_sequence FROM repertoire_lines rl"
        " JOIN chapters ch ON ch.id = rl.chapter_id JOIN books bk ON bk.id = ch.book_id"
        " WHERE rl.id = %s AND bk.player_id = %s",
        (line_id, PLAYER_ID),
    ).fetchone()
    if row is None:
        return None
    spine: set[str] = set()
    for f in strings(row["fen_sequence"]):
        try:
            spine.add(normalize_fen(f))
        except ValueError:
            continue
    return int(row["book_id"]), int(row["chapter_id"]), spine


_UPSERT = """
INSERT INTO repertoire_annotations
    (player_id, line_id, book_id, chapter_id, fen_norm, text, source, source_ref, author, book_title)
VALUES (%(pid)s, %(line_id)s, %(book_id)s, %(chapter_id)s, %(fen_norm)s, %(text)s, %(source)s, %(source_ref)s,
        %(author)s, %(book_title)s)
ON CONFLICT {target} DO UPDATE SET
    text = EXCLUDED.text, source = EXCLUDED.source, source_ref = EXCLUDED.source_ref, author = EXCLUDED.author,
    book_title = EXCLUDED.book_title, book_id = EXCLUDED.book_id, chapter_id = EXCLUDED.chapter_id,
    updated_at = now()
WHERE (repertoire_annotations.text, repertoire_annotations.source, repertoire_annotations.author,
       repertoire_annotations.book_title)
      IS DISTINCT FROM (EXCLUDED.text, EXCLUDED.source, EXCLUDED.author, EXCLUDED.book_title)
"""


def upsert(
    conn: Connection[Any],
    items: list[Row],
    line_id: int | None,
    *,
    source: str,
    preserve_manual: bool = False,
    source_ref: str | None = None,
) -> dict[str, int]:
    """Write notes (`{fen, text, author?, book_title?}`) into one namespace. Last write wins,
    except that `preserve_manual` leaves a manual note alone; an unchanged note is not
    rewritten (its `updated_at` holds). Returns counts: written, unchanged, skipped_no_line,
    skipped_off_spine, skipped_preserved, blank."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    counts = {
        "written": 0,
        "unchanged": 0,
        "skipped_no_line": 0,
        "skipped_off_spine": 0,
        "skipped_preserved": 0,
        "blank": 0,
    }
    book_id = chapter_id = None
    spine: set[str] | None = None
    if line_id is not None:
        ctx = _line_context(conn, line_id)
        if ctx is None:
            counts["skipped_no_line"] = len(items)
            return counts
        book_id, chapter_id, spine = ctx
    target = "(player_id, line_id, fen_norm)" if line_id is not None else "(player_id, fen_norm) WHERE line_id IS NULL"
    sql = _UPSERT.format(target=target)
    manual: set[str] = set()
    if preserve_manual:
        manual = {
            str(r["fen_norm"])
            for r in conn.execute(
                "SELECT fen_norm FROM repertoire_annotations WHERE player_id = %s AND source = 'manual'"
                " AND line_id IS NOT DISTINCT FROM %s",
                (PLAYER_ID, line_id),
            ).fetchall()
        }
    with conn.cursor() as cur:
        for item in items:
            fen_norm = normalize_fen(str(item["fen"]))
            clean = clean_text(str(item.get("text") or ""))
            if not clean:
                counts["blank"] += 1
                continue
            if spine is not None and fen_norm not in spine:
                counts["skipped_off_spine"] += 1
                continue
            if fen_norm in manual:
                counts["skipped_preserved"] += 1
                continue
            cur.execute(
                sql,  # type: ignore[arg-type]  # one of two literal conflict targets
                {
                    "pid": PLAYER_ID,
                    "line_id": line_id,
                    "book_id": book_id,
                    "chapter_id": chapter_id,
                    "fen_norm": fen_norm,
                    "text": clean,
                    "source": source,
                    "source_ref": source_ref,
                    "author": item.get("author"),
                    "book_title": item.get("book_title"),
                },
            )
            if cur.rowcount:
                counts["written"] += 1
            else:
                counts["unchanged"] += 1
    return counts


def upsert_many(
    conn: Connection[Any], notes: list[Row], *, source: str, preserve_manual: bool = False
) -> dict[str, int]:
    """The importer's write: every note of a file (`{line_id, fen_norm, text, author, book_title,
    source_ref}`) in a handful of statements — the file is copied into a temporary table and
    one INSERT … ON CONFLICT does the rest, with the spine rule, the derived book and chapter,
    the unchanged-note test and `preserve_manual` all in SQL. Same counts as `upsert`; a note
    whose position is not four FEN fields counts as off the spine."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    counts = {
        "written": 0,
        "unchanged": 0,
        "skipped_no_line": 0,
        "skipped_off_spine": 0,
        "skipped_preserved": 0,
        "blank": 0,
    }
    rows: dict[tuple[int, str], tuple[Any, ...]] = {}
    for n in notes:
        clean = clean_text(str(n.get("text") or ""))
        if not clean:
            counts["blank"] += 1
            continue
        try:
            fen_norm = normalize_fen(str(n["fen_norm"]))
        except ValueError:
            counts["skipped_off_spine"] += 1
            continue
        line_id = int(n["line_id"])  # a later copy of the same key replaces the earlier one
        rows[(line_id, fen_norm)] = (line_id, fen_norm, clean, n.get("author"), n.get("book_title"), n["source_ref"])
    if not rows:
        return counts
    conn.execute(
        "CREATE TEMP TABLE note_in (line_id int, fen_norm text, text text, author text, book_title text,"
        " source_ref text) ON COMMIT DROP"
    )
    with conn.cursor() as cur:
        with cur.copy("COPY note_in (line_id, fen_norm, text, author, book_title, source_ref) FROM STDIN") as copy:
            for r in rows.values():
                copy.write_row(r)
        cur.execute(
            """
            CREATE TEMP TABLE note_judged ON COMMIT DROP AS
            SELECT n.*, rl.chapter_id, ch.book_id,
                   (rl.id IS NOT NULL) AS owned,
                   EXISTS (SELECT 1 FROM jsonb_array_elements_text(rl.fen_sequence) f
                           WHERE bq_canonical_fen(f) = n.fen_norm) AS on_spine,
                   EXISTS (SELECT 1 FROM repertoire_annotations ra WHERE ra.player_id = %(pid)s
                           AND ra.line_id = n.line_id AND ra.fen_norm = n.fen_norm AND ra.source = 'manual') AS manual
            FROM note_in n
            LEFT JOIN repertoire_lines rl ON rl.id = n.line_id
            LEFT JOIN chapters ch ON ch.id = rl.chapter_id
            LEFT JOIN books bk ON bk.id = ch.book_id AND bk.player_id = %(pid)s
            """,
            {"pid": PLAYER_ID},
        )
        cur.execute(
            "SELECT count(*) FILTER (WHERE NOT owned OR book_id IS NULL) AS no_line,"
            " count(*) FILTER (WHERE owned AND book_id IS NOT NULL AND NOT on_spine) AS off_spine,"
            " count(*) FILTER (WHERE owned AND book_id IS NOT NULL AND on_spine AND manual AND %(keep)s) AS preserved"
            " FROM note_judged",
            {"keep": preserve_manual},
        )
        c = cur.fetchone()
        assert c is not None
        counts["skipped_no_line"] += int(c["no_line"])
        counts["skipped_off_spine"] += int(c["off_spine"])
        counts["skipped_preserved"] += int(c["preserved"])
        cur.execute(
            """
            INSERT INTO repertoire_annotations
                (player_id, line_id, book_id, chapter_id, fen_norm, text, source, source_ref, author, book_title)
            SELECT %(pid)s, line_id, book_id, chapter_id, fen_norm, text, %(source)s, source_ref, author, book_title
            FROM note_judged
            WHERE owned AND book_id IS NOT NULL AND on_spine AND NOT (manual AND %(keep)s)
            ON CONFLICT (player_id, line_id, fen_norm) DO UPDATE SET
                text = EXCLUDED.text, source = EXCLUDED.source, source_ref = EXCLUDED.source_ref,
                author = EXCLUDED.author, book_title = EXCLUDED.book_title, book_id = EXCLUDED.book_id,
                chapter_id = EXCLUDED.chapter_id, updated_at = now()
            WHERE (repertoire_annotations.text, repertoire_annotations.source, repertoire_annotations.author,
                   repertoire_annotations.book_title)
                  IS DISTINCT FROM (EXCLUDED.text, EXCLUDED.source, EXCLUDED.author, EXCLUDED.book_title)
            """,
            {"pid": PLAYER_ID, "source": source, "keep": preserve_manual},
        )
        counts["written"] += cur.rowcount
        eligible = len(rows) - int(c["no_line"]) - int(c["off_spine"]) - int(c["preserved"])
        counts["unchanged"] += eligible - cur.rowcount
        cur.execute("DROP TABLE note_in, note_judged")
    return counts


def delete(conn: Connection[Any], fen: str, line_id: int | None) -> bool:
    """Remove the note in one namespace; False when there was none."""
    fen_norm = normalize_fen(fen)
    if line_id is None:
        cur = conn.execute(
            "DELETE FROM repertoire_annotations WHERE player_id = %s AND fen_norm = %s AND line_id IS NULL",
            (PLAYER_ID, fen_norm),
        )
    else:
        cur = conn.execute(
            "DELETE FROM repertoire_annotations WHERE player_id = %s AND fen_norm = %s AND line_id = %s",
            (PLAYER_ID, fen_norm, line_id),
        )
    return cur.rowcount > 0


def strings(v: Any) -> list[str]:
    """A jsonb array of strings as a typed list; anything else is empty."""
    if not isinstance(v, list):
        return []
    return [str(x) for x in cast(list[Any], v)]


def _as_list(v: Any) -> list[str] | None:
    return strings(v) if isinstance(v, list) else None


def project(
    moves: list[str], fen_sequence: list[str], candidates: list[Row], line_id: int, chapter_id: int
) -> list[Row]:
    """One entry per spine position: `{ply, fen, move, annotation}`, the note being the
    winning eligible candidate or None. Candidates carry `fen_norm, text, source, author,
    book_title, ann_line_id, ann_chapter_id, ann_chapter_title, updated_at, ann_id, moves`."""
    prepared = [(c, _as_list(c.get("moves"))) for c in candidates]

    def precedence(c: Row) -> tuple[int, int, tuple[int, datetime | None], int]:
        ua = c.get("updated_at")
        return (
            1 if c.get("ann_line_id") == line_id else 0,
            1 if c.get("ann_chapter_id") == chapter_id else 0,
            (1, ua) if ua is not None else (0, None),
            int(c.get("ann_id") or 0),
        )

    positions: list[Row] = []
    for idx, fen in enumerate(fen_sequence):
        try:
            target: str | None = normalize_fen(fen)
        except ValueError:
            target = None
        note: Row | None = None
        if target is not None:
            through = moves[: idx + 1]
            eligible = [
                c
                for c, cm in prepared
                if c.get("fen_norm") == target
                and cm is not None
                and cm[: idx + 1] == through
                and (idx > 0 or c.get("ann_line_id") == line_id or c.get("ann_chapter_id") == chapter_id)
            ]
            if eligible:
                win = max(eligible, key=precedence)
                note = {
                    "text": win.get("text"),
                    "source": win.get("source"),
                    "author": win.get("author"),
                    "book_title": win.get("book_title"),
                    "from_chapter": win.get("ann_chapter_title") if win.get("ann_chapter_id") != chapter_id else None,
                }
        positions.append({"ply": idx, "fen": fen, "move": moves[idx] if idx < len(moves) else None, "annotation": note})
    return positions


def line_with_notes(conn: Connection[Any], line_id: int) -> Row | None:
    """The line as stored — active or not — with a note projected onto each ply, or None if
    the line is not the player's."""
    row = conn.execute(
        "SELECT rl.id AS line_id, rl.line_name, rl.moves, rl.fen_sequence, ch.id AS chapter_id,"
        " ch.title AS chapter_title, bk.id AS book_id, bk.title AS book_title, bk.color"
        " FROM repertoire_lines rl JOIN chapters ch ON ch.id = rl.chapter_id JOIN books bk ON bk.id = ch.book_id"
        " WHERE rl.id = %s AND bk.player_id = %s",
        (line_id, PLAYER_ID),
    ).fetchone()
    if row is None:
        return None
    moves = strings(row["moves"])
    fens = strings(row["fen_sequence"])
    spine: list[str] = []
    for f in fens:
        try:
            spine.append(normalize_fen(f))
        except ValueError:
            continue
    # Only notes on this line's positions can project onto it; the book's other notes stay put.
    candidates = [
        dict(r)
        for r in conn.execute(
            "SELECT ra.fen_norm, ra.text, ra.source, ra.author, ra.book_title, ra.line_id AS ann_line_id,"
            " ra.updated_at, ra.id AS ann_id, rl2.moves, rl2.chapter_id AS ann_chapter_id,"
            " ch2.title AS ann_chapter_title"
            " FROM repertoire_annotations ra JOIN repertoire_lines rl2 ON rl2.id = ra.line_id"
            " JOIN chapters ch2 ON ch2.id = rl2.chapter_id"
            " WHERE ra.player_id = %s AND ch2.book_id = %s AND ra.fen_norm = ANY(%s)",
            (PLAYER_ID, row["book_id"], spine),
        ).fetchall()
    ]
    return {
        "line_id": row["line_id"],
        "line_name": row["line_name"],
        "color": row["color"],
        "book_title": row["book_title"],
        "chapter_title": row["chapter_title"],
        "positions": project(moves, fens, candidates, line_id, int(row["chapter_id"])),
    }
