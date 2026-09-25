"""Opponent profiles: the list, adding one, removing one.

A profile is a name with up to one handle per platform. Adding verifies each handle against
its platform first — `GET api.chess.com/pub/player/{u}` and `GET lichess.org/api/user/{u}`;
the only network calls the API makes to a platform — and stores the platform's own spelling
of it. The new profile is `is_initialized = FALSE`; the next `pipeline import-opponents`
onboards it (core/scout/importing.py). Removing is a hard delete: the profile's sources and
views cascade away, the games stay in `chess_games` (housekeeping nulls their bulk payload once
no owner has them in window). A name that exists is refused rather than re-activated.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from psycopg import Connection

from core.constants import PLAYER_ID
from core.ingest import chesscom, lichess
from core.ingest.records import FetchError, text

PLATFORMS = ("chesscom", "lichess")


class HandleNotFound(LookupError):
    """The platform has no such account. `platform` names which."""

    def __init__(self, platform: str) -> None:
        super().__init__(platform)
        self.platform = platform


class NameTaken(ValueError):
    """A profile of that name exists."""


class BadProfile(ValueError):
    """Empty name, or no handle at all."""


def verify_handle(platform: str, username: str, *, client: httpx.Client | None = None) -> str:
    """The platform's spelling of an existing account's handle. 404 raises HandleNotFound;
    any other failure raises FetchError. The handle is never part of an error message."""
    if platform not in PLATFORMS:
        raise ValueError("unknown platform")
    client = client or httpx.Client()
    handle = quote(username.strip(), safe="")
    if platform == "chesscom":
        url = f"{chesscom.API}/player/{handle.lower()}"
        headers = chesscom.HEADERS
    else:
        url = f"{lichess.API}/user/{handle}"
        headers = {"User-Agent": chesscom.USER_AGENT, "Accept": "application/json"}
    try:
        r = client.get(url, headers=headers, timeout=chesscom.TIMEOUT)
    except httpx.HTTPError as exc:
        raise FetchError(f"{platform} transport error: {type(exc).__name__}") from exc
    if r.status_code == 404:
        raise HandleNotFound(platform)
    if r.status_code != 200:
        raise FetchError(f"{platform} HTTP {r.status_code} for player lookup")
    try:
        body = r.json()
    except ValueError as exc:
        raise FetchError(f"{platform} HTTP 200 with non-JSON body") from exc
    canonical = text(body, "username") if isinstance(body, dict) else ""  # type: ignore[reportUnknownArgumentType]
    return canonical or username.strip()


def listing(conn: Connection[Any]) -> list[dict[str, Any]]:
    """Active profiles by name, each with its handles, cursors and distinct game count."""
    rows = conn.execute(
        """
        SELECT op.id, op.name, op.is_initialized,
               COUNT(DISTINCT ov.chess_game_id) AS game_count,
               MAX(os_cc.username)     AS chesscom_username,
               MAX(os_li.username)     AS lichess_username,
               MAX(os_cc.last_fetched) AS chesscom_last_fetched,
               MAX(os_li.last_fetched) AS lichess_last_fetched
        FROM opponent_profiles op
        LEFT JOIN opponent_views ov ON ov.opponent_profile_id = op.id
        LEFT JOIN opponent_sources os_cc
               ON os_cc.opponent_profile_id = op.id AND os_cc.source_type = 'chesscom' AND os_cc.active
        LEFT JOIN opponent_sources os_li
               ON os_li.opponent_profile_id = op.id AND os_li.source_type = 'lichess' AND os_li.active
        WHERE op.player_id = %(pid)s AND op.active
        GROUP BY op.id, op.name, op.is_initialized
        ORDER BY op.name
        """,
        {"pid": PLAYER_ID},
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        row["game_count"] = int(row["game_count"])
        for key in ("chesscom_last_fetched", "lichess_last_fetched"):
            row[key] = row[key].isoformat() if row[key] is not None else None
        out.append(row)
    return out


def exists(conn: Connection[Any], profile_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM opponent_profiles WHERE id = %s AND player_id = %s AND active", (profile_id, PLAYER_ID)
    ).fetchone()
    return row is not None


def create(
    conn: Connection[Any],
    name: str,
    *,
    chesscom_username: str | None,
    lichess_username: str | None,
    client: httpx.Client | None = None,
) -> int:
    """Verify, then insert the profile and its sources. Returns the profile id."""
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise BadProfile("name must be 1 to 100 characters")
    handles = {
        "chesscom": (chesscom_username or "").strip() or None,
        "lichess": (lichess_username or "").strip() or None,
    }
    if not any(handles.values()):
        raise BadProfile("at least one platform username is required")
    taken = conn.execute(
        "SELECT 1 FROM opponent_profiles WHERE player_id = %s AND name = %s", (PLAYER_ID, name)
    ).fetchone()
    if taken is not None:
        raise NameTaken(name)
    canonical = {p: verify_handle(p, h, client=client) for p, h in handles.items() if h}
    row = conn.execute(
        "INSERT INTO opponent_profiles (player_id, name, active, is_initialized) VALUES (%s, %s, TRUE, FALSE)"
        " RETURNING id",
        (PLAYER_ID, name),
    ).fetchone()
    assert row is not None
    profile_id = int(row["id"])
    for platform, handle in canonical.items():
        conn.execute(
            "INSERT INTO opponent_sources (opponent_profile_id, source_type, username, active)"
            " VALUES (%s, %s, %s, TRUE)",
            (profile_id, platform, handle),
        )
    return profile_id


def delete(conn: Connection[Any], profile_id: int) -> bool:
    """True iff a profile was removed (sources and views cascade)."""
    cur = conn.execute("DELETE FROM opponent_profiles WHERE id = %s AND player_id = %s", (profile_id, PLAYER_ID))
    return cur.rowcount > 0
