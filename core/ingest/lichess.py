"""Lichess API: one NDJSON stream of a player's games, newest first.

What the old system learned about this API (the 429 and User-Agent rules are
in core/ingest/chesscom.py and hold here too):

- Never pass a server-side `max`: eligibility (variant, no moves, a FEN that
  will not parse) is decided here after the stream arrives, so a server cap
  would let skipped rows consume it and starve older eligible games. A caller
  that wants N games stops consuming after N are stored.
- It is one stream with no archive boundaries, so the importer commits every
  N games, and a connection dropped mid-stream is an INCOMPLETE import that
  must not advance the boundary; it says nothing about the platform's health,
  which did answer. One malformed line is one skipped game, not a failed run.
- Ids are 8 characters; the 12-character id in a player-perspective URL has
  the game id as its prefix. `players.<side>.user.name` is the display casing
  and the profile's `id` is the canonical lowercase form; lookups are
  case-insensitive and tolerant (200, no redirect). A closed account can be
  200 with `disabled` or `closed` set.
- Timestamps are milliseconds; `lastMoveAt` is preferred over `createdAt` for
  `played_at`. `clocks` are CENTISECONDS remaining for the mover. `speed`
  exists only in the API response, which is why time_class is materialised at
  import; correspondence games carry no `clock`, so their time control is the
  speed label. `initialFen` appears only for non-standard starts and has been
  seen in both X-FEN and Shredder-FEN forms.
- `status` collapses every draw mechanism to `draw`; `outoftime` is a clock
  that ran out and `timeout` an opponent who left with a clock running (both
  are `timeout` here). `winner` is absent on a draw.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from core.chess.board import canonicalize_chess960_fen, moves_to_fen_sequence
from core.chess.platform import lichess_termination, lichess_time_class
from core.ingest.chesscom import USER_AGENT
from core.ingest.records import FetchError, GameRecord, integer, sub, text

API = "https://lichess.org/api"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/x-ndjson"}
TIMEOUT = 30.0


ONGOING_STATUSES = ("created", "started")


def is_ongoing(game: dict[str, Any]) -> bool:
    return text(game, "status") in ONGOING_STATUSES


def stream_games(
    client: httpx.Client, username: str, since_ms: int, *, finished: bool = True, sort: str = "dateDesc"
) -> Iterator[dict[str, Any]]:
    """Yield raw game dicts, ONGOING GAMES INCLUDED (`ongoing=true`): an importer never
    stores them, but it needs their creation times to place the next import's boundary
    (see core/ingest/run.py and core/scout/importing.py). `since` filters by creation time.
    `finished=False` asks for the games in progress only — the cheap request the opponent
    importer discovers its boundary with. A transport failure mid-stream raises FetchError."""
    params = {
        "since": since_ms,
        "sort": sort,
        "moves": "true",
        "opening": "true",
        "clocks": "true",
        "evals": "false",
        "ongoing": "true",
        "finished": "true" if finished else "false",
    }
    try:
        with client.stream("GET", f"{API}/games/user/{username}", params=params, headers=HEADERS, timeout=TIMEOUT) as r:
            if r.status_code == 429:
                raise FetchError("lichess rate limited (429)")
            if r.status_code != 200:
                raise FetchError(f"lichess HTTP {r.status_code} for games stream")
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    game = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(game, dict):
                    yield sub({"g": game}, "g")
    except httpx.HTTPError as exc:
        raise FetchError(f"lichess transport error: {type(exc).__name__}") from exc


def parse_game(game: dict[str, Any], username: str) -> GameRecord | None:
    variant_raw = text(sub(game, "variant"), "key") or text(game, "variant") or "standard"
    variant = {"standard": "standard", "chess960": "chess960"}.get(variant_raw)
    if variant is None:
        return None
    game_id = text(game, "id")
    if not game_id or is_ongoing(game):
        return None
    moves = text(game, "moves").split()
    if not moves:
        return None

    players = sub(game, "players")
    white = sub(players, "white")
    black = sub(players, "black")
    is_white = text(sub(white, "user"), "name").lower() == username.lower()
    me, opp = (white, black) if is_white else (black, white)
    winner = text(game, "winner") or None
    status = text(game, "status") or None
    if status in ("draw", "stalemate") or winner is None:
        result = "draw"
    else:
        result = "win" if (winner == "white") == is_white else "loss"

    starting_fen = None
    if variant == "chess960":
        fen = text(game, "initialFen")
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

    clocks: list[int] | None = None
    raw_clocks = game.get("clocks")
    if isinstance(raw_clocks, list) and len(raw_clocks) == len(moves):  # type: ignore[reportUnknownArgumentType]
        try:
            clocks = [int(c) // 100 for c in raw_clocks]  # type: ignore[reportUnknownArgumentType]  # centiseconds → seconds
        except (TypeError, ValueError):
            clocks = None

    opening = sub(game, "opening")
    clock = sub(game, "clock")
    tc = f"{integer(clock, 'initial') or 0}+{integer(clock, 'increment') or 0}" if clock else text(game, "speed")
    played_ms = integer(game, "lastMoveAt") or integer(game, "createdAt")
    return GameRecord(
        platform="lichess",
        platform_game_id=str(game_id),
        url=f"https://lichess.org/{game_id}",
        played_at=datetime.fromtimestamp(played_ms / 1000, tz=UTC) if played_ms else None,
        time_control=tc,
        time_class=lichess_time_class(text(game, "speed")),
        opening_name=text(opening, "name"),
        opening_eco=text(opening, "eco"),
        moves=moves,
        fen_sequence=fens,
        clocks=clocks,
        termination=lichess_termination(status),
        variant=variant,
        starting_fen=starting_fen,
        player_color="white" if is_white else "black",
        opponent_username=text(sub(opp, "user"), "name") or None,
        opponent_rating=integer(opp, "rating"),
        player_rating=integer(me, "rating"),
        result=result,
    )
