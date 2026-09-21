"""Puzzles the player makes by hand, from a position on one of his pages.

A hand-made puzzle is an ordinary standard puzzle tagged `custom`, which is what makes it
the strongest owner of its board: neither generator will displace it (generate/_state.py).
It is written through the generators' own `create`, so its stored positions and the
one-active-puzzle-per-board rule are theirs, and a board that already has an active puzzle
refuses a second one instead of replacing it.

Removing one is a soft delete. A hard delete would cascade its attempts away and change
"solved today" and the streaks after the fact; an inactive puzzle is a state every reader
already handles, and the board's slot is freed either way.
"""

from __future__ import annotations

from typing import Any

import chess
from psycopg import Connection

from core.chess.san import parse as parse_san
from core.constants import PLAYER_ID
from core.puzzles.generate import _write

MAX_PLIES = 30
CONTEXT_TAGS = ("blunder", "deviation", "scout")  # the page the position was taken from
CUSTOM = "custom"


class InvalidPuzzle(ValueError):
    """The position or the line cannot be played. The message is safe to show."""


class BoardTaken(Exception):
    """The board already has an active puzzle."""


def validate(fen: str, solution_line: list[str], color: str) -> list[str]:
    """The line in canonical SAN, or `InvalidPuzzle`. The line may open with the opponent's
    move, but the player has to move in it somewhere."""
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise InvalidPuzzle("the position is not a valid FEN") from exc
    if not board.is_valid():
        raise InvalidPuzzle("the position is not a legal chess position")
    if not 1 <= len(solution_line) <= MAX_PLIES:
        raise InvalidPuzzle(f"the solution must have 1 to {MAX_PLIES} moves")
    player_is_white = color == "w"
    canonical: list[str] = []
    player_moves = 0
    for index, token in enumerate(solution_line):
        move = parse_san(board, token)
        if move is None:
            raise InvalidPuzzle(f"move {index + 1} ({token[:12]}) is not legal in its position")
        player_moves += board.turn == player_is_white
        canonical.append(board.san(move))
        board.push(move)
    if player_moves == 0:
        raise InvalidPuzzle("the solution has no move for your colour")
    return canonical


def create(
    conn: Connection[Any],
    *,
    fen: str,
    solution_line: list[str],
    color: str,
    context_tags: list[str],
    title: str | None,
    description: str | None,
) -> int:
    """Create the puzzle and return its id. `custom` is always stamped here, never taken
    from the request. Raises `InvalidPuzzle` or `BoardTaken`."""
    line = validate(fen, solution_line, color)
    tags = [t for t in CONTEXT_TAGS if t in context_tags] + [CUSTOM]
    created = _write.create(
        conn,
        [
            _write.NewPuzzle(
                fen=fen, solution_line=line, source_types=tags, color=color, title=title, description=description
            )
        ],
    )
    if not created:
        raise BoardTaken
    return created[0][0]


def remove(conn: Connection[Any], puzzle_id: int) -> bool:
    """Retire a hand-made puzzle. False when there is no such active custom puzzle —
    generated and repertoire puzzles are not the player's to remove."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM puzzles WHERE id = %s AND player_id = %s AND active = TRUE"
            " AND is_repertoire = FALSE AND source_types @> ARRAY['custom'] FOR UPDATE",
            (puzzle_id, PLAYER_ID),
        )
        if cur.fetchone() is None:
            return False
    return _write.deactivate(conn, [puzzle_id]) == 1
