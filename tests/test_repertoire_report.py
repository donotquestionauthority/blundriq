"""The line report (core/repertoire/report.py): statuses derived per attached line, reduced per
game, slips and contexts as observed tuples, gaps of both kinds, and the puzzle on a gap board.
Every game is matched by the real matcher; nothing is planted on the result rows."""

from __future__ import annotations

import json
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import PLAYER_ID, REPERTOIRE_PUZZLE_MIN_EVENTS
from core.puzzles.lines import fen_sequence
from core.repertoire import matching, report
from core.repertoire.importing import spine
from core.settings import Settings
from tests import repertoire_helpers as h

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]
SCANDI = ["e4", "d5", "exd5", "Qxd5"]


# --- the arithmetic, against the matcher -------------------------------------------


def test_own_moves_is_the_matchers_commitment_arithmetic() -> None:
    for ply in range(21):
        for colour in ("white", "black"):
            assert (report.own_moves(ply, colour) >= 1) == matching.player_committed(ply, colour)
    assert report.own_moves(7, "white") == 4 and report.own_moves(7, "black") == 3
    assert report.own_moves(6, "white") == 3 and report.own_moves(6, "black") == 3


def test_status_of_stops_where_who_deviated_stops() -> None:
    line = ITALIAN
    for glen in range(0, 9):
        game = line[:glen] + ["h3"] * max(0, glen - len(line)) if glen <= len(line) else line + ["h3"] * (glen - 6)
        game = game[:glen]
        for m in range(0, 9):
            for colour in ("white", "black"):
                by, expected, _ = matching.who_deviated(game, line, m, colour)
                status = report.status_of(m, len(line), len(game), colour)
                if by == "none":
                    assert status in (report.RAN_OUT, report.ENDED)
                    assert status == (report.RAN_OUT if m >= len(line) else report.ENDED)
                else:
                    assert status == by and expected == line[m]


def test_reduce_status_precedence() -> None:
    assert report.reduce_status(["ran_out", "me"]) == "me"
    assert report.reduce_status(["ran_out", "opponent"]) == "opponent"
    assert report.reduce_status(["ran_out", "ended"]) == "ended"
    assert report.reduce_status(["ran_out"]) == "ran_out"


# --- fixtures ---------------------------------------------------------------------


def _seed(conn: psycopg.Connection[DictRow], *, white: bool = True, black: bool = False) -> None:
    h.player(conn)
    if white:
        h.book(conn, 1, "Italian", "white")
        h.chapter(conn, 1, 1, "Giuoco")
    if black:
        h.book(conn, 2, "Scandi", "black")
        h.chapter(conn, 2, 2, "Main")


def _match(conn: psycopg.Connection[DictRow]) -> None:
    conn.commit()
    matching.match_player(conn, window=1000)
    conn.commit()


def _lines(conn: psycopg.Connection[DictRow], time_class: str = "all") -> dict[str, Any]:
    return report.lines(conn, Settings(), time_class=time_class)


def _gaps(conn: psycopg.Connection[DictRow], colour: str, time_class: str = "all") -> dict[str, Any]:
    return report.gaps(conn, Settings(), time_class=time_class, colour=colour)


