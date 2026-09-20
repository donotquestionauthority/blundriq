"""Normalisation of platform-specific vocabularies at import time.

Termination (chess_games.termination): checkmate, resignation, timeout,
abandonment, agreement, repetition, stalemate, insufficient, fifty_move, draw
(Lichess collapses every draw mechanism), other, or NULL when unknown.

Time class (chess_games.time_class): bullet, blitz, rapid, classical,
correspondence, or NULL. Materialised at import because the Lichess speed label
only exists in the API response.
"""

from __future__ import annotations

from typing import Any

from core.ingest.records import sub, text

_CHESSCOM_LOSS = {
    "checkmated": "checkmate",
    "resigned": "resignation",
    "timeout": "timeout",
    "abandoned": "abandonment",
    "lose": "other",
}
_CHESSCOM_DRAW = {
    "agreed": "agreement",
    "repetition": "repetition",
    "stalemate": "stalemate",
    "insufficient": "insufficient",
    "timevsinsufficient": "insufficient",
    "50move": "fifty_move",
}
_LICHESS_STATUS = {
    "mate": "checkmate",
    "resign": "resignation",
    "outoftime": "timeout",
    "timeout": "timeout",
    "noStart": "abandonment",
    "aborted": "abandonment",
    "stalemate": "stalemate",
    "draw": "draw",
    "cheat": "other",
    "unknownFinish": "other",
    "variantEnd": "other",
}


def chesscom_termination(game: dict[str, Any], username: str) -> str | None:
    """Chess.com puts the reason on the LOSER's record; for a win read the opponent's code."""
    white = sub(game, "white")
    black = sub(game, "black")
    if text(white, "username").lower() == username.lower():
        player, opponent = white, black
    else:
        player, opponent = black, white
    code = text(player, "result")
    if code == "win":
        code = text(opponent, "result")
    if not code or code == "win":
        return None
    return _CHESSCOM_LOSS.get(code) or _CHESSCOM_DRAW.get(code) or "other"


def lichess_termination(status: str | None) -> str | None:
    if not status:
        return None
    return _LICHESS_STATUS.get(status, "other")


def chesscom_time_class(time_control: str | None) -> str | None:
    """'600+5' style strings bucketed by base seconds; '1/86400' is daily."""
    if not time_control or not time_control.strip():
        return None
    s = time_control.strip()
    if s.startswith("1/"):
        return "correspondence"
    try:
        base = int(s.split("+", 1)[0].strip())
    except ValueError:
        return None
    if base < 180:
        return "bullet"
    if base < 600:
        return "blitz"
    if base < 1800:
        return "rapid"
    if base < 10800:
        return "classical"
    return "correspondence"


def lichess_time_class(speed: str | None) -> str | None:
    if not speed:
        return None
    s = speed.strip().lower()
    if s in ("ultrabullet", "bullet"):
        return "bullet"
    if s in ("blitz", "rapid", "classical", "correspondence"):
        return s
    return None
