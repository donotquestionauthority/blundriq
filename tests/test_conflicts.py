"""Where the repertoire disagrees with itself: the listing, the duplicates, the count, and
the gate on the Repertoire page's toggles."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import DictRow

from core.repertoire import books, conflicts, importing, matching
from tests import repertoire_helpers as h

AFTER_E5 = h.spine(None, ["e4", "e5"])[2]
AFTER_NC6 = h.spine(None, ["e4", "e5", "Nf3", "Nc6"])[4]


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    h.player(clean)
    return clean


def _fens(rows: list[dict[str, object]]) -> list[str]:
    return [str(p["fen"]) for p in rows]


def _line_flag(db: psycopg.Connection[DictRow], line_id: int) -> bool:
    row = db.execute("SELECT active FROM repertoire_lines WHERE id = %s", (line_id,)).fetchone()
    assert row is not None
    return bool(row["active"])


# --- the relation and the listing ----------------------------------------------------------


def test_the_relation_agrees_with_the_import_gates_index(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "W", "white")
    h.book(db, 2, "B", "black")
    h.chapter(db, 1, 1, "C1")
    h.chapter(db, 2, 2, "C2")
    h.chapter(db, 3, 1, "off", active=False)
    h.line(db, 1, 1, "a", ["e4", "e5", "Nf3", "Nc6", "Bc4"])
    h.line(db, 2, 1, "b", ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    h.line(db, 3, 1, "latent", ["e4", "c5"], active=False)
    h.line(db, 4, 2, "black", ["e4", "e5", "Nf3", "Nf6"])
    h.line(db, 5, 3, "under an off chapter", ["d4", "d5"])
    h.line(db, 6, 1, "short spine", ["e4", "e5", "Nf3"], fens=h.spine(None, ["e4", "e5"]))
    index, dirty = importing.existing_index(db)
    rows = db.execute(conflicts.REP_SIGNATURES, {"pid": 1}).fetchall()
    from_relation: dict[str, set[str]] = {}
    for r in rows:
        if r["effective"]:
            from_relation.setdefault(str(r["fen"]), set()).add(str(r["move"]))
    assert from_relation == index
    assert dirty == {AFTER_NC6}
    assert {int(r["line_id"]) for r in rows} == {1, 2, 3, 4, 5}  # the short spine contributes nothing
    assert all(str(r["fen"]).split(" ")[1] == "b" for r in rows if r["book_id"] == 2)


def test_listing_marks_contested_only_for_effective_disagreement(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "Italian", "white")
    h.chapter(db, 1, 1, "Giuoco")
    h.chapter(db, 2, 1, "Intro")
    h.line(db, 1, 1, "Bc4", ["e4", "e5", "Nf3", "Nc6", "Bc4"])
    h.line(db, 2, 2, "Bb5", ["e4", "e5", "Nf3", "Nc6", "Bb5"], active=False)
    h.line(db, 3, 2, "Bc4 again", ["e4", "e5", "Nf3", "Nc6", "Bc4"])  # a duplicate, not a conflict
    rows = conflicts.listing(db)
    assert _fens(rows) == [AFTER_NC6]
    pos = rows[0]
    assert pos["contested"] is False and pos["active_moves"] == 1 and pos["color"] == "white"
    assert [g["move"] for g in pos["moves"]] == ["Bc4", "Bb5"]  # effective moves first
    assert [ln["line_id"] for ln in pos["moves"][0]["lines"]] == [1, 3]
    assert pos["moves"][1]["lines"][0] == {
        "line_id": 2,
        "line_name": "Bb5",
        "chapter_id": 2,
        "chapter_title": "Intro",
        "book_id": 1,
        "book_title": "Italian",
        "move": "Bb5",
        "line_active": False,
        "chapter_active": True,
        "book_active": True,
        "effective": False,
    }
    assert conflicts.contested_count(db) == 0
    db.execute("UPDATE repertoire_lines SET active = TRUE WHERE id = 2")
    rows = conflicts.listing(db)
    assert rows[0]["contested"] is True and rows[0]["active_moves"] == 2
    assert conflicts.contested_count(db) == 1
    # A chapter off makes its lines ineffective, and the position is resolved again.
    db.execute("UPDATE chapters SET active = FALSE WHERE id = 2")
    rows = conflicts.listing(db)
    assert rows[0]["contested"] is False
    off = rows[0]["moves"][1]["lines"][0]
    assert off["line_active"] is True and off["chapter_active"] is False and off["effective"] is False
    assert conflicts.contested_count(db) == 0


def test_listing_orders_contested_first_then_by_lines_then_by_fen(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "B", "white")
    h.chapter(db, 1, 1, "C")
    # Four lines disagree after 1.e4 e5 2.Nf3 Nc6 (three of them off: resolved); two disagree
    # after 1.e4 c5 (both on: contested); two disagree after 1.e4 e5 (one off: resolved).
    h.line(db, 1, 1, "a", ["e4", "e5", "Nf3", "Nc6", "Bc4"])
    h.line(db, 2, 1, "b", ["e4", "e5", "Nf3", "Nc6", "Bb5"], active=False)
    h.line(db, 3, 1, "c", ["e4", "e5", "Nf3", "Nc6", "d4"], active=False)
    h.line(db, 4, 1, "d", ["e4", "e5", "Nf3", "Nc6", "Nc3"], active=False)
    h.line(db, 5, 1, "e", ["e4", "c5", "Nf3"])
    h.line(db, 6, 1, "f", ["e4", "c5", "Nc3"])
    h.line(db, 7, 1, "g", ["e4", "e5", "Bc4"], active=False)
    after_c5 = h.spine(None, ["e4", "c5"])[2]
    rows = conflicts.listing(db)
    # Contested first although it carries fewer lines; then 5 lines before 2; the two-line
    # positions (after c5 has 2, after e5 has 5 with a..d, g) — after e5 outranks by lines.
    assert _fens(rows) == [after_c5, AFTER_E5, AFTER_NC6]
    assert [p["contested"] for p in rows] == [True, False, False]
    assert [sum(len(g["lines"]) for g in p["moves"]) for p in rows] == [2, 5, 4]
    # Equal size, neither contested: by FEN string.
    db.execute("UPDATE repertoire_lines SET active = FALSE WHERE id = 6")
    h.line(db, 8, 1, "h", ["e4", "e6", "d4"])
    h.line(db, 9, 1, "i", ["e4", "e6", "Nf3"], active=False)
    rows = conflicts.listing(db)
    two = [p["fen"] for p in rows if sum(len(g["lines"]) for g in p["moves"]) == 2]
    assert len(two) == 2 and two == sorted(two) and after_c5 in two


def test_a_short_spine_and_a_single_move_are_never_listed(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "B", "white")
    h.chapter(db, 1, 1, "C")
    h.line(db, 1, 1, "a", ["e4", "e5", "Nf3"])
    h.line(db, 2, 1, "b", ["e4", "e5", "Bc4"], fens=h.spine(None, ["e4", "e5"]))  # spine too short
    assert conflicts.listing(db) == []
    assert conflicts.contested_count(db) == 0


def test_duplicates_are_identical_lines_in_different_chapters(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "W", "white")
    h.book(db, 2, "B", "black")
    h.chapter(db, 1, 1, "Intro")
    h.chapter(db, 2, 1, "Main", active=False)
    h.chapter(db, 3, 2, "Black")
    h.line(db, 1, 1, "x", ["e4", "e5", "Nf3"])
    h.line(db, 2, 2, "x again", ["e4", "e5", "Nf3"])
    h.line(db, 5, 3, "other colour", ["e4", "e5", "Nf3"])
    h.line(db, 6, 1, "y", ["e4", "c5"], active=False)
    h.line(db, 7, 2, "y again", ["e4", "c5"], active=False)
    groups = conflicts.duplicates(db)
    assert [g["moves"] for g in groups] == [["e4", "e5", "Nf3"], ["e4", "c5"]]  # effective lines first
    assert groups[0]["color"] == "white"
    assert [(ln["line_id"], ln["effective"]) for ln in groups[0]["lines"]] == [(1, True), (2, False)]
    assert groups[0]["lines"][1]["chapter_active"] is False and "move" not in groups[0]["lines"][1]
    assert conflicts.listing(db) == []  # identical lines never disagree


# --- the gate -----------------------------------------------------------------------------


def _italian(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "Italian", "white")
    h.chapter(db, 1, 1, "Giuoco")
    h.chapter(db, 2, 1, "Intro")
    h.line(db, 1, 1, "Main", ["e4", "e5", "Nf3", "Nc6", "Bc4"])


def test_a_line_is_refused_for_the_anchor_it_disagrees_with(db: psycopg.Connection[DictRow]) -> None:
    _italian(db)
    h.line(db, 2, 2, "Spanish", ["e4", "e5", "Nf3", "Nc6", "Bb5"], active=False)
    h.game(db, 1, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"], days_ago=1)
    matching.match_player(db, 100)
    refusals = conflicts.gate(db, "lines", 2)
    assert len(refusals) == 1
    r = refusals[0]
    assert (r.line_id, r.line_name, r.chapter_title, r.fen, r.move, r.reason) == (
        2,
        "Spanish",
        "Intro",
        AFTER_NC6,
        "Bb5",
        "anchor",
    )
    assert r.rivals == [
        {
            "line_id": 1,
            "line_name": "Main",
            "chapter_id": 1,
            "chapter_title": "Giuoco",
            "book_id": 1,
            "book_title": "Italian",
            "move": "Bc4",
        }
    ]
    with pytest.raises(books.Refused) as exc:
        books.set_active(db, "lines", 2, True, 100)
    assert exc.value.refusal == r
    # Nothing was written: the flag, the result, its line rows.
    assert _line_flag(db, 2) is False
    assert db.execute("SELECT count(*) AS n FROM game_repertoire_results").fetchone()["n"] == 1  # type: ignore[index]
    assert db.execute("SELECT count(*) AS n FROM game_result_lines WHERE line_id = 1").fetchone()["n"] == 1  # type: ignore[index]


def test_a_line_is_refused_where_the_active_repertoire_already_disagrees(db: psycopg.Connection[DictRow]) -> None:
    _italian(db)
    h.line(db, 2, 2, "Spanish", ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    h.line(db, 3, 2, "Scotch", ["e4", "e5", "Nf3", "Nc6", "d4"], active=False)
    (r,) = conflicts.gate(db, "lines", 3)
    assert r.reason == "dirty" and r.fen == AFTER_NC6 and r.move == "d4"
    assert [(x["line_id"], x["move"]) for x in r.rivals] == [(2, "Bb5"), (1, "Bc4")]
    # Even a line that agrees with one of them is refused there: the position is dirty.
    h.line(db, 4, 2, "Bc4 too", ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"], active=False)
    (r,) = conflicts.gate(db, "lines", 4)
    assert r.reason == "dirty" and [x["line_id"] for x in r.rivals] == [2]


def test_a_latent_flip_is_not_gated(db: psycopg.Connection[DictRow]) -> None:
    _italian(db)
    db.execute("UPDATE chapters SET active = FALSE WHERE id = 2")
    h.line(db, 2, 2, "Spanish", ["e4", "e5", "Nf3", "Nc6", "Bb5"], active=False)
    out = books.set_active(db, "lines", 2, True, 100)
    assert out == {"rematch": {"candidates": 0, "matched": 0, "no_match": 0, "lines": 0}, "held_back": []}
    assert _line_flag(db, 2) is True
    # ...and the gate runs when the chapter comes on: the line is held back.
    out = books.set_active(db, "chapters", 2, True, 100)
    assert out is not None and [r["line_id"] for r in out["held_back"]] == [2]
    assert out["held_back"][0]["reason"] == "anchor" and out["held_back"][0]["rivals"][0]["line_id"] == 1
    assert _line_flag(db, 2) is False
    assert db.execute("SELECT active FROM chapters WHERE id = 2").fetchone()["active"] is True  # type: ignore[index]


def test_a_chapter_coming_on_resolves_its_own_disagreement_by_plurality(db: psycopg.Connection[DictRow]) -> None:
    """A toggle runs the import's rule, plurality included. Three lines of an off
    chapter disagree at a position no active line covers; the winner comes on, the losers are
    switched off and reported as `cohort` with the winner's lines as rivals."""
    h.book(db, 1, "B", "white")
    h.chapter(db, 1, 1, "Sicilian", active=False)
    h.line(db, 1, 1, "Open", ["e4", "c5", "Nf3", "d6", "d4"])
    h.line(db, 2, 1, "Open again", ["e4", "c5", "Nf3", "Nc6", "d4"])
    h.line(db, 3, 1, "Closed", ["e4", "c5", "Nc3"])
    h.line(db, 4, 1, "Alapin", ["e4", "c5", "c3"])
    out = books.set_active(db, "chapters", 1, True, 100)
    assert out is not None
    held = {r["line_id"]: r for r in out["held_back"]}
    assert set(held) == {3, 4}
    after_c5 = h.spine(None, ["e4", "c5"])[2]
    assert held[3]["reason"] == "cohort" and held[3]["fen"] == after_c5 and held[3]["move"] == "Nc3"
    assert [(x["line_id"], x["move"]) for x in held[3]["rivals"]] == [(1, "Nf3"), (2, "Nf3")]
    assert [_line_flag(db, i) for i in (1, 2, 3, 4)] == [True, True, False, False]
    assert conflicts.contested_count(db) == 0


