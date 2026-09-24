"""The repertoire reducer (core/repertoire/read.py): exact identity first, canonical grouping,
fail-closed conflicts. Pure functions over hand-built occurrence rows, no database. The
fixtures are the old system's, plus the two mutation gates it named.
"""

from __future__ import annotations

import pytest

from core.repertoire import read

FEN = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
FEN_TRANSPOSED = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 4 4"


def cand(
    *,
    line_id: int,
    expected_move,
    occurrence_fen: str | None = FEN_TRANSPOSED,
    book="Book A",
    chapter="Chapter A",
    line_name="Line A",
    line_ply: int = 4,
    line_moves=None,
) -> dict:
    """One occurrence row in the shape get_scout_position_rep_lines returns."""
    if line_moves is None:
        line_moves = ["e4", "e5", "Nf3", "Nc6"] + ([expected_move] if expected_move is not None else [])
    return {
        "book": book,
        "chapter": chapter,
        "line_name": line_name,
        "line_id": line_id,
        "expected_move": expected_move,
        "occurrence_fen": occurrence_fen,
        "line_moves": line_moves,
        "line_ply": line_ply,
    }


def test_f40_exact_raw_fen_identity_is_match():
    """exact raw-FEN identity → status='match', transposed=False.

    Mutation: remove `occurrence_fen` from the candidate contract and exact
    identity becomes uncomputable, so this must FAIL.
    """
    entry = read.project_ply(FEN, [cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN)])
    assert entry["status"] == read.STATUS_MATCH
    assert entry["transposed"] is False
    assert entry["book_move"] == "Nf3"
    assert entry["plan"][0] == "Nf3"
    assert entry["conflict"] is None


def test_f41_board_only_agreement_is_agree_and_transposed():
    """board match only, candidates agree → 'agree', transposed=True."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3"),
            cand(line_id=2, expected_move="Nf3", line_name="Line B"),
        ],
    )
    assert entry["status"] == read.STATUS_AGREE
    assert entry["transposed"] is True
    assert entry["book_move"] == "Nf3"
    assert entry["more_lines"] == 1


def test_f46_null_occurrence_fen_is_ineligible_for_exact_but_still_compared():
    """a candidate with NULL occurrence_fen (short/malformed line
    fen_sequence) is ineligible for the exact branch and never salvaged, but it
    still participates in the agree/conflict comparison, because its
    expected_move is independently sourced."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", occurrence_fen=None),
            cand(line_id=2, expected_move="Nc3", occurrence_fen=None, line_name="Line B"),
        ],
    )
    assert entry["status"] == read.STATUS_CONFLICT
    assert {g["move"] for g in entry["conflict"]} == {"Nf3", "Nc3"}


def test_f47_exact_tie_across_two_books_terminates_and_is_stable():
    """an exact tie across two books terminates on the tie-break and the
    result is stable across repeated runs and across input order."""
    a = cand(line_id=9, expected_move="Nf3", occurrence_fen=FEN, book="Alpha")
    b = cand(line_id=2, expected_move="Nf3", occurrence_fen=FEN, book="Beta")
    first = read.project_ply(FEN, [a, b])
    second = read.project_ply(FEN, [b, a])
    assert first["book"] == "Alpha" == second["book"]
    assert first == second


def test_f44_one_line_containing_the_same_board_twice_terminates_on_line_ply():
    """ONE line contains the same canonical board TWICE. Every
    tie-break key up to and including repertoire_lines.id ties across the two
    occurrence rows, so only `line_ply` can terminate the selection.

    Mutation: drop `line_ply` from the tie-break and selection becomes
    input-order dependent — this must FAIL. The plan and
    `transposed` must come from the SELECTED occurrence, and the result must be
    identical whichever order the rows arrive in.
    """
    early = cand(
        line_id=7,
        expected_move="Nf3",
        occurrence_fen=FEN,
        line_ply=4,
        line_moves=["e4", "e5", "Nf3", "Nc6", "Nf3", "d4"],
    )
    late = cand(line_id=7, expected_move="d4", occurrence_fen=FEN, line_ply=12, line_moves=["e4"] * 12 + ["d4", "exd4"])
    forward = read.project_ply(FEN, [early, late])
    reversed_ = read.project_ply(FEN, [late, early])
    assert forward == reversed_
    assert forward["line_ply"] == 4
    assert forward["plan"][0] == "Nf3"
    assert forward["more_lines"] == 0


