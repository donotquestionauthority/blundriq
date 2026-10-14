"""Recent practice evidence per repertoire line and per decision, from the attempts' move traces.

What a load proves is in its first attempt's trace (`puzzle_attempts.moves_played`, the player's
moves of the attempt), not in what the solver displayed: a trace move that is the solution's move
at that ply is a **success at that decision** (the board before it); the first trace move that is
a legal, different move is a **failure at that decision** and the trace stops there; decisions
beyond the trace's end are **untested in that load**. A trace that does not replay — empty, a
token that is not a move on its board, more moves than the line has player plies — makes the load
**unknown**: it is in no denominator. `solved` is not trusted for any of this (the attempt route
takes a `False` claim at its word without validating the trace); the First try column keeps
using it. The segment shown (`player_puzzle_exposure.presentation_ply`) plays no part.

Token equality after `normalize_san` is the fast path; on a mismatch the submitted token is parsed
on its board and compared with the expected move, because `normalize_san` keeps redundant
disambiguation (`Ngf3` is `Nf3`) — the rule `attempts.validate_line` grades by.

A **load** and its **first attempt** are `core.puzzles.stats`'s: one per `session_id` (the lowest
`attempt_seq`, then id), and a sessionless row is a load of its own. Every first attempt of the
lines is read and classified before any cap, because whether a load reached a decision is known
only afterwards; the "last five" is the display's, applied per line and, pooled over the
decision's lines, per decision — among the loads that reached it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

import chess
from psycopg import Connection

from core.chess.san import normalize_san, parse
from core.constants import PLAYER_ID
from core.puzzles.lines import fen_sequence

Row = dict[str, Any]

RECENT = 5

_FIRST_ATTEMPTS = """
WITH firsts AS (
    SELECT DISTINCT ON (p.repertoire_line_id, pa.session_id)
           p.repertoire_line_id AS line_id, pa.puzzle_id, pa.solved, pa.moves_played, pa.attempt_at, pa.id
    FROM puzzle_attempts pa JOIN puzzles p ON p.id = pa.puzzle_id
    WHERE pa.player_id = %(pid)s AND p.repertoire_line_id = ANY(%(lines)s) AND pa.session_id IS NOT NULL
    ORDER BY p.repertoire_line_id, pa.session_id, pa.attempt_seq NULLS LAST, pa.id
),
loose AS (
    SELECT p.repertoire_line_id AS line_id, pa.puzzle_id, pa.solved, pa.moves_played, pa.attempt_at, pa.id
    FROM puzzle_attempts pa JOIN puzzles p ON p.id = pa.puzzle_id
    WHERE pa.player_id = %(pid)s AND p.repertoire_line_id = ANY(%(lines)s) AND pa.session_id IS NULL
),
every AS (SELECT * FROM firsts UNION ALL SELECT * FROM loose)
SELECT line_id, puzzle_id, solved, moves_played, attempt_at, id
FROM every
ORDER BY attempt_at DESC NULLS LAST, id DESC
"""

# The puzzles' lines once each, not once per attempt: a solution is a kilobyte or two.
_PUZZLES = "SELECT id, fen, solution_line, solution_fen_sequence, color FROM puzzles WHERE id = ANY(%(ids)s)"

OK = "ok"
FAIL = "fail"
UNKNOWN = "unknown"


@dataclass
class Load:
    line_id: int
    at: datetime | None
    verdict: str  # OK | FAIL | UNKNOWN
    complete: bool  # OK and the trace reproduced every player ply of the puzzle: the whole line
    reached: list[tuple[str, bool]]  # (canonical board, success) per decision the trace reached, in order
    through: int | None  # the own-move number of the last decision the trace reproduced; None for UNKNOWN
    failed_at: int | None  # the own-move number of the failed decision; FAIL only


@dataclass
class Puzzle:
    colour: str
    fens: list[str]
    solution: list[str]
    player_plies: list[int]


def _puzzle(r: Row) -> Puzzle:
    solution = [str(m) for m in r["solution_line"]]
    fens = (
        [str(f) for f in r["solution_fen_sequence"]]
        if r["solution_fen_sequence"]
        else fen_sequence(str(r["fen"]), solution)
    )
    side = "w" if str(r["color"]) == "w" else "b"
    plies = [
        i for i in range(min(len(solution), len(fens))) if len(fens[i].split(" ")) > 1 and fens[i].split(" ")[1] == side
    ]
    return Puzzle("white" if side == "w" else "black", fens, solution, plies)


def _canonical(fen: str) -> str:
    return " ".join(fen.split(" ")[:4])


@lru_cache(maxsize=32768)
def _same_move(fen: str, token: str, expected: str) -> bool | None:
    """Whether `token` and `expected` are one move on the board `fen`; None when `token` is not a
    move there (or the board or the expected token cannot be read). A pure function of three
    strings, memoised: the same wrong answers recur from one read to the next."""
    try:
        board = chess.Board(fen)
    except ValueError:
        return None
    given = parse(board, token)
    wanted = parse(board, expected)
    if given is None or not given or wanted is None or not wanted:
        return None
    return given == wanted


def classify(
    puzzle: Puzzle, moves_played: str | None
) -> tuple[str, list[tuple[str, bool]], int | None, int | None, bool]:
    """`(verdict, reached, through, failed_at, complete)` for one trace against its puzzle;
    `complete` is the whole line reproduced — every player ply of the puzzle — decided here from
    the trace and the puzzle's own plies, never from a count or a line's decision list."""
    trace = [m.strip() for m in (moves_played or "").split(",") if m.strip()]
    if not trace or len(trace) > len(puzzle.player_plies):
        return UNKNOWN, [], None, None, False
    reached: list[tuple[str, bool]] = []
    through: int | None = None
    for j, token in enumerate(trace):
        ply = puzzle.player_plies[j]
        fen = puzzle.fens[ply]
        board_key = _canonical(fen)
        own_move = int(fen.split(" ")[5]) if len(fen.split(" ")) >= 6 else 0
        expected = puzzle.solution[ply]
        if normalize_san(token) == normalize_san(expected):
            reached.append((board_key, True))
            through = own_move
            continue
        same = _same_move(fen, token, expected)
        if same is None:
            return UNKNOWN, [], None, None, False
        if same:
            reached.append((board_key, True))
            through = own_move
            continue
        reached.append((board_key, False))
        return FAIL, reached, through, own_move, False
    return OK, reached, through, None, len(trace) == len(puzzle.player_plies)


def read(conn: Connection[Any], line_ids: list[int]) -> list[Load]:
    """Every first attempt of the lines' puzzles, classified, newest first."""
    if not line_ids:
        return []
    rows = conn.execute(_FIRST_ATTEMPTS, {"pid": PLAYER_ID, "lines": sorted(set(line_ids))}).fetchall()
    if not rows:
        return []
    ids = sorted({int(r["puzzle_id"]) for r in rows})
    puzzles = {int(p["id"]): _puzzle(p) for p in conn.execute(_PUZZLES, {"ids": ids}).fetchall()}
    out: list[Load] = []
    for r in rows:
        puzzle = puzzles.get(int(r["puzzle_id"]))
        if puzzle is None:
            continue
        verdict, reached, through, failed_at, complete = classify(puzzle, r["moves_played"])
        out.append(Load(int(r["line_id"]), r["attempt_at"], verdict, complete, reached, through, failed_at))
    return out