def test_a_chapter_coming_on_holds_back_the_line_an_anchor_refuses(db: psycopg.Connection[DictRow]) -> None:
    _italian(db)
    db.execute("UPDATE chapters SET active = FALSE WHERE id = 2")
    h.line(db, 2, 2, "Spanish", ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    h.line(db, 3, 2, "Petroff", ["e4", "e5", "Nf3", "Nf6", "Nxe5"])
    h.game(db, 1, ["e4", "e5", "Nf3", "Nf6", "Nxe5", "d6"], days_ago=1)
    h.game(db, 2, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"], days_ago=2)
    matching.match_player(db, 100)
    out = books.set_active(db, "chapters", 2, True, 100)
    assert out is not None and [r["line_id"] for r in out["held_back"]] == [2]
    assert out["held_back"][0]["reason"] == "anchor"
    # The games the accepted line can touch (both open with e4) were matched again against the
    # repertoire as it now stands: the Petroff game follows its line, the Spanish game still deviates.
    assert out["rematch"]["candidates"] == 2
    rows = db.execute("SELECT chess_game_id, deviation_by FROM game_repertoire_results ORDER BY 1").fetchall()
    assert [(r["chess_game_id"], r["deviation_by"]) for r in rows] == [(1, "none"), (2, "me")]


def test_only_the_accepted_lines_games_are_matched_again(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "B", "black")
    h.chapter(db, 1, 1, "Sicilian")
    h.chapter(db, 2, 1, "Off", active=False)
    h.line(db, 1, 1, "Sicilian", ["e4", "c5"])
    h.line(db, 2, 2, "Held", ["e4", "e5"])  # the anchor refuses it
    h.line(db, 3, 2, "Accepted", ["d4", "d5"])
    h.game(db, 1, ["e4", "e5", "Nf3"], color="black", days_ago=1)
    h.game(db, 2, ["d4", "d5", "c4"], color="black", days_ago=2)
    matching.match_player(db, 100)
    out = books.set_active(db, "chapters", 2, True, 100)
    assert out is not None and [r["line_id"] for r in out["held_back"]] == [2]
    assert out["rematch"]["candidates"] == 1  # the d4 game; the e4 game cites nothing that came on


def test_a_cohort_refusal_names_only_the_winners_that_came_on(db: psycopg.Connection[DictRow]) -> None:
    """A candidate refused at another position still votes (the import's rule), but it does
    not hold the position, so it is not named as a rival."""
    h.book(db, 1, "B", "white")
    h.chapter(db, 1, 1, "Catalan")
    h.chapter(db, 2, 1, "Off", active=False)
    h.line(db, 1, 1, "Catalan", ["d4", "e6", "c4", "d5", "g3"])
    h.line(db, 2, 2, "A", ["d4", "d5", "c4", "e6", "Nc3"])  # refused: g3 anchors the transposition
    h.line(db, 3, 2, "B", ["d4", "d5", "c4", "e6", "Nf3"])  # refused likewise
    h.line(db, 4, 2, "C", ["d4", "d5", "Nf3"])  # loses the vote after d5 to A and B's c4
    h.line(db, 5, 2, "D", ["d4", "d5", "c4", "dxc4"])  # plays the winner and comes on
    out = books.set_active(db, "chapters", 2, True, 100)
    assert out is not None
    held = {r["line_id"]: r for r in out["held_back"]}
    assert set(held) == {2, 3, 4}
    assert held[4]["reason"] == "cohort" and [x["line_id"] for x in held[4]["rivals"]] == [5]


def test_a_book_gates_only_the_lines_its_active_chapters_bring(db: psycopg.Connection[DictRow]) -> None:
    """The inactive chapter's lines are not candidates: were they, their d4 lines would vote at
    the start position and hold back the active chapter's e4 line."""
    h.book(db, 1, "B", "white", active=False)
    h.chapter(db, 1, 1, "On")
    h.chapter(db, 2, 1, "Off", active=False)
    h.line(db, 1, 1, "e4", ["e4", "e5"])
    h.line(db, 2, 2, "d4 d5", ["d4", "d5"])
    h.line(db, 3, 2, "d4 Nf6", ["d4", "Nf6"])
    out = books.set_active(db, "books", 1, True, 100)
    assert out is not None and out["held_back"] == []
    assert [_line_flag(db, i) for i in (1, 2, 3)] == [True, True, True]
    assert conflicts.contested_count(db) == 0


def test_a_target_already_on_is_a_no_op_even_over_a_dirty_repertoire(db: psycopg.Connection[DictRow]) -> None:
    """Re-gating an active container would put its own effective lines through the cohort
    rule, switch the losers off and leave every result citing them stale."""
    h.book(db, 1, "B", "white")
    h.chapter(db, 1, 1, "C")
    h.line(db, 1, 1, "e4", ["e4", "e5"])
    h.line(db, 2, 1, "d4", ["d4", "d5"])
    h.game(db, 1, ["d4", "d5", "c4"], days_ago=1)
    matching.match_player(db, 100)
    before = db.execute("SELECT * FROM game_repertoire_results").fetchall()
    assert len(before) == 1 and conflicts.contested_count(db) == 1
    zeros = {"candidates": 0, "matched": 0, "no_match": 0, "lines": 0}
    for _ in range(2):
        for kind, id in (("lines", 2), ("chapters", 1), ("books", 1)):
            assert books.set_active(db, kind, id, True, 100) == {"rematch": zeros, "held_back": []}  # type: ignore[arg-type]
    assert [_line_flag(db, i) for i in (1, 2)] == [True, True]
    assert db.execute("SELECT * FROM game_repertoire_results").fetchall() == before
    assert db.execute("SELECT count(*) AS n FROM game_result_lines WHERE line_id = 2").fetchone()["n"] == 1  # type: ignore[index]
    assert books.set_active(db, "lines", 9, True, 100) is None


def test_an_empty_container_comes_on_with_nothing_held_back(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 1, "B", "white", active=False)
    h.chapter(db, 1, 1, "C")
    out = books.set_active(db, "books", 1, True, 100)
    assert out is not None and out["held_back"] == []
    assert books.books(db)[0]["active"] is True


def test_the_gate_holds_the_lock(fresh_db_url: str) -> None:
    """Two connections: a toggle that will be refused waits for the matcher's lock, so it
    judges the lines as they are once the matcher has published."""
    import threading

    from psycopg.rows import dict_row

    from tests.conftest import reset_game_data

    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as setup:
        reset_game_data(setup)
        h.player(setup)
        _italian(setup)
        h.line(setup, 2, 2, "Spanish", ["e4", "e5", "Nf3", "Nc6", "Bb5"], active=False)
        setup.commit()
    outcome: list[str] = []
    try:
        with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as matcher:
            with matcher.transaction():
                matching.lock(matcher)
                done = threading.Event()

                def toggle() -> None:
                    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as c:
                        try:
                            books.set_active(c, "lines", 2, True, 100)
                            outcome.append("on")
                        except books.Refused as exc:
                            outcome.append(exc.refusal.reason)
                    done.set()

                t = threading.Thread(target=toggle)
                t.start()
                assert not done.wait(0.5)  # the toggle is waiting on the lock
                matcher.execute("UPDATE repertoire_lines SET active = FALSE WHERE id = 1")  # the anchor goes
            t.join(5)
            assert done.is_set()
        assert outcome == ["on"]  # judged after the matcher's transaction, not before it
    finally:
        with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as setup:
            reset_game_data(setup)