def test_f121_f122_exact_terminal_is_end_of_line():
    """the game reaches the final position of an active line →
    'end_of_line', book_move None, plan [], and the book/chapter/line still
    named. Exact ⇒ transposed=False."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move=None, occurrence_fen=FEN, line_ply=4, line_moves=["e4", "e5", "Nf3", "Nc6"]),
        ],
    )
    assert entry["status"] == read.STATUS_END_OF_LINE
    assert entry["transposed"] is False
    assert entry["book_move"] is None
    assert entry["plan"] == []
    assert entry["line_name"] == "Line A"


def test_f123_board_only_terminal_is_end_of_line_transposed():
    """board-only terminal → 'end_of_line', transposed=True."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move=None, line_ply=4, line_moves=["e4", "e5", "Nf3", "Nc6"]),
        ],
    )
    assert entry["status"] == read.STATUS_END_OF_LINE
    assert entry["transposed"] is True
    assert entry["book_move"] is None


def test_f124_exact_terminal_beats_a_transposing_continuation():
    """the game follows line A EXACTLY to A's
    end, while line B merely TRANSPOSES onto the board and continues.

    A wins on exact identity: the answer is 'end_of_line' naming A, with B
    counted in more_lines. Presenting B's move here is a FAIL — it would tell
    the player their prepared line prescribes a move when it actually ends.

    Mutation: move the terminal/continuing partition ahead of the exact test and
    this must FAIL. No other leg detects that reordering.
    """
    a = cand(
        line_id=1,
        expected_move=None,
        occurrence_fen=FEN,
        line_ply=4,
        book="A book",
        line_name="A line",
        line_moves=["e4", "e5", "Nf3", "Nc6"],
    )
    b = cand(line_id=2, expected_move="Bc4", line_ply=4, book="B book", line_name="B line")
    entry = read.project_ply(FEN, [a, b])
    assert entry["status"] == read.STATUS_END_OF_LINE
    assert entry["line_name"] == "A line"
    assert entry["book_move"] is None
    assert entry["more_lines"] == 1


def test_f125_move_set_of_null_alone_is_never_agree():
    """a candidate move set of {NULL} alone must never read as 'agree'.
    A single-value test over the raw set would have been satisfied by {NULL} and
    then promised a book_move that does not exist."""
    entry = read.project_ply(FEN, [cand(line_id=1, expected_move=None)])
    assert entry["status"] != read.STATUS_AGREE
    assert entry["status"] == read.STATUS_END_OF_LINE
    assert entry["book_move"] is None


def test_f126_null_plus_move_is_never_conflict():
    """{NULL, 'Nf3'} must never read as 'conflict'. Conflict is computed
    over CONTINUING candidates only; a terminal occurrence has no move to
    disagree with, so including it would manufacture a conflict at a position
    that is not ambiguous at all.

    Mutation: compute conflict over continuing ∪ terminal → this FAILs.
    """
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move=None),
            cand(line_id=2, expected_move="Nf3", line_name="Line B"),
        ],
    )
    assert entry["status"] == read.STATUS_AGREE
    assert entry["book_move"] == "Nf3"
    assert entry["more_lines"] == 1


def test_f127_continuing_is_preferred_within_an_exact_set():
    """an exact set containing BOTH a continuing and a terminal
    occurrence selects the continuing one → 'match'. Both satisfy identity and
    only one carries a next move.

    Mutation: prefer the terminal occurrence within an exact set → FAILs.
    """
    terminal = cand(
        line_id=1, expected_move=None, occurrence_fen=FEN, book="A book", line_moves=["e4", "e5", "Nf3", "Nc6"]
    )
    continuing = cand(line_id=2, expected_move="Bc4", occurrence_fen=FEN, book="Z book", line_name="Line B")
    entry = read.project_ply(FEN, [terminal, continuing])
    assert entry["status"] == read.STATUS_MATCH
    assert entry["book_move"] == "Bc4"
    assert entry["line_name"] == "Line B"