def recent_by_line(loads: list[Load]) -> dict[int, Row]:
    """{line_id: {"recent": [[verdict, through, failed_at, complete]] newest first (RECENT at
    most), "unknown": n}} — the strip and the count of loads whose trace did not replay."""
    out: dict[int, Row] = {}
    for ld in loads:
        row = out.setdefault(ld.line_id, {"recent": [], "unknown": 0})
        if ld.verdict == UNKNOWN:
            row["unknown"] += 1
        if len(row["recent"]) < RECENT:
            row["recent"].append([ld.verdict, ld.through, ld.failed_at, ld.complete])
    return out


def by_decision(loads: list[Load], colour_of_line: dict[int, str]) -> dict[tuple[str, str], Row]:
    """{(colour, board): {"failed": k, "of": n}} over the newest RECENT loads that reached the
    board, pooled over every line through it. A load counts once per board however many times
    its trace reached it (a line can reach a board twice): a failure there, which ends the trace,
    is the load's result at the board; otherwise it is a success."""
    hits: dict[tuple[str, str], list[bool]] = {}
    for ld in loads:  # newest first
        colour = colour_of_line.get(ld.line_id)
        if colour is None:
            continue
        once: dict[str, bool] = {}
        for board, ok in ld.reached:
            once[board] = once.get(board, True) and ok
        for board, ok in once.items():
            bucket = hits.setdefault((colour, board), [])
            if len(bucket) < RECENT:
                bucket.append(ok)
    return {k: {"failed": sum(1 for ok in v if not ok), "of": len(v)} for k, v in hits.items()}
