"""Lines the player adds by hand and deletes (core/repertoire/lines.py), what the import may
not do to them, and migration 014 over a populated database."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from core import oracle, schema
from core.constants import CATCH_ALL_TITLE, PLAYER_ID
from core.puzzles import stats
from core.puzzles.generate import repertoire as gen
from core.repertoire import importing, lines, matching, read
from core.settings import Settings
from tests import repertoire_helpers as h
from tests.test_schema import FIXTURES, _scratch

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3"]
SCANDI_GAP = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5", "d4"]
FIXTURE = Path(__file__).parent / "fixtures" / "repertoire_import.json"


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    h.player(clean)
    h.book(clean, 901, "White: Italian", "white")
    h.chapter(clean, 901, 901, "Main line")
    h.line(clean, 901, 901, "Giuoco", ITALIAN)
    return clean


def _origin(conn: psycopg.Connection[DictRow], line_id: int) -> str:
    return str(conn.execute("SELECT origin FROM repertoire_lines WHERE id = %s", (line_id,)).fetchone()["origin"])  # type: ignore[index]


# --- add_manual -------------------------------------------------------------------------


def test_a_manual_line_goes_in_whole_from_move_one_and_is_matched_at_once(db: psycopg.Connection[DictRow]) -> None:
    h.game(db, 10, SCANDI_GAP + ["Nf6"], color="white")
    out = lines.add_manual(db, moves=SCANDI_GAP, line_name="Scandi: 4.d4", window=100, chapter_id=901)
    row = db.execute("SELECT * FROM repertoire_lines WHERE id = %s", (out["id"],)).fetchone()
    assert row is not None and row["origin"] == "manual" and row["active"] and row["source_line_id"] is None
    assert row["moves"] == SCANDI_GAP and row["fen_sequence"][0] == h.START and len(row["fen_sequence"]) == 8
    assert out["chapter_id"] == 901 and out["book_id"] == 901 and out["rematch"]["matched"] == 1
    cited = db.execute("SELECT line_id FROM game_result_lines").fetchall()
    assert [r["line_id"] for r in cited] == [out["id"]]


def test_moves_are_re_rendered_as_the_servers_san(db: psycopg.Connection[DictRow]) -> None:
    out = lines.add_manual(
        db, moves=["e4", "d5", "exd5", "Qxd5+?!", "Nc3", "Qa5", "d4!"], line_name="x", window=0, chapter_id=901
    )
    row = db.execute("SELECT moves FROM repertoire_lines WHERE id = %s", (out["id"],)).fetchone()
    assert row is not None and row["moves"] == SCANDI_GAP


@pytest.mark.parametrize(
    ("moves", "why"),
    [
        (["e4"], "2 to"),
        (SCANDI_GAP + ["Nf6"], "ends on your move"),  # White's line ending on Black's move
        (["e4", "e5", "Nf3", "Nc6", "Bc4", "Nxe4"], "does not play"),
        (["e4", "e5", "--", "Nc6", "Bc4"], "does not play"),  # the null move is not a move
    ],
)
def test_a_line_that_cannot_be_one_is_refused_before_anything_is_written(
    db: psycopg.Connection[DictRow], moves: list[str], why: str
) -> None:
    with pytest.raises(lines.InvalidLine, match=why):
        lines.add_manual(db, moves=moves, line_name="x", window=0, chapter_id=901)
    assert db.execute("SELECT count(*) AS n FROM repertoire_lines").fetchone()["n"] == 1  # type: ignore[index]


def test_a_tabiya_rooted_chapter_is_not_a_destination(db: psycopg.Connection[DictRow]) -> None:
    db.execute(
        "INSERT INTO chapters (id, book_id, title, active, root_fen) VALUES (902, 901, 'Tabiya', TRUE, %s)",
        ("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",),
    )
    with pytest.raises(lines.NoChapter, match="rooted"):
        lines.add_manual(db, moves=SCANDI_GAP, line_name="x", window=0, chapter_id=902)
    with pytest.raises(lines.NoChapter):
        lines.add_manual(db, moves=SCANDI_GAP, line_name="x", window=0, chapter_id=99)
    h.chapter(db, 903, 901, "Off", active=False)
    with pytest.raises(lines.NoChapter):
        lines.add_manual(db, moves=SCANDI_GAP, line_name="x", window=0, chapter_id=903)


def test_an_identical_line_or_one_the_book_already_teaches_further_is_a_duplicate(
    db: psycopg.Connection[DictRow],
) -> None:
    with pytest.raises(lines.Duplicate) as e1:
        lines.add_manual(db, moves=ITALIAN, line_name="again", window=0, chapter_id=901)
    assert e1.value.kind == "identical" and e1.value.line_id == 901 and e1.value.active
    with pytest.raises(lines.Duplicate) as e2:
        lines.add_manual(db, moves=ITALIAN[:5], line_name="shorter", window=0, chapter_id=901)
    assert e2.value.kind == "prefix" and e2.value.line_id == 901
    # A switched-off line is still an identical duplicate, but no longer a prefix anchor.
    db.execute("UPDATE repertoire_lines SET active = FALSE WHERE id = 901")
    with pytest.raises(lines.Duplicate) as e3:
        lines.add_manual(db, moves=ITALIAN, line_name="again", window=0, chapter_id=901)
    assert e3.value.kind == "identical" and not e3.value.active
    out = lines.add_manual(db, moves=ITALIAN[:5], line_name="shorter", window=0, chapter_id=901)
    assert _origin(db, out["id"]) == "manual"


def test_duplicates_are_found_through_check_marks_and_only_an_effective_line_holds_a_prefix(
    db: psycopg.Connection[DictRow],
) -> None:
    checking = ["e4", "d5", "Bb5+", "c6", "Ba4"]  # the server renders Bb5 as Bb5+
    first = lines.add_manual(db, moves=checking, line_name="check", window=0, chapter_id=901)
    stored = db.execute("SELECT moves FROM repertoire_lines WHERE id = %s", (first["id"],)).fetchone()
    assert stored is not None and stored["moves"][2] == "Bb5+"
    with pytest.raises(lines.Duplicate) as e1:
        lines.add_manual(db, moves=["e4", "d5", "Bb5", "c6", "Ba4"], line_name="again", window=0, chapter_id=901)
    assert e1.value.kind == "identical" and e1.value.line_id == first["id"]
    h.chapter(db, 905, 901, "Other")
    with pytest.raises(lines.Duplicate) as e2:
        lines.add_manual(db, moves=["e4", "d5", "Bb5"], line_name="short", window=0, chapter_id=905)
    assert e2.value.kind == "prefix" and e2.value.line_id == first["id"]
    # The holder's chapter switched off: the line flag is still on, but it teaches nothing now.
    db.execute("UPDATE chapters SET active = FALSE WHERE id = 901")
    out = lines.add_manual(db, moves=["e4", "d5", "Bb5"], line_name="short", window=0, chapter_id=905)
    assert _origin(db, out["id"]) == "manual"


def test_the_gate_refuses_a_line_the_active_repertoire_disagrees_with_and_names_the_holder(
    db: psycopg.Connection[DictRow],
) -> None:
    # The Italian plays 3.Bc4; a manual 3.Bb5 at the same board is a conflict, not an inactive row.
    with pytest.raises(lines.Refused) as e:
        lines.add_manual(db, moves=["e4", "e5", "Nf3", "Nc6", "Bb5"], line_name="Spanish", window=0, chapter_id=901)
    r = e.value.refusal
    assert r.reason == "anchor" and r.move == "Bb5" and [x["line_id"] for x in r.rivals] == [901]
    assert db.execute("SELECT count(*) AS n FROM repertoire_lines").fetchone()["n"] == 1  # type: ignore[index]
    # Switched off, the Italian no longer anchors.
    db.execute("UPDATE repertoire_lines SET active = FALSE WHERE id = 901")
    lines.add_manual(db, moves=["e4", "e5", "Nf3", "Nc6", "Bb5"], line_name="Spanish", window=0, chapter_id=901)


def test_a_manual_line_is_effective_for_the_read_side(db: psycopg.Connection[DictRow]) -> None:
    lines.add_manual(db, moves=SCANDI_GAP, line_name="Scandi: 4.d4", window=0, chapter_id=901)
    fen = importing.spine(None, SCANDI_GAP[:-1])[-1]
    assert read.coverage(db, fen)["status"] in ("covered", "match")


# --- which games a new line rematches ----------------------------------------------------

NAJDORF = ["e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"]
ALAPIN = ["e4", "c5", "c3", "d5"]
QGD = ["d4", "d5", "c4", "e6"]


def _rematch_fixture(db: psycopg.Connection[DictRow]) -> None:
    """Both colours' books and games through shared first moves, matched once by the matcher;
    then one Black game imported after the match, so it has no result row and no flag."""
    h.book(db, 902, "Black", "black")
    h.chapter(db, 902, 902, "Lines")
    h.line(db, 902, 902, "Najdorf", NAJDORF)
    h.line(db, 903, 902, "Alapin", ALAPIN)
    h.line(db, 904, 902, "QGD", QGD)
    games = {
        11: (NAJDORF + ["Be2", "e5", "Nb3", "Be7"], "black"),  # the Najdorf whole: b = 10
        12: (["e4", "c5", "Nf3", "d6", "Bb5+", "Bd7", "Bxd7+", "Qxd7"], "black"),  # leaves at Bb5+: b = 4
        13: (ALAPIN + ["exd5", "Qxd5", "d4", "Nf6"], "black"),  # the Alapin whole: b = 4
        14: (["e4", "c5", "Nc3", "Nc6", "g3", "g6"], "black"),  # Najdorf and Alapin tied: b = 2
        15: (["c4", "e5", "Nc3", "Nf6", "g3", "d5"], "black"),  # no Black line: flagged no-match
        16: (QGD + ["Nc3", "Nf6"], "black"),  # never holds 1.e4
        21: (ITALIAN + ["Nf6", "d4"], "white"),  # the Italian whole: b = 7
        22: (["e4", "c5", "Nf3", "d6", "d4", "cxd4"], "white"),  # White through Black's Sicilian: b = 1
        23: (["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"], "white"),  # leaves the Italian at Bb5: b = 4
    }
    for gid, (moves, colour) in games.items():
        h.game(db, gid, moves, color=colour, days_ago=gid)
    matching.match_player(db, 100)
    h.game(db, 17, ["e4", "c5", "Nf3", "d6", "d4", "Nf6"], color="black", days_ago=0.5)  # after the match


def _tables(conn: psycopg.Connection[DictRow], new_line: int) -> tuple[Any, ...]:
    """Every matcher output, keyed so two runs that minted different ids compare equal."""
    fields = ", ".join(f"grr.{f}" for f in oracle.RESULT_FIELDS)
    results = conn.execute(
        f"SELECT grr.chess_game_id, {fields} FROM game_repertoire_results grr ORDER BY 1"  # type: ignore[arg-type]
    ).fetchall()
    cited = conn.execute(
        "SELECT grr.chess_game_id, grl.line_id, grl.matched_ply FROM game_result_lines grl"
        " JOIN game_repertoire_results grr ON grr.id = grl.game_repertoire_result_id ORDER BY 1, 2"
    ).fetchall()
    flags = conn.execute("SELECT chess_game_id, no_repertoire_match FROM player_games ORDER BY 1").fetchall()
    return (
        [tuple(r.values()) for r in results],
        [(r["chess_game_id"], "new" if r["line_id"] == new_line else r["line_id"], r["matched_ply"]) for r in cited],
        [tuple(r.values()) for r in flags],
    )


def _reference_selection(
    conn: psycopg.Connection[Any], line_id: int, colour: str, line_fens: list[str], window: int
) -> tuple[list[int], int]:
    touched = matching.games_touched_by(conn, [line_id], activated=True, window=window)
    return touched, len(touched)


def _add_both_ways(
    db: psycopg.Connection[DictRow],
    monkeypatch: pytest.MonkeyPatch,
    moves: list[str],
    chapter_id: int,
    window: int = 100,
) -> tuple[tuple[Any, ...], tuple[Any, ...], dict[str, int]]:
    """The tables after add_manual and after the reference (every touched game rematched,
    written a round trip per game), each in its own rolled-back savepoint."""
    out: list[tuple[Any, ...]] = []
    counts: dict[str, int] = {}
    for reference in (False, True):
        with monkeypatch.context() as m:
            if reference:
                m.setattr(matching, "games_a_new_line_can_change", _reference_selection)
                m.setattr(matching, "write_results", oracle.write_results_per_row)
            try:
                with db.transaction():
                    made = lines.add_manual(db, moves=moves, line_name="new", window=window, chapter_id=chapter_id)
                    out.append(_tables(db, int(made["id"])))
                    if not reference:
                        counts = made["counts"]
                    raise psycopg.Rollback
            except psycopg.Rollback:
                pass
    return out[0], out[1], counts


@pytest.mark.parametrize(
    ("moves", "chapter_id", "candidates", "rematched"),
    [
        # Candidates are the games holding the line's first move of its colour, and pending games
        # (no row, no flag) of the other: Black 1.e4 games 11, 12, 13, 14 and 17; Black 1.c4 game
        # 15; White 1.e4 games 21, 22, 23 with pending Black game 17.
        (NAJDORF + ["Be2", "e5"], 902, 5, 4),  # 11 beaten (12 > 10), 12 tied (4), 14 (2), 17 (no row)
        (["e4", "c5", "Nf3", "d6", "Bb5+", "Bd7"], 902, 5, 3),  # 12 beaten (6 > 4), 14, 17
        (["e4", "c5", "Nf3", "d6", "Bb5+", "Nd7"], 902, 5, 3),  # 12 beaten by a line it leaves (5 > 4)
        (["e4", "c5", "Nf3", "d6", "c3", "Nf6"], 902, 5, 3),  # 12 tied at its stored ply (4 = 4)
        (["e4", "c5", "Nc3", "Nc6", "Nf3", "g6"], 902, 5, 2),  # 14 beaten (4 > 2), 17; short of 11-13
        (["c4", "e5", "Nc3", "Nf6"], 902, 1, 1),  # the no-match game becomes a match
        (["e4", "c5", "Nf3", "d6", "d4"], 901, 4, 2),  # White through Black's Sicilian: 22, pending 17
    ],
)
def test_a_new_line_rematches_only_games_it_can_change_and_the_tables_match_a_full_rematch(
    db: psycopg.Connection[DictRow],
    monkeypatch: pytest.MonkeyPatch,
    moves: list[str],
    chapter_id: int,
    candidates: int,
    rematched: int,
) -> None:
    _rematch_fixture(db)
    new, reference, counts = _add_both_ways(db, monkeypatch, moves, chapter_id)
    assert new == reference
    assert (counts["candidates"], counts["rematched"]) == (candidates, rematched)


def test_a_game_flagged_before_lines_it_now_matches_is_rematched_whatever_the_new_lines_colour(
    db: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Black game flagged no-match, then a Black line it matches added while the game was
    outside the window (so the flag is stale), then a White line through the same first move:
    the save rematches the Black game as a full rematch would, because a flag can predate lines
    the game never met."""
    h.book(db, 902, "Black", "black")
    h.chapter(db, 902, 902, "Lines")
    h.line(db, 902, 902, "QGD", QGD)
    h.game(db, 31, ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"], color="black", days_ago=60)  # no Black 1.e4 line
    h.game(db, 32, ITALIAN + ["Nf6"], color="white", days_ago=1)
    matching.match_player(db, 100)
    flagged = db.execute("SELECT no_repertoire_match FROM player_games WHERE chess_game_id = 31").fetchone()
    assert flagged == {"no_repertoire_match": True}
    lines.add_manual(db, moves=["e4", "e5", "Nf3", "Nc6"], line_name="Open", window=1, chapter_id=902)
    new, reference, counts = _add_both_ways(db, monkeypatch, ITALIAN + ["Nf6", "d4"], 901)
    assert new == reference
    assert any(row[0] == 31 for row in new[0])  # the stale flag is healed: game 31 now has a result
    assert (counts["candidates"], counts["rematched"]) == (2, 2)  # White's game 32, Black's flagged 31


def test_a_narrow_window_leaves_the_games_outside_it_as_a_full_rematch_does(
    db: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    _rematch_fixture(db)
    new, reference, counts = _add_both_ways(db, monkeypatch, NAJDORF + ["Be2", "e5"], 902, window=4)
    assert new == reference
    assert counts["candidates"] < 5


def test_the_gate_index_of_one_colour_is_that_colours_part_of_the_whole(db: psycopg.Connection[DictRow]) -> None:
    """A key is a full FEN whose side to move is the book's colour, so reading one colour's books
    gives exactly the entries of the whole index for that side, and the same disagreements."""
    _rematch_fixture(db)
    h.line(db, 905, 902, "Rival", ["e4", "c5", "Nf3", "Nc6"])  # disagrees with the Najdorf at Nf3
    whole, whole_dirty = importing.existing_index(db)
    for colour, side in (("white", "w"), ("black", "b")):
        part, dirty = importing.existing_index(db, colour=colour)
        assert part == {k: v for k, v in whole.items() if k.split(" ")[1] == side} and part
        assert dirty == {k for k in whole_dirty if k.split(" ")[1] == side}
    assert importing.existing_index(db, colour="black")[1]  # the disagreement is Black's


# --- the catch-all ----------------------------------------------------------------------


def test_the_catch_all_book_and_family_chapter_are_created_once_per_colour_and_reused(
    db: psycopg.Connection[DictRow],
) -> None:
    a = lines.add_manual(db, moves=SCANDI_GAP, line_name="a", window=0, catch_all=("white", "Scandinavian"))
    b = lines.add_manual(
        db, moves=SCANDI_GAP[:-2] + ["Qd8", "d4"], line_name="b", window=0, catch_all=("white", "Scandinavian")
    )
    c = lines.add_manual(db, moves=["e4", "c5", "Nf3", "d6", "d4"], line_name="c", window=0, catch_all=("white", None))
    d = lines.add_manual(
        db, moves=["d4", "d5", "c4", "e6"], line_name="d", window=0, catch_all=("black", "Queen's Gambit")
    )
    books = db.execute(
        "SELECT id, title, color, source_book_id, active FROM books WHERE title = %s ORDER BY id", (CATCH_ALL_TITLE,)
    ).fetchall()
    assert [(bk["color"], bk["source_book_id"], bk["active"]) for bk in books] == [
        ("white", None, True),
        ("black", None, True),
    ]
    assert a["book_id"] == b["book_id"] == c["book_id"] == books[0]["id"] and d["book_id"] == books[1]["id"]
    assert a["chapter_id"] == b["chapter_id"] != c["chapter_id"]
    titles = db.execute("SELECT title FROM chapters WHERE book_id = %s ORDER BY id", (a["book_id"],)).fetchall()
    assert [t["title"] for t in titles] == ["Scandinavian", lines.CATCH_ALL_FAMILY_FALLBACK]


def test_a_switched_off_catch_all_stays_off(db: psycopg.Connection[DictRow]) -> None:
    a = lines.add_manual(db, moves=SCANDI_GAP, line_name="a", window=0, catch_all=("white", "Scandinavian"))
    db.execute("UPDATE chapters SET active = FALSE WHERE id = %s", (a["chapter_id"],))
    with pytest.raises(lines.NoChapter, match="switched off"):
        lines.add_manual(
            db, moves=SCANDI_GAP[:-2] + ["Qd8", "d4"], line_name="b", window=0, catch_all=("white", "Scandinavian")
        )
    db.execute("UPDATE chapters SET active = TRUE WHERE id = %s", (a["chapter_id"],))
    db.execute("UPDATE books SET active = FALSE WHERE id = %s", (a["book_id"],))
    with pytest.raises(lines.NoChapter, match="switched off"):
        lines.add_manual(
            db, moves=SCANDI_GAP[:-2] + ["Qd8", "d4"], line_name="b", window=0, catch_all=("white", "Scandinavian")
        )
    flags = db.execute(
        "SELECT bk.active AS b, ch.active AS c FROM chapters ch JOIN books bk ON bk.id = ch.book_id WHERE ch.id = %s",
        (a["chapter_id"],),
    ).fetchone()
    assert flags is not None and (flags["b"], flags["c"]) == (False, True)


def test_a_destination_is_one_thing(db: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(lines.InvalidLine, match="one destination"):
        lines.add_manual(db, moves=SCANDI_GAP, line_name="x", window=0)
    with pytest.raises(lines.InvalidLine, match="one destination"):
        lines.add_manual(db, moves=SCANDI_GAP, line_name="x", window=0, chapter_id=901, catch_all=("white", None))


# --- the import never touches a manual line ----------------------------------------------


def _import(conn: psycopg.Connection[DictRow], tmp_path: Path, doc: dict[str, Any]) -> dict[str, Any]:
    p = tmp_path / "rep.json"
    p.write_text(json.dumps(doc))
    return importing.import_file(conn, p, mode="scratch", window=100)


def test_a_complete_replacement_keeps_a_manual_line_and_the_chapter_that_holds_it(
    clean: psycopg.Connection[DictRow], tmp_path: Path
) -> None:
    h.player(clean)
    doc = json.loads(FIXTURE.read_text())
    _import(clean, tmp_path, doc)
    chapter_id = int(clean.execute("SELECT id FROM chapters WHERE title LIKE '%%Giuoco Piano'").fetchone()["id"])  # type: ignore[index]
    manual = lines.add_manual(clean, moves=SCANDI_GAP, line_name="Scandi: 4.d4", window=0, chapter_id=chapter_id)
    # Its puzzle, with SRS state, as the generator would leave it.
    clean.execute(
        "INSERT INTO puzzles (id, player_id, fen, solution_line, color, is_repertoire, repertoire_line_id, source_types)"
        " VALUES (1, %s, %s, %s::jsonb, 'w', TRUE, %s, ARRAY['deviation'])",
        (PLAYER_ID, h.START, json.dumps(SCANDI_GAP), manual["id"]),
    )
    clean.execute("INSERT INTO player_puzzle_state (player_id, puzzle_id) VALUES (%s, 1)", (PLAYER_ID,))

    # The file drops the whole Giuoco Piano chapter: its course lines go off, the chapter and
    # the manual line stay on, and so does the puzzle through the generator.
    del doc["books"][0]["chapters"][0]
    out = _import(clean, tmp_path, doc)
    assert out["lines"]["vanished_deactivated"] == 2 and out["chapters"]["deactivated"] == 0
    rows = clean.execute(
        "SELECT rl.line_name, rl.active, rl.origin, ch.active AS ch_active FROM repertoire_lines rl"
        " JOIN chapters ch ON ch.id = rl.chapter_id WHERE ch.id = %s ORDER BY rl.id",
        (chapter_id,),
    ).fetchall()
    assert [(r["line_name"], r["active"], r["origin"], r["ch_active"]) for r in rows] == [
        ("Main", False, "course", True),
        ("Two Knights", False, "course", True),
        ("Scandi: 4.d4", True, "manual", True),
    ]
    assert read.coverage(clean, importing.spine(None, SCANDI_GAP[:-1])[-1])["status"] in ("covered", "match")
    config = Settings(deviation_puzzle_min_occurrences=1)
    gen.generate(clean, config)
    pz = clean.execute("SELECT active, drilled_at FROM puzzles WHERE id = 1").fetchone()
    assert pz is not None and pz["active"] is False  # below the deviation gate and never drilled: off, as before
    assert clean.execute("SELECT count(*) AS n FROM player_puzzle_state").fetchone()["n"] == 1  # type: ignore[index]

    # The manual line's chapter is not in the file, so a replacement that keeps the file's
    # other chapter still retires nothing of hers; and a course line arriving later with the
    # same moves is found, keeps `manual`, and gets the file's id.
    doc2 = json.loads(FIXTURE.read_text())
    doc2["books"][0]["chapters"][0]["lines"].append(
        {"name": "Scandi from the course", "moves": SCANDI_GAP, "source_line_id": "s1"}
    )
    out = _import(clean, tmp_path, doc2)
    assert out["lines"]["inserted"] == 0 and out["lines"]["existing"] >= 3
    row = clean.execute(
        "SELECT origin, source_line_id, line_name FROM repertoire_lines WHERE id = %s", (manual["id"],)
    ).fetchone()
    assert row is not None and (row["origin"], row["source_line_id"], row["line_name"]) == (
        "manual",
        "s1",
        "Scandi: 4.d4",
    )


# --- delete -----------------------------------------------------------------------------


def test_delete_takes_the_line_its_puzzle_and_history_and_the_results_citing_it(
    db: psycopg.Connection[DictRow],
) -> None:
    h.game(db, 10, ITALIAN + ["d6"], color="white")
    h.game(db, 11, ["d4", "d5"], color="white")
    from core.repertoire import matching

    matching.rematch_window(db, 100)
    db.execute(
        "INSERT INTO puzzles (id, player_id, fen, solution_line, color, is_repertoire, repertoire_line_id, source_types)"
        " VALUES (1, %s, %s, %s::jsonb, 'w', TRUE, 901, ARRAY['deviation'])",
        (PLAYER_ID, h.START, json.dumps(ITALIAN)),
    )
    db.execute("INSERT INTO player_puzzle_state (player_id, puzzle_id) VALUES (%s, 1)", (PLAYER_ID,))
    for sid, solved in (
        ("11111111-1111-1111-1111-111111111111", False),
        ("22222222-2222-2222-2222-222222222222", True),
    ):
        db.execute(
            "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, session_id, attempt_seq) VALUES (1, %s, %s, %s, 1)",
            (PLAYER_ID, solved, sid),
        )
    assert db.execute("SELECT count(*) AS n FROM game_result_lines").fetchone()["n"] == 1  # type: ignore[index]
    out = lines.delete(db, 901)
    assert out == {"loads": 2, "rematch": {"candidates": 1, "matched": 0, "no_match": 1, "lines": 0}}
    for table in ("repertoire_lines", "puzzles", "player_puzzle_state", "puzzle_attempts", "game_result_lines"):
        assert db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0, table  # type: ignore[index]
    assert db.execute("SELECT count(*) AS n FROM chapters").fetchone()["n"] == 1  # type: ignore[index]
    assert lines.delete(db, 901) is None
    nm = db.execute("SELECT no_repertoire_match FROM player_games WHERE chess_game_id = 10").fetchone()
    assert nm is not None and nm["no_repertoire_match"] is True


def test_a_delete_waits_for_a_transaction_holding_the_repertoire_lock(fresh_db_url: str) -> None:
    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as a:
        h.player(a)
        h.book(a, 1, "B", "white")
        h.chapter(a, 1, 1, "C")
        h.line(a, 1, 1, "L", ITALIAN)
        a.commit()
        from core.repertoire import matching

        matching.lock(a)  # a matcher mid-read
        done = threading.Event()

        def deleter() -> None:
            with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as b:
                lines.delete(b, 1)
                b.commit()
            done.set()

        t = threading.Thread(target=deleter)
        t.start()
        assert not done.wait(1.0)  # blocked behind the lock
        a.commit()
        assert done.wait(10.0)
        t.join()
        assert a.execute("SELECT count(*) AS n FROM repertoire_lines").fetchone()["n"] == 0  # type: ignore[index]
        a.execute("DELETE FROM chapters")
        a.execute("DELETE FROM books")
        a.execute("DELETE FROM players")
        a.commit()


# --- stats ------------------------------------------------------------------------------


def _attempt(
    conn: psycopg.Connection[DictRow], puzzle: int, *, solved: bool, session: str | None, seq: int | None
) -> None:
    conn.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, session_id, attempt_seq) VALUES (%s, %s, %s, %s, %s)",
        (puzzle, PLAYER_ID, solved, session, seq),
    )


def test_loads_and_first_attempts_follow_the_solvers_order_not_the_servers(db: psycopg.Connection[DictRow]) -> None:
    db.execute(
        "INSERT INTO puzzles (id, player_id, fen, solution_line, color, is_repertoire, repertoire_line_id, source_types)"
        " VALUES (1, %s, %s, %s::jsonb, 'w', TRUE, 901, ARRAY['deviation'])",
        (PLAYER_ID, h.START, json.dumps(ITALIAN)),
    )
    s1, s2, s3 = (
        f"{d}" * 8 + "-" + f"{d}" * 4 + "-" + f"{d}" * 4 + "-" + f"{d}" * 4 + "-" + f"{d}" * 12 for d in "123"
    )
    # Load 1: the right attempt (seq 2) reached the server before the wrong one (seq 1).
    _attempt(db, 1, solved=True, session=s1, seq=2)
    _attempt(db, 1, solved=False, session=s1, seq=1)
    # Load 2: wrong, then right, then a Replay: one load, first wrong.
    _attempt(db, 1, solved=False, session=s2, seq=1)
    _attempt(db, 1, solved=True, session=s2, seq=2)
    _attempt(db, 1, solved=True, session=s2, seq=3)
    # Load 3: an older client's rows, unnumbered: id order.
    _attempt(db, 1, solved=True, session=s3, seq=None)
    _attempt(db, 1, solved=False, session=s3, seq=None)
    # Two rows from before sessions existed: a load each.
    _attempt(db, 1, solved=True, session=None, seq=None)
    _attempt(db, 1, solved=False, session=None, seq=None)
    assert stats.by_line(db, [901]) == {901: {"loads": 5, "first_right": 2, "first_wrong": 3}}
    assert stats.by_line(db, [2]) == {} and stats.by_line(db, []) == {}


# --- migration 014 over a populated database ------------------------------------------------


def test_migration_014_leaves_every_line_course_every_puzzle_undrilled_and_every_attempt_unnumbered(
    fresh_db_url: str, tmp_path: Path
) -> None:
    url = _scratch(fresh_db_url, "v13")
    v13 = tmp_path / "v13"
    v13.mkdir()
    for n, path in schema.migration_files():
        if n <= 13:
            (v13 / path.name).write_text(path.read_text())
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row) as c:
        c.execute(sql.SQL((FIXTURES / "schema_baseline.sql").read_text()))  # type: ignore[arg-type]  # repo fixture
        c.execute("INSERT INTO schema_version (version) VALUES (0)")
        assert schema.upgrade(c, v13)[-1] == 13
        h.player(c)
        h.book(c, 1, "B", "white")
        h.chapter(c, 1, 1, "C")
        h.line(c, 1, 1, "L", ITALIAN)
        c.execute(
            "INSERT INTO puzzles (id, player_id, fen, solution_line, color, is_repertoire, repertoire_line_id, source_types)"
            " VALUES (1, %s, %s, %s::jsonb, 'w', TRUE, 1, ARRAY['deviation'])",
            (PLAYER_ID, h.START, json.dumps(ITALIAN)),
        )
        c.execute("INSERT INTO puzzle_attempts (puzzle_id, player_id, solved) VALUES (1, %s, TRUE)", (PLAYER_ID,))
        c.commit()
        assert 14 in schema.upgrade(c)
        assert c.execute("SELECT origin FROM repertoire_lines").fetchone()["origin"] == "course"  # type: ignore[index]
        assert c.execute("SELECT drilled_at FROM puzzles").fetchone()["drilled_at"] is None  # type: ignore[index]
        assert c.execute("SELECT attempt_seq FROM puzzle_attempts").fetchone()["attempt_seq"] is None  # type: ignore[index]
        assert stats.by_line(c, [1]) == {1: {"loads": 1, "first_right": 1, "first_wrong": 0}}
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE repertoire_lines SET origin = 'other'")
        c.rollback()
