"""Analysis: classification and the per-game walk against a scripted engine
(the best-move gate, the miss gate, ply_analysis alignment), then the database
side — the worklist resolves window, depth and the Chess960 rule in one query,
and save_analysis writes blunders, motif events and the per-ply series behind
the depth gate."""

from __future__ import annotations

import json
from typing import Any

import chess
import chess.engine
import psycopg
import pytest
from psycopg.rows import DictRow

from core.analysis.game import GameAnalysis, analyze_game, classify, phase_of
from core.analysis.run import Task, save_analysis, worklist
from core.constants import PLAYER_ID, STOCKFISH_DEPTH
from core.settings import Settings

S = Settings()


class ScriptedEngine:
    """Stands in for SimpleEngine: evals keyed by FEN (White POV cp), PV = one move."""

    def __init__(self, evals: dict[str, tuple[int, str | None]]) -> None:
        self.evals = evals
        self.calls = 0

    def analyse(self, board: chess.Board, limit: Any, game: Any = None) -> dict[str, Any]:
        self.calls += 1
        cp, best = self.evals[board.fen()]
        pv = [board.parse_san(best)] if best else []
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE), "pv": pv}


def _fens(moves: list[str]) -> list[str]:
    b = chess.Board()
    out = [b.fen()]
    for m in moves:
        b.push_san(m)
        out.append(b.fen())
    return out


def test_classify_thresholds_and_miss_gate() -> None:
    assert classify(30, 0, "white", S) is None
    assert classify(50, 0, "white", S) == "inaccuracy"
    assert classify(100, 0, "white", S) == "mistake"
    assert classify(200, 0, "white", S) == "blunder"
    assert classify(300, 0, "white", S) == "miss"
    assert classify(300, -250, "black", S) == "miss"  # black was +250: contested
    assert classify(900, 800, "white", S) is None  # already decided: dropped, not downgraded
    assert classify(900, 800, "black", S) is None


def test_phase() -> None:
    assert phase_of(5, chess.Board()) == "opening"
    assert phase_of(30, chess.Board()) == "middlegame"
    assert phase_of(30, chess.Board("4k3/8/8/8/8/8/8/R3K3 w - - 0 1")) == "endgame"


def test_analyze_game_walk() -> None:
    moves = ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]  # black's Nf6?? is the blunder
    fens = _fens(moves)
    evals = {
        fens[0]: (30, "e4"),
        fens[1]: (25, "e5"),
        fens[2]: (30, "Nf3"),
        fens[3]: (40, "Nc6"),
        fens[4]: (20, "Bc4"),
        fens[5]: (30, "g6"),
        fens[6]: (10000, "Qxf7#"),
        fens[7]: (10000, None),
    }
    engine = ScriptedEngine(evals)
    res = analyze_game(engine, moves, "black", S, depth=1)  # type: ignore[arg-type]
    assert engine.calls == len(moves) + 1  # rolling cache: N+1 analyses, not 2N
    assert len(res.ply_analysis) == len(moves) + 1 and [p["ply"] for p in res.ply_analysis] == list(range(8))
    assert res.ply_analysis[7]["best_move"] is None  # terminal position, no PV
    assert [(b.ply, b.classification, b.move_played) for b in res.blunders] == [(5, "miss", "Nf6")]
    b = res.blunders[0]
    assert b.centipawn_loss == 9970 and b.best_move == "g6" and b.post_blunder_line == "Qxf7#" and b.phase == "opening"
    assert b.fen == fens[5]
    assert res.final_eval == -30  # black's eval before its last move
    # white's view of the same game: Qh5 is not the engine's move but loses only 10 cp → no error
    white = analyze_game(ScriptedEngine(evals), moves, "white", S, depth=1)  # type: ignore[arg-type]
    assert white.blunders == [] and white.peak_advantage is None


def test_best_move_is_never_an_error() -> None:
    moves = ["e4", "e5"]
    fens = _fens(moves)
    evals = {fens[0]: (30, "e4"), fens[1]: (-500, "e5"), fens[2]: (0, "Nf3")}  # eval collapses but e4 WAS best
    res = analyze_game(ScriptedEngine(evals), moves, "white", S, depth=1)  # type: ignore[arg-type]
    assert res.blunders == []


# --- database side ------------------------------------------------------------------------


