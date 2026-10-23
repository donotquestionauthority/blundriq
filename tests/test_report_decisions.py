"""Decisions (core/repertoire/decisions.py): what the player did at every board the repertoire
prescribes a move at, by canonical board, over every in-scope game — matched or not — and what
that says per line. Each case the module must get right is a test here, by name."""

from __future__ import annotations

from collections import Counter
from typing import Any

import chess
import psycopg
import pytest
from psycopg.rows import DictRow

from core import oracle
from core.repertoire import conflicts, decisions, matching, read, report
from core.repertoire.importing import spine
from core.settings import Settings
from tests import repertoire_helpers as h

Row = dict[str, Any]


def _seed(conn: psycopg.Connection[DictRow]) -> None:
    h.player(conn)
    h.book(conn, 1, "Queen's pawn", "white")
    h.chapter(conn, 1, 1, "Catalan")


def _rows(conn: psycopg.Connection[DictRow], games: int | None = None, time_class: str = "all") -> Row:
    """Both reads, merged as the page sees them: the first read's rows with each line's evidence
    from the second read folded in (a line with none carries `decisions_total` alone)."""
    conn.commit()
    out = report.full(conn, Settings(), time_class=time_class, games=games)
    view = report.decisions_view(conn, Settings(), time_class=time_class, games=games)
    for b in out["lines"]["books"]:
        for ch in b["chapters"]:
            for ln in ch["lines"]:
                ln.update(view["lines"].get(int(ln["id"]), {}))
    out["decisions"] = {k: v for k, v in view.items() if k != "lines"}
    return out


def _decisions(out: Row) -> dict[tuple[str, int], Row]:
    """{(colour, own move): row} — the fixtures never put two tested boards at one move number."""
    rows = {(str(r["colour"]), int(r["own_move"])): r for r in out["decisions"]["rows"]}
    assert len(rows) == len(out["decisions"]["rows"])
    return rows


def _line(out: Row, title: str) -> Row:
    return next(ln for b in out["lines"]["books"] for ch in b["chapters"] for ln in ch["lines"] if ln["title"] == title)


def _canonical(fen: str) -> str:
    return " ".join(fen.split(" ")[:4])


# --- the cases the module exists for ------------------------------------------------------------------

CATALAN = ["d4", "Nf6", "c4", "e6", "Nf3", "d5", "g3"]  # White decides at moves 1, 2, 3, 4


