"""SAN normalisation, and move identity that does not depend on notation.

Two things live here because they are the same problem seen twice.

`normalize_san` strips what is decoration rather than move: digit-zero castles
(`0-0`) become letter-O castles (`O-O`), trailing `+`/`#` and the annotation
glyphs `!`, `?`, `!!`, `??`, `!?`, `?!` are removed, looping until stable so
`Bxf7+!` and `Bxf7!+` both reduce to `Bxf7`. python-chess raises on an
annotated token, so normalisation happens before every `parse_san`/`push_san`,
not only before a comparison.

`resolves_to_same_move` answers whether two SAN strings mean the same move on a
given board by resolving both to `chess.Move` objects. SAN is not canonical: a
course-imported line can carry an over-disambiguated `Nd4e2` where python-chess
would write `Nde2`. Comparing the strings rejected correct solutions; comparing
the parsed moves compares what the move actually is, including promotion, since
`Move.__eq__` covers from square, to square and promotion piece.

`ui/src/utils/chess.ts` carries the mirror of `normalize_san` for the board, and
tests/test_san.py pins the two against one table of cases.
"""

from __future__ import annotations

import re

import chess

_DECORATION_CHECK_MATE = re.compile(r"[+#]$")
_DECORATION_ANNOTATION = re.compile(r"(\?\?|!!|\?!|!\?|\?|!)$")


def normalize_san(move: str) -> str:
    """Canonical form of a SAN token. Pure; malformed input round-trips unchanged."""
    out = move.strip()
    while True:
        previous = out
        out = _DECORATION_CHECK_MATE.sub("", out)
        out = _DECORATION_ANNOTATION.sub("", out)
        if out == previous:
            break
    # Whole-token only: never rewrite the prefix of a longer token.
    if out == "0-0-0":
        return "O-O-O"
    if out == "0-0":
        return "O-O"
    return out


def parse(board: chess.Board, move: str) -> chess.Move | None:
    """The move a SAN token means on this board, or None if it is not legal here."""
    try:
        return board.parse_san(normalize_san(move))
    except (ValueError, AssertionError):
        return None


def resolves_to_same_move(board: chess.Board, a: str, b: str) -> bool:
    """True iff two SAN tokens are the same move on this board. Notation-independent:
    an over-disambiguated token and its canonical form compare equal, and a promotion
    to a different piece does not."""
    move_a = parse(board, a)
    return move_a is not None and move_a == parse(board, b)
