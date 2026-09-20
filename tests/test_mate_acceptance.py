"""Forced mate in exactly N: what the map accepts, and what it refuses to build.

The contract these pin: every optimal line is a solve, a longer forced mate is not, the
map is the same every time it is built, and a position whose true distance is not the
stored one produces no puzzle at all.
"""

from __future__ import annotations

import chess

from core.chess.mate_acceptance import (
    MAP_VERSION,
    BuildStats,
    build_acceptance_map,
    walk_acceptance_map,
)

# Back-rank mate in one: Ra8#. Black's king is boxed in by its own pawns.
MATE_IN_ONE = "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1"

# Two rooks against a bare king, mate in two: 1.Ra7 Kb8 2.Rh8#  (and the mirror with
# the other rook). Several first moves force mate in two, so the accepted set has more
# than one member.
MATE_IN_TWO = "7k/8/8/8/8/8/R7/1R5K w - - 0 1"


def test_mate_in_one_accepts_the_mate_and_nothing_else() -> None:
    built = build_acceptance_map(MATE_IN_ONE, 1)
    assert built is not None
    assert built["v"] == MAP_VERSION and built["n"] == 1
    nodes = built["p"]
    assert isinstance(nodes, dict)
    accepted = nodes[chess.Board(MATE_IN_ONE).epd()]
    assert accepted == ["a1a8"]
    assert walk_acceptance_map(MATE_IN_ONE, built, ["Ra8#"])
    assert walk_acceptance_map(MATE_IN_ONE, built, ["Ra8"])  # the mark is decoration
    assert not walk_acceptance_map(MATE_IN_ONE, built, ["Ra7"])
    assert not walk_acceptance_map(MATE_IN_ONE, built, [])


def test_every_optimal_first_move_is_accepted() -> None:
    built = build_acceptance_map(MATE_IN_TWO, 2)
    assert built is not None
    nodes = built["p"]
    assert isinstance(nodes, dict)
    accepted = nodes[chess.Board(MATE_IN_TWO).epd()]
    assert isinstance(accepted, list)
    assert len(accepted) > 1, "this position has competing optimal first moves"
    # Each accepted first move, followed by the map's own defence, must reach a node
    # where some accepted move mates.
    for uci in accepted:
        board = chess.Board(MATE_IN_TWO)
        board.push(chess.Move.from_uci(uci))
        defences = built["d"]
        assert isinstance(defences, dict)
        board.push(chess.Move.from_uci(defences[board.epd()]))
        finishing = nodes[board.epd()]
        assert isinstance(finishing, list) and finishing


def test_a_slower_forced_mate_is_wrong() -> None:
    """The rule is mate in exactly N, so a move that still wins but takes longer fails."""
    built = build_acceptance_map(MATE_IN_TWO, 2)
    assert built is not None
    board = chess.Board(MATE_IN_TWO)
    nodes = built["p"]
    assert isinstance(nodes, dict)
    accepted = set(nodes[board.epd()])
    slower = next(m for m in board.legal_moves if m.uci() not in accepted and not board.is_capture(m))
    assert not walk_acceptance_map(MATE_IN_TWO, built, [board.san(slower)])


def test_a_wrong_stored_distance_builds_nothing() -> None:
    """If the analyser recorded a distance the exhaustive solve disagrees with, there is
    no puzzle: a map that contradicts its own `mate_in_moves` would be ungradable."""
    stats = BuildStats()
    assert build_acceptance_map(MATE_IN_ONE, 2, stats=stats) is None
    assert stats.skip_reason == "distance_mismatch"


def test_a_position_with_no_forced_mate_builds_nothing() -> None:
    stats = BuildStats()
    assert build_acceptance_map(chess.Board().fen(), 2, stats=stats) is None
    assert stats.skip_reason == "distance_mismatch"


def test_budget_and_size_limits_skip_rather_than_serve() -> None:
    small = BuildStats()
    assert build_acceptance_map(MATE_IN_TWO, 2, cap_bytes=1, stats=small) is None
    assert small.skip_reason == "oversize"
    cheap = BuildStats()
    assert build_acceptance_map(MATE_IN_TWO, 2, max_nodes=1, stats=cheap) is None
    assert cheap.skip_reason == "budget"


def test_the_map_is_the_same_every_time() -> None:
    """A rebuild has to reproduce the stored map exactly, or a regenerated puzzle would
    grade differently from the one the player saw."""
    first = build_acceptance_map(MATE_IN_TWO, 2)
    second = build_acceptance_map(MATE_IN_TWO, 2)
    assert first == second


def test_malformed_input_is_never_a_solve() -> None:
    built = build_acceptance_map(MATE_IN_ONE, 1)
    assert built is not None
    assert not walk_acceptance_map(MATE_IN_ONE, None, ["Ra8#"])
    assert not walk_acceptance_map(MATE_IN_ONE, {"p": {}, "d": {}, "n": 1}, ["Ra8#"])
    assert not walk_acceptance_map("not a fen", built, ["Ra8#"])
    assert not walk_acceptance_map(MATE_IN_ONE, built, ["Ra8#", "Ra8#"])  # wrong length
