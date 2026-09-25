"""The opponent import step: fetch every active profile's games and record its side of them.

One fetch, two stores: the Chess.com archive walk (`core/ingest/walk.py`) and the Lichess
stream (`core/ingest/lichess.py`) are the player's importer's; what differs is what is stored
(an `opponent_views` row on top of the shared `chess_games` upsert, so a game the player and
a scouted opponent both played is one row seen from both sides), where the cursor lives
(`opponent_sources.last_fetched`) and how the Lichess boundary is found.

Onboarding is this step: a profile with `is_initialized = FALSE` gets its newest `window`
eligible games per source and is then marked initialised, once every source completed with
nothing failed. An initialised profile is fetched from its cursor.

What the cursor means differs by platform:

* Chess.com — an **end time**, the newest stored game's `played_at`. The walk takes the
  archives from that month on and skips only games strictly older (`<`), so a game sharing
  the cursor's second is fetched again and dedups. A game still in progress at the previous
  run ends later and is picked up then.
* Lichess — a **creation-time boundary**. `since` filters by creation time and returns
  finished games only, so the boundary must sit at or before the creation time of every game
  that was in progress when the run happened, or that game is never returned once it finishes.
  An onboarding stream is capped and can close before it has seen an ongoing game created
  long ago, so the boundary is discovered by its own uncapped request for the games in
  progress (`finished=False`), before the walk, and stamped only after the walk stored
  everything. NULL on an initialised source means "walk the whole history once": the cursors
  the migration brought over are the old importer's end times, which would have excluded a
  game created before them and finished after; `reset_lichess_cursors` puts every one into
  that state (`pipeline import-opponents --reset-lichess-cursors`, a deliberate, one-time
  switch the hourly chain never passes). Interrupted, the cursor stays NULL and the next run
  repeats the walk.

The cursor is all-or-nothing: a source's cursor advances only when every eligible record was
stored. A game that fails to store is rolled back under its own savepoint, leaves the cursor
where it was so the next run re-fetches it, and counts in `failed` — as does a source whose
fetch raised — so the run is red and alerts. Games already stored stay stored. An opponent's
Chess960 game is never stored (`is_analysable` decides): nothing here uses it and it costs
storage. Every step is idempotent: both writes dedup.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from psycopg import Connection

from core.chess.eligibility import is_analysable
from core.constants import PLAYER_ID
from core.ingest import lichess, walk
from core.ingest.records import FetchError, GameRecord, integer
from core.ingest.run import INITIAL_IMPORT_MONTHS, LICHESS_BATCH
from core.ingest.store import upsert_game


@dataclass
class Summary:
    profiles: int = 0
    sources: int = 0
    fetched: int = 0  # eligible records that reached the store
    new: int = 0  # opponent_views rows inserted
    skipped: int = 0  # unparseable or not analysable: never stored, never a cursor candidate
    failed: int = 0  # records that failed to store, plus sources whose fetch raised
    onboarded: int = 0  # profiles marked initialised this run


def reset_lichess_cursors(conn: Connection[Any]) -> int:
    """NULL the cursor of every active Lichess source of an initialised profile, so the next
    walk covers the whole history once. Chess.com cursors are end times and stay."""
    cur = conn.execute(
        """
        UPDATE opponent_sources os SET last_fetched = NULL
        FROM opponent_profiles op
        WHERE op.id = os.opponent_profile_id AND op.player_id = %(pid)s
          AND os.source_type = 'lichess' AND os.active AND op.is_initialized
        """,
        {"pid": PLAYER_ID},
    )
    conn.commit()
    return cur.rowcount


def store_opponent_game(conn: Connection[Any], profile_id: int, record: GameRecord) -> bool:
    """Both writes for one game inside a savepoint; True iff the view row is new."""
    with conn.transaction():
        game_id = upsert_game(conn, record)
        cur = conn.execute(
            """
            INSERT INTO opponent_views (opponent_profile_id, chess_game_id, source_type, played_as, result)
            VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (profile_id, game_id, record.platform, record.player_color, record.result),
        )
        return cur.rowcount > 0


def _sources(conn: Connection[Any], profile_id: int | None) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT os.id, os.opponent_profile_id AS profile_id, os.source_type, os.username, os.last_fetched,
                   op.is_initialized
            FROM opponent_sources os JOIN opponent_profiles op ON op.id = os.opponent_profile_id
            WHERE op.player_id = %(pid)s AND op.active AND os.active AND os.source_type <> 'manual'
              AND (%(profile_id)s::int IS NULL OR op.id = %(profile_id)s)
            ORDER BY op.id, os.id
            """,
            {"pid": PLAYER_ID, "profile_id": profile_id},
        ).fetchall()
    ]


class _SourceRun:
    """One source's walk: stores records as they come, tracks whether the cursor may move."""

    def __init__(self, conn: Connection[Any], profile_id: int, summary: Summary, cap: int | None) -> None:
        self.conn = conn
        self.profile_id = profile_id
        self.summary = summary
        self.cap = cap
        self.eligible = 0
        self.clean = True
        self.newest_played_at: datetime | None = None

    def full(self) -> bool:
        return self.cap is not None and self.eligible >= self.cap

    def store_batch(self, records: list[GameRecord | None]) -> None:
        """One transaction per batch, a savepoint per game, then commit: what is stored stays
        stored whatever happens to the rest of the stream."""
        with self.conn.transaction():
            for record in records:
                if self.full():
                    break
                self.store(record)
        self.conn.commit()

    def store(self, record: GameRecord | None) -> None:
        """One record; a Chess960 or unparseable one is skipped and consumes no cap slot."""
        if record is None or not is_analysable(record.variant):
            self.summary.skipped += 1
            return
        self.eligible += 1
        self.summary.fetched += 1
        try:
            if store_opponent_game(self.conn, self.profile_id, record):
                self.summary.new += 1
        except Exception:
            self.summary.failed += 1
            self.clean = False
            return
        if record.played_at is not None and (self.newest_played_at is None or record.played_at > self.newest_played_at):
            self.newest_played_at = record.played_at


