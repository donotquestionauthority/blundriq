"""Chess.com public API: monthly archives of a player's games.

`parse_game` is pure (fixture-tested); `fetch_archives` / `fetch_archive` do
the HTTP. Games with rules other than chess/chess960, or without a PGN, are
skipped and never stored.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx

from core.chess.board import canonicalize_chess960_fen, moves_to_fen_sequence
from core.chess.platform import chesscom_termination, chesscom_time_class
from core.ingest.records import FetchError, GameRecord, integer, sub, text

USER_AGENT = "blundriq-personal/2 (+https://github.com/donotquestionauthority/blundriq)"
HEADERS = {"User-Agent": USER_AGENT}
API = "https://api.chess.com/pub"
TIMEOUT = 10.0

_TAG = re.compile(r'\[(\w+)\s+"([^"]*)"\]')
_CLK = re.compile(r"\[%clk\s+([0-9:.]+)\]")


def _get_json(client: httpx.Client, url: str) -> dict[str, Any]:  # noqa: D103
    try:
        r = client.get(url, headers=HEADERS, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise FetchError(f"chesscom transport error: {type(exc).__name__}") from exc
    if r.status_code == 429:
        raise FetchError("chesscom rate limited (429)")
    if r.status_code != 200:
        raise FetchError(f"chesscom HTTP {r.status_code} for archive request")
    try:
        body = r.json()
    except ValueError as exc:
        raise FetchError("chesscom HTTP 200 with non-JSON body") from exc
    if not isinstance(body, dict):
        raise FetchError("chesscom HTTP 200 with unexpected JSON shape")
    return {str(k): v for k, v in body.items()}  # type: ignore[reportUnknownVariableType]


def fetch_archives(client: httpx.Client, username: str) -> list[str]:
    """Archive URLs, oldest first (the API's order)."""
    body = _get_json(client, f"{API}/player/{username}/games/archives")
    archives: Any = body.get("archives", [])
    return [str(a) for a in archives] if isinstance(archives, list) else []  # type: ignore[reportUnknownVariableType]


def fetch_archive(client: httpx.Client, url: str) -> list[dict[str, Any]]:
    body = _get_json(client, url)
    games: Any = body.get("games", [])
    if not isinstance(games, list):
        return []
    return [sub({"g": g}, "g") for g in games if isinstance(g, dict)]  # type: ignore[reportUnknownVariableType]


def archives_since(archives: list[str], cutoff: datetime) -> list[str]:
    """Keep archives whose month is at or after the cutoff's UTC month. Archives are keyed by
    UTC month, and a cutoff read from the database carries the session's time zone (a
    mid-September instant in New York is still September 4:00 UTC as a month start, which
    would drop September's archive), so the cutoff is moved to UTC first; a naive cutoff is
    taken as UTC."""
    at = cutoff.astimezone(UTC) if cutoff.tzinfo is not None else cutoff.replace(tzinfo=UTC)
    first = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    keep: list[str] = []
    for url in archives:
        parts = url.rstrip("/").split("/")
        year, month = int(parts[-2]), int(parts[-1])
        if datetime(year, month, 1, tzinfo=UTC) >= first:
            keep.append(url)
    return keep


def parse_headers(pgn: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _TAG.finditer(pgn)}


def parse_moves(pgn: str) -> list[str]:
    text = re.sub(r"\[[^\]]*\]", "", pgn)
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"\s*(1-0|0-1|1/2-1/2)\s*$", "", text)
    text = re.sub(r"\d+\.+", "", text)
    return [m for m in text.split() if m.strip()]


def parse_clocks(pgn: str, n_moves: int) -> list[int] | None:
    """Whole seconds remaining after each move, or None unless exactly one clock per move."""
    parts = re.split(r"\n\s*\n", pgn, maxsplit=1)
    movetext = parts[1] if len(parts) == 2 else pgn
    tokens = _CLK.findall(movetext)
    if len(tokens) != n_moves:
        return None
    clocks: list[int] = []
    for tok in tokens:
        fields = tok.split(":")
        if not 2 <= len(fields) <= 3:
            return None
        secs = 0.0
        try:
            for f in fields:
                secs = secs * 60 + float(f)
        except ValueError:
            return None
        clocks.append(int(secs))
    return clocks


def parse_game(game: dict[str, Any], username: str) -> GameRecord | None:
    """One archive entry → GameRecord, or None when the game is not storable
    (other variant, no PGN, unparseable moves or Chess960 FEN)."""
    variant = {"chess": "standard", "chess960": "chess960"}.get(text(game, "rules") or "chess")
    if variant is None:
        return None
    pgn = text(game, "pgn")
    if not pgn:
        return None
    white = sub(game, "white")
    black = sub(game, "black")
    is_white = text(white, "username").lower() == username.lower()
    me, opp = (white, black) if is_white else (black, white)
    my_result = text(me, "result")
    result = (
        "win"
        if my_result == "win"
        else "loss"
        if my_result in ("checkmated", "timeout", "resigned", "lose", "abandoned")
        else "draw"
    )

    headers = parse_headers(pgn)
    moves = parse_moves(pgn)
    starting_fen = None
    if variant == "chess960":
        fen = headers.get("FEN")
        if not fen:
            return None
        try:
            starting_fen = canonicalize_chess960_fen(fen)
        except ValueError:
            return None
    try:
        fens = moves_to_fen_sequence(moves, starting_fen, variant)
    except ValueError:
        return None

    end_time = integer(game, "end_time")
    eco_url = headers.get("ECOUrl", "")
    opening_name = eco_url.split("/openings/")[-1].replace("-", " ") if eco_url else ""
    url = text(game, "url")
    time_control = text(game, "time_control") or None
    return GameRecord(
        platform="chesscom",
        platform_game_id=url.split("/")[-1],
        url=url,
        played_at=datetime.fromtimestamp(end_time, tz=UTC) if end_time else None,
        time_control=time_control,
        time_class=chesscom_time_class(time_control),
        opening_name=opening_name,
        opening_eco=headers.get("ECO", ""),
        moves=moves,
        fen_sequence=fens,
        clocks=parse_clocks(pgn, len(moves)),
        termination=chesscom_termination(game, username),
        variant=variant,
        starting_fen=starting_fen,
        player_color="white" if is_white else "black",
        opponent_username=text(opp, "username") or None,
        opponent_rating=integer(opp, "rating"),
        player_rating=integer(me, "rating"),
        result=result,
    )
