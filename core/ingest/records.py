"""The one shape both importers produce and the store writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


def sub(d: dict[str, Any], key: str) -> dict[str, Any]:
    """A nested object from a platform payload, or {} — typed, so strict checking holds."""
    v: Any = d.get(key)
    return dict(v) if isinstance(v, dict) else {}  # type: ignore[reportUnknownArgumentType]


def text(d: dict[str, Any], key: str) -> str:
    v = d.get(key)
    return v if isinstance(v, str) else ""


def integer(d: dict[str, Any], key: str) -> int | None:
    v = d.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


class FetchError(RuntimeError):
    """A platform request failed (transport, non-200, rate limit, bad body).
    The import step fails and the next scheduled run retries; nothing is recorded
    as a completed import."""


@dataclass(frozen=True)
class GameRecord:
    platform: str  # 'chesscom' | 'lichess'
    platform_game_id: str
    url: str
    played_at: datetime | None
    time_control: str | None
    time_class: str | None
    opening_name: str | None
    opening_eco: str | None
    moves: list[str]
    fen_sequence: list[str]
    clocks: list[int] | None  # seconds remaining for the mover after each move; None = not captured
    termination: str | None
    variant: str  # 'standard' | 'chess960'
    starting_fen: str | None  # canonical X-FEN for chess960, None for standard
    player_color: str  # 'white' | 'black'
    opponent_username: str | None
    opponent_rating: int | None
    player_rating: int | None
    result: str  # 'win' | 'loss' | 'draw'
