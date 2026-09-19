"""One connection helper for the whole application. One role, no RLS.

`connect()` opens a plain psycopg connection from the DSN in core/secrets.py.
`pool()` returns a process-wide connection pool for the API. Nothing here runs
at import time; a module that imports `core.db` does not open a connection.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from core import secrets

_pool: ConnectionPool[psycopg.Connection[DictRow]] | None = None


def connect(*, autocommit: bool = False) -> psycopg.Connection[DictRow]:
    """A fresh connection with dict rows. Caller owns the transaction and closes it."""
    return psycopg.Connection[DictRow].connect(
        secrets.database().database_url, autocommit=autocommit, row_factory=dict_row
    )


def pool() -> ConnectionPool[psycopg.Connection[DictRow]]:
    """Lazily created pool for the API process (min 1, max 4: Supabase free tier is small)."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            secrets.database().database_url,
            connection_class=psycopg.Connection[DictRow],
            min_size=1,
            max_size=4,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


@contextmanager
def transaction() -> Generator[psycopg.Connection[DictRow], None, None]:
    """Pooled connection that commits on success and rolls back on any exception."""
    with pool().connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