def test_opponents_always_leave_early_leaves_the_continuation_untested(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", CATALAN)
    h.game(clean, 1, ["d4", "d5", "c4"])  # Black left at move 1; White's second decision is another board
    h.game(clean, 2, ["d4", "d5", "Nf3"])
    out = _rows(clean)
    ln = _line(out, "Main")
    assert (ln["tested"], ln["decisions_total"], ln["deepest_tested"], ln["correct_through"]) == (1, 4, 1, 1)
    assert ln["trouble_spot"] is None
    # The list ends at the last decision with anything to show; the untested tail is the count's.
    assert [d[3] for d in ln["decisions"]] == ["clean"] and ln["decisions_total"] == 4
    assert _decisions(out)[("white", 1)]["opportunities"] == 2


def test_a_repeated_failure_after_a_long_correct_prefix_is_the_trouble_spot(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", CATALAN)
    for gid in (1, 2, 3):
        h.game(clean, gid, CATALAN[:6] + ["Nc3"], days_ago=gid)  # right at 1–3, wrong at 4, every game
    out = _rows(clean)
    ln = _line(out, "Main")
    assert ln["trouble_spot"] == {"own_move": 4, "move": "g3", "slips": 3, "opportunities": 3}
    assert ln["correct_through"] == 3 and ln["tested"] == 4
    d = _decisions(out)[("white", 4)]
    assert (d["opportunities"], d["slips"], d["most_played"]) == (3, 3, "Nc3")
    # The Decisions view is in Study order: the only slipping decision first.
    assert out["decisions"]["rows"][0]["own_move"] == 4


def test_one_failure_in_one_opportunity_is_shown_as_is_below_two_of_two(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", CATALAN)
    h.chapter(clean, 2, 1, "London")
    h.line(clean, 2, 2, "London", ["d4", "d5", "Bf4"])
    h.game(clean, 1, ["d4", "Nf6", "Nc3"], days_ago=1)  # one slip at the Catalan's move 2 in one game
    h.game(clean, 2, ["d4", "d5", "Nf3"], days_ago=2)  # two slips at the London's move 2
    h.game(clean, 3, ["d4", "d5", "e3"], days_ago=3)
    out = _rows(clean)
    rows = out["decisions"]["rows"]
    assert [(r["slips"], r["opportunities"]) for r in rows] == [(2, 2), (1, 1), (0, 3)]
    assert _line(out, "Main")["trouble_spot"]["slips"] == 1  # "missed 1 of 1", no demotion


def test_several_lines_through_one_board_are_one_decision(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    for i in range(1, 5):
        h.line(clean, i, 1, f"Line {i}", CATALAN[:6] + ["g3", ["Be7", "Bb4+", "dxc4", "c6"][i - 1]])
    for gid in (1, 2, 3):
        h.game(clean, gid, CATALAN[:6] + ["Nc3"], days_ago=gid)
    out = _rows(clean)
    rows = out["decisions"]["rows"]
    assert len(rows) == 4  # moves 1–4, one row each, not one per line
    top = rows[0]
    assert (top["own_move"], top["slips"], top["opportunities"]) == (4, 3, 3)
    assert "key" not in top and "board" not in top  # the page keys a row by its first line and ply
    for i in range(1, 5):
        assert _line(out, f"Line {i}")["trouble_spot"]["slips"] == 3


def test_games_continuing_into_different_branches_count_as_successes_at_the_shared_decision(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Be7", CATALAN + ["Be7", "Bg2"])
    h.line(clean, 2, 1, "dxc4", CATALAN + ["dxc4", "Bg2"])
    h.game(clean, 1, CATALAN + ["Be7", "Bg2"], days_ago=1)
    h.game(clean, 2, CATALAN + ["dxc4", "Qa4+"], days_ago=2)
    out = _rows(clean)
    (d,) = [r for r in out["decisions"]["rows"] if r["own_move"] == 4]
    assert (d["opportunities"], d["slips"]) == (2, 0)
    for title in ("Be7", "dxc4"):
        fourth = _line(out, title)["decisions"][3]
        assert fourth[3:] == ["clean", 2, 0]


# --- identity: the canonical board, not the position ---------------------------------------------------------------


def test_a_board_reached_by_another_move_order_is_the_same_decision(clean: psycopg.Connection[DictRow]) -> None:
    """`d4 Nf6 Nf3 d5` and `Nf3 d5 d4 Nf6` reach one board with different halfmove counters."""
    _seed(clean)
    h.line(clean, 1, 1, "Main", ["d4", "Nf6", "Nf3", "d5", "c4"])
    a, b = ["d4", "Nf6", "Nf3", "d5"], ["Nf3", "d5", "d4", "Nf6"]
    assert spine(None, a)[-1] != spine(None, b)[-1] and _canonical(spine(None, a)[-1]) == _canonical(spine(None, b)[-1])
    h.game(clean, 1, a + ["c4"], days_ago=1)
    h.game(clean, 2, b + ["c4"], days_ago=2)
    h.game(clean, 3, b + ["Bg5"], days_ago=3)  # matched to nothing by the matcher, an opportunity all the same
    _match_all(clean)
    out = _rows(clean)
    d = _decisions(out)[("white", 3)]
    assert (d["opportunities"], d["slips"], d["most_played"]) == (3, 1, "Bg5")
    assert _line(out, "Main")["games"] == 1  # the matcher's attachment is another population


def test_a_board_reached_twice_in_one_game_is_one_visit(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", ["Nf3", "Nf6", "g3"])
    # The start board comes back after Ng1 Ng8 (other counters), the move-2 board likewise.
    h.game(clean, 1, ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "g3"])
    out = _rows(clean)
    rows = _decisions(out)
    assert (rows[("white", 1)]["opportunities"], rows[("white", 1)]["slips"]) == (1, 0)
    assert (rows[("white", 2)]["opportunities"], rows[("white", 2)]["slips"], rows[("white", 2)]["most_played"]) == (
        1,
        1,
        "Ng1",
    )


def test_a_line_that_reaches_a_board_twice_numbers_each_occurrence_as_its_own(
    clean: psycopg.Connection[DictRow],
) -> None:
    """`Nf3 Nf6 Ng1 Ng8 Nf3`: the start board is the line's move 1 and its move 3; the shared
    decision counts the game once, but the line's own numbers stay 1, 2, 3 — and a short line
    sharing the start board does not drag them to 1 either."""
    _seed(clean)
    h.line(clean, 1, 1, "Knights out and back", ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "d5", "g3"])
    h.line(clean, 2, 1, "Short", ["Nf3", "d5", "g3"])
    h.game(clean, 1, ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "d5", "g3"])
    out = _rows(clean)
    ln = _line(out, "Knights out and back")
    assert [d[1] for d in ln["decisions"]] == [1, 2, 3, 4]
    assert [d[3] for d in ln["decisions"]] == ["clean", "clean", "clean", "clean"]
    assert (ln["deepest_tested"], ln["correct_through"]) == (4, 4)
    start = [r for r in out["decisions"]["rows"] if r["move"] == "Nf3" and r["own_move"] == 1]
    assert len(start) == 1 and start[0]["opportunities"] == 1  # the game's first visit, once
    assert start[0]["lines"] == [[0, [1, 2]], [4, [1]]]  # line 1 at both its plies, once each
    assert _line(out, "Short")["decisions"][0][1] == 1


def test_san_spellings_of_one_move_are_one_decision(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Plain", ["Nf3", "d5", "g3"])
    h.line(clean, 2, 1, "Over-disambiguated", ["Ngf3", "d5", "g3"])
    h.game(clean, 1, ["Nf3", "d5", "e3"])
    out = _rows(clean)
    first = [r for r in out["decisions"]["rows"] if r["own_move"] == 1]
    assert len(first) == 1 and first[0]["move"] == "Nf3"
    assert first[0]["lines"] == [[0, [2, 1]]]  # at ply 0 in both; the report's tie-break: by title
    assert out["decisions"]["contested"] == []


def test_a_gate_valid_disagreement_at_a_transposed_board_is_contested_and_excluded(
    clean: psycopg.Connection[DictRow],
) -> None:
    """Two effective lines reach one board with different counters and prescribe different
    continuations: the gate admits both (it keys the full FEN); the report excludes the board and
    lists it; the reader agrees unless the game's FEN matches an occurrence exactly."""
    _seed(clean)
    h.line(clean, 1, 1, "Via Nf6", ["d4", "Nf6", "Nf3", "d5", "c4"])
    h.line(clean, 2, 1, "Via d5", ["d4", "d5", "Nf3", "Nf6", "g3"])
    exact = spine(None, ["d4", "Nf6", "Nf3", "d5"])[-1]
    other = spine(None, ["d4", "d5", "Nf3", "Nf6"])[-1]
    assert exact != other and _canonical(exact) == _canonical(other)
    clean.commit()
    assert conflicts.contested_count(clean) == 0  # the gate's identity sees no conflict
    h.game(clean, 1, ["d4", "Nf6", "Nf3", "d5", "c4"], days_ago=1)
    h.game(clean, 2, ["d4", "d5", "Nf3", "Nf6", "Bg5"], days_ago=2)
    out = _rows(clean)
    assert sorted(r["own_move"] for r in out["decisions"]["rows"]) == [1, 2, 2]  # nothing counted at move 3
    (c,) = out["decisions"]["contested"]
    assert (
        c["own_move"] == 3 and sorted(a["move"] for a in c["alternatives"]) == ["c4", "g3"] and c["line_ids"] == [1, 2]
    )
    for title in ("Via Nf6", "Via d5"):
        assert _line(out, title)["decisions"][2][3] == "contested" and _line(out, title)["correct_through"] == 2
    # The reader keeps its exact-occurrence preference: an occurrence's own FEN is a match for it,
    # a FEN with other counters is the conflict the report sees — the allowed difference.
    third = exact.rsplit(" ", 2)[0] + " 7 3"
    assert (
        read.project_ply(exact, read.rep_lines(clean, [exact], book_color="by_turn", with_stats=False)[exact])["status"]
        == read.STATUS_MATCH
    )
    assert (
        read.project_ply(third, read.rep_lines(clean, [third], book_color="by_turn", with_stats=False)[third])["status"]
        == read.STATUS_CONFLICT
    )


def test_a_board_first_reached_late_in_a_game_is_still_an_opportunity(clean: psycopg.Connection[DictRow]) -> None:
    """The only line is three plies long; the game reaches its second decision's board at ply 6,
    after knights out and back — no ply bounds where an opportunity may lie."""
    _seed(clean)
    h.line(clean, 1, 1, "Main", ["d4", "Nf6", "c4"])
    h.game(clean, 1, ["Nf3", "Nf6", "Ng1", "Ng8", "d4", "Nf6", "c4", "e6"])
    out = _rows(clean)
    ln = _line(out, "Main")
    # The start board: reached at ply 0 (Nf3 played: a slip); the board after d4 Nf6: first reached
    # at ply 6, c4 played — an opportunity and a success, past the line's three plies.
    assert [d[3] for d in ln["decisions"]] == ["slipped", "clean"]
    assert _decisions(out)[("white", 2)] == {**_decisions(out)[("white", 2)], "opportunities": 1, "slips": 0}
    assert ln["correct_through"] is None and ln["tested"] == 2


def test_a_tabiya_rooted_line_reached_after_a_long_unrelated_opening(clean: psycopg.Connection[DictRow]) -> None:
    """A chapter rooted at a board after `d4 Nf6 c4 e6` (its lines start there); a game reaches
    that board at ply 12 by another order and plays the line's first move."""
    _seed(clean)
    root = spine(None, ["d4", "Nf6", "c4", "e6"])[-1]
    h.chapter(clean, 2, 1, "Tabiya")
    clean.execute("UPDATE chapters SET root_fen = %s WHERE id = 2", (root,))
    h.line(clean, 1, 2, "From the tabiya", ["Nf3", "d5", "g3"], fens=spine(root, ["Nf3", "d5", "g3"]))
    h.game(
        clean, 1, ["Nf3", "Nf6", "Ng1", "Ng8", "c4", "e6", "Nf3", "Nf6", "Ng1", "Ng8", "d4", "Nf6", "Nf3", "d5", "g3"]
    )
    out = _rows(clean)
    ln = _line(out, "From the tabiya")
    assert [(d[1], d[3]) for d in ln["decisions"]] == [(3, "clean"), (4, "clean")]  # the tabiya's own move numbers
    assert _decisions(out)[("white", 3)]["opportunities"] == 1  # first reached at ply 12


# --- the line's evidence: holes are visible, never implied ----------------------------------------------------


def test_a_transposition_that_skips_a_decision_leaves_a_hole_and_through_stops_before_it(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", CATALAN)
    h.game(clean, 1, ["d4", "e6", "c4", "Nf6", "Nf3", "d5", "g3"])  # move 2's board (after d4 Nf6) never occurs
    out = _rows(clean)
    ln = _line(out, "Main")
    assert [d[3] for d in ln["decisions"]] == ["clean", "untested", "clean", "clean"]
    assert (ln["tested"], ln["deepest_tested"], ln["correct_through"]) == (3, 4, 1)


def test_a_later_success_after_a_slip_keeps_the_prefix_short_and_the_spot_early(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", CATALAN)
    # Wrong at 2 (Nf3 for c4); move 3's board can no longer occur; move 4's is reached by transposition.
    h.game(clean, 1, ["d4", "Nf6", "Nf3", "e6", "c4", "d5", "g3"])
    out = _rows(clean)
    ln = _line(out, "Main")
    assert [d[3] for d in ln["decisions"]] == ["clean", "slipped", "untested", "clean"]
    assert (ln["tested"], ln["deepest_tested"], ln["correct_through"], ln["trouble_spot"]["own_move"]) == (3, 4, 1, 2)


def test_a_line_no_game_reached_has_no_through_and_no_spot(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", CATALAN)
    h.game(clean, 1, ["e4", "e5"], color="black")  # a Black game reaches no White decision
    out = _rows(clean)
    ln = _line(out, "Main")
    # A line none of whose decisions was reached is sent compact: its count alone.
    assert ln["decisions_total"] == 4 and "decisions" not in ln and "tested" not in ln
    assert out["decisions"]["rows"] == []


# --- the pick, the scope, the parity --------------------------------------------------------


def _match_all(conn: psycopg.Connection[DictRow]) -> None:
    conn.commit()
    matching.match_player(conn, window=1000)
    conn.commit()


def test_the_pick_is_the_most_played_line_then_the_shortest_then_the_tie_break(
    clean: psycopg.Connection[DictRow],
) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Long", CATALAN + ["Be7", "Bg2", "O-O", "O-O"])
    h.line(clean, 2, 1, "Short", CATALAN + ["Be7", "Bg2"])
    h.chapter(clean, 2, 1, "Also")
    h.line(clean, 3, 2, "Also short", CATALAN + ["Be7", "Bg2"])
    h.game(clean, 1, CATALAN[:6] + ["Nc3"], days_ago=1)  # slips at move 4; attached to all three equally
    _match_all(clean)
    out = _rows(clean)
    d = _decisions(out)[("white", 4)]
    assert d["pick"] == 3  # equal games, equal length among the short ones, chapter "Also" < "Catalan" by code point
    h.game(clean, 2, CATALAN + ["Be7", "Bg2", "O-O", "O-O"], days_ago=2)  # the long line alone gets a deeper game
    _match_all(clean)
    assert _decisions(_rows(clean))[("white", 4)]["pick"] == 1


@pytest.mark.parametrize(
    ("limit", "games", "expected", "options"),
    [
        (1000, None, 500, [100, 250, 500, 1000]),
        (1000, 250, 250, [100, 250, 500, 1000]),
        (1000, 5000, 1000, [100, 250, 500, 1000]),
        (1000, 7, 500, [100, 250, 500, 1000]),
        (100, None, 100, [100]),
        (100, 500, 100, [100]),
        (50, None, 50, [50]),
        (500, None, 500, [100, 250, 500]),
    ],
)
def test_the_scope_is_the_request_clamped_to_the_window_with_options_inside_it(
    limit: int, games: int | None, expected: int, options: list[int]
) -> None:
    assert report.scope(Settings(analysis_game_limit=limit), games) == {
        "games": expected,
        "limit": limit,
        "options": options,
    }


def test_the_scope_bounds_every_number_of_the_read(clean: psycopg.Connection[DictRow]) -> None:
    _seed(clean)
    h.line(clean, 1, 1, "Main", CATALAN)
    h.game(clean, 1, CATALAN[:6] + ["Nc3"], days_ago=100)  # the oldest game, the only slip
    for gid in range(2, 53):
        h.game(clean, gid, CATALAN, days_ago=gid / 10)
    _match_all(clean)
    wide = _rows(clean, games=1000)
    narrow = _rows(clean, games=50)
    assert wide["scope"]["games"] == 1000 and narrow["scope"]["games"] == 50
    assert _decisions(wide)[("white", 4)]["slips"] == 1 and _line(wide, "Main")["left_by_me"] == 1
    assert _decisions(narrow)[("white", 4)]["slips"] == 0 and _line(narrow, "Main")["left_by_me"] == 0
    assert _decisions(narrow)[("white", 4)]["opportunities"] == 50 and _line(narrow, "Main")["games"] == 50


def _reference(
    lines: dict[int, list[str]], games: list[list[str]]
) -> dict[tuple[int, str], tuple[int, int, str | None]]:
    """Decisions by python-chess: {(move number, prescribed move): (opportunities, slips, most
    played)} for White lines rooted at the start, a board once per game at its first visit, a board
    with two continuations skipped, the most played wrong move by count then code point."""
    prescribed: dict[str, set[str]] = {}
    label: dict[str, tuple[int, str]] = {}
    for moves in lines.values():
        board = chess.Board()
        for m in moves:
            if board.turn == chess.WHITE:
                nxt = board.copy()
                nxt.push_san(m)
                prescribed.setdefault(_canonical(board.fen()), set()).add(_canonical(nxt.fen()))
                label[_canonical(board.fen())] = (board.fullmove_number, board.san(board.parse_san(m)))
            board.push_san(m)
    opps: dict[str, int] = Counter()
    slips: dict[str, Counter[str]] = {}
    for moves in games:
        board = chess.Board()
        seen: set[str] = set()
        for m in moves:
            key = _canonical(board.fen())
            if board.turn == chess.WHITE and len(prescribed.get(key, ())) == 1 and key not in seen:
                seen.add(key)
                nxt = board.copy()
                nxt.push_san(m)
                opps[key] += 1
                if _canonical(nxt.fen()) not in prescribed[key]:
                    slips.setdefault(key, Counter())[board.san(board.parse_san(m))] += 1
            board.push_san(m)
    out: dict[tuple[int, str], tuple[int, int, str | None]] = {}
    for key, n in opps.items():
        played = slips.get(key, Counter())
        most = min(played.items(), key=lambda kv: (-kv[1], kv[0]))[0] if played else None
        out[label[key]] = (n, sum(played.values()), most)
    return out


def test_the_split_board_equals_the_canonical_function_on_every_stored_fen(clean: psycopg.Connection[DictRow]) -> None:
    """The decisions statement takes a board as the FEN's first four fields by splitting; the
    identity `position_keys` hash is bq_canonical_fen's. On the FENs the importer and the ingest
    store (python-chess's, single-spaced) the two are one text — asserted over every stored FEN."""
    _seed(clean)
    h.line(clean, 1, 1, "Main", CATALAN)
    h.line(clean, 2, 1, "Other", ["d4", "Nf6", "Nf3", "d5", "c4"])
    h.game(clean, 1, ["Nf3", "d5", "d4", "Nf6", "Bg5"])
    h.game(clean, 2, ["d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7", "e3", "O-O"], color="black")
    clean.commit()
    row = clean.execute(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE array_to_string((string_to_array(f, ' '))[1:4], ' ') <> bq_canonical_fen(f)) AS differ
        FROM (SELECT x AS f FROM repertoire_lines, jsonb_array_elements_text(fen_sequence) x
              UNION ALL SELECT x FROM chess_games, jsonb_array_elements_text(fen_sequence) x) q
        """
    ).fetchone()
    assert row is not None and int(row["total"]) > 20 and int(row["differ"]) == 0


def test_the_sql_agrees_with_a_python_chess_reference_including_the_text_tie_break(
    clean: psycopg.Connection[DictRow],
) -> None:
    collation = clean.execute("SELECT datcollate FROM pg_database WHERE datname = current_database()").fetchone()
    assert collation is not None
    if str(collation["datcollate"]).upper() in ("C", "POSIX", "C.UTF-8", "C.UTF8"):
        pytest.fail(f"the scratch database collates under {collation['datcollate']}: this test proves nothing there")
    _seed(clean)
    lines = {1: CATALAN, 2: ["d4", "Nf6", "Nf3", "d5", "c4"], 3: ["d4", "d5", "Bf4", "Nf6", "e3"]}
    for lid, moves in lines.items():
        h.line(clean, lid, 1, f"L{lid}", moves)
    games = [
        CATALAN,
        ["d4", "Nf6", "c4", "e6", "Nf3", "d5", "Nc3"],  # slip at 4: Nc3
        ["d4", "Nf6", "c4", "e6", "Nf3", "d5", "a3"],  # slip at 4: a3 — tied with Nc3; "Nc3" < "a3" by code point
        ["Nf3", "d5", "d4", "Nf6", "Bg5"],  # into line 2's third board by transposition, a slip there
        ["d4", "d5", "Bf4", "Nf6", "Nf3"],  # a slip at the London's third board
        [
            "d4",
            "d5",
            "Nf3",
            "Nf6",
            "Bf4",
            "e6",
            "e3",
        ],  # a slip at the London's second decision; its third board never occurs
    ]
    for gid, moves in enumerate(games, start=1):
        h.game(clean, gid, moves, days_ago=gid)
    out = _rows(clean)
    # The rows carry no board; a (move number, move) pair is one board in this fixture.
    got = {
        (r["own_move"], r["move"]): (r["opportunities"], r["slips"], r["most_played"]) for r in out["decisions"]["rows"]
    }
    ref = _reference(lines, games)
    assert got == ref
    # The board after d4 Nf6 is the same full FEN with two continuations (c4, Nf3): contested, in neither.
    assert len(out["decisions"]["contested"]) == 1
    assert got[(4, "g3")] == (3, 2, "Nc3")


# --- what the decisions read sends: the boards a line's evidence lists, and every line's count ----------------


def _every_case(conn: psycopg.Connection[DictRow]) -> None:
    """One repertoire holding every case the read's filter must get right, both colours, every
    White line from 1.d4 (a second first move would contest the start board)."""
    _seed(conn)
    # A hole: move 2's board (after d4 Nf6) is never reached; moves 3–5 are, by transposition; moves
    # 6 and 7 are this line's alone and untested — a trailing tail no other line references.
    h.line(conn, 1, 1, "Hole", CATALAN + ["Be7", "Bg2", "O-O", "O-O", "dxc4", "Qc2"])
    # Every decision tested, the last at the line's last player ply.
    h.line(conn, 2, 1, "Tested to the end", ["d4", "d5", "c4", "e6", "Nc3"])
    # Its own boards after 1.d4 e6 untested; its fourth decision is line 1's fourth by transposition.
    h.line(conn, 3, 1, "Only by transposition", ["d4", "e6", "Nf3", "Nf6", "c4", "d5", "g3"])
    # A contested board: the same board after d4 c5 Nf3 g6 / d4 g6 Nf3 c5, two continuations.
    h.line(conn, 4, 1, "Via c5", ["d4", "c5", "Nf3", "g6", "e3"])
    h.line(conn, 5, 1, "Via g6", ["d4", "g6", "Nf3", "c5", "c3"])
    # The board after d4 c5 twice (knights out and back), then the contested board.
    h.line(conn, 6, 1, "Twice", ["d4", "c5", "Nf3", "Nc6", "Ng1", "Nb8", "Nf3", "g6", "e3"])
    h.book(conn, 2, "Defence", "black")
    h.chapter(conn, 3, 2, "Caro")
    h.line(conn, 7, 3, "Caro", ["e4", "c6", "d4", "d5", "Nc3", "dxe4", "Nxe4", "Bf5"])
    # No game reaches any of its boards: its count alone, no board of its own sent.
    h.line(conn, 8, 3, "Never reached", ["c4", "e5", "Nc3", "Nf6"])
    h.game(conn, 1, ["c4", "Nf6", "d4", "e6", "Nf3", "d5", "g3", "Be7", "Nc3"], days_ago=1)
    h.game(conn, 2, ["d4", "d5", "c4", "e6", "Nc3"], days_ago=2)
    h.game(conn, 3, ["d4", "d5", "c4", "e6", "Nf3"], days_ago=3, time_class="blitz")
    h.game(conn, 4, ["d4", "c5", "Nf3", "g6", "e3"], days_ago=4)
    h.game(conn, 5, ["e4", "c6", "d4", "d5", "e5", "Bf5"], color="black", days_ago=5)
    # Older games, enough that the last 100 leave some out: line 2's third decision slips in them.
    for gid in range(6, 106):
        h.game(conn, gid, ["d4", "d5", "c4", "e6", "e3"], days_ago=gid)
    conn.commit()


def _kept_by_rule(old: list[Any]) -> set[tuple[str, str]]:
    """The boards a line's evidence lists, from every board: shown (tested or contested) and every
    board a line reaches at or before its deepest shown occurrence."""
    shown = {(d.colour, d.board) for d in old if d.contested or d.opportunities > 0}
    last: dict[int, int] = {}
    for d in old:
        if (d.colour, d.board) in shown:
            for lid, ply, _ in d.occurrences:
                last[lid] = max(last.get(lid, -1), ply)
    return {
        (d.colour, d.board)
        for d in old
        if (d.colour, d.board) in shown or any(ply <= last.get(lid, -1) for lid, ply, _ in d.occurrences)
    }


@pytest.mark.parametrize("time_class", ["all", "focus"])
@pytest.mark.parametrize("games", [100, 1000])
def test_the_view_over_the_kept_boards_equals_the_view_over_every_board(
    clean: psycopg.Connection[DictRow], time_class: str, games: int
) -> None:
    _every_case(clean)
    got = report.decisions_view(clean, Settings(), time_class=time_class, games=games)
    want = oracle.decisions_view_every_board(clean, Settings(), time_class=time_class, games=games)
    assert got == want
    lines = got["lines"]
    assert [d[3] for d in lines[1]["decisions"]][:3] == ["slipped", "untested", "clean"]  # the hole, listed
    assert lines[1]["decisions_total"] == 7 and len(lines[1]["decisions"]) == 5  # the tail counted, not listed
    assert [d[3] for d in lines[3]["decisions"]] == ["slipped", "untested", "untested", "clean"]
    assert [d[1] for d in lines[6]["decisions"]] == [1, 2, 3, 4, 5] and lines[6]["decisions_total"] == 5
    assert lines[8] == {"decisions_total": 2}
    assert len(got["contested"]) == 1
    third = next(r for r in got["rows"] if r["lines"][0][1][0] == 2 and r["own_move"] == 3)
    # The blitz game slips there too; the window is the last 100 games, the time class within it.
    assert (
        third["slips"]
        == {("all", 100): 96, ("all", 1000): 101, ("focus", 100): 95, ("focus", 1000): 100}[(time_class, games)]
    )


def test_the_read_sends_exactly_the_listed_boards_and_every_lines_count(clean: psycopg.Connection[DictRow]) -> None:
    _every_case(clean)
    for time_class in ("all", "focus"):
        old = oracle.decisions_every_board(clean, Settings(), time_class=time_class, games=1000)
        new, totals = report._decisions(clean, Settings(), time_class, 1000)  # pyright: ignore[reportPrivateUsage]
        kept = _kept_by_rule(old)
        assert {(d.colour, d.board) for d in new} == kept
        assert len(new) == len(kept) < len(old)  # the tails were there to drop
        assert {(d.colour, d.board): d for d in new} == {
            (d.colour, d.board): d for d in old if (d.colour, d.board) in kept
        }
        assert totals == Counter(lid for d in old for lid, _, _ in d.occurrences)
    # Line 8's boards are its own and never reached: none of them is sent, its count is.
    assert not any(lid == 8 for d in new for lid, _, _ in d.occurrences) and totals[8] == 2


def test_a_lines_evidence_takes_its_count_from_the_read_not_from_the_boards_given() -> None:
    def dec(board: str, ply: int, opportunities: int, successes: int) -> Any:
        return decisions.Decision(
            colour="white",
            board=board,
            fen=board + " 0 1",
            own_move=ply // 2 + 1,
            move="d4",
            unreadable=False,
            contested=False,
            opportunities=opportunities,
            successes=successes,
            last_played=None,
            last_slip=None,
            most_played=None,
            occurrences=[(1, ply, ply // 2 + 1)],
            alternatives=[],
        )

    given = [dec("a", 0, 2, 2), dec("b", 2, 0, 0), dec("c", 4, 1, 0), dec("d", 6, 0, 0)]  # one past the last shown
    ev = decisions.line_evidence(1, given, 9)
    assert ev["decisions_total"] == 9
    assert [r[3] for r in ev["decisions"]] == ["clean", "untested", "slipped"]
    assert (ev["tested"], ev["deepest_tested"], ev["correct_through"]) == (2, 3, 1)
    assert decisions.line_evidence(1, given[:3], 9) == ev  # the board past the last shown changes nothing
