"""Recent practice evidence (core/repertoire/practice.py): what a first attempt's move trace proves
per decision, the populations the display draws from, and the caps applied only after
eligibility is known."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.repertoire import practice, report
from core.repertoire.importing import spine
from core.settings import Settings
from tests import repertoire_helpers as h

Row = dict[str, Any]

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]  # White decides at moves 1, 2, 3
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _seed(conn: psycopg.Connection[DictRow]) -> None:
    h.player(conn)
    h.book(conn, 1, "Italian", "white")
    h.chapter(conn, 1, 1, "Giuoco")


def _puzzle(conn: psycopg.Connection[DictRow], pid: int, line_id: int, moves: list[str]) -> None:
    conn.execute(
        "INSERT INTO puzzles (id, fen, solution_line, solution_fen_sequence, source_types, color, player_id,"
        " is_repertoire, repertoire_line_id) VALUES (%s, %s, %s::jsonb, %s::jsonb, ARRAY['deviation'], 'w', %s, TRUE, %s)",
        (pid, h.START, json.dumps(moves), json.dumps(spine(None, moves)), PLAYER_ID, line_id),
    )


def _load(
    conn: psycopg.Connection[DictRow],
    pid: int,
    trace: str | None,
    *,
    solved: bool,
    minutes_ago: int,
    session: str | None = "auto",
    seq: int | None = 1,
) -> None:
    """One attempt row: a load of its own (`session="auto"`), a row of a named load, or a
    sessionless row (`session=None`)."""
    sid = str(uuid.uuid4()) if session == "auto" else session
    conn.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, moves_played, attempt_at, session_id, attempt_seq)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (pid, PLAYER_ID, solved, trace, T0 - timedelta(minutes=minutes_ago), sid, seq),
    )


def _report(conn: psycopg.Connection[DictRow]) -> Row:
    conn.commit()
    return report.full(conn, Settings(), time_class="all")


def _line(out: Row, title: str) -> Row:
    return next(ln for b in out["lines"]["books"] for ch in b["chapters"] for ln in ch["lines"] if ln["title"] == title)


def _practice_at(out: Row, own_move: int) -> Row:
    (row,) = [r for r in out["decisions"]["rows"] if r["own_move"] == own_move]
    return row["practice"]


# --- classification --------------------------------------------------------------------------


def _italian() -> practice.Puzzle:
    return practice.Puzzle("white", spine(None, ITALIAN), ITALIAN, [0, 2, 4])


@pytest.mark.parametrize(
    ("trace", "verdict", "oks", "through", "failed_at"),
    [
        ("e4,Nf3,Bc4", practice.OK, [True, True, True], 3, None),
        ("e4,Nf3", practice.OK, [True, True], 2, None),  # a short solved segment: through its own extent
        ("e4,Nc3", practice.FAIL, [True, False], 1, 2),  # early failure after a success
        ("e4,Nf3,Bb5", practice.FAIL, [True, True, False], 2, 3),  # a later failure keeps the earlier successes
        ("Nc3", practice.FAIL, [False], None, 1),
        ("e4,Ngf3,Bc4", practice.OK, [True, True, True], 3, None),  # an alias of the prescribed move is the move
        ("e4,Nf3+,Bc4", practice.OK, [True, True, True], 3, None),  # a decorated token likewise
        ("", practice.UNKNOWN, [], None, None),
        (None, practice.UNKNOWN, [], None, None),
        ("e4,--", practice.UNKNOWN, [], None, None),  # the null move is not a move
        ("e4,Qh7", practice.UNKNOWN, [], None, None),  # not legal here
        ("e4,xyz", practice.UNKNOWN, [], None, None),
        ("e4,Nf3,Bc4,d3", practice.UNKNOWN, [], None, None),  # more moves than the line has player plies
    ],
)
def test_a_trace_is_classified_by_what_it_reproduces(
    trace: str | None, verdict: str, oks: list[bool], through: int | None, failed_at: int | None
) -> None:
    got_verdict, reached, got_through, got_failed = practice.classify(_italian(), trace)
    assert (got_verdict, [ok for _, ok in reached], got_through, got_failed) == (verdict, oks, through, failed_at)


def test_the_reverse_alias_pair_is_a_success_too() -> None:
    stored = ["Ngf3", "d5", "g3", "c6"]
    puzzle = practice.Puzzle("white", spine(None, stored), stored, [0, 2])
    assert practice.classify(puzzle, "Nf3,g3")[0] == practice.OK
    assert practice.classify(puzzle, "Nf3,Nc3")[:2] == (
        practice.FAIL,
        [
            (" ".join(spine(None, stored)[0].split(" ")[:4]), True),
            (" ".join(spine(None, stored)[2].split(" ")[:4]), False),
        ],
    )


# --- the strip and the decision pool ------------------------------------------------------------


def test_the_strip_is_the_newest_five_first_tries_with_the_tested_extent(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    _puzzle(clean, 10, 1, ITALIAN)
    for i, (trace, solved) in enumerate(
        [
            ("e4,Nf3,Bc4", True),
            ("e4,Nc3", False),
            ("e4,Nf3", True),
            ("", False),
            ("e4,Nf3,Bc4", True),
            ("e4,Nf3,Bb5", False),
        ]
    ):
        _load(clean, 10, trace, solved=solved, minutes_ago=i)
    out = _report(clean)
    ln = _line(out, "Main")
    assert ln["recent"] == [["ok", 3, None], ["fail", 1, 2], ["ok", 2, None], ["unknown", None, None], ["ok", 3, None]]
    assert ln["unknown_loads"] == 1 and ln["practice"] == {"loads": 6, "first_right": 3, "first_wrong": 3}
    assert out["decisions"]["unknown_loads"] == 1


def test_a_line_without_practice_has_an_empty_strip(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    out = _report(clean)
    assert _line(out, "Main")["recent"] == [] and _line(out, "Main")["unknown_loads"] == 0


def test_the_first_try_is_the_lowest_sequence_and_a_sessionless_row_is_a_load(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    _puzzle(clean, 10, 1, ITALIAN)
    sid = "11111111-1111-1111-1111-111111111111"
    _load(clean, 10, "e4,Nf3,Bc4", solved=True, minutes_ago=5, session=sid, seq=2)  # the retry, recorded first
    _load(clean, 10, "e4,Nc3", solved=False, minutes_ago=4, session=sid, seq=1)  # the first try, recorded later
    _load(clean, 10, "e4,Nf3,Bc4", solved=True, minutes_ago=3, session=None, seq=None)  # before sessions: a load
    out = _report(clean)
    assert [r[0] for r in _line(out, "Main")["recent"]] == ["ok", "fail"]


def test_a_decisions_five_are_the_newest_that_reached_it_not_the_lines_newest_five(
    clean: psycopg.Connection[DictRow],
) -> None:
    """Ten loads: the older five reached the third decision, the newest five failed at the first."""
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    _puzzle(clean, 10, 1, ITALIAN)
    h.game(clean, 1, ITALIAN)  # so the decisions have an opportunity and appear in the view
    for i in range(5):
        _load(clean, 10, "d4", solved=False, minutes_ago=i)
    for i in range(5, 10):
        _load(clean, 10, "e4,Nf3,Bc4", solved=True, minutes_ago=i)
    out = _report(clean)
    assert [r[0] for r in _line(out, "Main")["recent"]] == ["fail"] * 5
    assert _practice_at(out, 3) == {"failed": 0, "of": 5}
    assert _practice_at(out, 1) == {"failed": 5, "of": 5}


def test_unknown_and_short_traces_among_the_newest_do_not_hide_older_eligible_loads(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    _puzzle(clean, 10, 1, ITALIAN)
    h.game(clean, 1, ITALIAN)
    traces = ["", "e4", "e4,xyz", "e4,Nf3", "", "e4,Nf3,Bb5", "e4,Nf3,Bc4", "e4,Nf3,Bc4"]
    for i, t in enumerate(traces):
        _load(clean, 10, t, solved=t == "e4,Nf3,Bc4", minutes_ago=i)
    out = _report(clean)
    assert _practice_at(out, 3) == {"failed": 1, "of": 3}  # the three loads that reached move 3: ✗ ✓ ✓
    assert _practice_at(out, 2) == {"failed": 0, "of": 4}
    assert [r[0] for r in _line(out, "Main")["recent"]] == ["unknown", "ok", "unknown", "ok", "unknown"]


def test_a_shared_decision_pools_the_loads_of_every_line_through_it_once_each(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Bc5", ITALIAN)
    h.line(clean, 2, 1, "Nf6", ITALIAN[:5] + ["Nf6", "d3"])
    _puzzle(clean, 10, 1, ITALIAN)
    _puzzle(clean, 20, 2, ITALIAN[:5] + ["Nf6", "d3"])
    h.game(clean, 1, ITALIAN)
    # Line 1's newest five all stop at move 1; line 2's loads reach move 3 — they supply the decision's five.
    for i in range(5):
        _load(clean, 10, "e4", solved=True, minutes_ago=i)
    for i in range(5):
        _load(clean, 20, "e4,Nf3,Bb5" if i % 2 else "e4,Nf3,Bc4,d3", solved=i % 2 == 0, minutes_ago=10 + i)
    out = _report(clean)
    assert _practice_at(out, 3) == {"failed": 2, "of": 5}
    assert _practice_at(out, 1) == {"failed": 0, "of": 5}  # five newest of ten that reached move 1


def test_a_load_that_reaches_a_board_twice_counts_once_there(clean: psycopg.Connection[DictRow]) -> None:
    """`Nf3 Nf6 Ng1 Ng8 Nf3`: one load reaches the start board at its move 1 and move 3 — one
    eligible load at that board, a failure if either visit failed (the second ends the trace)."""
    _seed(clean)
    h.line(clean, 1, 1, "Knights", ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "d5", "g3"])
    _puzzle(clean, 10, 1, ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "d5", "g3"])
    h.game(clean, 1, ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "d5", "g3"])
    _load(clean, 10, "Nf3,Ng1,Nf3,g3", solved=True, minutes_ago=1)  # both visits right
    _load(
        clean, 10, "Nf3,Ng1,d4", solved=False, minutes_ago=2
    )  # right at move 1, wrong at move 3: one failure at the board
    for i in range(3, 9):
        _load(clean, 10, "Nf3,Ng1,Nf3,g3", solved=True, minutes_ago=i)
    out = _report(clean)
    assert _practice_at(out, 1) == {"failed": 1, "of": 5}  # five distinct loads, each once
    assert [r[0] for r in _line(out, "Knights")["recent"]] == ["ok", "fail", "ok", "ok", "ok"]


def test_fewer_than_five_eligible_loads_report_their_real_number(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ITALIAN)
    _puzzle(clean, 10, 1, ITALIAN)
    h.game(clean, 1, ITALIAN)
    _load(clean, 10, "e4,Nc3", solved=False, minutes_ago=1)
    _load(clean, 10, "e4,Nf3,Bc4", solved=True, minutes_ago=2)
    out = _report(clean)
    assert _practice_at(out, 2) == {"failed": 1, "of": 2}
    assert _practice_at(out, 3) == {"failed": 0, "of": 1}
    assert _practice_at(out, 1) == {"failed": 0, "of": 2}