def test_f128_repeated_terminal_board_within_one_line():
    """one line whose terminal board repeats: two occurrence rows, one
    line, deterministic selection on line_ply, more_lines 0."""
    first = cand(line_id=3, expected_move=None, occurrence_fen=FEN, line_ply=4, line_moves=["e4", "e5", "Nf3", "Nc6"])
    second = cand(line_id=3, expected_move=None, occurrence_fen=FEN, line_ply=8, line_moves=["e4", "e5", "Nf3", "Nc6"])
    entry = read.project_ply(FEN, [second, first])
    assert entry["status"] == read.STATUS_END_OF_LINE
    assert entry["line_ply"] == 4
    assert entry["more_lines"] == 0


def test_f42_f151_three_lines_three_distinct_moves_render_three_rows():
    """three lines, THREE distinct moves → three groups, none
    truncated, nothing selected, transposed null."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", line_name="L1"),
            cand(line_id=2, expected_move="Nc3", line_name="L2"),
            cand(line_id=3, expected_move="Bc4", line_name="L3"),
        ],
    )
    assert entry["status"] == read.STATUS_CONFLICT
    assert entry["transposed"] is None
    assert entry["book_move"] is None
    assert entry["plan"] == []
    assert len(entry["conflict"]) == 3
    assert {g["move"] for g in entry["conflict"]} == {"Nf3", "Nc3", "Bc4"}


def test_f150_lines_sharing_a_move_collapse_into_one_row():
    """three lines, TWO distinct moves. The two lines sharing a move
    collapse into ONE row carrying `+1 more`; the disagreement is between
    MOVES, so a move is the unit."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", line_name="L1"),
            cand(line_id=2, expected_move="Nf3", line_name="L2"),
            cand(line_id=3, expected_move="Nc3", line_name="L3"),
        ],
    )
    assert len(entry["conflict"]) == 2
    groups = {g["move"]: g for g in entry["conflict"]}
    assert groups["Nf3"]["more_lines"] == 1
    assert groups["Nc3"]["more_lines"] == 0


def test_f156_group_more_lines_counts_distinct_lines_not_occurrence_rows():
    """one line reaching the board twice with the SAME move contributes
    1 to a group's more_lines, not 2.

    Mutation: count occurrence rows instead of distinct lines → FAILs.
    """
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", line_ply=4, line_name="L1"),
            cand(line_id=1, expected_move="Nf3", line_ply=9, line_name="L1"),
            cand(line_id=2, expected_move="Nc3", line_name="L2"),
        ],
    )
    groups = {g["move"]: g for g in entry["conflict"]}
    assert groups["Nf3"]["more_lines"] == 0


def test_f152_f155_conflict_ordering_is_stable_and_input_order_independent():
    """group ordering is deterministic (the tie-break on
    each group's representative) and identical across runs and input orders, so
    the array the client renders unaltered cannot reshuffle."""
    rows = [
        cand(line_id=3, expected_move="Bc4", book="C book", line_name="L3"),
        cand(line_id=1, expected_move="Nf3", book="A book", line_name="L1"),
        cand(line_id=2, expected_move="Nc3", book="B book", line_name="L2"),
    ]
    forward = read.project_ply(FEN, rows)
    reversed_ = read.project_ply(FEN, list(reversed(rows)))
    assert forward == reversed_
    assert [g["move"] for g in forward["conflict"]] == ["Nf3", "Nc3", "Bc4"]


