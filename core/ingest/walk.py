"""The Chess.com archive walk, shared by the player's importer and the opponent importer.

Archives are keyed by month and a game sits in the archive of the month it ended. The walk
yields one list of parsed records per archive, so a caller can store each archive in its own
transaction (an interrupted run leaves whole archives unfetched, never half of one).

Two directions:

* oldest first (`newest=False`), from the archive of `cutoff`'s month onwards, skipping games
  strictly older than `since` (`<`, never `<=`: end times have second resolution and two games
  can end in the same second, so a game sharing the cursor's second is fetched again and dedups
  on the upsert). This is the incremental walk.
* newest first (`newest=True`), the games of each archive reversed to newest first, so a caller
  can take the N most recent games and stop consuming.

`username` is the account the records are parsed for: the player's own, or a scouted
opponent's — the record's colour and result are that account's.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from core.ingest import chesscom
from core.ingest.records import GameRecord


def chesscom_games(
    client: httpx.Client,
    username: str,
    *,
    since: datetime | None = None,
    cutoff: datetime | None = None,
    newest: bool = False,
) -> Iterator[list[GameRecord | None]]:
    """One list per archive; an element is None for a game that does not parse (other rules,
    no PGN, bad moves). A game strictly older than `since` is not in the list at all."""
    archives = chesscom.fetch_archives(client, username)
    if cutoff is not None:
        archives = chesscom.archives_since(archives, cutoff)
    if newest:
        archives = list(reversed(archives))
    for url in archives:
        games = chesscom.fetch_archive(client, url)
        if newest:
            games = list(reversed(games))
        records: list[GameRecord | None] = []
        for game in games:
            end_time = game.get("end_time")
            played_at = datetime.fromtimestamp(int(end_time), tz=UTC) if end_time else None
            if since is not None and played_at is not None and played_at < since:
                continue
            records.append(chesscom.parse_game(game, username))
        yield records
