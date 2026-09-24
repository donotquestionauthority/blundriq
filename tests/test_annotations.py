"""Position notes (core/repertoire/annotations.py): two namespaces, the spine rule, last write
wins, and the walk-through's projection."""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.repertoire import annotations as ann
from tests import repertoire_helpers as h

MAIN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]
SIBLING = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]
OTHER_CH = ["e4", "e5", "Nf3", "Nf6", "Nxe5"]


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    h.player(clean)
    h.book(clean, 1, "Italian", "white")
    h.chapter(clean, 1, 1, "Giuoco")
    h.chapter(clean, 2, 1, "Petroff")
    h.line(clean, 1, 1, "Main", MAIN)
    h.line(clean, 2, 1, "Ruy", SIBLING)
    h.line(clean, 3, 2, "Petroff main", OTHER_CH)
    h.book(clean, 2, "Other opening", "white")
    h.chapter(clean, 3, 2, "Ch")
    h.line(clean, 4, 3, "Same moves elsewhere", MAIN)
    return clean


def test_normalize_fen_keeps_four_fields_and_rejects_fewer() -> None:
    assert ann.normalize_fen(h.START) == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
    with pytest.raises(ValueError):
        ann.normalize_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq")


def test_clean_text_strips_commands_and_html_and_keeps_move_references() -> None:
    raw = "Play @@SANStart@@Nf3@@SANEnd@@ here [%cal Gg1f3] <b>now</b>\n\n@@StartFEN@@x@@EndFEN@@ ok"
    assert ann.clean_text(raw) == "Play @@SANStart@@Nf3@@SANEnd@@ here now ok"
    assert ann.clean_text("   ") == ""


def test_unattached_and_attached_notes_are_separate_namespaces(db: psycopg.Connection[DictRow]) -> None:
    fens = h.spine(None, MAIN)
    ann.upsert(db, [{"fen": fens[4], "text": "bare"}], None, source="manual")
    ann.upsert(db, [{"fen": fens[4], "text": "on the line"}], 1, source="manual")
    assert ann.get(db, fens[4])["text"] == "bare"  # type: ignore[index]
    assert ann.get(db, fens[4] + " 9 9")["text"] == "bare"  # type: ignore[index]  # counters do not matter
    line = ann.line_with_notes(db, 1)
    assert line is not None and line["positions"][4]["annotation"]["text"] == "on the line"
    assert ann.delete(db, fens[4], None) and ann.get(db, fens[4]) is None
    assert not ann.delete(db, fens[4], None)
    assert ann.line_with_notes(db, 1)["positions"][4]["annotation"]["text"] == "on the line"  # type: ignore[index]


def test_an_attached_note_must_sit_on_the_line(db: psycopg.Connection[DictRow]) -> None:
    off = h.spine(None, OTHER_CH)[4]
    out = ann.upsert(db, [{"fen": off, "text": "x"}], 1, source="manual")
    assert out["skipped_off_spine"] == 1 and out["written"] == 0
    assert ann.upsert(db, [{"fen": off, "text": "x"}], 999, source="manual")["skipped_no_line"] == 1
    assert ann.upsert(db, [{"fen": off, "text": "[%eval 0.3]"}], 3, source="manual")["blank"] == 1


def test_last_write_wins_unless_a_manual_note_is_preserved(db: psycopg.Connection[DictRow]) -> None:
    fen = h.spine(None, MAIN)[2]
    ann.upsert(db, [{"fen": fen, "text": "course says", "author": "GM"}], 1, source="course", source_ref="7")
    ann.upsert(db, [{"fen": fen, "text": "my note"}], 1, source="manual")
    line = ann.line_with_notes(db, 1)
    assert line is not None and line["positions"][2]["annotation"] == {
        "text": "my note",
        "source": "manual",
        "author": None,
        "book_title": None,
        "from_chapter": None,
    }
    kept = ann.upsert(db, [{"fen": fen, "text": "course again"}], 1, source="course", preserve_manual=True)
    assert kept["skipped_preserved"] == 1 and kept["written"] == 0
    replaced = ann.upsert(db, [{"fen": fen, "text": "course again"}], 1, source="course")
    assert replaced["written"] == 1
    again = ann.upsert(db, [{"fen": fen, "text": "course again"}], 1, source="course")
    assert again == {**again, "written": 0, "unchanged": 1}


