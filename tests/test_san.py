"""SAN normalisation and notation-independent move identity.

The browser needs the same rules when it decides whether a move the player made is the
move the solution expects. That copy arrives with the Practice page, and its tests use
the table below so the two cannot drift.
"""

from __future__ import annotations

import chess
import pytest

from core.chess.san import normalize_san, parse, resolves_to_same_move

START = chess.Board()

CASES = [
    ("e4", "e4"),
    ("  e4  ", "e4"),
    ("Bxf7+", "Bxf7"),
    ("Qh5#", "Qh5"),
    ("Bxf7+!", "Bxf7"),
    ("Bxf7!+", "Bxf7"),
    ("Nf3!?", "Nf3"),
    ("Nf3?!", "Nf3"),
    ("Nf3??", "Nf3"),
    ("Nf3!!", "Nf3"),
    ("0-0", "O-O"),
    ("0-0-0", "O-O-O"),
    ("O-O", "O-O"),
    ("0-0+", "O-O"),
    ("0-0abc", "0-0abc"),  # whole-token only: never rewrite part of a longer token
    ("", ""),
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_normalize_san(raw: str, expected: str) -> None:
    assert normalize_san(raw) == expected


def test_parse_accepts_an_annotated_token() -> None:
    """python-chess raises on a glyph, which is why normalisation happens before parsing
    and not only before comparing."""
    board = chess.Board("rnbqk1nr/pppp1ppp/8/2b1p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 0 1")
    with pytest.raises(ValueError):
        board.parse_san("Bxf7+!")
    assert parse(board, "Bxf7+!") == chess.Move.from_uci("c4f7")


def test_parse_returns_none_for_an_illegal_move() -> None:
    assert parse(START, "e5") is None
    assert parse(START, "not a move") is None


def test_over_disambiguated_token_is_the_same_move() -> None:
    """A course export can spell a move with more disambiguation than python-chess uses.
    Comparing the strings rejected correct solutions; comparing the moves does not."""
    board = chess.Board("4k3/8/8/8/3N1N2/8/8/4K3 w - - 0 1")
    assert board.san(chess.Move.from_uci("d4e2")) == "Nde2"
    assert resolves_to_same_move(board, "Nd4e2", "Nde2")
    assert not resolves_to_same_move(board, "Nfe2", "Nde2")


def test_promotion_piece_is_part_of_the_move() -> None:
    board = chess.Board("8/4P3/8/8/8/8/8/4K2k w - - 0 1")
    assert resolves_to_same_move(board, "e8=Q", "e8=Q")
    assert not resolves_to_same_move(board, "e8=Q", "e8=N")
