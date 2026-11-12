"""The player's most recent finished game on one platform, fetched and parsed, never stored.

Review latest game (GET /review/latest) reads it so a game can be stepped through minutes
after it ends. Nothing here writes: the Chess.com import resumes after the newest stored game
(`run.latest_played_at`), so storing only the latest game would make the next import skip
every game played between the last import and this one.

The game is parsed by the importer's own `parse_game`, so its moves and boards are the ones
the next import stores. Refusals are checked in order: no handle, no game, Chess960 (read from
the raw entry, so a Chess960 game the parser cannot read is still refused as Chess960), then
a game the parser refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx
from psycopg import Connection

from core.chess.eligibility import is_analysable
from core.ingest import chesscom, lichess
from core.ingest.records import GameRecord, sub, text
from core.player import usernames

Platform = Literal["chesscom", "lichess"]
PLATFORM_LABEL: dict[str, str] = {"chesscom": "Chess.com", "lichess": "Lichess"}


@dataclass
class LatestRefused(Exception):
    """A refusal the page shows: `status` is the HTTP status, `code` a stable token the client
    reads, `detail` the sentence it shows. None of them names a handle or a game."""

    status: int
    code: str
    detail: str


def handle_for(conn: Connection[Any], platform: Platform) -> str:
    """The stored handle, or LatestRefused 422 `no handle`. A missing players row is the same."""
    try:
        names = usernames(conn)
    except RuntimeError:
        names = {}
    handle = (names.get(platform) or "").strip()
    if not handle:
        raise LatestRefused(422, "no_handle", f"No {PLATFORM_LABEL[platform]} username is set.")
    return handle


def _raw_variant(platform: Platform, game: dict[str, Any]) -> str:
    if platform == "chesscom":
        return {"chess": "standard"}.get(text(game, "rules") or "chess", text(game, "rules"))
    return text(sub(game, "variant"), "key") or text(game, "variant") or "standard"


def newest_chesscom(client: httpx.Client, username: str) -> dict[str, Any] | None:
    """The entry with the greatest `end_time` in the newest archive. The archives list holds only
    months with games, so the newest archive has one; an empty answer is None."""
    archives = chesscom.fetch_archives(client, username)
    if not archives:
        return None
    games = chesscom.fetch_archive(client, archives[-1])
    best: dict[str, Any] | None = None
    best_at = -1
    for g in games:
        at = g.get("end_time")
        at = at if isinstance(at, int) and not isinstance(at, bool) else -1
        if best is None or at > best_at:
            best, best_at = g, at
    return best


def newest_lichess(client: httpx.Client, username: str) -> dict[str, Any] | None:
    """The first game of a newest-first stream of finished games, one game long."""
    for game in lichess.stream_games(client, username, 0, ongoing=False, max_games=1):
        if not lichess.is_ongoing(game):
            return game
    return None


def latest_game(platform: Platform, username: str, client: httpx.Client) -> GameRecord:
    """Fetch and parse; FetchError propagates (the route maps it). Raises LatestRefused."""
    site = PLATFORM_LABEL[platform]
    raw = newest_chesscom(client, username) if platform == "chesscom" else newest_lichess(client, username)
    if raw is None:
        raise LatestRefused(404, "no_games", f"No finished {site} game was found.")
    if _raw_variant(platform, raw) == "chess960":
        raise LatestRefused(422, "chess960", f"Your latest {site} game is Chess960 — kept as history, not reviewed.")
    rec = chesscom.parse_game(raw, username) if platform == "chesscom" else lichess.parse_game(raw, username)
    if rec is None or not is_analysable(rec.variant):
        raise LatestRefused(
            422, "not_reviewable", f"Your latest {site} game is not a standard game that can be reviewed."
        )
    return rec


def client() -> httpx.Client:
    """The HTTP client the route uses: one place for tests to substitute a transport."""
    return httpx.Client()