def _chesscom(conn: Connection[Any], client: httpx.Client, src: dict[str, Any], run: _SourceRun, now: datetime) -> Any:
    """Walk one Chess.com source; returns the cursor candidate."""
    cursor: datetime | None = src["last_fetched"]
    if not src["is_initialized"]:
        batches = walk.chesscom_games(client, src["username"], newest=True)
    elif cursor is not None:
        batches = walk.chesscom_games(client, src["username"], since=cursor, cutoff=cursor)
    else:
        batches = walk.chesscom_games(client, src["username"], cutoff=now - timedelta(days=30 * INITIAL_IMPORT_MONTHS))
    for records in batches:
        run.store_batch(records)
        if run.full():
            break
    if run.newest_played_at is None:
        return cursor
    return max(cursor, run.newest_played_at) if cursor is not None else run.newest_played_at


def _lichess_boundary(client: httpx.Client, username: str, now: datetime) -> datetime:
    """The earliest creation time among the games in progress, or the run's start."""
    boundary = now
    for game in lichess.stream_games(client, username, 0, finished=False):
        created = integer(game, "createdAt")
        if created is not None:
            boundary = min(boundary, datetime.fromtimestamp(created / 1000, tz=UTC))
    return boundary


def _lichess(conn: Connection[Any], client: httpx.Client, src: dict[str, Any], run: _SourceRun, now: datetime) -> Any:
    """Walk one Lichess source; returns the cursor candidate (the boundary discovered first)."""
    boundary = _lichess_boundary(client, src["username"], now)
    cursor: datetime | None = src["last_fetched"]
    since_ms = int(cursor.timestamp() * 1000) if cursor is not None and src["is_initialized"] else 0
    stream: Iterator[dict[str, Any]] = lichess.stream_games(client, src["username"], since_ms)
    batch: list[GameRecord | None] = []
    eligible = 0
    for game in stream:
        # The cap closes the stream as soon as it is met (leaving the `with` inside
        # stream_games): the boundary never depends on what this stream happened to show.
        if run.cap is not None and eligible >= run.cap:
            break
        if lichess.is_ongoing(game):
            continue
        record = lichess.parse_game(game, src["username"])
        if record is not None and is_analysable(record.variant):
            eligible += 1
        batch.append(record)
        if len(batch) >= LICHESS_BATCH:
            run.store_batch(batch)
            batch = []
    if batch:
        run.store_batch(batch)
    return boundary


def import_profiles(
    conn: Connection[Any],
    *,
    window: int,
    profile_id: int | None = None,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Every active profile (or one), every non-manual active source. Counts and labels only."""
    client = client or httpx.Client()
    started = now or datetime.now(UTC)
    summary = Summary()
    sources = _sources(conn, profile_id)
    by_profile: dict[int, list[dict[str, Any]]] = {}
    for src in sources:
        by_profile.setdefault(int(src["profile_id"]), []).append(src)
    profiles = [
        dict(r)
        for r in conn.execute(
            "SELECT id, is_initialized FROM opponent_profiles WHERE player_id = %(pid)s AND active"
            " AND (%(profile_id)s::int IS NULL OR id = %(profile_id)s) ORDER BY id",
            {"pid": PLAYER_ID, "profile_id": profile_id},
        ).fetchall()
    ]
    conn.commit()
    summary.profiles = len(profiles)
    for profile in profiles:
        pid = int(profile["id"])
        all_ok = True
        for src in by_profile.get(pid, []):
            summary.sources += 1
            cap = None if src["is_initialized"] else window
            run = _SourceRun(conn, pid, summary, cap)
            try:
                if src["source_type"] == "chesscom":
                    candidate = _chesscom(conn, client, src, run, started)
                else:
                    candidate = _lichess(conn, client, src, run, started)
            except FetchError:
                conn.commit()  # what was stored stays stored
                summary.failed += 1
                all_ok = False
                continue
            if run.clean:
                conn.execute("UPDATE opponent_sources SET last_fetched = %s WHERE id = %s", (candidate, src["id"]))
            else:
                all_ok = False
            conn.commit()
        if all_ok and not profile["is_initialized"]:
            conn.execute("UPDATE opponent_profiles SET is_initialized = TRUE WHERE id = %s", (pid,))
            conn.commit()
            summary.onboarded += 1
    return asdict(summary)