def test_f157_same_book_and_chapter_different_moves_still_two_groups():
    """two lines in the SAME book and chapter prescribing different
    moves → two groups, both named."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", line_name="Main"),
            cand(line_id=2, expected_move="Nc3", line_name="Sideline"),
        ],
    )
    assert len(entry["conflict"]) == 2
    assert {g["line_name"] for g in entry["conflict"]} == {"Main", "Sideline"}


def test_f159_a_single_line_can_supply_every_group():
    """ONE line reaches the same canonical board
    TWICE with different next moves, and there is no exact raw-FEN match.

    Result: TWO groups, the SAME line_name named in both, `more_lines: 0` in
    both, groups NOT collapsed. This is the data on which any headline claiming
    multiple lines — "two of your lines", "both", "your lines disagree" — is
    flatly FALSE, which is why the copy pins the neutral "Your prep has different
    moves here." and the copy asserts the headline claims neither a count nor a
    source.
    """
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=5, expected_move="Nf3", line_ply=4, line_name="Repeater"),
            cand(line_id=5, expected_move="d4", line_ply=10, line_name="Repeater"),
        ],
    )
    assert entry["status"] == read.STATUS_CONFLICT
    assert len(entry["conflict"]) == 2
    assert [g["line_name"] for g in entry["conflict"]] == ["Repeater", "Repeater"]
    assert all(g["more_lines"] == 0 for g in entry["conflict"])


@pytest.mark.parametrize("n", [2, 3, 7, 20])
def test_f153_server_half_every_distinct_move_becomes_a_group(n):
    """parametric over N ∈ {2, 3, 7, 20}: the number of
    groups equals the number of distinct canonical moves at EVERY N, with no cap
    and no truncation. Deliberately includes values above six so that no single
    number can be mistaken for a maximum.

    The client half of this case (rendered_rows === conflict.length, reachability,
    no ancestor clamp) lives in the UI suite; this leg proves the SERVER never
    truncates, which is the half a render cap would hide.
    """
    wide = "R6R/3Q4/1Q4Q1/4Q3/2Q4Q/Q4Q2/pp1Q4/kBNN1KB1 w - - 0 1"
    import chess

    board = chess.Board(wide)
    sans = sorted({board.san(m) for m in board.legal_moves})
    assert len(sans) >= n
    rows = [
        cand(
            line_id=i,
            expected_move=sans[i],
            line_name=f"L{i}",
            occurrence_fen="8/8/8/8/8/8/8/K6k w - - 9 9",
            book=f"Book {i:03d}",
            line_moves=["e4", sans[i]],
            line_ply=1,
        )
        for i in range(n)
    ]
    entry = read.project_ply(wide, rows)
    assert entry["status"] == read.STATUS_CONFLICT
    assert len(entry["conflict"]) == n


def test_f168_group_count_never_exceeds_the_positions_legal_move_count():
    """the chess-domain bound. Because grouping runs on CANONICAL SAN in
    the matched position, a group is one distinct legal move there, so the array
    cannot exceed that position's legal-move count. (Attainability of 218 is a
    separate, executed receipt; universality is cited from the literature, never
    derived here.)"""
    import chess

    board = chess.Board(FEN)
    legal = len({board.san(m) for m in board.legal_moves})
    rows = [
        cand(line_id=i, expected_move=m, line_name=f"L{i}")
        for i, m in enumerate(sorted({board.san(m) for m in board.legal_moves}))
    ]
    entry = read.project_ply(FEN, rows)
    assert len(entry["conflict"]) <= legal


def test_f162_two_spellings_of_one_move_form_ONE_group():
    """two lines storing `Nf3` and `Ng1f3` are the SAME chess move.
    String equality says they disagree and manufactures a conflict out of a
    repertoire that is unanimous.

    Mutation: group on the raw `expected_move` instead of `canonical_move` →
    this must FAIL.
    """
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", line_name="L1"),
            cand(line_id=2, expected_move="Ng1f3", line_name="L2"),
        ],
    )
    assert entry["status"] == read.STATUS_AGREE
    assert entry["book_move"] == "Nf3"


def test_f163_long_algebraic_and_san_form_one_group():
    """`e4` and `e2e4` are one move. (Exercised at a position where a
    pawn push to e4 is legal.)"""
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    entry = read.project_ply(
        start,
        [
            cand(
                line_id=1, expected_move="e4", occurrence_fen=start, line_name="L1", line_moves=["e4", "e5"], line_ply=0
            ),
            cand(
                line_id=2,
                expected_move="e2e4",
                occurrence_fen=start,
                line_name="L2",
                line_moves=["e2e4", "c5"],
                line_ply=0,
            ),
        ],
    )
    assert entry["status"] == read.STATUS_MATCH
    assert entry["book_move"] == "e4"


def test_f164_unparseable_stored_move_suppresses_the_whole_ply():
    """a stored `expected_move` that will not parse → status
    'unreadable', the WHOLE ply suppressed: no book arrow, no continuation, no
    selected line."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Zx9", line_name="L1"),
            cand(line_id=2, expected_move="Nf3", line_name="L2"),
        ],
    )
    assert entry["status"] == read.STATUS_UNREADABLE
    assert entry["book_move"] is None
    assert entry["transposed"] is None
    assert entry["line_name"] is None


