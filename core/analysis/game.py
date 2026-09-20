"""Analyse one game with Stockfish: per-ply evals and best lines, the player's
classified errors, and the peak/final advantage.

One `analyse` per position (N+1 for N moves): the "after" result of one move
is the "before" of the next. Every call passes a fresh `game=object()`, which
makes python-chess send `ucinewgame` and clears the transposition table, so
the same game at the same depth with the same binary always gives the same
result.

Classification (player-POV centipawn loss, thresholds from settings):
  miss      >= miss_threshold and the position was contested
            (|player eval before| <= miss_contested_gate); a large loss from an
            already-decided position is dropped entirely, not downgraded
  blunder   >= blunder_threshold
  mistake   >= mistake_threshold
  inaccuracy>= inaccuracy_threshold
A move that IS the engine's best move is never an error, whatever the swing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import chess
import chess.engine

from core.chess.board import starting_board
from core.settings import Settings

MATE_SCORE = 10000
PEAK_THRESHOLD_CP = 300  # advantage that counts toward peak_advantage ...
PEAK_SUSTAINED_MOVES = 3  # ... when held for this many consecutive player moves


@dataclass(frozen=True)
class Blunder:
    ply: int
    phase: str
    fen: str
    move_played: str
    best_move: str | None
    best_line: str | None
    post_blunder_line: str | None
    centipawn_loss: int
    classification: str


@dataclass(frozen=True)
class GameAnalysis:
    blunders: list[Blunder]
    peak_advantage: int | None
    final_eval: int | None
    ply_analysis: list[dict[str, Any]]  # one per position, len == len(moves)+1 unless a move failed to parse


def classify(cp_loss: int, eval_before_white: int, player_color: str, s: Settings) -> str | None:
    player_eval = eval_before_white if player_color == "white" else -eval_before_white
    if cp_loss >= s.miss_threshold:
        return "miss" if abs(player_eval) <= s.miss_contested_gate else None
    if cp_loss >= s.blunder_threshold:
        return "blunder"
    if cp_loss >= s.mistake_threshold:
        return "mistake"
    if cp_loss >= s.inaccuracy_threshold:
        return "inaccuracy"
    return None


def phase_of(ply: int, board: chess.Board) -> str:
    if ply < 20:
        return "opening"
    pieces = sum(
        len(board.pieces(pt, color))
        for color in chess.COLORS
        for pt in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT)
    )
    return "endgame" if pieces <= 6 else "middlegame"


def pv_san(board: chess.Board, pv: list[chess.Move]) -> str | None:
    out: list[str] = []
    b = board.copy()
    for m in pv:
        try:
            out.append(b.san(m))
            b.push(m)
        except Exception:
            break
    return " ".join(out) if out else None


def _analyse(
    engine: chess.engine.SimpleEngine, board: chess.Board, depth: int
) -> tuple[int | None, int | None, list[chess.Move]]:
    info = engine.analyse(board, chess.engine.Limit(depth=depth), game=object())
    score = info.get("score")
    if score is None:
        return None, None, []
    pov = score.white()
    return pov.score(mate_score=MATE_SCORE), pov.mate(), list(info.get("pv", []))


def analyze_game(
    engine: chess.engine.SimpleEngine,
    moves: list[str],
    player_color: str,
    s: Settings,
    depth: int,
    *,
    variant: str = "standard",
    starting_fen: str | None = None,
) -> GameAnalysis:
    if not moves:
        return GameAnalysis([], None, None, [])
    board = starting_board(starting_fen, variant)
    blunders: list[Blunder] = []
    ply_analysis: list[dict[str, Any]] = []
    streak = 0
    streak_max = 0
    peak: int | None = None
    final_eval: int | None = None

    cached = False
    after_score: int | None = None
    after_mate: int | None = None
    after_pv: list[chess.Move] = []

    for ply, san in enumerate(moves):
        try:
            move = board.parse_san(san)
        except Exception:
            break
        if cached:
            score_before, mate_before, pv = after_score, after_mate, after_pv
        else:
            score_before, mate_before, pv = _analyse(engine, board, depth)
        best_move_obj = pv[0] if pv else None
        best_move_san = board.san(best_move_obj) if best_move_obj else None
        best_line = pv_san(board, pv)
        ply_analysis.append(
            {
                "ply": ply,
                "eval": score_before,
                "best_move": best_move_san,
                "best_line": best_line,
                "mate_in_moves": mate_before,
            }
        )

        board.push(move)
        after_score, after_mate, after_pv = _analyse(engine, board, depth)
        cached = True

        if score_before is None or after_score is None:
            continue
        if (ply % 2 == 0) != (player_color == "white"):
            continue

        player_eval = score_before if player_color == "white" else -score_before
        final_eval = player_eval
        if player_eval >= PEAK_THRESHOLD_CP:
            streak += 1
            streak_max = max(streak_max, player_eval)
            if streak >= PEAK_SUSTAINED_MOVES and (peak is None or streak_max > peak):
                peak = streak_max
        else:
            streak = 0
            streak_max = 0

        if best_move_obj is not None and move == best_move_obj:
            continue
        cp_loss = (score_before - after_score) if player_color == "white" else (after_score - score_before)
        cp = max(0, cp_loss)
        classification = classify(cp, score_before, player_color, s)
        if classification is None:
            continue
        board.pop()
        fen = board.fen()
        phase = phase_of(ply, board)
        post = board.copy()
        post.push(move)
        post_line = pv_san(post, after_pv)
        board.push(move)
        blunders.append(Blunder(ply, phase, fen, san, best_move_san, best_line, post_line, cp, classification))

    if cached:
        term_best = after_pv[0] if after_pv else None
        ply_analysis.append(
            {
                "ply": len(board.move_stack),
                "eval": after_score,
                "best_move": board.san(term_best) if term_best else None,
                "best_line": pv_san(board, after_pv),
                "mate_in_moves": after_mate,
            }
        )
    return GameAnalysis(blunders, peak, final_eval, ply_analysis)
