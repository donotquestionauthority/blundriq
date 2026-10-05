"""`pipeline backfill-openings`: the opening prefix for games stored before it existed.

Housekeeping nulls `moves` and `fen_sequence` outside the analysis window, so a game that left
the window before migration 007 has no prefix to derive one from. This re-fetches the player's own
games from both platforms and writes ONLY the prefix (`opening_moves`, `opening_keys`) onto
rows that already exist and have none:

- never `upsert_game`: its NULL → value ratchet would put `moves` and `fen_sequence` back on
  every old game, for housekeeping to null an hour later;
- never an insert: a fetched game that is not in chess_games is counted (`not_in_db`) and left
  to the importer, which owns inserts;
- never an overwrite: a row with a prefix keeps it (`already_had`);
- a variant the pipeline does not analyse gets none (core.chess.eligibility; counted
  `chess960`).

How far back is Review's own history, from the same SQL (`core.review.positions.history_start`):
`months` calendar months before the newest of the player's analysable games, inclusive. Chess.com is
walked by monthly archive from that month (its archives and `played_at` are both the game's end
time). Lichess cannot be walked that way: its stream's lower bound is a game's CREATION time
while `played_at` is its last move, so a correspondence game begun before the boundary and
finished inside it would never be streamed. Instead the stored Lichess games inside the history
that still lack a prefix are fetched by id (`lichess.export_games`, up to IDS_PER_REQUEST at a
time); an id the platform does not return is counted `not_returned`.

Rows go by COPY into a temporary table and one UPDATE per batch (a Chess.com monthly archive,
or one Lichess export), one transaction each, so an interrupted run keeps every batch it
finished and a rerun skips them. A platform request that fails stops that platform and counts
`failed`, which fails the step. Counts only: no handle, URL or game id is printed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, LiteralString, cast

import httpx
from psycopg import Connection

from core.chess.eligibility import analysable_sql, is_analysable
from core.constants import PLAYER_ID
from core.ingest import lichess, walk
from core.ingest.records import FetchError, GameRecord
from core.ingest.store import opening_prefix
from core.player import usernames
from core.review import positions


@dataclass
class BackfillSummary:
    fetched: int = 0
    updated: int = 0
    already_had: int = 0
    not_in_db: int = 0
    unparseable: int = 0
    chess960: int = 0
    not_returned: int = 0
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


def _missing_lichess_ids(conn: Connection[Any], months: int) -> list[str]:
    """The player's stored Lichess games inside the history that have no prefix, oldest first."""
    query = cast(
        LiteralString,
        f"""
        SELECT cg.platform_game_id FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s AND cg.platform = 'lichess' AND {analysable_sql("cg")}
          AND cg.opening_keys IS NULL AND cg.played_at >= {positions.history_start_sql()}
        ORDER BY cg.played_at, cg.id
        """,
    )
    ids = [str(r["platform_game_id"]) for r in conn.execute(query, {"pid": PLAYER_ID, "months": months})]
    conn.commit()
    return ids


def backfill_openings(conn: Connection[Any], *, months: int, client: httpx.Client | None = None) -> dict[str, int]:
    summary = BackfillSummary()
    names = usernames(conn)
    start = positions.history_start(conn, months)
    conn.commit()
    if start is None:  # no analysable game at all: there is no history to fill
        return asdict(summary)
    client = client or httpx.Client()
    if names.get("chesscom"):
        try:
            for records in walk.chesscom_games(client, str(names["chesscom"]), since=start, cutoff=start):
                _apply(conn, records, summary)
        except FetchError:
            summary.failed += 1
    if names.get("lichess"):
        username = str(names["lichess"])
        missing = _missing_lichess_ids(conn, months)
        try:
            for i in range(0, len(missing), lichess.IDS_PER_REQUEST):
                asked = missing[i : i + lichess.IDS_PER_REQUEST]
                games = [g for g in lichess.export_games(client, asked) if not lichess.is_ongoing(g)]
                summary.not_returned += len(set(asked) - {str(g.get("id")) for g in games})
                _apply(conn, [lichess.parse_game(g, username) for g in games], summary)
        except FetchError:
            summary.failed += 1
    return asdict(summary)