def test_f165_legal_looking_but_illegal_here_is_unreadable():
    """a stored move that PARSES but is illegal in this position is
    equally unreadable. `Qh5` is well-formed SAN; `Nf6` is not a legal White
    move here."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf6", line_name="L1"),
        ],
    )
    assert entry["status"] == read.STATUS_UNREADABLE


def test_f166_one_corrupt_ACTIVE_candidate_never_becomes_a_confident_match():
    """a genuine two-move conflict where ONE ACTIVE candidate is
    corrupt resolves to 'unreadable', NEVER to a confident match on the
    surviving candidate.

    Mutation: exclude the uncanonicalisable candidate and proceed → this must
    FAIL. Dropping it would silently turn a real conflict into an authoritative
    answer precisely when a second line disagreed but could not be read.
    Corrupt data must never be laundered into a trustworthy answer.
    """
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", line_name="L1"),
            cand(line_id=2, expected_move="!!broken!!", line_name="L2"),
        ],
    )
    assert entry["status"] == read.STATUS_UNREADABLE
    assert entry["book_move"] is None


def test_f169_a_transposing_corrupt_candidate_has_no_veto_over_an_exact_match():
    """line A matches EXACTLY with a valid continuation;
    line B reaches the board only by transposition and is CORRUPT.

    The answer is 'match' on A, with the book move and plan shown. `unreadable`
    here is a FAIL: it would tell the player their prep could not be read while
    the line they actually followed sat there, exact and intact.

    Mutation: move canonicalisation ABOVE the exact partition, or widen it from
    the active pool to all candidates → this must FAIL. That is the pre-R12
    terminal-partition defect one layer down — a lower-priority consideration
    evaluated ahead of exact identity — and no other leg detects it.
    """
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN, line_name="Exact"),
            cand(line_id=2, expected_move="@@@", line_name="Transposing"),
        ],
    )
    assert entry["status"] == read.STATUS_MATCH
    assert entry["book_move"] == "Nf3"
    assert entry["line_name"] == "Exact"


def test_f170_exact_terminal_survives_a_corrupt_non_exact_continuation():
    """an exact TERMINAL occurrence plus a corrupt non-exact
    continuation resolves to 'end_of_line', not 'unreadable'. The exact branch
    needs no canonicalisation at all: a terminal occurrence's expected_move is
    NULL, so there is nothing to parse and nothing that can fail."""
    entry = read.project_ply(
        FEN,
        [
            cand(
                line_id=1,
                expected_move=None,
                occurrence_fen=FEN,
                line_name="Exact",
                line_moves=["e4", "e5", "Nf3", "Nc6"],
            ),
            cand(line_id=2, expected_move="%%%", line_name="Transposing"),
        ],
    )
    assert entry["status"] == read.STATUS_END_OF_LINE
    assert entry["line_name"] == "Exact"


def test_f171_a_corrupt_peer_INSIDE_the_exact_pool_does_fail_closed():
    """the active pool DOES fail closed. A corrupt peer inside the EXACT
    CONTINUING pool can decide the active step, so it suppresses the ply.

    This is the positive control for the case above: without it, the canonical-grouping check
    would pass on an implementation that had simply stopped canonicalising.
    """
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN, line_name="L1"),
            cand(line_id=2, expected_move="###", occurrence_fen=FEN, line_name="L2"),
        ],
    )
    assert entry["status"] == read.STATUS_UNREADABLE


def test_f172_a_corrupt_non_exact_candidate_still_counts_toward_more_lines():
    """a corrupt candidate OUTSIDE the active pool is inert: it neither
    suppresses nor contributes a move, but its line_id is still counted in
    more_lines. Counting a line does not require reading its move."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN, line_name="L1"),
            cand(line_id=2, expected_move="&&&", line_name="L2"),
        ],
    )
    assert entry["status"] == read.STATUS_MATCH
    assert entry["more_lines"] == 1


