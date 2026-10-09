"""Analysis: classification and the per-game walk against a scripted engine
(the best-move gate, the miss gate, ply_analysis alignment), then the database
side — the worklist resolves window, depth, engine and the Chess960 rule in one
query, and save_analysis writes blunders, motif events and the per-ply series
under one precedence: the current engine replaces any other engine's result at
any depth; within the same engine, depth is monotonic."""

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
from core.constants import PLAYER_ID, STOCKFISH_DEPTH, STOCKFISH_STAMP
from core.settings import Settings
from tests.conftest import stockfish_skip

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
        (2, "standard", None, json.dumps(["d4"]), 18),  # already at depth, by this engine
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
    conn.execute("UPDATE chess_games SET analysis_engine = %s, analysis_depth = 18 WHERE id = 2", (STOCKFISH_STAMP,))
    conn.commit()


def _stamp(conn: psycopg.Connection[DictRow], gid: int, engine: str, game_depth: int, player_depth: int | None) -> None:
    """An existing analysis: the shared row's engine and depths, the player row's depth."""
    conn.execute(
        "UPDATE chess_games SET analysis_engine = %s, analysis_depth = %s, ply_analysis_depth = %s,"
        " analysis_status = 'completed' WHERE id = %s",
        (engine, game_depth, game_depth, gid),
    )
    conn.execute(
        "UPDATE player_games SET analyzed_at_depth = %s WHERE player_id = %s AND chess_game_id = %s",
        (player_depth, PLAYER_ID, gid),
    )
    conn.commit()


def test_worklist_resolves_window_depth_and_variant(clean: psycopg.Connection[DictRow]) -> None:
    _seed_games(clean)
    # the window counts analysable games only: the Chess960 game (4) never takes a slot
    assert [r["chess_game_id"] for r in worklist(clean, window=4, depth=STOCKFISH_DEPTH)] == [1, 3]
    assert [r["chess_game_id"] for r in worklist(clean, window=5, depth=STOCKFISH_DEPTH)] == [1, 3, 6]
    assert [r["chess_game_id"] for r in worklist(clean, window=6, depth=STOCKFISH_DEPTH, game_ids=[3, 4])] == [3]


def test_worklist_is_engine_aware(clean: psycopg.Connection[DictRow]) -> None:
    """A game another engine analysed is pending whatever its depth; one this engine analysed
    at depth is not; a Chess960 game never is, whatever is stamped on it."""
    _seed_games(clean)
    _stamp(clean, 2, "stockfish_0", 20, 20)  # deeper, other engine → pending again
    _stamp(clean, 4, "stockfish_0", 12, 12)  # Chess960 → never
    assert [r["chess_game_id"] for r in worklist(clean, window=10)] == [1, 2, 3, 6]
    _stamp(clean, 2, STOCKFISH_STAMP, 18, 18)
    assert [r["chess_game_id"] for r in worklist(clean, window=10)] == [1, 3, 6]
    # the player row decides: a shared row the same engine holds deeper does not stand in for a
    # player row below the target (3), nor for one housekeeping reset to NULL (2)
    _stamp(clean, 3, STOCKFISH_STAMP, 20, 12)
    _stamp(clean, 2, STOCKFISH_STAMP, 18, None)
    assert [r["chess_game_id"] for r in worklist(clean, window=10)] == [1, 2, 3, 6]


