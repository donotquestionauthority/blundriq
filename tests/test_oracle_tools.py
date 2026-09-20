"""The live-engine oracle check cannot pass on retained rows: it fails when a worker
fails, when the worklist omits a requested game, and when a game ends without a
fresh analysis; it passes only when every requested game was analysed afresh."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core import oracle as q
from core.analysis.game import GameAnalysis
from core.analysis.run import Task, save_analysis
from core.constants import PLAYER_ID, STOCKFISH_DEPTH
from core.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "oracle"))
import diff_analysis  # noqa: E402  # pyright: ignore[reportMissingImports]


def _seed(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'p')", (PLAYER_ID,))
    for gid in (1, 2):
        conn.execute(
            "INSERT INTO chess_games (id, platform, platform_game_id, played_at, moves, ply_analysis, ply_analysis_depth,"
            " analysis_status) VALUES (%s, 'lichess', %s, now(), '[\"e4\", \"e5\"]'::jsonb, '[]'::jsonb, %s, 'completed')",
            (gid, f"g{gid}", STOCKFISH_DEPTH),
        )
        conn.execute(
            "INSERT INTO player_games (player_id, chess_game_id, player_color, source, analyzed_at_depth)"
            " VALUES (%s, %s, 'white', 'lichess', %s)",
            (PLAYER_ID, gid, STOCKFISH_DEPTH),
        )
        conn.execute(
            "INSERT INTO blunders (player_id, chess_game_id, ply, fen, classification) VALUES (%s, %s, 0, 'f', 'blunder')",
            (PLAYER_ID, gid),
        )
    conn.commit()


@pytest.fixture()
def tool_env(
    clean: psycopg.Connection[DictRow], fresh_db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Any:
    _seed(clean)
    monkeypatch.setenv("DATABASE_URL", fresh_db_url)
    monkeypatch.setenv("ORACLE_DATABASE_URL", fresh_db_url)  # the same rows stand in for the oracle
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"random": [1, 2], "edge_cases": {"dup": 2}}))
    monkeypatch.setattr(diff_analysis, "sample_ids", lambda: [1, 2])
    return clean


def test_failed_worker_fails_the_check(tool_env: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(
        diff_analysis, "analyze_pending", lambda *a, **k: {"pending": 2, "analyzed": 1, "failed": 1, "issues": 0}
    )
    assert diff_analysis.with_stockfish(None, None) == 1
    assert "failed 1" in capsys.readouterr().out


def test_omitted_game_fails_the_check(tool_env: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(
        diff_analysis, "analyze_pending", lambda *a, **k: {"pending": 1, "analyzed": 1, "failed": 0, "issues": 0}
    )
    assert diff_analysis.with_stockfish(None, None) == 1
    assert "worklist 1" in capsys.readouterr().out


def test_retained_rows_never_satisfy_the_check(tool_env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run that claims success but writes nothing: the reset removed the old rows and no
    fresh analysis exists, so coverage fails even though the counts look right."""
    monkeypatch.setattr(
        diff_analysis, "analyze_pending", lambda *a, **k: {"pending": 2, "analyzed": 2, "failed": 0, "issues": 0}
    )
    assert diff_analysis.with_stockfish(None, None) == 1
    with psycopg.connect(tool_env.info.dsn, row_factory=psycopg.rows.dict_row) as c:  # type: ignore[arg-type]
        assert q.freshly_analysed(c, [1, 2]) == []


def test_fresh_analysis_of_every_game_passes(tool_env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        conn: Any, s: Settings, *, workers: Any = None, game_ids: Any = None, limit: Any = None
    ) -> dict[str, Any]:
        pa = [{"ply": i, "eval": 0, "best_move": None, "best_line": None, "mate_in_moves": None} for i in range(3)]
        for gid in game_ids:
            save_analysis(
                conn,
                Task(gid, "white", ["e4", "e5"], None, "standard", None, STOCKFISH_DEPTH, s, "sf"),
                GameAnalysis([], None, 0, pa),
            )
        conn.commit()
        return {"pending": len(game_ids), "analyzed": len(game_ids), "failed": 0, "issues": 0}

    monkeypatch.setattr(diff_analysis, "analyze_pending", fake_run)
    # the "oracle" (same database) now also has zero blunders for these games, so the diff is clean
    assert diff_analysis.with_stockfish(None, None) == 0
