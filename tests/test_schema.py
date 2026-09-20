"""Schema behaviour.

- fresh install works and the objects the plan promised exist
- representative writes exercise generated columns and every retained ON CONFLICT target
- the upgrade runner really upgrades: representative data survives, re-running is a no-op,
  init refuses a populated database
- once real migrations exist, fresh-install == baseline + migrations (columns, indexes,
  constraints AND function bodies), using tests/fixtures/schema_baseline.sql
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from core import schema
from tests.conftest import _admin_url, with_dbname

FIXTURES = Path(__file__).parent / "fixtures"
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"


# --- objects and writes ------------------------------------------------------


def test_functions_and_generated_columns(conn: psycopg.Connection[DictRow]) -> None:
    row = conn.execute("SELECT bq_canonical_fen(%s) AS c, bq_position_key(%s) AS k", (START_FEN, START_FEN)).fetchone()
    assert row is not None
    assert row["c"] == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
    assert isinstance(row["k"], int)
    conn.execute(
        "INSERT INTO chess_games (platform, platform_game_id, played_at, moves, fen_sequence, variant) "
        "VALUES ('lichess', 'test1', now(), %s::jsonb, %s::jsonb, 'standard')",
        (json.dumps(["e4"]), json.dumps([START_FEN, AFTER_E4])),
    )
    inserted = conn.execute("SELECT position_keys FROM chess_games WHERE platform_game_id = 'test1'").fetchone()
    assert inserted is not None
    keys = inserted["position_keys"]
    assert len(keys) == 2 and keys[0] == row["k"]


def test_retained_conflict_targets(conn: psycopg.Connection[DictRow]) -> None:
    """Every ON CONFLICT target a retained write relies on must have its index."""
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (1, 'p') ON CONFLICT DO NOTHING")
    # blunder dismissal (Blunders page): unique on (player_id, canonical_fen)
    for _ in range(2):
        conn.execute(
            "INSERT INTO dismissed_blunder_fens (player_id, fen) VALUES (1, %s) "
            "ON CONFLICT (player_id, canonical_fen) DO NOTHING",
            (START_FEN,),
        )
    # puzzle-candidate dismissal: unique on canonical_fen
    for _ in range(2):
        conn.execute(
            "INSERT INTO dismissed_puzzle_candidates (fen) VALUES (%s) ON CONFLICT (canonical_fen) DO NOTHING",
            (START_FEN,),
        )
    n1 = conn.execute("SELECT count(*) AS n FROM dismissed_blunder_fens").fetchone()
    n2 = conn.execute("SELECT count(*) AS n FROM dismissed_puzzle_candidates").fetchone()
    assert n1 and n2 and n1["n"] == 1 and n2["n"] == 1


def test_expected_indexes_present(conn: psycopg.Connection[DictRow]) -> None:
    names = {r["indexname"] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")}
    for ix in (
        "ix_chess_games_position_keys",  # GIN: repertoire/scout matching
        "ix_repertoire_lines_position_keys",
        "ix_lichess_puzzles_themes",
        "ix_lichess_puzzles_rating",
        "ix_lichess_puzzles_topk",
        "ix_chapters_identity",
        "ix_dismissed_blunder_fens_player_canonical",
        "ix_puzzle_attempts_idempotency",
        "idx_puzzles_repertoire_line",
        "ix_pps_due",
    ):
        assert ix in names, ix
    # 27 harvested tables kept 52 of their 55 indexes (three belonged to dropped columns/features)
    assert len([n for n in names if not n.endswith("_pkey")]) >= 52


def test_expected_tables_present(conn: psycopg.Connection[DictRow]) -> None:
    names = {r["tablename"] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    for t in (
        "players",
        "chess_games",
        "player_games",
        "blunders",
        "puzzles",
        "puzzle_attempts",
        "player_puzzle_state",
        "lichess_puzzles",
        "books",
        "chapters",
        "repertoire_lines",
        "repertoire_annotations",
        "opponent_profiles",
        "review_events",
        "settings",
        "pipeline_runs",
        "schema_version",
    ):
        assert t in names, t
    for gone in ("users", "refresh_tokens", "trial_events", "creators", "app_settings", "player_drill_state"):
        assert gone not in names, gone


def test_no_source_specific_or_tenancy_leftovers(conn: psycopg.Connection[DictRow]) -> None:
    """Repertoire provenance columns are the neutral source_* names; no tenancy columns survive."""
    cols = conn.execute(
        "SELECT table_name||'.'||column_name AS c FROM information_schema.columns WHERE table_schema='public'"
    ).fetchall()
    names = {r["c"] for r in cols}
    assert {"books.source_book_id", "chapters.source_chapter_id", "repertoire_lines.source_line_id"} <= names
    assert not [c for c in names if c.endswith(("_bid", "_lid"))]
    joined = " ".join(names)
    assert "onboarding" not in joined and "trial_" not in joined
    policies = conn.execute("SELECT count(*) AS n FROM pg_policies").fetchone()
    assert policies is not None and policies["n"] == 0


def test_settings_row_is_single(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO settings (id, data) VALUES (1, '{}'::jsonb) ON CONFLICT (id) DO NOTHING")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO settings (id, data) VALUES (2, '{}'::jsonb)")


# --- upgrade runner ------------------------------------------------------------


def _scratch(fresh_db_url: str, suffix: str) -> str:
    admin, name = _admin_url(fresh_db_url)
    target = f"{name}_{suffix}"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(target)))
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
    return with_dbname(fresh_db_url, target)


@pytest.mark.parametrize("baseline", [0, 1, 7], ids=["baseline-0", "baseline-1", "baseline-7"])
def test_upgrade_applies_pending_migrations_and_keeps_data(fresh_db_url: str, tmp_path: Path, baseline: int) -> None:
    """A real upgrade against a populated database, using a temporary migrations directory.

    Runs at baseline 0 and at nonzero baselines: migrations numbered at or below the
    recorded baseline are skipped, those above it run in order, data survives, a second
    run is a no-op, and init refuses a populated database. Independent of the repo's real
    migration number: the probe numbers are chosen relative to the baseline.
    """
    for n in range(1, baseline + 1):  # pretend these already shipped; init records the highest
        (tmp_path / f"{n:03d}_already_applied.sql").write_text("SELECT 'must not run';\n")
    url = _scratch(fresh_db_url, f"upg{baseline}")
    with psycopg.connect(url) as c:
        assert schema.init(c, tmp_path) == baseline
        c.execute("INSERT INTO players (id, chesscom_username) VALUES (1, 'p')")
        c.execute("INSERT INTO settings (id, data) VALUES (1, '{\"daily_puzzle_target\": 7}'::jsonb)")
        c.commit()
    a, b = baseline + 1, baseline + 2
    (tmp_path / f"{a:03d}_add_probe.sql").write_text(
        "ALTER TABLE players ADD COLUMN probe_note text;\n"
        "CREATE TABLE probe_log (id serial PRIMARY KEY, note text NOT NULL);\n"
    )
    (tmp_path / f"{b:03d}_fill_probe.sql").write_text("UPDATE players SET probe_note = 'migrated';\n")
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row) as c:
        assert schema.upgrade(c, tmp_path) == [a, b]
        assert schema.current_version(c) == b
        row = c.execute("SELECT probe_note FROM players WHERE id = 1").fetchone()
        assert row and row["probe_note"] == "migrated"
        kept = c.execute("SELECT data->>'daily_puzzle_target' AS v FROM settings").fetchone()
        assert kept and kept["v"] == "7"
        assert schema.upgrade(c, tmp_path) == []  # idempotent: nothing pending
        with pytest.raises(RuntimeError, match="already initialised"):
            schema.init(c, tmp_path)


def test_migration_filenames_are_strict(tmp_path: Path) -> None:
    (tmp_path / "001_ok.sql").write_text("SELECT 1;")
    (tmp_path / "baseline.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="does not match"):
        schema.migration_files(tmp_path)


# --- fresh-install == baseline + migrations ------------------------------------


def _signature(url: str) -> list[tuple[object, ...]]:
    queries = [
        "SELECT table_name, column_name, data_type, is_nullable, column_default, is_generated, generation_expression"
        " FROM information_schema.columns WHERE table_schema='public' ORDER BY 1,2",
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1",
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE connamespace='public'::regnamespace ORDER BY 1",
        "SELECT p.proname, pg_get_function_identity_arguments(p.oid),"
        " regexp_replace(pg_get_functiondef(p.oid), '\\s+', ' ', 'g')"
        " FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY 1,2",
    ]
    out: list[tuple[object, ...]] = []
    with psycopg.connect(url) as c:
        for q in queries:
            out.extend(tuple(r) for r in c.execute(q).fetchall())  # type: ignore[arg-type]
    return out


def test_fresh_install_equals_baseline_plus_migrations(fresh_db_url: str) -> None:
    if not schema.migration_files():
        pytest.skip("no migrations yet: nothing to diff (test_upgrade_* covers the runner)")
    baseline = FIXTURES / "schema_baseline.sql"
    assert baseline.exists(), "tests/fixtures/schema_baseline.sql must hold the schema before the first migration"
    m = re.match(r"-- baseline_version: (\d+)", baseline.read_text())
    assert m, "schema_baseline.sql must start with '-- baseline_version: N'"
    url = _scratch(fresh_db_url, "base")
    with psycopg.connect(url) as c:
        c.execute(sql.SQL(baseline.read_text()))  # type: ignore[arg-type]  # repo fixture, trusted
        c.execute("INSERT INTO schema_version (version) VALUES (%s)", (int(m.group(1)),))
        c.commit()
        schema.upgrade(c)
    assert _signature(url) == _signature(fresh_db_url)
