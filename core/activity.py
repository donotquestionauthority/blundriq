"""How many games an account has played lately: the four counts Home shows for the player
and the Scout header shows for an opponent, from one query.

The player's games are every `player_games` row, all variants: Home measures playing, not
analysis, so a Chess960 game counts (as it does in Home's daily target). An opponent's games
are that profile's `opponent_views` rows. The query counts games whose `played_at` falls in
each trailing interval, in total and per platform; the pages render 24 h / 7 d / 30 d / all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.constants import PLAYER_ID

INTERVALS: tuple[tuple[str, str], ...] = (
    ("last_1", "1 day"),
    ("last_7", "7 days"),
    ("last_30", "30 days"),
    ("last_90", "90 days"),
    ("last_365", "365 days"),
)


@dataclass(frozen=True)
class Player:
    """The player's own games."""


@dataclass(frozen=True)
class Opponent:
    profile_id: int


Owner = Player | Opponent


def _counts_sql(join: str, predicate: str, platform: str) -> str:
    cols: list[str] = []
    for name, interval in INTERVALS:
        window = f"cg.played_at >= now() - INTERVAL '{interval}'"
        cols.append(f"COUNT(*) FILTER (WHERE {window}) AS {name}")
        cols.append(f"COUNT(*) FILTER (WHERE {window} AND {platform} = 'chesscom') AS {name}_cc")
        cols.append(f"COUNT(*) FILTER (WHERE {window} AND {platform} = 'lichess') AS {name}_li")
    cols.append("COUNT(*) AS total")
    cols.append(f"COUNT(*) FILTER (WHERE {platform} = 'chesscom') AS chesscom_total")
    cols.append(f"COUNT(*) FILTER (WHERE {platform} = 'lichess') AS lichess_total")
    return f"SELECT {', '.join(cols)} FROM {join} JOIN chess_games cg ON cg.id = o.chess_game_id WHERE {predicate}"


_PLAYER_SQL = _counts_sql("player_games o", "o.player_id = %(pid)s", "o.source")
_OPPONENT_SQL = _counts_sql("opponent_views o", "o.opponent_profile_id = %(profile_id)s", "o.source_type")


def counts(conn: Connection[Any], owner: Owner) -> dict[str, int]:
    """Every interval's count, in total and per platform, plus `total`."""
    if isinstance(owner, Player):
        row = conn.execute(cast(LiteralString, _PLAYER_SQL), {"pid": PLAYER_ID}).fetchone()
    else:
        row = conn.execute(cast(LiteralString, _OPPONENT_SQL), {"profile_id": owner.profile_id}).fetchone()
    assert row is not None
    return {k: int(v) for k, v in dict(row).items()}