def _line_rows(out: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {ln["title"]: ln for b in out["books"] for ch in b["chapters"] for ln in ch["lines"]}


def _analyse(conn: psycopg.Connection[DictRow], gid: int, moves: list[str], at: dict[int, dict[str, Any]]) -> None:
    """Give a game stored analysis with the given entries at the given plies (others empty)."""
    pa = [
        {"ply": i, "eval": 0, "best_move": None, "best_line": None, "mate_in_moves": None}
        for i in range(len(moves) + 1)
    ]
    for ply, entry in at.items():
        pa[ply] = {**pa[ply], **entry}
    conn.execute("UPDATE chess_games SET ply_analysis = %s::jsonb WHERE id = %s", (json.dumps(pa), gid))
    conn.execute(
        "UPDATE player_games SET analyzed_at_depth = 18 WHERE chess_game_id = %s AND player_id = %s", (gid, PLAYER_ID)
    )


# --- lines: the short/long tie ----------------------------------------------------------


@pytest.mark.parametrize("short_first", [True, False])
def test_a_tied_complete_line_ran_out_while_the_longer_one_was_left(
    clean: psycopg.Connection[DictRow], short_first: bool
) -> None:
    """A = e4 e5, B = e4 e5 Nf3; the game e4 e5 Bc4 ties at two plies. Whichever id the matcher
    writes first, A ran out, B was left by the player (Nf3 expected, Bc4 played, own move 2), and
    the chapter counts one game, left by the player."""
    _seed(clean)
    a, b = (1, 2) if short_first else (2, 1)
    h.line(clean, a, 1, "A", ["e4", "e5"])
    h.line(clean, b, 1, "B", ["e4", "e5", "Nf3"])
    h.game(clean, 1, ["e4", "e5", "Bc4", "Nc6"], result="win")
    _match(clean)
    row = clean.execute("SELECT deviation_by FROM game_repertoire_results").fetchone()
    assert row is not None and row["deviation_by"] == ("none" if short_first else "me")  # the matcher's own view

    out = _lines(clean)
    rows = _line_rows(out)
    assert rows["A"]["ran_out"] == 1 and rows["A"]["left_by_me"] == 0 and rows["A"]["slip"] is None
    assert rows["B"]["left_by_me"] == 1 and rows["B"]["ran_out"] == 0
    assert rows["B"]["slip"] == {"own_move": 2, "expected": "Nf3", "played": "Bc4", "count": 1}
    assert rows["A"]["games"] == rows["B"]["games"] == 1 and rows["A"]["shared"] and rows["B"]["shared"]
    chapter = out["books"][0]["chapters"][0]
    assert chapter["games"] == 1 and chapter["left_by_me"] == 1 and chapter["ran_out"] == 0
    assert chapter["slip"] == {"own_move": 2, "expected": "Nf3", "played": "Bc4", "count": 1}
    assert out["books"][0]["games"] == 1 and out["books"][0]["left_by_me"] == 1 and out["books"][0]["faithful"] == 0
    assert rows["A"]["depth_total"] == 1 and rows["B"]["depth_total"] == 2
    assert rows["B"]["depth_reached"] == {"1": 1}


@pytest.mark.parametrize("colour", ["white", "black"])
@pytest.mark.parametrize("stop_after", [2, 3])
def test_a_game_that_ends_in_book_is_faithful_with_no_slip_and_no_gap(
    clean: psycopg.Connection[DictRow], colour: str, stop_after: int
) -> None:
    line = ["e4", "e5", "Nf3", "Nc6", "Bb5"]
    _seed(clean, white=colour == "white", black=colour == "black")
    h.line(clean, 1, 1 if colour == "white" else 2, "Ruy", line)
    h.game(clean, 1, line[:stop_after], color=colour, result="draw")
    _match(clean)
    row = clean.execute("SELECT deviation_by FROM game_repertoire_results").fetchone()
    assert row is not None and row["deviation_by"] == "none"
    out = _lines(clean)
    ln = _line_rows(out)["Ruy"]
    assert ln["ended"] == 1 and ln["faithful"] == 1 and ln["left_by_me"] == ln["left_by_opponent"] == ln["ran_out"] == 0
    assert ln["slip"] is None and ln["draws"] == 1
    chapter = out["books"][0]["chapters"][0]
    assert chapter["ended"] == 1 and chapter["faithful"] == 1
    assert _gaps(clean, colour)["gaps"] == []


def test_a_game_that_exhausts_the_line_as_it_ends_ran_out(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Short", ["e4", "e5"])
    h.game(clean, 1, ["e4", "e5"])
    _match(clean)
    ln = _line_rows(_lines(clean))["Short"]
    assert ln["ran_out"] == 1 and ln["ended"] == 0


def test_a_game_whose_lines_are_all_complete_is_not_a_gap(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "A", ["e4", "e5", "Nf3"])
    h.game(clean, 1, ["e4", "e5", "Nf3", "d6", "d4"])
    _match(clean)
    assert _gaps(clean, "white")["gaps"] == [] and _gaps(clean, "white")["prepared"] == []
    assert _line_rows(_lines(clean))["A"]["ran_out"] == 1


@pytest.mark.parametrize("a_first", [True, False])
def test_two_left_lines_of_one_chapter_reduce_to_the_first_by_name_whatever_their_ids(
    clean: psycopg.Connection[DictRow], a_first: bool
) -> None:
    """A = e4 e5 Nf3 and B = e4 e5 Nc3 tie on the game e4 e5 d4: both say the player left, with
    different expected moves. The chapter's slip is A's (first by name) under both id orderings."""
    _seed(clean)
    a, b = (1, 2) if a_first else (2, 1)
    h.line(clean, a, 1, "A", ["e4", "e5", "Nf3"])
    h.line(clean, b, 1, "B", ["e4", "e5", "Nc3"])
    h.game(clean, 1, ["e4", "e5", "d4", "exd4"])
    _match(clean)
    out = _lines(clean)
    rows = _line_rows(out)
    assert rows["A"]["slip"]["expected"] == "Nf3" and rows["B"]["slip"]["expected"] == "Nc3"
    chapter = out["books"][0]["chapters"][0]
    assert chapter["games"] == 1 and chapter["left_by_me"] == 1
    assert chapter["slip"] == {"own_move": 2, "expected": "Nf3", "played": "d4", "count": 1}
    assert out["books"][0]["slip"] == chapter["slip"]


# --- lines: the slip is one observed tuple ----------------------------------------------


def test_the_slip_is_the_most_common_whole_departure_not_a_mode_per_field(
    clean: psycopg.Connection[DictRow],
) -> None:
    """Three departures at own move 2 (Nf3 expected, d4 played), two at move 3 (Bb5/Bc4) and two
    at move 4 (Ba4/Bc4): the slip is (2, Nf3, d4) three of seven — never (2, Nf3, Bc4)."""
    _seed(clean)
    line = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"]
    h.line(clean, 1, 1, "Ruy", line)
    gid = 1
    for _ in range(3):
        h.game(clean, gid, ["e4", "e5", "d4", "exd4"])
        gid += 1
    for _ in range(2):
        h.game(clean, gid, ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"])
        gid += 1
    for _ in range(2):
        h.game(clean, gid, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bc4", "Nf6"])
        gid += 1
    _match(clean)
    out = _lines(clean)
    ln = _line_rows(out)["Ruy"]
    assert ln["games"] == 7 and ln["left_by_me"] == 7
    assert ln["slip"] == {"own_move": 2, "expected": "Nf3", "played": "d4", "count": 3}
    assert out["books"][0]["chapters"][0]["slip"] == ln["slip"]
    assert ln["depth_reached"] == {"1": 3, "2": 2, "3": 2} and ln["depth_total"] == 4


# --- lines: counting, ordering, scope ---------------------------------------------


def test_results_scope_ordering_and_chess960(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    h.line(clean, 2, 1, "Evans", ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "b4"])
    # Four games follow the Italian far enough to tie both lines; one is blitz; one is Chess960.
    h.game(clean, 1, ITALIAN + ["c3"], result="win")
    h.game(clean, 2, ITALIAN + ["c3"], result="loss", time_class="blitz")
    h.game(clean, 3, ITALIAN[:4] + ["d4", "exd4"], result="draw")  # left by me at own move 3
    h.game(clean, 4, ITALIAN + ["b4", "Bxb4"], result="win")  # Evans only
    h.game(clean, 5, ITALIAN + ["c3"], result="win", variant="chess960")
    _match(clean)
    # A Chess960 game never gets a result row; plant rows for it anyway to prove rule 7 holds.
    fens = spine(None, ITALIAN + ["c3"])
    h.result(
        clean, 5, book_id=1, chapter_id=1, ply=6, by="none", expected=None, played=None, fen=fens[6], line_ids=[1, 2]
    )
    clean.commit()

    out = _lines(clean)
    rows = _line_rows(out)
    assert rows["Main"]["games"] == 3 and rows["Evans"]["games"] == 4  # game 4 is Evans alone; the rest tie both
    # At ply 6 it is White to move: c3 against the Evans' b4 is the player leaving; the Italian ran out.
    assert rows["Main"]["ran_out"] == 2 and rows["Main"]["left_by_me"] == 1
    assert rows["Evans"]["left_by_me"] == 3 and rows["Evans"]["ran_out"] == 1
    assert rows["Evans"]["slip"] == {"own_move": 4, "expected": "b4", "played": "c3", "count": 2}
    assert (rows["Evans"]["wins"], rows["Evans"]["draws"], rows["Evans"]["losses"]) == (2, 1, 1)
    assert rows["Evans"]["score"] == 0.625
    chapter = out["books"][0]["chapters"][0]
    assert chapter["games"] == 4 and chapter["left_by_me"] == 3 and chapter["ran_out"] == 1
    assert [ln["title"] for ln in chapter["lines"]] == ["Evans", "Main"]
    assert rows["Main"]["shared"] and not rows["Evans"]["shared"]
    # The focus (rapid and slower) drops the blitz game everywhere.
    focus = _line_rows(_lines(clean, "focus"))
    assert focus["Main"]["games"] == 2 and focus["Evans"]["games"] == 3
    assert _line_rows(_lines(clean, "blitz"))["Main"]["games"] == 1


def test_lines_are_ordered_most_games_first(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Alpha", ["e4", "e5", "Nf3", "Nc6", "Bc4"])
    h.line(clean, 2, 1, "Beta", ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    h.game(clean, 1, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"])
    h.game(clean, 2, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"])
    h.game(clean, 3, ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"])
    _match(clean)
    out = _lines(clean)
    assert [ln["title"] for ln in out["books"][0]["chapters"][0]["lines"]] == ["Beta", "Alpha"]


def test_the_lines_rows_carry_no_puzzle_count(clean: psycopg.Connection[DictRow]) -> None:
    """The positions Practice serves per scope are not on the lines rows (the page never shows
    them); a prepared row still says whether its line's board has a puzzle."""
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    for gid in range(1, REPERTOIRE_PUZZLE_MIN_EVENTS + 1):
        h.game(clean, gid, ITALIAN[:4] + ["d4", "exd4"])  # the player leaves at Bc4 every time
    clean.execute(
        "INSERT INTO puzzles (fen, solution_line, solution_fen_sequence, source_types, color, player_id,"
        " is_repertoire, repertoire_line_id, active) VALUES (%s, %s::jsonb, %s::jsonb, ARRAY['deviation'], 'w', %s,"
        " TRUE, 1, TRUE)",
        (h.START, json.dumps(ITALIAN), json.dumps(fen_sequence(h.START, ITALIAN)), PLAYER_ID),
    )
    _match(clean)
    out = _lines(clean)
    assert "puzzles" not in _line_rows(out)["Main"]
    assert "puzzles" not in out["books"][0]["chapters"][0] and "puzzles" not in out["books"][0]


# --- gaps, kind A --------------------------------------------------------------------


@pytest.mark.parametrize("short_first", [True, False])
def test_the_opponent_turn_tie_is_a_gap_with_the_continuing_lines_context(
    clean: psycopg.Connection[DictRow], short_first: bool
) -> None:
    """A = e4 e5 Nf3, B = e4 e5 Nf3 Nc6; the game e4 e5 Nf3 d6. Under both id orderings the game is
    one gap at the board after d6, with B's Nc6 expected and d6 played."""
    _seed(clean)
    a, b = (1, 2) if short_first else (2, 1)
    h.line(clean, a, 1, "A", ["e4", "e5", "Nf3"])
    h.line(clean, b, 1, "B", ["e4", "e5", "Nf3", "Nc6"])
    moves = ["e4", "e5", "Nf3", "d6", "d4"]
    fens = h.game(clean, 1, moves, result="loss")
    _match(clean)
    out = _gaps(clean, "white")
    assert out["no_book"] is False and out["prepared"] == []
    assert len(out["gaps"]) == 1
    card = out["gaps"][0]
    assert card["fen"] == fens[4] and len(card["fen"].split()) == 6
    assert card["context"] == {
        "book_id": 1,
        "book": "Italian",
        "chapter_id": 1,
        "chapter": "Giuoco",
        "line_id": b,
        "line_name": "B",
        "expected": "Nc6",
        "played": "d6",
        "count": 1,
    }
    assert card["games"] == 1 and card["losses"] == 1 and card["score"] == 0.0
    assert card["continuations"] == [{"moves": ["d4"], "games": 1, "score": 0.0}]
    assert card["engine"] is None and card["colour"] == "white"
    # The lines report agrees: A ran out, B left by the opponent, the chapter says opponent.
    rows = _line_rows(_lines(clean))
    assert rows["A"]["ran_out"] == 1 and rows["B"]["left_by_opponent"] == 1
    assert _lines(clean)["books"][0]["chapters"][0]["left_by_opponent"] == 1


def test_a_gap_groups_by_canonical_board_with_one_context_tuple_and_one_engine_row(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean)
    h.line(clean, 2, 1, "Main", ITALIAN)  # the higher id: the context is chosen by name, not id
    h.line(clean, 1, 1, "Two knights", ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6"])
    base = ["e4", "e5", "Nf3", "Nc6", "Bc4", "d6"]  # the opponent leaves both lines with d6
    fens = h.game(clean, 1, base + ["c3", "Be7", "d4", "exd4"], result="win", days_ago=3)
    h.game(clean, 2, base + ["c3", "Be7", "d4", "exd4"], result="loss", days_ago=2)
    h.game(clean, 3, base + ["d4"], result="draw", days_ago=1)  # ended two plies in
    _match(clean)
    # Game 3 reaches the same board with a different halfmove clock: the canonical key groups it.
    f3 = spine(None, base + ["d4"])
    parts = f3[6].split()
    parts[4] = "7"
    f3[6] = " ".join(parts)
    clean.execute("UPDATE chess_games SET fen_sequence = %s::jsonb WHERE id = 3", (json.dumps(f3),))
    # Only games 1 and 2 are analysed; game 2 is the more recent, so its row is the card's.
    _analyse(clean, 1, base + ["c3", "Be7", "d4", "exd4"], {6: {"best_move": "c3", "best_line": "c3 Nf6 d4"}})
    _analyse(
        clean, 2, base + ["c3", "Be7", "d4", "exd4"], {6: {"best_move": "O-O", "best_line": "O-O Nf6 Re1", "eval": 35}}
    )
    clean.commit()
    out = _gaps(clean, "white")
    assert len(out["gaps"]) == 1
    card = out["gaps"][0]
    assert card["games"] == 3 and (card["wins"], card["draws"], card["losses"]) == (1, 1, 1) and card["score"] == 0.5
    assert card["fen"] == fens[6]  # game 2's board (identical to game 1's), six fields
    assert card["engine"] == {"best_move": "O-O", "line": ["O-O", "Nf6", "Re1"], "eval": 35, "mate_in": None}
    # Both lines continue past d6; the context is the first by name (the read module's tie-break).
    assert (
        card["context"]["count"] == 3
        and card["context"]["expected"] == "Bc5"
        and card["context"]["line_name"] == "Main"
    )
    assert card["context"]["played"] == "d6"
    assert card["continuations"] == [
        {"moves": ["c3", "Be7", "d4", "exd4"], "games": 2, "score": 0.5},
        {"moves": ["d4"], "games": 1, "score": 0.5},
    ]
    # A newer game whose analysis stops before the board (the analyser stops at a move it cannot
    # read) does not hide the older game's entry: the card's engine line, board and stem are the
    # older game's. A newer game with no analysis at all never did.
    clean.execute(
        "UPDATE chess_games SET ply_analysis = %s::jsonb WHERE id = 3", (json.dumps([{"ply": 0}, {"ply": 1}]),)
    )
    clean.execute("UPDATE player_games SET analyzed_at_depth = 18 WHERE chess_game_id = 3")
    clean.commit()
    card = _gaps(clean, "white")["gaps"][0]
    assert card["engine"]["best_move"] == "O-O" and card["fen"] == fens[6] and card["stem"] == base
    clean.execute("UPDATE chess_games SET ply_analysis = %s::jsonb WHERE id = 2", (json.dumps([{"ply": 0}]),))
    clean.commit()
    card = _gaps(clean, "white")["gaps"][0]
    assert card["engine"] == {"best_move": "c3", "line": ["c3", "Nf6", "d4"], "eval": 0, "mate_in": None}


def test_a_black_gap_board_is_the_board_after_whites_move(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean, white=False, black=True)
    h.line(clean, 1, 2, "Main", SCANDI + ["Nc3", "Qa5"])
    fens = h.game(clean, 1, ["e4", "d5", "exd5", "Qxd5", "Nf3", "Bg4"], color="black", result="win")
    _match(clean)
    out = _gaps(clean, "black")
    assert len(out["gaps"]) == 1
    card = out["gaps"][0]
    assert card["fen"] == fens[5] and card["fen"].split()[1] == "b" and card["colour"] == "black"
    assert card["arrived_by"] == "Nf3" and card["context"]["expected"] == "Nc3" and card["context"]["played"] == "Nf3"
    assert card["continuations"] == [{"moves": ["Bg4"], "games": 1, "score": 1.0}]
    assert _gaps(clean, "white")["no_book"] is True


def test_a_card_without_any_analysed_game_still_has_a_real_board(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    fens = h.game(clean, 1, ITALIAN[:5] + ["d6", "c3"])
    _match(clean)
    card = _gaps(clean, "white")["gaps"][0]
    assert card["fen"] == fens[6] and card["engine"] is None


def test_the_time_class_scopes_the_gaps(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    h.game(clean, 1, ITALIAN[:5] + ["d6", "c3"], time_class="blitz")
    _match(clean)
    assert _gaps(clean, "white", "focus")["gaps"] == []
    assert len(_gaps(clean, "white", "all")["gaps"]) == 1


# --- gaps, kind B: the unmatched games --------------------------------------------------


def test_an_unmatched_black_game_with_a_book_reply_is_prepared_not_a_gap(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean, white=False, black=True)
    h.line(clean, 1, 2, "Main", SCANDI)
    fens = h.game(clean, 1, ["e4", "c5", "Nf3"], color="black", result="loss")
    h.game(clean, 2, ["e4", "c5", "Nc3"], color="black", result="win")
    h.game(clean, 3, ["d4", "d5", "c4"], color="black", result="loss")  # no book through 1.d4
    _match(clean)
    flagged = {
        r["chess_game_id"] for r in clean.execute("SELECT chess_game_id FROM player_games WHERE no_repertoire_match")
    }
    assert flagged == {1, 2, 3}
    out = _gaps(clean, "black")
    assert out["no_book"] is False
    assert out["prepared"] == [
        {
            "fen": fens[1],
            "book_move": "d5",
            "played": "c5",
            "games": 2,
            "wins": 1,
            "draws": 0,
            "losses": 1,
            "line_id": 1,
            "line_name": "Main",
            "book": "Scandi",
            "chapter": "Main",
            "puzzles": 0,
            "last_played": out["prepared"][0]["last_played"],
        }
    ]
    assert out["prepared"][0]["last_played"] is not None
    assert len(out["gaps"]) == 1
    card = out["gaps"][0]
    assert card["fen"] == spine(None, ["d4"])[1] and card["context"] is None and card["colour"] == "black"
    assert card["continuations"] == [{"moves": ["d5", "c4"], "games": 1, "score": 0.0}]


def test_an_unmatched_white_game_is_prepared_when_a_white_book_exists(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    h.game(clean, 1, ["Nf3", "d5", "g3"], result="win")
    h.game(clean, 2, ["Nf3", "d5", "g3"], result="win")
    _match(clean)
    out = _gaps(clean, "white")
    assert out["gaps"] == []
    assert len(out["prepared"]) == 1
    row = out["prepared"][0]
    assert (row["fen"], row["book_move"], row["played"], row["games"], row["wins"]) == (h.START, "e4", "Nf3", 2, 2)


def test_white_without_an_effectively_active_line_gets_the_notice_and_black_gets_cards(
    clean: psycopg.Connection[DictRow],
) -> None:
    """As White every book starts at the start position, so no active White line means every
    White game is on its own: a notice, not a card per first move. As Black an uncovered first
    move is a gap whether or not any Black line is active — that is the hole to fill."""
    h.player(clean)
    h.book(clean, 2, "Scandi", "black", active=False)  # a Black book, switched off
    h.chapter(clean, 2, 2, "Main")
    h.line(clean, 1, 2, "Main", SCANDI)
    fens = h.game(clean, 1, ["Nf3", "d5", "g3"], result="win")
    h.game(clean, 2, ["d4", "d5", "c4"], color="black", result="loss")
    h.game(clean, 3, ["d4", "Nf6", "c4"], color="black", result="draw")
    _match(clean)
    assert _gaps(clean, "white") == {
        "colour": "white",
        "no_book": True,
        "prepared": [],
        "gaps": [],
        "destinations": [],
        "prefill_plies": 10,
        "max_plies": 60,
    }
    out = _gaps(clean, "black")
    assert out["no_book"] is False and out["prepared"] == []
    assert len(out["gaps"]) == 1
    card = out["gaps"][0]
    assert card["fen"] == spine(None, ["d4"])[1] and card["games"] == 2 and card["context"] is None
    assert card["arrived_by"] == "d4" and {c["moves"][0] for c in card["continuations"]} == {"d5", "Nf6"}
    # No Black book at all: the same cards.
    clean.execute("DELETE FROM books WHERE id = 2")
    clean.commit()
    out = _gaps(clean, "black")
    assert out["no_book"] is False and len(out["gaps"]) == 1 and out["gaps"][0]["games"] == 2
    # An active Black book whose every line is off: still the cards, nothing "prepared".
    h.book(clean, 3, "Caro", "black")
    h.chapter(clean, 3, 3, "Main")
    h.line(clean, 2, 3, "Main", ["e4", "c6", "d4", "d5"], active=False)
    clean.commit()
    out = _gaps(clean, "black")
    assert out["no_book"] is False and len(out["gaps"]) == 1 and out["prepared"] == []
    # A White game with no active White line is the notice even with Black lines active.
    assert _gaps(clean, "white")["no_book"] is True
    assert fens[0] == h.START


def test_a_board_where_a_line_only_ends_is_not_preparation(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean, white=False, black=True)
    h.line(clean, 1, 2, "Stub", ["e4"])  # the line ends at the board after 1.e4: no reply prescribed
    h.game(clean, 1, ["e4", "c5", "Nf3"], color="black")
    _match(clean)
    out = _gaps(clean, "black")
    assert out["prepared"] == [] and len(out["gaps"]) == 1


# --- the stem and the destinations of a gap card ----------------------------------------


def test_a_gap_card_carries_the_games_own_stem_to_the_board_and_the_chapters_a_line_may_go_into(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    h.game(clean, 1, ["e4", "e5", "Nf3", "Nc6", "Bc4", "d6", "c3"])
    clean.execute(
        "INSERT INTO chapters (id, book_id, title, active, root_fen) VALUES (7, 1, 'Tabiya', TRUE, %s)",
        (spine(None, ITALIAN[:4])[4],),
    )
    h.chapter(clean, 8, 1, "Off", active=False)
    h.chapter(clean, 9, 1, "Also start-rooted")
    _match(clean)
    white = _gaps(clean, "white")
    (card,) = white["gaps"]
    assert card["stem"] == ["e4", "e5", "Nf3", "Nc6", "Bc4", "d6"] and card["fen"] == spine(None, card["stem"])[-1]
    assert "puzzle" not in card and "chapters" not in card
    # Start-rooted active chapters of White's books only, once per colour, in the Repertoire
    # page's order; the card names its context chapter and the modal preselects it from the list.
    assert [(c["id"], c["title"], c["book"]) for c in white["destinations"]] == [
        (9, "Also start-rooted", "Italian"),
        (1, "Giuoco", "Italian"),
    ]
    assert card["context"]["chapter_id"] == 1
    assert card["catch_all"] == {"book": "Catch-all (White)", "chapter": card["family"] or "Unnamed opening"}
    # Kind B as Black: the stem is the one opponent move; no Black book means no destinations.
    h.game(clean, 2, ["d4", "d5", "c4"], color="black")
    _match(clean)
    out = _gaps(clean, "black")
    (b,) = out["gaps"]
    assert b["stem"] == ["d4"] and out["destinations"] == [] and b["catch_all"]["book"] == "Catch-all (Black)"
    assert out["max_plies"] == 60 and out["prefill_plies"] == 10


def test_a_stem_that_does_not_replay_to_the_board_is_none(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    h.game(clean, 1, ["e4", "e5", "Nf3", "Nc6", "Bc4", "d6", "c3"])
    _match(clean)
    # A game whose stored moves were damaged after matching: the card still exists, without a stem.
    damaged = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Zz9", "c3"]
    clean.execute("UPDATE chess_games SET moves = %s::jsonb WHERE id = 1", (json.dumps(damaged),))
    clean.commit()
    (card,) = _gaps(clean, "white")["gaps"]
    assert card["stem"] is None and card["fen"] == spine(None, ["e4", "e5", "Nf3", "Nc6", "Bc4", "d6"])[-1]


def test_every_active_line_is_a_row_with_its_origin_and_practice_numbers(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    h.line(clean, 2, 1, "Never played", ["e4", "c5", "Nf3"])
    clean.execute("UPDATE repertoire_lines SET origin = 'manual' WHERE id = 2")
    h.line(clean, 3, 1, "Off", ["e4", "c6", "d4"], active=False)
    h.game(clean, 1, ITALIAN[:5] + ["d6", "c3"], result="win")
    clean.execute(
        "INSERT INTO puzzles (id, fen, solution_line, solution_fen_sequence, source_types, color, player_id,"
        " is_repertoire, repertoire_line_id) VALUES (50, %s, %s::jsonb, %s::jsonb, ARRAY['deviation'], 'w', %s, TRUE, 2)",
        (h.START, json.dumps(["e4", "c5", "Nf3"]), json.dumps(spine(None, ["e4", "c5", "Nf3"])), PLAYER_ID),
    )
    sid = "33333333-3333-3333-3333-333333333333"
    clean.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, session_id, attempt_seq)"
        " VALUES (50, %s, FALSE, %s, 1), (50, %s, TRUE, %s, 2)",
        (PLAYER_ID, sid, PLAYER_ID, sid),
    )
    _match(clean)
    (book,) = _lines(clean)["books"]
    (chapter,) = book["chapters"]
    rows = chapter["lines"]
    # A line carries the ids of its chapter and book; their titles are on the holders, once.
    assert [(r["title"], r["games"], r["origin"], r["chapter_id"], r["book_id"]) for r in rows] == [
        ("Main", 1, "course", 1, 1),
        ("Never played", 0, "manual", 1, 1),
    ]
    assert "book" not in rows[0] and "chapter" not in rows[0] and "colour" not in rows[0]
    assert rows[1]["practice"] == {"loads": 1, "first_right": 0, "first_wrong": 1} and rows[1]["shared"] is False
    assert rows[1]["score"] is None and rows[1]["faithful"] == 0 and rows[1]["depth_total"] == 2
    assert rows[0]["practice"] == {"loads": 0, "first_right": 0, "first_wrong": 0}
    assert chapter["practice"] == book["practice"] == {"loads": 1, "first_right": 0, "first_wrong": 1}
    assert chapter["title"] == "Giuoco" and chapter["book_id"] == 1 and "book" not in chapter
    assert book["title"] == "Italian" and book["colour"] == "white"