def test_f167_book_move_is_the_canonical_string_everywhere():
    """`book_move` on the wire, the value displayed, and the value
    snapshotted into learn_commits.book_move are the SAME canonical string by
    construction: the projection emits `canonical_move` and nothing re-derives
    it downstream."""
    entry = read.project_ply(
        FEN,
        [
            cand(line_id=1, expected_move="Ng1f3", occurrence_fen=FEN),
        ],
    )
    assert entry["book_move"] == "Nf3"


def test_f71_no_coverage_is_an_explicit_none_entry_not_an_absent_key():
    """an uncovered ply carries status 'none' EXPLICITLY, with
    transposed null. "Not in your repertoire" is information, not an absent
    panel; and null is not a falsy false."""
    entry = read.project_ply(FEN, [])
    assert entry["status"] == read.STATUS_NONE
    assert entry["transposed"] is None
    assert entry["book_move"] is None
    assert entry["conflict"] is None
    assert entry["plan"] == []


def test_every_entry_carries_the_full_key_set():
    """Every status returns the SAME key set — a client that had to test for key
    presence could not tell 'not computed' from 'no coverage'."""
    expected = {
        "status",
        "book_move",
        "book",
        "chapter",
        "line_name",
        "line_ply",
        "plan",
        "more_lines",
        "transposed",
        "conflict",
    }
    pools = [
        [],
        [cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN)],
        [cand(line_id=1, expected_move="Nf3")],
        [cand(line_id=1, expected_move=None, occurrence_fen=FEN)],
        [cand(line_id=1, expected_move="Nf3", line_name="a"), cand(line_id=2, expected_move="Nc3", line_name="b")],
        [cand(line_id=1, expected_move="~~~")],
    ]
    for pool in pools:
        assert set(read.project_ply(FEN, pool)) == expected


# --- singular_move: the one reduction of a rep_lines list to a move ---------------------------


def test_a_terminal_occurrence_at_row_zero_does_not_hide_the_move_in_row_one():
    pool = [
        cand(line_id=1, expected_move=None, occurrence_fen=FEN, line_ply=2),
        cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN, line_ply=4),
    ]
    assert read.singular_move(FEN, pool) == "Nf3"


def test_disagreement_yields_no_move_at_all():
    pool = [
        cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN),
        cand(line_id=2, expected_move="d4", occurrence_fen=FEN, book="Book B", line_name="Line B"),
    ]
    assert read.singular_move(FEN, pool) is None
    assert read.singular_move(FEN, []) is None


def test_two_spellings_of_one_move_across_two_lines_agree():
    pool = [
        cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN),
        cand(line_id=2, expected_move="Ng1f3", occurrence_fen=FEN, book="Book B", line_name="Line B"),
    ]
    assert read.singular_move(FEN, pool) == "Nf3"


# --- the two mutation gates the old suite named ------------------------------------------------


def test_mutation_exact_first_reordered_would_be_caught():
    """A transposed continuation must never outrank an exact terminal (the mixed exact-terminal case); and
    an exact match must never be reported as transposed."""
    pool = [
        cand(line_id=1, expected_move=None, occurrence_fen=FEN, line_ply=4),
        cand(line_id=2, expected_move="Nf3", occurrence_fen=FEN_TRANSPOSED, book="Book B"),
    ]
    out = read.project_ply(FEN, pool)
    assert (out["status"], out["transposed"]) == (read.STATUS_END_OF_LINE, False)
    out = read.project_ply(FEN, [cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN)])
    assert (out["status"], out["transposed"], out["book_move"]) == (read.STATUS_MATCH, False, "Nf3")


def test_mutation_grouping_by_raw_token_would_be_caught():
    """Grouping conflicts by the stored spelling instead of the canonical move would report
    two groups for one move."""
    pool = [
        cand(line_id=1, expected_move="Nf3", occurrence_fen=FEN),
        cand(line_id=2, expected_move="Ng1f3", occurrence_fen=FEN, book="Book B"),
        cand(line_id=3, expected_move="d4", occurrence_fen=FEN, book="Book C"),
    ]
    out = read.project_ply(FEN, pool)
    assert out["status"] == read.STATUS_CONFLICT
    assert [g["move"] for g in out["conflict"]] == ["Nf3", "d4"]
    assert out["conflict"][0]["more_lines"] == 1
