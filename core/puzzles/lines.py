"""Solution lines: replaying SAN from a FEN, and the FEN sequence a puzzle stores.

A puzzle carries both its moves and the position after each of them, because the
board and the serve path read positions, not moves. The old system computed this in
three places; here it is one function.
"""

from __future__ import annotations

import chess

from core.chess.san import parse as parse_san


def fen_sequence(fen: str, solution_line: list[str]) -> list[str]:
    """`[fen, fen after move 1, ...]`, one entry longer than the line.

    An unreplayable move stops the sequence rather than raising: the stored line is
    what it is, and a short sequence is visible downstream, where an exception in a
    bulk generator would not be.
    """
    fens = [fen]
    try:
        board = chess.Board(fen)
    except (ValueError, AssertionError):
        return fens
    for move_san in solution_line:
        move = parse_san(board, move_san)
        if move is None:
            break
        board.push(move)
        fens.append(board.fen())
    return fens


def is_mate_line(fen: str, solution_line: list[str], color: str) -> tuple[bool, int]:
    """`(the line ends in checkmate, index of the last ply the player moves on)`.

    Whether a line is a mate is decided by replaying it, not by its `source_types`:
    a delivered checkmate is a solve whatever the puzzle was generated from. The
    index is where the final-move relaxation applies — any move that mates is
    accepted there. `color` is the player's side ('w'/'b'); a repertoire line may open
    with the opponent's move, so the player's plies are found by whose turn it is.
    """
    try:
        board = chess.Board(fen)
    except (ValueError, AssertionError):
        return False, -1
    player_is_white = color == "w"
    last_player_ply = -1
    for index, move_san in enumerate(solution_line):
        move = parse_san(board, move_san)
        if move is None:
            return False, -1
        if board.turn == player_is_white:
            last_player_ply = index
        board.push(move)
    return board.is_checkmate(), last_player_ply
