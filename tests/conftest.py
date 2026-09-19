"""Test fixtures. Tests run against a scratch Postgres given by TEST_DATABASE_URL.

The scratch database is dropped and recreated per session, then loaded via the
fresh-install path (core.schema.init). Tests never touch a real database.
"""

from __future__ import annotations

import os  # noqa: TID251 — test bootstrap is the one other legitimate env read
from collections.abc import Generator

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from core import schema

TEST_DB_URL: str = os.environ.get("TEST_DATABASE_URL", "")  # noqa: TID251


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


@pytest.fixture()
def app_env(fresh_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment for API tests: real DSN, throwaway session secret, known password."""
    import bcrypt

    monkeypatch.setenv("DATABASE_URL", fresh_db_url)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-not-for-production")
    monkeypatch.setenv("PASSWORD_HASH", bcrypt.hashpw(b"correct horse", bcrypt.gensalt(rounds=4)).decode())
