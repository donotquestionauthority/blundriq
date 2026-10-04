"""`pipeline backfill-openings`: the opening prefix for games stored before it existed.

Housekeeping nulls `moves` and `fen_sequence` outside the analysis window, so a game that left
the window before migration 007 has no prefix to derive one from. This re-fetches Rob's own
games from both platforms for `months` months back from his newest game, with the importers' own walkers and
parsers, and writes ONLY the prefix (`opening_moves`, `opening_keys`) onto rows that already
exist and have none:

- never `upsert_game`: its NULL → value ratchet would put `moves` and `fen_sequence` back on
  every old game, for housekeeping to null an hour later;
- never an insert: a fetched game that is not in chess_games is counted (`not_in_db`) and left
  to the importer, which owns inserts;
- never an overwrite: a row with a prefix keeps it (`already_had`);
- a variant the pipeline does not analyse gets none (rule 7; counted `chess960`).

Rows go by COPY into a temporary table and one UPDATE per batch (a Chess.com monthly archive,
or LICHESS_BATCH games), one transaction each, so an interrupted run keeps every batch it
finished and a rerun skips them. A platform request that fails stops that platform and counts
`failed`, which fails the step. Counts only: no handle, URL or game id is printed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString, cast

import httpx
from psycopg import Connection

from core.chess.eligibility import analysable_sql, is_analysable
from core.constants import PLAYER_ID
from core.ingest import lichess, walk
from core.ingest.records import FetchError, GameRecord
from core.ingest.store import opening_prefix
from core.player import usernames

LICHESS_BATCH = 500


@dataclass
class BackfillSummary:
    fetched: int = 0
    updated: int = 0
    already_had: int = 0
    not_in_db: int = 0
    unparseable: int = 0
    chess960: int = 0
    failed: int = 0


def _apply(conn: Connection[Any], records: list[GameRecord | None], summary: BackfillSummary) -> None:
    rows: list[tuple[str, str, str, str]] = []
    for rec in records:
        summary.fetched += 1
        if rec is None:
            summary.unparseable += 1
            continue
        if not is_analysable(rec.variant):
            summary.chess960 += 1
            continue
        moves, fens = opening_prefix(rec)
        if moves is None or fens is None:
            summary.unparseable += 1
            continue
        rows.append((rec.platform, rec.platform_game_id, moves, fens))
    if not rows:
        return
    with conn.transaction():
        conn.execute(
            "CREATE TEMP TABLE backfill_prefix (platform text NOT NULL, platform_game_id text NOT NULL,"
            " moves jsonb NOT NULL, fens jsonb NOT NULL) ON COMMIT DROP"
        )
        with conn.cursor().copy("COPY backfill_prefix (platform, platform_game_id, moves, fens) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        found = conn.execute(
            "SELECT count(DISTINCT (t.platform, t.platform_game_id)) AS n FROM backfill_prefix t"
            " JOIN chess_games cg ON cg.platform = t.platform AND cg.platform_game_id = t.platform_game_id"
        ).fetchone()
        update = cast(
            LiteralString,
            f"""
            UPDATE chess_games cg
               SET opening_moves = t.moves, opening_keys = public.bq_opening_keys(t.fens)
              FROM (SELECT DISTINCT ON (platform, platform_game_id) * FROM backfill_prefix) t
             WHERE cg.platform = t.platform AND cg.platform_game_id = t.platform_game_id
               AND cg.opening_keys IS NULL AND {analysable_sql("cg")}
            """,
        )  # the only interpolation is the shared eligibility predicate
        updated = conn.execute(update).rowcount
    n_found = int(found["n"]) if found else 0
    distinct = len({(r[0], r[1]) for r in rows})
    summary.updated += updated
    summary.already_had += n_found - updated
    summary.not_in_db += distinct - n_found


def _history_end(conn: Connection[Any], now: datetime) -> datetime:
    """Where Review's history ends: the newest of Rob's analysable games (core.review.positions
    counts back from it), or now when he has none. A break in his play must not leave the
    oldest part of the history without a prefix."""
    query = cast(
        LiteralString,
        f"""
        SELECT max(cg.played_at) AS newest FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %s AND {analysable_sql("cg")}
        """,
    )
    row = conn.execute(query, (PLAYER_ID,)).fetchone()
    newest = row["newest"] if row else None
    conn.commit()
    return min(now, newest) if newest is not None else now


def backfill_openings(
    conn: Connection[Any],
    *,
    months: int,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    summary = BackfillSummary()
    names = usernames(conn)
    conn.commit()
    client = client or httpx.Client()
    cutoff = _history_end(conn, now or datetime.now(UTC)) - timedelta(days=30 * months)
    if names.get("chesscom"):
        try:
            for records in walk.chesscom_games(client, str(names["chesscom"]), since=cutoff, cutoff=cutoff):
                _apply(conn, records, summary)
        except FetchError:
            summary.failed += 1
    if names.get("lichess"):
        try:
            batch: list[GameRecord | None] = []
            for game in lichess.stream_games(client, str(names["lichess"]), int(cutoff.timestamp() * 1000)):
                if lichess.is_ongoing(game):
                    continue
                batch.append(lichess.parse_game(game, str(names["lichess"])))
                if len(batch) >= LICHESS_BATCH:
                    _apply(conn, batch, summary)
                    batch = []
            _apply(conn, batch, summary)
        except FetchError:
            summary.failed += 1
    return asdict(summary)