def test_a_game_with_no_moves_is_stamped_and_does_not_come_back(
    clean: psycopg.Connection[DictRow], fresh_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-moves path writes no analysis; it stamps the engine as well as the depth, or the
    engine-aware worklist would list the game every hour."""
    from core import db
    from core.analysis.run import analyze_one

    _seed_games(clean)
    clean.execute("UPDATE chess_games SET moves = '[]'::jsonb WHERE id = 1")
    clean.commit()
    monkeypatch.setenv("DATABASE_URL", fresh_db_url)
    db.close_pool()
    task = Task(1, "white", [], None, "standard", None, STOCKFISH_DEPTH, S, "stockfish")
    assert analyze_one(task)["ok"] is True
    db.close_pool()
    assert 1 not in [r["chess_game_id"] for r in worklist(clean, window=10)]
    g = clean.execute("SELECT analysis_status, analysis_engine FROM chess_games WHERE id = 1").fetchone()
    assert g and (g["analysis_status"], g["analysis_engine"]) == ("failed_permanent", STOCKFISH_STAMP)


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


def _count(conn: psycopg.Connection[DictRow], gid: int) -> int:
    row = conn.execute("SELECT count(*) AS n FROM blunders WHERE chess_game_id = %s", (gid,)).fetchone()
    assert row
    return int(row["n"])


def _stamps(conn: psycopg.Connection[DictRow], gid: int) -> tuple[str | None, int | None, int | None, int | None]:
    g = conn.execute(
        "SELECT cg.analysis_engine, cg.analysis_depth, cg.ply_analysis_depth, pg.analyzed_at_depth"
        " FROM chess_games cg JOIN player_games pg ON pg.chess_game_id = cg.id WHERE cg.id = %s",
        (gid,),
    ).fetchone()
    assert g
    return g["analysis_engine"], g["analysis_depth"], g["ply_analysis_depth"], g["analyzed_at_depth"]


def test_save_analysis_replaces_another_engine_at_any_depth(clean: psycopg.Connection[DictRow]) -> None:
    """The precedence, on populated rows. Another engine's deeper analysis (the old database
    carried games at depth 20) is replaced whole and leaves at this engine's depth; so is the
    case where only the shared row is deeper than the player row. The same engine's deeper
    result is kept and reported as not written; an equal depth rewrites."""
    conn = clean
    _seed_games(conn)
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, phase, fen, move_played, best_move, best_line,"
        " post_blunder_line, centipawn_loss, classification, opening_eco, engine_version, analysis_depth)"
        " VALUES (%s, 1, 1, 'opening', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1', 'e5', 'd5',"
        " 'd5', 'e5', 300, 'blunder', 'C20', 'stockfish_0', 20)",
        (PLAYER_ID,),
    )
    _stamp(conn, 1, "stockfish_0", 20, 20)
    assert save_analysis(conn, _task(1), _result()) is True
    conn.commit()
    assert _stamps(conn, 1) == (STOCKFISH_STAMP, STOCKFISH_DEPTH, STOCKFISH_DEPTH, STOCKFISH_DEPTH)
    b = conn.execute("SELECT engine_version, analysis_depth, ply FROM blunders WHERE chess_game_id = 1").fetchall()
    assert [(r["engine_version"], r["analysis_depth"], r["ply"]) for r in b] == [(STOCKFISH_STAMP, STOCKFISH_DEPTH, 0)]
    assert 1 not in [r["chess_game_id"] for r in worklist(conn, window=10)]

    # player row at depth, shared row deeper, other engine: the second guard admits it too
    _stamp(conn, 3, "stockfish_0", 20, 18)
    assert save_analysis(conn, _task(3), _result()) is True
    conn.commit()
    assert _stamps(conn, 3) == (STOCKFISH_STAMP, STOCKFISH_DEPTH, STOCKFISH_DEPTH, STOCKFISH_DEPTH)

    # same engine, deeper: kept, nothing written, and the stamps are untouched
    _stamp(conn, 6, STOCKFISH_STAMP, 20, 20)
    assert save_analysis(conn, _task(6), _result()) is False
    conn.commit()
    assert _stamps(conn, 6) == (STOCKFISH_STAMP, 20, 20, 20)
    assert _count(conn, 6) == 0

    # same engine, equal depth: rewritten
    assert save_analysis(conn, _task(3), _result()) is True
    conn.commit()
    assert _count(conn, 3) == 1


def test_save_analysis_refuses_a_shared_row_it_cannot_take(clean: psycopg.Connection[DictRow]) -> None:
    """The first guard reads the deeper of both rows' depths with the stamp, so the shared row's
    guard cannot refuse what it admitted; if the row still does not take the update (something
    changed it under the lock — forced here by a trigger that drops the update), the game's
    rows roll back rather than stay under the older stamp."""
    conn = clean
    _seed_games(conn)
    conn.execute("CREATE FUNCTION drop_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$")
    conn.execute("CREATE TRIGGER drop_it BEFORE UPDATE ON chess_games FOR EACH ROW EXECUTE FUNCTION drop_update()")
    conn.commit()
    try:
        with pytest.raises(RuntimeError, match="affected 0 rows"):
            save_analysis(conn, _task(1), _result())
        conn.rollback()
        assert _count(conn, 1) == 0
    finally:
        conn.execute("DROP TRIGGER drop_it ON chess_games")
        conn.execute("DROP FUNCTION drop_update()")
        conn.commit()

    # same engine, player row at depth but the shared row deeper: the player row decides, and
    # the shared row follows the analysis that is written (one analysis, one depth)
    _stamp(conn, 3, STOCKFISH_STAMP, 20, 18)
    assert save_analysis(conn, _task(3), _result()) is True
    conn.commit()
    assert _stamps(conn, 3) == (STOCKFISH_STAMP, STOCKFISH_DEPTH, STOCKFISH_DEPTH, STOCKFISH_DEPTH)


@pytest.mark.parametrize("shared_depth", [STOCKFISH_DEPTH, 20], ids=["equal", "deeper"])
def test_a_game_that_returns_to_the_window_gets_its_results_back(
    clean: psycopg.Connection[DictRow], shared_depth: int
) -> None:
    """Housekeeping deletes an aged-out game's blunders and motif events and resets the player
    row's depth, but leaves the shared row's engine and depth (another owner may keep its
    payload). When the window grows back over the game, the NULL player row is what says
    "rebuild": the shared row carrying the current engine at the target depth, or deeper, must
    not hide it from the worklist or the guard, and the rebuilt results must land."""
    from core import housekeeping

    conn = clean
    _seed_games(conn)
    for gid in (1, 3, 6):
        assert save_analysis(conn, _task(gid), _result()) is True
    conn.commit()
    conn.execute(
        "UPDATE chess_games SET analysis_depth = %s, ply_analysis_depth = %s WHERE id = 6", (shared_depth,) * 2
    )
    conn.commit()
    # the window shrinks to the three newest standard games (1, 2, 3): 6 ages out
    assert housekeeping.cleanup_analysis_beyond_window(conn, 3)["games"] == 1
    conn.commit()
    assert _count(conn, 6) == 0
    assert _stamps(conn, 6) == (STOCKFISH_STAMP, shared_depth, shared_depth, None)
    assert 6 not in [r["chess_game_id"] for r in worklist(conn, window=3)]
    # the window grows back: 6 is pending, admitted, and rebuilt whole
    assert 6 in [r["chess_game_id"] for r in worklist(conn, window=10)]
    assert save_analysis(conn, _task(6), _result()) is True
    conn.commit()
    assert _count(conn, 6) == 1
    assert _stamps(conn, 6) == (STOCKFISH_STAMP, STOCKFISH_DEPTH, STOCKFISH_DEPTH, STOCKFISH_DEPTH)
    assert 6 not in [r["chess_game_id"] for r in worklist(conn, window=10)]


class _NamedEngine:
    """What `check_version` reads of a SimpleEngine: its `id` map."""

    def __init__(self, name: str) -> None:
        self.id = {"name": name}


def test_engine_version_is_checked_against_the_stamp() -> None:
    from core.analysis.engine import check_version, engine_version
    from core.notify import OperatorError

    assert engine_version("Stockfish 19 by the Stockfish developers (see AUTHORS file)") == "19"
    assert engine_version("Stockfish 18.1") == "18"
    assert engine_version("Stockfish dev-20260101") == "dev-20260101"
    assert engine_version("Leela Chess Zero") is None
    assert engine_version("") is None
    check_version(_NamedEngine("Stockfish 19 by the Stockfish developers (see AUTHORS file)"))  # type: ignore[arg-type]
    for name in (
        "Stockfish 18 by the Stockfish developers (see AUTHORS file)",
        "Stockfish 20",
        "Fairy-Stockfish 19",
        "",
    ):
        with pytest.raises(OperatorError, match="Stockfish 19 only"):
            check_version(_NamedEngine(name))  # type: ignore[arg-type]


def test_open_engine_quits_an_engine_of_another_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused engine is closed before the error leaves: no process is left behind."""
    from core.analysis import engine as mod
    from core.notify import OperatorError

    class Fake:
        id = {"name": "Stockfish 18 by the Stockfish developers (see AUTHORS file)"}
        quit_calls = 0

        def configure(self, options: dict[str, Any]) -> None:
            raise AssertionError("configured before the version check")

        def quit(self) -> None:
            Fake.quit_calls += 1

    monkeypatch.setattr(mod.chess.engine.SimpleEngine, "popen_uci", staticmethod(lambda path: Fake()))
    with pytest.raises(OperatorError):
        mod.open_engine("stockfish")
    assert Fake.quit_calls == 1


@pytest.mark.skipif(stockfish_skip() is not None, reason=stockfish_skip() or "")
def test_real_engine_smoke() -> None:
    from core.analysis.engine import find_stockfish, open_engine

    with open_engine(find_stockfish()) as engine:
        res = analyze_game(engine, ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"], "black", S, depth=6)
    assert len(res.ply_analysis) == 8
    assert any(b.ply == 5 and b.classification == "miss" for b in res.blunders)
