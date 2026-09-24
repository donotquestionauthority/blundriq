"""`pipeline import-repertoire` (core/repertoire/importing.py) and the Repertoire page's toggles
(core/repertoire/books.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.notify import OperatorError
from core.repertoire import books, importing
from tests import repertoire_helpers as h

FIXTURE = Path(__file__).parent / "fixtures" / "repertoire_import.json"
TABIYA = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def _doc() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def _write(tmp_path: Path, doc: dict[str, Any]) -> Path:
    p = tmp_path / "rep.json"
    p.write_text(json.dumps(doc))
    return p


def _state(conn: psycopg.Connection[DictRow]) -> list[Any]:
    out: list[Any] = []
    for table, cols in (
        ("books", "id, source_book_id, title, color, active"),
        ("chapters", "id, book_id, source_chapter_id, title, root_fen, active"),
        (
            "repertoire_lines",
            "id, chapter_id, line_name, moves::text, fen_sequence::text, active, is_alternative, source_line_id",
        ),
        (
            "repertoire_annotations",
            "id, line_id, book_id, chapter_id, fen_norm, text, source, source_ref, author, book_title, updated_at",
        ),
    ):
        out.extend(tuple(r.values()) for r in conn.execute(f"SELECT {cols} FROM {table} ORDER BY 1").fetchall())  # type: ignore[arg-type]
    return out


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    h.player(clean)
    return clean


def _run(conn: psycopg.Connection[DictRow], path: Path, mode: str = "scratch", **kw: Any) -> dict[str, Any]:
    return importing.import_file(conn, path, mode=mode, window=100, **kw)  # type: ignore[arg-type]


def test_scratch_creates_everything_and_a_second_run_writes_nothing(
    db: psycopg.Connection[DictRow], tmp_path: Path
) -> None:
    path = _write(tmp_path, _doc())
    first = _run(db, path)
    assert first["books"]["created"] == 1 and first["chapters"]["created"] == 2
    assert first["lines"]["inserted"] == 4 and first["annotations"]["written"] == 3
    assert "rematch" in first
    snapshot = _state(db)
    second = _run(db, path)
    assert second["lines"] == {**second["lines"], "inserted": 0, "existing": 4}
    assert second["annotations"]["written"] == 0 and second["annotations"]["unchanged"] == 3
    assert "rematch" not in second
    assert _state(db) == snapshot
    row = db.execute("SELECT fen_sequence FROM repertoire_lines WHERE line_name = 'From the tabiya'").fetchone()
    assert row is not None and row["fen_sequence"][0] == TABIYA  # replayed from the chapter's root, not the start


def test_update_never_creates(db: psycopg.Connection[DictRow], tmp_path: Path) -> None:
    out = _run(db, _write(tmp_path, _doc()), mode="update")
    assert out["books"]["unknown"] == 1 and out["lines"]["inserted"] == 0
    assert db.execute("SELECT count(*) AS n FROM books").fetchone()["n"] == 0  # type: ignore[index]
    _run(db, _write(tmp_path, _doc()))
    doc = _doc()
    doc["books"][0]["chapters"][0]["lines"][0]["annotations"][0]["text"] = "rewritten"
    doc["books"][0]["chapters"][0]["lines"].append({"name": "New", "moves": ["d4"], "annotations": []})
    out = _run(db, _write(tmp_path, doc), mode="update")
    assert out["annotations"]["written"] == 1 and out["lines"]["unknown"] == 1 and out["lines"]["inserted"] == 0


def test_scratch_switches_off_what_vanished_and_never_deletes_a_line(
    db: psycopg.Connection[DictRow], tmp_path: Path
) -> None:
    _run(db, _write(tmp_path, _doc()))
    line_id = db.execute("SELECT id FROM repertoire_lines WHERE line_name = 'Main'").fetchone()["id"]  # type: ignore[index]
    # A puzzle on the line, with SRS state: both must survive.
    db.execute(
        "INSERT INTO puzzles (id, player_id, fen, solution_line, color, is_repertoire, repertoire_line_id, source_types)"
        " VALUES (1, %s, %s, '[\"e4\"]'::jsonb, 'w', TRUE, %s, ARRAY['deviation'])",
        (PLAYER_ID, h.START, line_id),
    )
    db.execute("INSERT INTO player_puzzle_state (player_id, puzzle_id) VALUES (%s, 1)", (PLAYER_ID,))
    doc = _doc()
    doc["books"][0]["chapters"][0]["lines"] = doc["books"][0]["chapters"][0]["lines"][1:]  # 'Main' is gone
    del doc["books"][0]["chapters"][1]  # and the tabiya chapter
    out = _run(db, _write(tmp_path, doc))
    assert out["lines"]["vanished_deactivated"] == 3 and out["chapters"]["deactivated"] == 1
    assert out["annotations"]["stale_deleted"] == 2 and "rematch" in out
    rows = db.execute("SELECT line_name, active FROM repertoire_lines ORDER BY id").fetchall()
    assert [(r["line_name"], r["active"]) for r in rows][:2] == [("Main", False), ("Two Knights", True)]
    assert db.execute("SELECT count(*) AS n FROM player_puzzle_state").fetchone()["n"] == 1  # type: ignore[index]
    # Present again: stays off, and is reported.
    out = _run(db, _write(tmp_path, _doc()))
    assert out["lines"]["present_but_inactive"] == 3 and out["lines"]["inserted"] == 0
    assert db.execute("SELECT active FROM repertoire_lines WHERE line_name = 'Main'").fetchone()["active"] is False  # type: ignore[index]


def test_manual_notes_survive_a_scratch_run(db: psycopg.Connection[DictRow], tmp_path: Path) -> None:
    from core.repertoire import annotations

    _run(db, _write(tmp_path, _doc()))
    line_id = db.execute("SELECT id FROM repertoire_lines WHERE line_name = 'Main'").fetchone()["id"]  # type: ignore[index]
    fen = h.spine(None, ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"])[4]  # where the course has a note too
    annotations.upsert(db, [{"fen": fen, "text": "mine"}], line_id, source="manual")
    out = _run(db, _write(tmp_path, _doc()), preserve_manual=True)
    assert out["annotations"]["skipped_preserved"] == 1 and out["annotations"]["stale_deleted"] == 0
    out = _run(db, _write(tmp_path, _doc()))
    assert out["annotations"]["written"] == 1  # without the flag the course note takes the position back


def test_dry_run_leaves_no_rows(db: psycopg.Connection[DictRow], tmp_path: Path) -> None:
    out = _run(db, _write(tmp_path, _doc()), dry_run=True)
    assert out["lines"]["inserted"] == 4 and out["dry_run"] is True
    assert db.execute("SELECT count(*) AS n FROM repertoire_lines").fetchone()["n"] == 0  # type: ignore[index]


def test_a_bad_file_names_only_indexes(db: psycopg.Connection[DictRow], tmp_path: Path) -> None:
    doc = _doc()
    doc["books"][0]["chapters"][1]["lines"][0]["moves"] = ["e4", "Ke2"]
    with pytest.raises(OperatorError) as exc:
        _run(db, _write(tmp_path, doc))
    assert "book 0 chapter 1" in str(exc.value) and "Ke2" not in str(exc.value)
    with pytest.raises(OperatorError):
        _run(db, _write(tmp_path, {"books": [{"title": "x"}]}))


def test_off_spine_notes_are_counted_and_dropped(db: psycopg.Connection[DictRow], tmp_path: Path) -> None:
    doc = _doc()
    off = "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -"
    doc["books"][0]["chapters"][0]["lines"][0]["annotations"].append({"fen_norm": off, "text": "elsewhere"})
    out = _run(db, _write(tmp_path, doc))
    assert out["annotations"]["skipped_off_spine"] == 1


# --- the cohort gate --------------------------------------------------------------------------


def _prep(color: str, *lines: list[str]) -> list[importing.Prepared]:
    out: list[importing.Prepared] = []
    for i, moves in enumerate(lines):
        fens = importing.spine(None, moves)
        line = importing.LineIn(name=f"L{i}", moves=moves)
        out.append(importing.Prepared(0, i, line, fens, importing.signatures(moves, fens, color)))
    return out


def test_the_batch_resolves_its_own_disagreement_by_plurality_then_file_order() -> None:
    batch = _prep("white", ["e4", "e5", "Nf3"], ["e4", "e5", "Bc4"], ["e4", "e5", "Nf3", "Nc6"], ["e4", "c5", "Nf3"])
    assert importing.decide({}, set(), batch) == ["active", "inactive", "active", "active"]
    tie = _prep("white", ["e4", "e5", "Bc4"], ["e4", "e5", "Nf3"])
    assert importing.decide({}, set(), tie) == ["active", "inactive"]  # earliest chapter wins a tie


def test_an_active_existing_line_anchors_and_a_dirty_position_blocks() -> None:
    after_e5 = importing.spine(None, ["e4", "e5"])[2]
    batch = _prep("white", ["e4", "e5", "Nf3"], ["e4", "e5", "Bc4"])
    assert importing.decide({after_e5: {"Bc4"}}, set(), batch) == ["inactive", "active"]
    assert importing.decide({after_e5: {"Bc4", "Nf3"}}, {after_e5}, batch) == ["inactive", "inactive"]


def test_the_gate_reads_only_effectively_active_lines(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "B", "white")
    h.chapter(db, 1, 1, "C")
    h.line(db, 1, 1, "active", ["e4", "e5", "Nf3"])
    h.line(db, 2, 1, "latent", ["e4", "e5", "Bc4"], active=False)
    h.line(db, 3, 1, "dirty", ["e4", "e5", "d4"])
    index, dirty = importing.existing_index(db)
    after_e5 = importing.spine(None, ["e4", "e5"])[2]
    assert index[after_e5] == {"Nf3", "d4"} and dirty == {after_e5}
    assert index[h.START] == {"e4"}


def test_a_new_line_the_repertoire_disagrees_with_comes_in_switched_off(
    db: psycopg.Connection[DictRow], tmp_path: Path
) -> None:
    h.book(db, 5, "B", "white")
    h.chapter(db, 5, 5, "C")
    h.line(db, 5, 5, "anchor", ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3"])
    out = _run(db, _write(tmp_path, _doc()))
    assert out["lines"]["imported_inactive"] == 1  # 'Main' plays c3 where the anchor plays d3
    row = db.execute("SELECT active FROM repertoire_lines WHERE line_name = 'Main'").fetchone()
    assert row is not None and row["active"] is False


# --- the page's toggles -------------------------------------------------------------------------


def test_toggles_flip_one_flag_and_rematch_only_the_games_it_can_touch(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "Italian", "white")
    h.chapter(db, 1, 1, "Giuoco")
    h.line(db, 1, 1, "Main", ["e4", "e5", "Nf3", "Nc6", "Bc4"])
    h.line(db, 2, 1, "Spanish", ["e4", "e5", "Nf3", "Nc6", "Bb5"], active=False)
    h.game(db, 1, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"], days_ago=1)
    h.game(db, 2, ["d4", "d5"], days_ago=2)
    from core.repertoire import matching

    matching.match_player(db, 100)

    def by(gid: int) -> str | None:
        row = db.execute("SELECT deviation_by FROM game_repertoire_results WHERE chess_game_id = %s", (gid,)).fetchone()
        return None if row is None else str(row["deviation_by"])

    assert by(1) == "me" and by(2) is None
    row = db.execute("SELECT no_repertoire_match FROM player_games WHERE chess_game_id = 2").fetchone()
    assert row is not None and row["no_repertoire_match"] is True
    # Switching the Spanish line on: game 1 contains its second position, game 2 does not.
    out = books.set_active(db, "lines", 2, True, 100)
    assert out is not None and out["candidates"] == 1
    assert by(1) == "none"
    # Switching the whole book off: only game 1 cites its lines.
    out = books.set_active(db, "books", 1, False, 100)
    assert out is not None and out["candidates"] == 1 and out["lines"] == 0
    assert db.execute("SELECT count(*) AS n FROM game_repertoire_results").fetchone()["n"] == 0  # type: ignore[index]
    assert books.set_active(db, "chapters", 99, True, 100) is None
    assert books.books(db)[0]["active"] is False
    sections = books.sections(db, 1)
    assert sections is not None and [ln["name"] for ln in sections[0]["lines"]] == ["Main", "Spanish"]
    assert books.sections(db, 99) is None


# --- the design review's cases --------------------------------------------------------------


def test_a_replacement_line_is_not_blocked_by_the_line_it_replaces(
    db: psycopg.Connection[DictRow], tmp_path: Path
) -> None:
    """The course changed its mind: e4 became d4. The old line is switched off by the same
    run, so it must not anchor the position against its own replacement."""
    _run(db, _write(tmp_path, _doc()))
    doc = _doc()
    for line, reply in zip(doc["books"][0]["chapters"][0]["lines"], ("d5", "Nf6"), strict=True):
        line["moves"] = ["d4", reply, "c4"]
        line["annotations"] = []
    del doc["books"][0]["chapters"][1]
    out = _run(db, _write(tmp_path, doc))
    assert out["lines"]["inserted"] == 2 and out["lines"]["imported_inactive"] == 0
    assert out["lines"]["vanished_deactivated"] == 4
    active = db.execute("SELECT line_name FROM repertoire_lines WHERE active ORDER BY id").fetchall()
    assert [r["line_name"] for r in active] == ["Main", "Two Knights"]
    assert [r["moves"][0] for r in db.execute("SELECT moves FROM repertoire_lines WHERE active").fetchall()] == [
        "d4",
        "d4",
    ]


def test_a_book_not_declared_complete_is_only_added_to(db: psycopg.Connection[DictRow], tmp_path: Path) -> None:
    """An extraction that lost a note or a line is not the author removing it."""
    _run(db, _write(tmp_path, _doc()))
    doc = _doc()
    doc["books"][0]["complete"] = False
    doc["books"][0]["chapters"][0]["lines"] = doc["books"][0]["chapters"][0]["lines"][1:]
    del doc["books"][0]["chapters"][1]
    out = _run(db, _write(tmp_path, doc))
    assert out["books"]["not_complete"] == 1
    assert out["lines"]["vanished_deactivated"] == 0 and out["chapters"]["deactivated"] == 0
    assert out["annotations"]["stale_deleted"] == 0 and "rematch" not in out
    assert db.execute("SELECT count(*) AS n FROM repertoire_lines WHERE active").fetchone()["n"] == 4  # type: ignore[index]
    assert db.execute("SELECT count(*) AS n FROM repertoire_annotations").fetchone()["n"] == 3  # type: ignore[index]


def test_a_renamed_chapter_keeps_its_lines_when_its_source_id_is_known(
    db: psycopg.Connection[DictRow], tmp_path: Path
) -> None:
    _run(db, _write(tmp_path, _doc()))
    before = [r["id"] for r in db.execute("SELECT id FROM repertoire_lines ORDER BY id").fetchall()]
    doc = _doc()
    doc["books"][0]["chapters"][0]["title"] = "1) Giuoco Piano, revised"
    out = _run(db, _write(tmp_path, doc))
    assert out["chapters"]["renamed"] == 1 and out["chapters"]["created"] == 0
    assert out["lines"]["inserted"] == 0 and out["lines"]["vanished_deactivated"] == 0
    assert [r["id"] for r in db.execute("SELECT id FROM repertoire_lines ORDER BY id").fetchall()] == before
    titles = [r["title"] for r in db.execute("SELECT title FROM chapters ORDER BY id").fetchall()]
    assert titles[0] == "1) Giuoco Piano, revised"


def test_a_chapter_without_a_source_id_is_adopted_by_title_and_a_clash_stops_the_import(
    db: psycopg.Connection[DictRow], tmp_path: Path
) -> None:
    h.book(db, 7, "Course: The Italian", "white")
    db.execute("UPDATE books SET source_book_id = 170001 WHERE id = 7")
    h.chapter(db, 7, 7, "1) Giuoco Piano")  # migrated without a source id
    h.line(db, 7, 7, "Main", ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3"])
    out = _run(db, _write(tmp_path, _doc()))
    assert out["chapters"]["existing"] == 1 and out["chapters"]["created"] == 1 and out["lines"]["existing"] == 1
    row = db.execute("SELECT source_chapter_id FROM chapters WHERE id = 7").fetchone()
    assert row is not None and row["source_chapter_id"] == 0
    doc = _doc()
    doc["books"][0]["chapters"][0]["source_chapter_id"] = 42  # same title, another id
    with pytest.raises(OperatorError):
        _run(db, _write(tmp_path, doc))
    assert db.execute("SELECT count(*) AS n FROM chapters WHERE book_id = 7").fetchone()["n"] == 2  # type: ignore[index]


def test_a_matcher_that_read_the_lines_before_a_toggle_cannot_publish_after_it(fresh_db_url: str) -> None:
    """Two connections: the hourly matcher and a toggle. The lock is held from reading the
    lines to publishing, so the toggle waits, and the matcher's results reflect the lines
    it read while nothing else changed them."""
    import threading

    from psycopg.rows import dict_row

    from core.repertoire import matching
    from tests.conftest import reset_game_data

    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as setup:
        reset_game_data(setup)
        h.player(setup)
        h.book(setup, 1, "Italian", "white")
        h.chapter(setup, 1, 1, "Giuoco")
        h.line(setup, 1, 1, "Main", ["e4", "e5", "Nf3", "Nc6", "Bc4"])
        h.game(setup, 1, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"], days_ago=1)
        setup.commit()
    try:
        with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as matcher:
            with matcher.transaction():
                matching.lock(matcher)
                lines = matching.active_lines(matcher)  # the matcher has read the lines
                assert len(lines) == 1
                done = threading.Event()

                def toggle() -> None:
                    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as c:
                        with c.transaction():
                            books.set_active(c, "books", 1, False, 100)
                    done.set()

                t = threading.Thread(target=toggle)
                t.start()
                assert not done.wait(0.5)  # the toggle is waiting on the lock
                games = matching.unmatched_games(matcher, 100)
                results = [
                    matching.match_game(
                        g["chess_game_id"], g["player_color"], list(g["moves"]), list(g["fen_sequence"]), lines
                    )
                    for g in games
                ]
                matching.write_results(matcher, [r for r in results if r], [])
            t.join(5)
            assert done.is_set()
        with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as check:
            assert check.execute("SELECT count(*) AS n FROM game_repertoire_results").fetchone()["n"] == 0  # type: ignore[index]
            assert check.execute("SELECT active FROM books WHERE id = 1").fetchone()["active"] is False  # type: ignore[index]
    finally:
        with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as setup:
            reset_game_data(setup)


def test_a_tie_inside_one_chapter_goes_to_the_lexically_first_move() -> None:
    lines = [["e4", "e5", "Nf3"], ["e4", "e5", "Bc4"]]
    batch = [
        importing.Prepared(
            0,
            0,
            importing.LineIn(name=f"L{i}", moves=m),
            importing.spine(None, m),
            importing.signatures(m, importing.spine(None, m), "white"),
        )
        for i, m in enumerate(lines)
    ]
    assert importing.decide({}, set(), batch) == ["inactive", "active"]


def test_notes_are_written_in_bulk_with_every_outcome_counted(db: psycopg.Connection[DictRow]) -> None:
    from core.repertoire import annotations

    h.book(db, 1, "B", "white")
    h.chapter(db, 1, 1, "C")
    fens = h.line(db, 1, 1, "Main", ["e4", "e5", "Nf3"])
    annotations.upsert(db, [{"fen": fens[1], "text": "mine"}], 1, source="manual")
    notes = [
        {"line_id": 1, "fen_norm": fens[0], "text": "one", "author": "A", "book_title": "B", "source_ref": "9"},
        {"line_id": 1, "fen_norm": fens[0], "text": "one again", "author": "A", "book_title": "B", "source_ref": "9"},
        {"line_id": 1, "fen_norm": fens[1], "text": "over mine", "author": "A", "book_title": "B", "source_ref": "9"},
        {"line_id": 1, "fen_norm": fens[2], "text": "   ", "author": "A", "book_title": "B", "source_ref": "9"},
        {
            "line_id": 1,
            "fen_norm": "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
            "text": "off",
            "author": None,
            "book_title": "B",
            "source_ref": "9",
        },
        {"line_id": 1, "fen_norm": "not a fen", "text": "bad", "author": None, "book_title": "B", "source_ref": "9"},
        {"line_id": 99, "fen_norm": fens[0], "text": "no line", "author": None, "book_title": "B", "source_ref": "9"},
    ]
    out = annotations.upsert_many(db, notes, source="course", preserve_manual=True)
    assert out == {
        "written": 1,
        "unchanged": 0,
        "skipped_no_line": 1,
        "skipped_off_spine": 2,
        "skipped_preserved": 1,
        "blank": 1,
    }
    row = db.execute(
        "SELECT text FROM repertoire_annotations WHERE fen_norm = %s", (annotations.normalize_fen(fens[0]),)
    ).fetchone()
    assert row is not None and row["text"] == "one again"  # the later copy of a key wins
    again = annotations.upsert_many(db, notes, source="course", preserve_manual=True)
    assert again["written"] == 0 and again["unchanged"] == 1
    taken = annotations.upsert_many(db, notes[2:3], source="course")
    assert taken == {**taken, "written": 1, "skipped_preserved": 0}


def test_a_rename_onto_another_chapters_title_stops_the_import(db: psycopg.Connection[DictRow], tmp_path: Path) -> None:
    _run(db, _write(tmp_path, _doc()))
    doc = _doc()
    doc["books"][0]["chapters"][0]["title"] = "2) From the tabiya"
    doc["books"][0]["chapters"][0]["root_fen"] = doc["books"][0]["chapters"][1]["root_fen"]
    with pytest.raises(OperatorError):
        _run(db, _write(tmp_path, doc))


def test_a_complete_book_without_lines_is_refused(db: psycopg.Connection[DictRow], tmp_path: Path) -> None:
    _run(db, _write(tmp_path, _doc()))
    doc = _doc()
    doc["books"][0]["chapters"] = []
    with pytest.raises(OperatorError):
        _run(db, _write(tmp_path, doc))
    assert db.execute("SELECT count(*) AS n FROM repertoire_lines WHERE active").fetchone()["n"] == 4  # type: ignore[index]


def test_a_chapter_toggle_rematches_the_games_that_cite_its_lines(db: psycopg.Connection[DictRow]) -> None:
    from core.repertoire import matching

    h.book(db, 1, "Italian", "white")
    h.chapter(db, 1, 1, "Giuoco")
    h.chapter(db, 2, 1, "Tabiya")
    h.line(db, 1, 1, "Main", ["e4", "e5", "Nf3", "Nc6", "Bc4"])
    root = h.spine(None, ["e4", "e5", "Nf3", "Nc6"])[4]
    h.line(db, 2, 2, "From the tabiya", ["Bb5", "a6"], fens=importing.spine(root, ["Bb5", "a6"]), active=False)
    h.game(db, 1, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"], days_ago=1)
    matching.match_player(db, 100)
    assert db.execute("SELECT deviation_by FROM game_repertoire_results").fetchone()["deviation_by"] == "me"  # type: ignore[index]
    # A tabiya line coming on is found by the game containing its first position after the root.
    out = books.set_active(db, "chapters", 2, True, 100)
    assert out is not None and out["candidates"] == 1
    out = books.set_active(db, "chapters", 1, False, 100)
    assert out is not None and out["candidates"] == 1
