"""The import step: fetch new games for the player from one platform and store them.

Incremental mode (the hourly default):
- Chess.com walks the archives of the last `months` months, oldest first, one
  transaction per archive, skipping games strictly older than the newest
  stored game (`<`, not `<=`: end times have second resolution and a new game
  can share the newest game's second; the upsert dedups). An interrupted run
  leaves only newer archives unfetched, and they are still newer than the
  stored games, so the next run fetches them.
- Lichess streams newest first from `since`, committing every 100 games. A
  timestamp taken from the stored games would move past games an interrupted
  stream never delivered, so `since` is the START time of the last COMPLETED
  import (players.lichess_last_checked, stamped only after the whole stream
  was stored). Games the interrupted run did store are fetched again and
  dedup on the upsert.

`all_history=True` walks everything. A savepoint per game means one bad game
never loses its batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from psycopg import Connection, sql

from core.constants import PLAYER_ID
from core.ingest import chesscom, lichess
from core.ingest.records import GameRecord
from core.ingest.store import store_game
from core.player import usernames

INITIAL_IMPORT_MONTHS = 3
LICHESS_BATCH = 100


@dataclass
class ImportSummary:
    platform: str
    fetched: int = 0
    new: int = 0
    skipped: int = 0


def latest_played_at(conn: Connection[Any], platform: str) -> datetime | None:
    row = conn.execute(
        """
        SELECT MAX(cg.played_at) AS latest FROM player_games pg
        JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %s AND pg.source = %s
        """,
        (PLAYER_ID, platform),
    ).fetchone()
    return row["latest"] if row else None


def last_completed(conn: Connection[Any], platform: str) -> datetime | None:
    column = "chesscom_last_checked" if platform == "chesscom" else "lichess_last_checked"
    row = conn.execute(
        sql.SQL("SELECT {} AS at FROM players WHERE id = %s").format(sql.Identifier(column)), (PLAYER_ID,)
    ).fetchone()
    return row["at"] if row else None


def _store_batch(conn: Connection[Any], records: list[GameRecord | None], summary: ImportSummary) -> None:
    with conn.transaction():
        for rec in records:
            summary.fetched += 1
            if rec is None:
                summary.skipped += 1
                continue
            if store_game(conn, rec):
                summary.new += 1


def _stamp_completed(conn: Connection[Any], platform: str, started_at: datetime) -> None:
    column = "chesscom_last_checked" if platform == "chesscom" else "lichess_last_checked"
    conn.execute(
        sql.SQL("UPDATE players SET {} = %s WHERE id = %s").format(sql.Identifier(column)), (started_at, PLAYER_ID)
    )
    conn.commit()


def import_chesscom(
    conn: Connection[Any],
    username: str,
    *,
    months: int | None = None,
    all_history: bool = False,
    client: httpx.Client | None = None,
) -> ImportSummary:
    summary = ImportSummary("chesscom")
    client = client or httpx.Client()
    started_at = datetime.now(UTC)
    latest = None if all_history else latest_played_at(conn, "chesscom")
    conn.commit()
    archives = chesscom.fetch_archives(client, username)
    if not all_history:
        cutoff = datetime.now(UTC) - timedelta(days=30 * (months or INITIAL_IMPORT_MONTHS))
        archives = chesscom.archives_since(archives, cutoff)
    for url in archives:
        games = chesscom.fetch_archive(client, url)
        records: list[GameRecord | None] = []
        for game in games:
            end_time = game.get("end_time")
            played_at = datetime.fromtimestamp(int(end_time), tz=UTC) if end_time else None
            if latest is not None and played_at is not None and played_at < latest:
                continue
            records.append(chesscom.parse_game(game, username))
        _store_batch(conn, records, summary)
    _stamp_completed(conn, "chesscom", started_at)
    return summary


def import_lichess(
    conn: Connection[Any], username: str, *, all_history: bool = False, client: httpx.Client | None = None
) -> ImportSummary:
    summary = ImportSummary("lichess")
    client = client or httpx.Client()
    started_at = datetime.now(UTC)
    completed = None if all_history else last_completed(conn, "lichess")
    conn.commit()
    if all_history:
        since_ms = 0
    elif completed is not None:
        since_ms = int(completed.timestamp() * 1000)
    else:
        since_ms = int((datetime.now(UTC) - timedelta(days=30 * INITIAL_IMPORT_MONTHS)).timestamp() * 1000)
    batch: list[GameRecord | None] = []
    for game in lichess.stream_games(client, username, since_ms):
        batch.append(lichess.parse_game(game, username))
        if len(batch) >= LICHESS_BATCH:
            _store_batch(conn, batch, summary)
            batch = []
    if batch:
        _store_batch(conn, batch, summary)
    _stamp_completed(conn, "lichess", started_at)
    return summary


def import_platform(
    conn: Connection[Any],
    platform: str,
    *,
    months: int | None = None,
    all_history: bool = False,
    client: httpx.Client | None = None,
) -> ImportSummary:
    names = usernames(conn)
    username = names.get(platform)
    if not username:
        return ImportSummary(platform)
    if platform == "chesscom":
        return import_chesscom(conn, username, months=months, all_history=all_history, client=client)
    if platform == "lichess":
        return import_lichess(conn, username, all_history=all_history, client=client)
    raise ValueError(f"unknown platform {platform!r}")
