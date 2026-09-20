"""Puzzles from blunders the player keeps making.

A board qualifies when the player blundered there in at least
`blunder_puzzle_min_occurrences` *distinct* games inside the blunder window
(`blunders_default_last_n_games`) — a
position reached twice in one game is one game, not two — and when the tagger knows
what the tactic was. That second half is the certainty gate: the blunder's themes must
overlap the five the tagger detects geometrically, must not be `mate` (missed mates are
their own generator, and a stronger lesson), and the engine must have left a best move
and a principal variation behind. A positional or untagged blunder never becomes a
puzzle, because nobody could say what the right answer demonstrates.

The solution is a prefix of that stored PV. Any prefix ending on a player move is
engine-endorsed, so truncation can only pick a worse *teaching* stopping point, never a
wrong line. It cuts as soon as the point is cashed — a capture onto a square the tactic
won — or once the material gain has survived the opponent's reply, or at the cap.

Boards drop out for ordinary reasons: the recurrence ages out of the window, the player
dismisses the position, or the repertoire grows to cover it. A repertoire line that
already prescribes a move at that board, for that colour, makes the puzzle redundant —
the player is being taught the position twice — so it suppresses generation here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, LiteralString, cast

import chess
from psycopg import Connection

from core.analysis.motifs import TACTICAL_THEMES, won_target_squares
from core.chess.eligibility import analysable_sql, evidence_sql
from core.chess.san import parse as parse_san
from core.constants import PLAYER_ID
from core.puzzles.generate import _state
from core.puzzles.generate._write import NewPuzzle, create, deactivate
from core.settings import Settings

_PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _material(board: chess.Board, player_is_white: bool) -> int:
    """Simple material balance from the player's point of view."""
    total = 0
    for piece_type, value in _PIECE_VALUES.items():
        total += value * (len(board.pieces(piece_type, chess.WHITE)) - len(board.pieces(piece_type, chess.BLACK)))
    return total if player_is_white else -total


def truncate_solution(fen: str, best_line: str | None, max_player_plies: int) -> list[str] | None:
    """The teaching prefix of an engine PV, always ending on a player move.

    Cuts after the first player move that captures onto a square the tactic won ("the
    point is cashed"), or after an opponent reply that leaves the player a piece up (so
    a capture that gets recaptured does not end the puzzle early), or at the cap, or
    when the PV runs out. None when the PV is empty or does not replay.
    """
    tokens = (best_line or "").split()
    if not tokens:
        return None
    try:
        board = chess.Board(fen)
    except (ValueError, AssertionError):
        return None
    best_move = parse_san(board, tokens[0])
    if best_move is None:
        return None
    player_is_white = board.turn
    targets = won_target_squares(board, best_move)
    start_material = _material(board, player_is_white)

    solution: list[str] = []
    player_plies = 0
    index = 0
    while index < len(tokens):
        move = parse_san(board, tokens[index])
        if move is None:
            break  # the stored PV stops replaying; keep what did
        if index % 2 == 0:
            cashed = board.is_capture(move) and move.to_square in targets
            solution.append(tokens[index])
            board.push(move)
            player_plies += 1
            if cashed or player_plies >= max_player_plies:
                break
        else:
            board.push(move)
            if _material(board, player_is_white) - start_material >= 1:
                break  # the gain survived the reply; the solution already ends correctly
            if index + 1 >= len(tokens):
                break  # no player move follows, so do not append the reply
            solution.append(tokens[index])
        index += 1

    # The loop never appends a trailing opponent move; this keeps that true if it changes.
    if solution and len(solution) % 2 == 0:
        solution.pop()
    return solution or None