def _seed_games(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'p')", (PLAYER_ID,))
    rows = [
        (1, "standard", None, json.dumps(["e4", "e5"]), None),  # pending
        (2, "standard", None, json.dumps(["d4"]), 18),  # already at depth
        (3, "standard", None, json.dumps(["c4"]), 12),  # shallower → pending
        (
            4,
            "chess960",
            "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1",
            json.dumps(["e4"]),
            None,
        ),  # excluded
        (5, "standard", None, None, None),  # payload gone → excluded
        (6, "standard", None, json.dumps(["Nf3"]), None),  # oldest; falls outside a window of 5
    ]
    for gid, variant, start, moves, depth in rows:
        conn.execute(
            "INSERT INTO chess_games (id, platform, platform_game_id, played_at, moves, variant, starting_fen)"
            " VALUES (%s, 'lichess', %s, now() - make_interval(mins => %s), %s::jsonb, %s, %s)",
            (gid, f"g{gid}", gid, moves, variant, start),
        )
        conn.execute(
            "INSERT INTO player_games (player_id, chess_game_id, player_color, source, analyzed_at_depth)"
            " VALUES (%s, %s, 'white', 'lichess', %s)",
            (PLAYER_ID, gid, depth),
        )
    conn.commit()


def test_worklist_resolves_window_depth_and_variant(clean: psycopg.Connection[DictRow]) -> None:
    _seed_games(clean)
    # the window counts analysable games only: the Chess960 game (4) never takes a slot
    assert [r["chess_game_id"] for r in worklist(clean, window=4, depth=STOCKFISH_DEPTH)] == [1, 3]
    assert [r["chess_game_id"] for r in worklist(clean, window=5, depth=STOCKFISH_DEPTH)] == [1, 3, 6]
    assert [r["chess_game_id"] for r in worklist(clean, window=6, depth=STOCKFISH_DEPTH, game_ids=[3, 4])] == [3]


def _task(gid: int, depth: int = STOCKFISH_DEPTH) -> Task:
    return Task(gid, "white", ["e4", "e5"], "C20", "standard", None, depth, S, "stockfish")


def _result() -> GameAnalysis:
    from core.analysis.game import Blunder

    fens = _fens(["e4", "e5"])
    pa = [
        {"ply": 0, "eval": 30, "best_move": "d4", "best_line": "d4 d5", "mate_in_moves": None},
        {"ply": 1, "eval": -250, "best_move": "e5", "best_line": "e5", "mate_in_moves": None},
        {"ply": 2, "eval": -240, "best_move": "Nf3", "best_line": "Nf3", "mate_in_moves": None},
    ]
    return GameAnalysis([Blunder(0, "opening", fens[0], "e4", "d4", "d4 d5", "e5", 280, "blunder")], None, 30, pa)


def test_save_analysis_writes_everything_behind_the_depth_gate(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _seed_games(conn)
    assert save_analysis(conn, _task(1), _result()) is True
    conn.commit()
    b = conn.execute("SELECT * FROM blunders WHERE chess_game_id = 1").fetchall()
    assert len(b) == 1 and b[0]["classification"] == "blunder" and b[0]["analysis_depth"] == STOCKFISH_DEPTH
    assert b[0]["canonical_fen"] == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    ev = conn.execute("SELECT metric_type, theme, cp_loss FROM player_motif_events WHERE chess_game_id = 1").fetchall()
    assert [(r["metric_type"], r["theme"], r["cp_loss"]) for r in ev] == [("positional", "untagged", 280)]
    g = conn.execute(
        "SELECT analysis_status, analysis_depth, ply_analysis, final_eval FROM chess_games WHERE id = 1"
    ).fetchone()
    assert (
        g
        and g["analysis_status"] == "completed"
        and g["analysis_depth"] == STOCKFISH_DEPTH
        and len(g["ply_analysis"]) == 3
    )
    pg = conn.execute("SELECT analyzed_at_depth FROM player_games WHERE chess_game_id = 1").fetchone()
    assert pg and pg["analyzed_at_depth"] == STOCKFISH_DEPTH
    assert [r["chess_game_id"] for r in worklist(conn, window=10)] == [3, 6]  # game 1 no longer pending

    # a shallower rerun is refused whole; a same-depth rerun replaces, never duplicates
    assert save_analysis(conn, _task(1, depth=12), _result()) is False
    assert save_analysis(conn, _task(1), _result()) is True
    conn.commit()
    n = conn.execute("SELECT count(*) AS n FROM blunders WHERE chess_game_id = 1").fetchone()
    assert n and n["n"] == 1


@pytest.mark.skipif(__import__("shutil").which("stockfish") is None, reason="stockfish not installed")
def test_real_engine_smoke() -> None:
    from core.analysis.engine import find_stockfish, open_engine

    with open_engine(find_stockfish()) as engine:
        res = analyze_game(engine, ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"], "black", S, depth=6)
    assert len(res.ply_analysis) == 8
    assert any(b.ply == 5 and b.classification == "miss" for b in res.blunders)
