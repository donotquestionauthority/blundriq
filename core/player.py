"""The one players row: platform usernames and the last completed import per platform."""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from core.constants import PLAYER_ID


def set_usernames(conn: Connection[Any], chesscom: str | None, lichess: str | None) -> dict[str, str | None]:
    """Create or update the row; a None leaves that platform's username as it is."""
    row = conn.execute(
        """
        INSERT INTO players (id, chesscom_username, lichess_username) VALUES (%s, %s, %s)
        ON CONFLICT (id) DO UPDATE
        SET chesscom_username = COALESCE(EXCLUDED.chesscom_username, players.chesscom_username),
            lichess_username = COALESCE(EXCLUDED.lichess_username, players.lichess_username)
        RETURNING chesscom_username, lichess_username
        """,
        (PLAYER_ID, chesscom, lichess),
    ).fetchone()
    conn.commit()
    assert row is not None
    return {"chesscom": row["chesscom_username"], "lichess": row["lichess_username"]}


def usernames(conn: Connection[Any]) -> dict[str, str | None]:
    row = conn.execute("SELECT chesscom_username, lichess_username FROM players WHERE id = %s", (PLAYER_ID,)).fetchone()
    if row is None:
        raise RuntimeError("no players row; run `pipeline player set` first")
    return {"chesscom": row["chesscom_username"], "lichess": row["lichess_username"]}