def _worklist_sql(focus: str) -> LiteralString:
    """The worklist. Built per call because the evidence predicate depends on the
    time-class focus, which is a setting, not a parameter."""
    return cast(
        LiteralString,
        f"""
WITH windowed AS (
    SELECT pg.chess_game_id, cg.time_class
    FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
    WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")}
    ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC
    LIMIT %(window)s
),
evidence AS (
    SELECT chess_game_id FROM windowed w WHERE {evidence_sql("w", focus)}
),
counts AS (
    SELECT b.canonical_fen, count(DISTINCT b.chess_game_id) AS games
    FROM blunders b JOIN evidence e ON e.chess_game_id = b.chess_game_id
    WHERE b.player_id = %(pid)s
    GROUP BY b.canonical_fen
),
candidates AS (
    SELECT DISTINCT ON (b.canonical_fen)
           b.canonical_fen, b.fen, b.themes, b.best_move, b.best_line, b.centipawn_loss
    FROM blunders b
    JOIN evidence e ON e.chess_game_id = b.chess_game_id
    JOIN counts c ON c.canonical_fen = b.canonical_fen AND c.games >= %(min_occurrences)s
    WHERE b.player_id = %(pid)s
      AND b.themes && %(tactical)s::text[]
      AND NOT ('mate' = ANY(b.themes))
      AND b.best_move IS NOT NULL
      AND b.best_line IS NOT NULL
    ORDER BY b.canonical_fen, b.centipawn_loss DESC NULLS LAST, b.chess_game_id DESC, b.id DESC
),
qualifying AS (
    SELECT c.* FROM candidates c
    WHERE NOT EXISTS (
        SELECT 1 FROM dismissed_blunder_fens d
        WHERE d.player_id = %(pid)s AND d.canonical_fen = c.canonical_fen)
      AND NOT EXISTS (
        SELECT 1
        FROM repertoire_lines rl
        JOIN chapters ch ON ch.id = rl.chapter_id AND ch.active = TRUE
        JOIN books bk ON bk.id = ch.book_id AND bk.active = TRUE
                     AND bk.player_id = %(pid)s
                     AND CASE bk.color WHEN 'white' THEN 'w' WHEN 'black' THEN 'b' END
                         = split_part(c.fen, ' ', 2),
             LATERAL jsonb_array_elements_text(rl.fen_sequence) WITH ORDINALITY AS step(fen, ord)
        WHERE rl.active = TRUE
          AND step.ord < jsonb_array_length(rl.fen_sequence)
          AND bq_canonical_fen(step.fen) || ' 0 1' = c.canonical_fen)
),
{_state.ACTIVE_PUZZLES_CTE}
SELECT COALESCE(q.fen, a.fen) AS fen,
       (q.canonical_fen IS NOT NULL) AS qualifies,
       q.themes, q.best_line,
       COALESCE(a.active_class, '{_state.NONE}') AS active_class,
       a.active_puzzle_id
FROM qualifying q
FULL OUTER JOIN active_puzzles a ON a.canonical_fen = q.canonical_fen
""",
    )


@dataclass
class Stats:
    boards: int = 0
    created: int = 0
    deactivated: int = 0
    unchanged: int = 0
    skipped_stronger: int = 0
    skipped_no_solution: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "boards": self.boards,
            "created": self.created,
            "deactivated": self.deactivated,
            "unchanged": self.unchanged,
            "skipped_stronger": self.skipped_stronger,
            "skipped_no_solution": self.skipped_no_solution,
        }


def generate(conn: Connection[Any], config: Settings) -> dict[str, int]:
    """Reconcile blunder puzzles with the evidence. Idempotent; does not commit."""
    cap = min(6, max(1, config.blunder_puzzle_max_player_plies))
    with conn.cursor() as cur:
        cur.execute(
            _worklist_sql(config.time_class_focus),
            {
                "pid": PLAYER_ID,
                "window": config.blunders_default_last_n_games,
                "min_occurrences": config.blunder_puzzle_min_occurrences,
                "tactical": list(TACTICAL_THEMES),
            },
        )
        rows = cur.fetchall()

    stats = Stats(boards=len(rows))
    to_deactivate: list[int] = []
    to_create: list[NewPuzzle] = []

    for row in rows:
        active_class = str(row["active_class"])
        if not row["qualifies"]:
            # Only this generator's own rows are its to clean up.
            if active_class == _state.BLUNDER_AUTO and row["active_puzzle_id"] is not None:
                to_deactivate.append(int(row["active_puzzle_id"]))
            continue
        if active_class == _state.BLUNDER_AUTO:
            stats.unchanged += 1
            continue
        if active_class != _state.NONE and not _state.displaceable(_state.BLUNDER_AUTO, active_class):
            stats.skipped_stronger += 1
            continue
        # Prove the puzzle can be built before displacing anything: a failed truncation
        # must leave whatever is there serving, never strand the board with nothing.
        solution = truncate_solution(str(row["fen"]), row["best_line"], cap)
        if not solution:
            stats.skipped_no_solution += 1
            continue
        if row["active_puzzle_id"] is not None:
            to_deactivate.append(int(row["active_puzzle_id"]))
        themes = [t for t in TACTICAL_THEMES if t in (row["themes"] or [])]
        to_create.append(
            NewPuzzle(
                fen=str(row["fen"]),
                solution_line=solution,
                source_types=["blunder"],
                # A blunder is the player's own move, so the side to move at the
                # position is the player.
                color=str(row["fen"]).split()[1],
                themes=themes,
            )
        )

    stats.deactivated = deactivate(conn, to_deactivate)
    stats.created = len(create(conn, to_create))
    return stats.as_dict()
