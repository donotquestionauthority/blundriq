"""Fresh install and upgrade of the database schema. Two paths, never mixed.

  init    — an EMPTY database loads core/sql/schema.sql (always the latest
            schema) and records the current baseline version in schema_version.
  upgrade — an EXISTING database applies only core/sql/migrations/NNN_*.sql
            files with NNN greater than its recorded version, each in its own
            transaction, recording each as it goes.

The SQL lives inside the `core` package (core/sql/) and is shipped as package
data, so an ordinary `pip install .` works the same as an editable checkout.
`schema.sql` is regenerated whenever a migration lands, so a fresh install and
an upgraded database are identical; CI checks that (tests/test_schema.py) using
tests/fixtures/schema_baseline.sql, which is deliberately outside the numbered
migration directory.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import Any, LiteralString, cast

from psycopg import Connection
from psycopg.rows import tuple_row

SQL_DIR = Path(str(files("core") / "sql"))
SCHEMA_FILE = SQL_DIR / "schema.sql"
MIGRATIONS_DIR = SQL_DIR / "migrations"

_MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def _sql_from_repo_file(path: Path) -> LiteralString:
    """SQL text from a file tracked in this repository. It is trusted code, not user input,
    which is the assumption psycopg's LiteralString type expresses."""
    return cast(LiteralString, path.read_text())


def migration_files(directory: Path | None = None) -> list[tuple[int, Path]]:
    """All migrations in numeric order. A malformed filename is an error, not a skip;
    a missing directory is an error too (an installed package without its SQL is broken)."""
    directory = directory if directory is not None else MIGRATIONS_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"migrations directory missing: {directory}")
    if not SCHEMA_FILE.is_file():
        raise FileNotFoundError(f"schema file missing: {SCHEMA_FILE}")
    found: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        m = _MIGRATION_RE.match(path.name)
        if not m:
            raise ValueError(f"migration filename does not match NNN_name.sql: {path.name}")
        found.append((int(m.group(1)), path))
    numbers = [n for n, _ in found]
    if len(numbers) != len(set(numbers)):
        raise ValueError(f"duplicate migration numbers: {numbers}")
    return found


def latest_version(directory: Path | None = None) -> int:
    """The baseline a fresh install records: the highest migration number, or 0."""
    found = migration_files(directory)
    return found[-1][0] if found else 0


def current_version(conn: Connection[Any]) -> int | None:
    """Recorded version, or None if the database has no schema_version table (empty DB)."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT to_regclass('public.schema_version') IS NOT NULL")
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        row = cur.fetchone()
        return int(row[0]) if row else 0


def init(conn: Connection[Any], directory: Path | None = None) -> int:
    """Fresh install. Refuses to run on a database that already has a schema."""
    if current_version(conn) is not None:
        raise RuntimeError("database already initialised; use upgrade")
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        row = cur.fetchone()
        if row and int(row[0]) > 0:
            raise RuntimeError("database is not empty; refusing to load schema.sql over existing tables")
        cur.execute(_sql_from_repo_file(SCHEMA_FILE))
        version = latest_version(directory)
        cur.execute("INSERT INTO schema_version (version) VALUES (%s)", (version,))
    conn.commit()
    return version


def upgrade(conn: Connection[Any], directory: Path | None = None) -> list[int]:
    """Apply pending migrations in order; returns the numbers applied."""
    version = current_version(conn)
    if version is None:
        raise RuntimeError("database has no schema; use init")
    applied: list[int] = []
    for number, path in migration_files(directory):
        if number <= version:
            continue
        with conn.cursor() as cur:
            cur.execute(_sql_from_repo_file(path))
            cur.execute("INSERT INTO schema_version (version) VALUES (%s)", (number,))
        conn.commit()
        applied.append(number)
    return applied
