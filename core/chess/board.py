"""Boards and FEN sequences. Chess960-aware, one way to build a board.

Every consumer that walks a game's moves (importers, matcher, analyser, motif
tagger) builds its board here so the Chess960 handling — `chess960=True`,
canonical X-FEN castling notation — is applied once.
"""

from __future__ import annotations

import chess


def starting_board(starting_fen: str | None, variant: str = "standard") -> chess.Board:
    """The board at move zero. Chess960 needs its start FEN and the chess960 flag
    (without the flag python-chess mis-parses castling in SAN)."""
    if variant == "chess960":
        if not starting_fen:
            raise ValueError("chess960 game requires starting_fen")
        return chess.Board(fen=starting_fen, chess960=True)
    return chess.Board()


def canonicalize_chess960_fen(fen: str) -> str:
    """Round-trip a Chess960 start FEN through python-chess so the castling field is
    X-FEN ('KQkq'), the form `Board.fen()` emits. Chess.com sends Shredder-FEN ('GBgb');
    the schema CHECK requires `starting_fen == fen_sequence[0]` byte for byte."""
    try:
        return chess.Board(fen=fen, chess960=True).fen()
    except Exception as exc:  # python-chess raises several types
        raise ValueError(f"unparseable chess960 FEN {fen!r}: {exc}") from exc


def moves_to_fen_sequence(moves: list[str], starting_fen: str | None = None, variant: str = "standard") -> list[str]:
    """FEN before each move plus the final position: len == len(moves) + 1.
    Raises ValueError on the first unparseable SAN; callers skip the game rather
    than store a partial sequence."""
    board = starting_board(starting_fen, variant)
    fens = [board.fen()]
    for ply, san in enumerate(moves):
        try:
            move = board.parse_san(san)
        except Exception as exc:
            raise ValueError(f"SAN parse failed at ply {ply} ({san!r}): {exc}") from exc
        board.push(move)
        fens.append(board.fen())
    return fens
