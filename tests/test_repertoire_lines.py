"""`read.rep_lines`: which lines pass through a board, by colour, with the games' record there."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import DictRow

from core.repertoire import read
from tests import repertoire_helpers as h

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]
SCANDI = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5"]


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    h.player(clean)
    h.book(clean, 1, "White: Italian", "white")
    h.chapter(clean, 1, 1, "Main line")
    h.line(clean, 1, 1, "Giuoco", ITALIAN)
    h.book(clean, 2, "Black: Scandinavian", "black")
    h.chapter(clean, 2, 2, "Qa5")
    h.line(clean, 2, 2, "Qa5 main", SCANDI)
    return clean


def test_book_color_has_no_default() -> None:
    with pytest.raises(TypeError):
        read.rep_lines(None, [h.START])  # type: ignore[call-arg, arg-type]
    with pytest.raises(ValueError):
        read.rep_lines(None, [h.START], book_color="any")  # type: ignore[arg-type]


def test_a_black_book_never_answers_for_a_white_to_move_position(db: psycopg.Connection[DictRow]) -> None:
    """Both books contain the start position at ply 0; the Black book's row there carries
    White's first move, which is the move it answers, not the player's prep."""
    rows = read.rep_lines(db, [h.START], book_color="by_turn")[h.START]
    assert [r["book"] for r in rows] == ["White: Italian"]
    assert rows[0]["expected_move"] == "e4" and rows[0]["line_ply"] == 0
    assert read.rep_lines(db, [h.START], book_color="white")[h.START] == rows
    with pytest.raises(ValueError):
        read.rep_lines(db, [h.START], book_color="black")


def test_by_turn_selects_per_fen_and_a_repeated_fen_is_looked_up_once(db: psycopg.Connection[DictRow]) -> None:
    after_e4 = h.spine(None, ["e4"])[1]
    out = read.rep_lines(db, [after_e4, h.START, after_e4], book_color="by_turn")
    assert [r["book"] for r in out[after_e4]] == ["Black: Scandinavian"]
    assert out[after_e4][0]["expected_move"] == "d5" and out[after_e4][0]["occurrence_fen"] == after_e4
    assert len(out[h.START]) == 1


def test_an_inactive_line_chapter_or_book_is_not_in_play(db: psycopg.Connection[DictRow]) -> None:
    db.execute("UPDATE chapters SET active = FALSE WHERE id = 1")
    assert read.rep_lines(db, [h.START], book_color="white")[h.START] == []
    db.execute("UPDATE chapters SET active = TRUE WHERE id = 1")
    db.execute("UPDATE books SET active = FALSE WHERE id = 1")
    assert read.rep_lines(db, [h.START], book_color="white")[h.START] == []


def test_a_terminal_occurrence_is_returned_with_no_move(db: psycopg.Connection[DictRow]) -> None:
    end = h.spine(None, ITALIAN)[-1]
    rows = read.rep_lines(db, [end], book_color="white")[end]
    assert len(rows) == 1 and rows[0]["expected_move"] is None and rows[0]["line_ply"] == 6


def test_a_line_reaching_the_same_board_twice_gives_two_occurrences(db: psycopg.Connection[DictRow]) -> None:
    shuffle = ["e4", "e5", "Nf3", "Nc6", "Ng1", "Nb8", "Nf3", "Nc6", "Bc4"]
    h.line(db, 3, 1, "Repetition", shuffle)
    board = h.spine(None, shuffle)[4]  # after Nc6, and again at ply 8
    rows = [r for r in read.rep_lines(db, [board], book_color="white")[board] if r["line_id"] == 3]
    assert [(r["line_ply"], r["expected_move"]) for r in rows] == [(4, "Ng1"), (8, "Bc4")]
    assert read.singular_move(board, rows) == "Ng1"  # the tie-break's line_ply picks the earlier one


def test_stats_count_distinct_games_per_board_and_book(db: psycopg.Connection[DictRow]) -> None:
    fens = h.game(db, 1, ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6"], days_ago=3)
    h.result(
        db, 1, book_id=1, chapter_id=1, ply=5, by="opponent", expected="Bc5", played="Nf6", fen=fens[5], line_ids=[1]
    )
    h.game(db, 2, ["e4", "e5", "Nf3", "Nc6", "d4", "exd4"], days_ago=2)
    h.result(db, 2, book_id=1, chapter_id=1, ply=4, by="me", expected="Bc4", played="d4", fen=fens[4], line_ids=[1])
    h.game(db, 3, ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"], days_ago=1)
    h.result(db, 3, book_id=1, chapter_id=1, ply=6, by="none", expected=None, played=None, fen=fens[4], line_ids=[1])
    row = read.rep_lines(db, [fens[4]], book_color="white")[fens[4]][0]
    assert (row["followed"], row["deviated_by_me"], row["deviated_by_opp"]) == (1, 1, 1)
    assert (row["me_dev_expected"], row["me_dev_played"], row["me_dev_ply"]) == ("Bc4", "d4", 4)
    assert row["last_followed"] is not None and row["last_deviated"] is not None
    bare = read.rep_lines(db, [fens[4]], book_color="white", with_stats=False)[fens[4]][0]
    assert (bare["followed"], bare["last_followed"]) == (0, None)


def test_stats_never_count_a_chess960_game(db: psycopg.Connection[DictRow]) -> None:
    fens = h.spine(None, ITALIAN)
    h.game(db, 7, ["e4", "e5", "Nf3", "Nc6", "d4"], variant="chess960")  # history, whatever rows it carries
    h.result(db, 7, book_id=1, chapter_id=1, ply=4, by="me", expected="Bc4", played="d4", fen=fens[4], line_ids=[1])
    row = read.rep_lines(db, [fens[4]], book_color="white")[fens[4]][0]
    assert (row["followed"], row["deviated_by_me"], row["me_dev_played"]) == (0, 0, None)
    h.game(db, 8, ["e4", "e5", "Nf3", "Nc6", "d4"])
    h.result(db, 8, book_id=1, chapter_id=1, ply=4, by="me", expected="Bc4", played="d4", fen=fens[4], line_ids=[1])
    row = read.rep_lines(db, [fens[4]], book_color="white")[fens[4]][0]
    assert (row["deviated_by_me"], row["me_dev_played"]) == (1, "d4")
