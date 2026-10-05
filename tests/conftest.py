"""Test fixtures. Tests run against a scratch Postgres given by TEST_DATABASE_URL.

The scratch database is DROPPED and recreated per session (and a few tests create and
drop siblings named after it), then loaded via the fresh-install path (core.schema.init).
So TEST_DATABASE_URL must name a throwaway database. Before anything is dropped,
`scratch_refusal` stops the session when the name carries no `test` or `scratch` word, and
when it is the database DATABASE_URL names (or that database is one of its siblings).
"""

from __future__ import annotations

import os  # noqa: TID251 — test bootstrap is the one other legitimate env read
import re
from collections.abc import Generator

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from core import schema

TEST_DB_URL: str = os.environ.get("TEST_DATABASE_URL", "")  # noqa: TID251
APP_DB_URL: str = os.environ.get("DATABASE_URL", "")  # noqa: TID251 — only compared, never connected to

_SCRATCH_WORD = re.compile(r"(^|[_-])(test|scratch)([_-]|$)", re.I)


def scratch_refusal(test_url: str, app_url: str) -> str | None:
    """Why `test_url` must not be dropped, or None when it is a throwaway database.
    Never connects: it reads the two DSNs and nothing else."""
    from psycopg.conninfo import conninfo_to_dict

    try:
        test = conninfo_to_dict(test_url)
    except psycopg.ProgrammingError:
        return "TEST_DATABASE_URL is not a valid connection string"
    name = str(test.get("dbname") or "")
    if not name:
        return "TEST_DATABASE_URL names no database"
    if not _SCRATCH_WORD.search(name):
        return f"database {name!r} is not named as a scratch database (put a test or scratch word in its name)"
    if app_url:
        try:
            app = conninfo_to_dict(app_url)
        except psycopg.ProgrammingError:
            return None  # nothing to compare with; the name rule above still holds
        app_name = str(app.get("dbname") or "")
        if app_name == name or app_name.startswith(name + "_"):
            return f"database {name!r} is the one DATABASE_URL names, or a sibling of it"
    return None


def _admin_url(url: str) -> tuple[str, str]:
    """(url to the maintenance db, dbname) for any DSN form, query strings included."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(url)
    name = str(params.get("dbname", "postgres"))
    return make_conninfo(url, dbname="postgres"), name


def with_dbname(url: str, name: str) -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(url, dbname=name)


@pytest.fixture(scope="session")
def fresh_db_url() -> Generator[str, None, None]:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    refused = scratch_refusal(TEST_DB_URL, APP_DB_URL)
    if refused:
        pytest.exit(f"refusing to drop the test database: {refused}", returncode=2)
    admin, name = _admin_url(TEST_DB_URL)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(TEST_DB_URL) as conn:
        schema.init(conn)
    yield TEST_DB_URL


@pytest.fixture()
def conn(fresh_db_url: str) -> Generator[psycopg.Connection[DictRow], None, None]:
    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as c:
        c.execute("DELETE FROM settings")  # tests start from defaults; API tests may have committed a row
        c.commit()
        yield c
        c.rollback()


def reset_game_data(conn: psycopg.Connection[DictRow]) -> None:
    """Truncate every game/repertoire/analysis table (tests that commit share one scratch DB)."""
    conn.execute(
        "TRUNCATE chess_games, player_games, blunders, player_motif_events, books, chapters, repertoire_lines,"
        " repertoire_annotations, game_repertoire_results, game_result_lines, puzzles, opponent_profiles,"
        " pipeline_runs, ai_calls, ai_explanation_cache, position_evals, review_snapshots, players RESTART IDENTITY CASCADE"
    )
    conn.commit()


@pytest.fixture()
def clean(conn: psycopg.Connection[DictRow]) -> Generator[psycopg.Connection[DictRow], None, None]:
    """A connection over a database with no game data; tests using it may commit.
    The data is cleared again afterwards so explicit ids never collide with later serial ones."""
    reset_game_data(conn)
    yield conn
    conn.rollback()
    reset_game_data(conn)


@pytest.fixture()
def app_env(fresh_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment for API tests: real DSN, throwaway session secret, known password."""
    import bcrypt

    monkeypatch.setenv("DATABASE_URL", fresh_db_url)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-not-for-production")
    monkeypatch.setenv("PASSWORD_HASH", bcrypt.hashpw(b"correct horse", bcrypt.gensalt(rounds=4)).decode())
