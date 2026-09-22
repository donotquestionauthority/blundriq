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


def test_retained_rows_never_satisfy_the_check(
    tool_env: Any, fresh_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that claims success but writes nothing: the reset removed the old rows and no
    fresh analysis exists, so coverage fails even though the counts look right."""
    monkeypatch.setattr(
        diff_analysis, "analyze_pending", lambda *a, **k: {"pending": 2, "analyzed": 2, "failed": 0, "issues": 0}
    )
    assert diff_analysis.with_stockfish(None, None) == 1
    with psycopg.connect(fresh_db_url, row_factory=psycopg.rows.dict_row) as c:  # type: ignore[arg-type]
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


# --- the puzzle oracle -----------------------------------------------------------------

import diff_puzzles  # noqa: E402  # pyright: ignore[reportMissingImports]

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
MATE_FEN = "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1"


def _puzzle(conn: psycopg.Connection[DictRow], fen: str, sources: list[str], *, active: bool = True) -> int:
    row = conn.execute(
        "INSERT INTO puzzles (fen, solution_line, source_types, color, player_id, active, acceptance_map)"
        " VALUES (%s, %s::jsonb, %s::text[], 'w', %s, %s, %s::jsonb) RETURNING id",
        (
            fen,
            json.dumps(["Ra8#"] if fen == MATE_FEN else ["e4"]),
            sources,
            PLAYER_ID,
            active,
            json.dumps({"v": 1, "n": 1, "p": {}, "d": {}}) if "own_mate" in sources else None,
        ),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def test_the_map_reference_includes_retired_puzzles(clean: psycopg.Connection[DictRow]) -> None:
    """A mastered puzzle is inactive to the serve path but still graded when it is
    re-attempted, so its map belongs in a parity check. The first version of this tool
    only ever looked at active rows."""
    clean.execute("INSERT INTO players (id) VALUES (%s)", (PLAYER_ID,))
    live = _puzzle(clean, MATE_FEN, ["own_mate"])
    retired = _puzzle(clean, START_FEN, ["own_mate"], active=False)
    assert [r["id"] for r in q.acceptance_maps(clean)] == sorted([live, retired])


def test_generated_puzzles_leaves_hand_made_ones_out(clean: psycopg.Connection[DictRow]) -> None:
    clean.execute("INSERT INTO players (id) VALUES (%s)", (PLAYER_ID,))
    generated = _puzzle(clean, START_FEN, ["blunder"])
    _puzzle(clean, MATE_FEN, ["blunder", "custom"])
    assert [r["id"] for r in q.generated_puzzles(clean)] == [generated]


def test_rewriting_puzzles_refuses_outside_a_scratch_database(clean: psycopg.Connection[DictRow]) -> None:
    """Deleting a puzzle cascades its spaced-repetition row away, so the destructive half
    of the oracle will not run against a database that is not named as disposable."""
    name = clean.execute("SELECT current_database() AS n").fetchone()
    assert name and "scratch" not in str(name["n"]), "the test database must not look disposable"
    with pytest.raises(RuntimeError, match="refusing to rewrite puzzles"):
        q.forget_generated_puzzles(clean)


def test_maps_are_compared_before_anything_is_regenerated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regeneration replaces the very rows the map check reads. If the order ever flips,
    the map check compares the new builder against itself and can never fail."""
    order: list[str] = []
    monkeypatch.setattr(diff_puzzles, "diff_maps", lambda: order.append("maps") or 0)
    monkeypatch.setattr(diff_puzzles, "diff_generation", lambda: order.append("generation") or 0)
    monkeypatch.setattr(sys, "argv", ["diff_puzzles.py"])
    assert diff_puzzles.main() == 0
    assert order == ["maps", "generation"]


def test_the_map_check_reads_the_archive_not_the_scratch_copy(
    clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """The reference has to come from the database regeneration never writes to. A map
    that no longer rebuilds the same way must be reported."""
    clean.execute("INSERT INTO players (id) VALUES (%s)", (PLAYER_ID,))
    _puzzle(clean, MATE_FEN, ["own_mate"])  # a deliberately wrong stored map
    monkeypatch.setattr(diff_puzzles, "oracle", lambda: _NoClose(clean))
    # This scratch database has the current schema, so stand in for the archive's shape.
    read = q.acceptance_maps
    monkeypatch.setattr(q, "acceptance_maps", lambda conn, old_schema=False: read(conn))
    assert diff_puzzles.diff_maps() == 1
    assert "differs from the archived one" in capsys.readouterr().out


class _NoClose:
    """A connection the tool may use in a `with` block without closing the fixture's."""

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self._conn = conn

    def __enter__(self) -> psycopg.Connection[DictRow]:
        return self._conn

    def __exit__(self, *_: object) -> None:
        return None


# --- the Blunders ranking oracle -------------------------------------------------------------


def test_the_ranking_oracle_reports_a_wrong_port_of_the_weights(
    clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reference weights are frozen in core/oracle.py, not read from core/constants.py:
    the same database ranked by the old query and by the page must agree, and stop agreeing
    the moment the page's weights are wrong."""
    import diff_blunders  # pyright: ignore[reportMissingImports]

    from core import blunders

    assert q.OLD_BLUNDER_WEIGHTS == blunders.BLUNDER_SCORE_WEIGHTS
    clean.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'p')", (PLAYER_ID,))
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"
    for gid, cls in ((1, "blunder"), (2, "miss")):
        clean.execute(
            "INSERT INTO chess_games (id, platform, platform_game_id, played_at, variant, time_class, moves)"
            " VALUES (%s, 'lichess', %s, now() - make_interval(days => %s), 'standard', 'rapid', '[]'::jsonb)",
            (gid, f"g{gid}", gid),
        )
        clean.execute(
            "INSERT INTO player_games (player_id, chess_game_id, player_color, source) VALUES (%s, %s, 'white', 'lichess')",
            (PLAYER_ID, gid),
        )
        clean.execute(
            "INSERT INTO blunders (player_id, chess_game_id, ply, fen, classification, centipawn_loss) VALUES (%s, %s, 6, %s, %s, 300)",
            (PLAYER_ID, gid, fen, cls),
        )
    classes = ("miss", "blunder")
    old_rows = q.old_blunder_ranking(clean, classes, 2, 0)
    assert [(r["count"], r["score"]) for r in old_rows] == [(2, 12)]
    assert diff_blunders.compare("same", old_rows, diff_blunders.new_cards(clean, classes, 2, 0)) == []

    monkeypatch.setattr(blunders, "_WEIGHT_CASE", blunders._WEIGHT_CASE.replace("THEN 4", "THEN 400"))
    diffs = diff_blunders.compare("mutated", old_rows, diff_blunders.new_cards(clean, classes, 2, 0))
    assert diffs and "score: old 12 new 408" in diffs[0]