def test_an_unchanged_note_keeps_its_updated_at(db: psycopg.Connection[DictRow]) -> None:
    fen = h.spine(None, MAIN)[2]
    ann.upsert(db, [{"fen": fen, "text": "t"}], 1, source="course")
    db.execute("UPDATE repertoire_annotations SET updated_at = '2020-01-01'")
    ann.upsert(db, [{"fen": fen, "text": "t"}], 1, source="course")
    row = db.execute("SELECT updated_at FROM repertoire_annotations").fetchone()
    assert row is not None and row["updated_at"].year == 2020


def test_projection_walls_by_book_and_by_path_through(db: psycopg.Connection[DictRow]) -> None:
    fens = h.spine(None, MAIN)
    # A note on the sibling at the shared position (after Nc6) is about Bb5, not Bc4.
    ann.upsert(db, [{"fen": fens[4], "text": "sibling"}], 2, source="course")
    # A note on the same moves in another book must never cross over.
    ann.upsert(db, [{"fen": fens[4], "text": "other book"}], 4, source="course")
    # A note on the sibling at an earlier shared position (after e5) IS about Nf3: it projects.
    ann.upsert(db, [{"fen": fens[2], "text": "shared prefix"}], 2, source="course")
    line = ann.line_with_notes(db, 1)
    assert line is not None
    notes = [p["annotation"]["text"] if p["annotation"] else None for p in line["positions"]]
    assert notes == [None, None, "shared prefix", None, None, None, None]
    assert [p["move"] for p in line["positions"]] == MAIN + [None]
    assert line["positions"][2]["annotation"]["from_chapter"] is None  # same chapter


def test_at_the_root_only_the_line_and_its_chapter_lend_a_note(db: psycopg.Connection[DictRow]) -> None:
    ann.upsert(db, [{"fen": h.START, "text": "petroff preamble"}], 3, source="course")
    assert ann.line_with_notes(db, 1)["positions"][0]["annotation"] is None  # type: ignore[index]
    ann.upsert(db, [{"fen": h.START, "text": "giuoco preamble"}], 2, source="course")
    got = ann.line_with_notes(db, 1)["positions"][0]["annotation"]  # type: ignore[index]
    assert got["text"] == "giuoco preamble" and got["from_chapter"] is None
    ann.upsert(db, [{"fen": h.START, "text": "own"}], 1, source="manual")
    assert ann.line_with_notes(db, 1)["positions"][0]["annotation"]["text"] == "own"  # type: ignore[index]


def test_precedence_is_own_line_then_chapter_then_freshness(db: psycopg.Connection[DictRow]) -> None:
    fens = h.spine(None, MAIN)
    at = fens[1]  # after e4: every line of the book passes through with the same next move e5
    h.line(db, 5, 2, "Petroff too", MAIN)  # same moves, other chapter
    ann.upsert(db, [{"fen": at, "text": "other chapter, newest"}], 5, source="course")
    db.execute("UPDATE repertoire_annotations SET updated_at = %s", (datetime(2030, 1, 1, tzinfo=UTC),))
    ann.upsert(db, [{"fen": at, "text": "same chapter, older"}], 2, source="course")
    db.execute(
        "UPDATE repertoire_annotations SET updated_at = %s WHERE line_id = 2", (datetime(2020, 1, 1, tzinfo=UTC),)
    )
    got = ann.line_with_notes(db, 1)["positions"][1]["annotation"]  # type: ignore[index]
    assert got["text"] == "same chapter, older" and got["from_chapter"] is None
    db.execute("DELETE FROM repertoire_annotations WHERE line_id = 2")
    got = ann.line_with_notes(db, 1)["positions"][1]["annotation"]  # type: ignore[index]
    assert got["text"] == "other chapter, newest" and got["from_chapter"] == "Petroff"


def test_a_retired_line_is_still_readable(db: psycopg.Connection[DictRow]) -> None:
    db.execute("UPDATE repertoire_lines SET active = FALSE WHERE id = 1")
    db.execute("UPDATE books SET active = FALSE WHERE id = 1")
    line = ann.line_with_notes(db, 1)
    assert line is not None and line["line_name"] == "Main" and line["color"] == "white"
    assert ann.line_with_notes(db, 999) is None


def test_notes_belong_to_the_player(db: psycopg.Connection[DictRow]) -> None:
    fen = h.spine(None, MAIN)[2]
    ann.upsert(db, [{"fen": fen, "text": "t"}], None, source="manual")
    row = db.execute("SELECT player_id, line_id, book_id FROM repertoire_annotations").fetchone()
    assert row is not None and (row["player_id"], row["line_id"], row["book_id"]) == (PLAYER_ID, None, None)
