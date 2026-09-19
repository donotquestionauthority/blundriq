"""Fresh install and upgrade of the database schema. Two paths, never mixed.

  init    — an EMPTY database loads schema.sql (always the latest schema) and
            records the current baseline version in schema_version.
  upgrade — an EXISTING database applies only migrations/NNN_*.sql files with
            NNN greater than its recorded version, each in its own transaction,
            recording each as it goes.

`schema.sql` is regenerated whenever a migration lands, so a fresh install and
an upgraded database are identical; CI checks that (tests/test_schema.py).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, LiteralString, cast

from psycopg import Connection
from psycopg.rows import tuple_row

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = REPO_ROOT / "schema.sql"
MIGRATIONS_DIR = REPO_ROOT / "migrations"

_MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def _sql_from_repo_file(path: Path) -> LiteralString:
    """SQL text from a file tracked in this repository. It is trusted code, not user input,
    which is the assumption psycopg's LiteralString type expresses."""
    return cast(LiteralString, path.read_text())


def migration_files() -> list[tuple[int, Path]]:
    """All migrations in numeric order. A malformed filename is an error, not a skip."""
    found: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _MIGRATION_RE.match(path.name)
        if not m:
            raise ValueError(f"migration filename does not match NNN_name.sql: {path.name}")
        found.append((int(m.group(1)), path))
    numbers = [n for n, _ in found]
    if len(numbers) != len(set(numbers)):
        raise ValueError(f"duplicate migration numbers: {numbers}")
    return found


def latest_version() -> int:
    """The baseline a fresh install records: the highest migration number, or 0."""
    files = migration_files()
    return files[-1][0] if files else 0


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


def init(conn: Connection[Any]) -> int:
    """Fresh install. Refuses to run on a database that already has a schema."""
    if current_version(conn) is not None:
        raise RuntimeError("database already initialised; use upgrade")
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        row = cur.fetchone()
        if row and int(row[0]) > 0:
            raise RuntimeError("database is not empty; refusing to load schema.sql over existing tables")
        cur.execute(_sql_from_repo_file(SCHEMA_FILE))
        version = latest_version()
        cur.execute("INSERT INTO schema_version (version) VALUES (%s)", (version,))
    conn.commit()
    return version


def upgrade(conn: Connection[Any]) -> list[int]:
    """Apply pending migrations in order; returns the numbers applied."""
    version = current_version(conn)
    if version is None:
        raise RuntimeError("database has no schema; use init")
    applied: list[int] = []
    for number, path in migration_files():
        if number <= version:
            continue
        with conn.cursor() as cur:
            cur.execute(_sql_from_repo_file(path))
            cur.execute("INSERT INTO schema_version (version) VALUES (%s)", (number,))
        conn.commit()
        applied.append(number)
    return applied
