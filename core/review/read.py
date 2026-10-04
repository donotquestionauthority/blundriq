"""The Review page: positions ranked by what they cost now, mistake habits, and lost wins.

Everything is derived at read time under the two filters (`core.review.filters`): the positions
from the opening prefix of every game in the history (`core.review.positions`), the habits and
the lost wins from the stored review events (`core.review.habits`, below). Nothing is stored.

The event fetch joins `analysable_sql('cg')`, the shared eligibility predicate: a review event
for a Chess960 game (the writer never produces one) is invisible here.
"""

from __future__ import annotations

import json
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import CLOCK_DECIDED_TERMINATIONS, PLAYER_ID
from core.review import habits, positions
from core.review.filters import (
    OPENING_ALL,
    Opening,
    ReviewParamError,
    opening_params,
    opening_sql,
    parse_opening,
    time_class_sql,
)
from core.review.position import move_label
from core.settings import Settings

Event = dict[str, Any]


def fetch_events(conn: Connection[Any], config: Settings, time_class: str, opening: Opening) -> list[Event]:
    """Every stored review event under both filters, with the game context a row renders."""
    query = cast(
        LiteralString,
        f"""
        SELECT re.chess_game_id, re.anchor_ply, re.base_route, re.evidence, re.cost, re.phase, re.piece_label,
               cg.moves ->> re.anchor_ply AS san, cg.played_at, cg.time_class, cg.termination, cg.url,
               cg.canonical_family, pg.player_color, pg.result, pg.opponent_username, pg.opponent_rating,
               pg.reviewed_at
        FROM review_events re
        JOIN chess_games cg ON cg.id = re.chess_game_id
        JOIN player_games pg ON pg.chess_game_id = re.chess_game_id AND pg.player_id = re.player_id
        WHERE re.player_id = %(pid)s AND {analysable_sql("cg")}
          AND {time_class_sql(time_class, config.time_class_focus)} AND {opening_sql(opening)}
        ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC, re.anchor_ply
        """,
    )
    rows = [dict(r) for r in conn.execute(query, {"pid": PLAYER_ID, **opening_params(opening)}).fetchall()]
    for r in rows:
        ev = r.get("evidence")
        if isinstance(ev, str):
            try:
                r["evidence"] = json.loads(ev)
            except ValueError:
                r["evidence"] = {}
        elif not isinstance(ev, dict):
            r["evidence"] = {}
    return rows


# --- Lost wins --------------------------------------------------------------------------------


def _peak(events: list[Event]) -> float | None:
    peaks: list[float] = []
    for e in events:
        try:
            peaks.append(float(e["evidence"].get("game_peak_es")))
        except (TypeError, ValueError):
            pass
    return max(peaks) if peaks else None


def _turning(events: list[Event]) -> Event:
    """A game's turning point: its costliest event, ties to the earlier move."""
    return min(events, key=lambda e: (-float(e["cost"] or 0), int(e["anchor_ply"])))


def select_lost_wins(events: list[Event], faded_peak_es: int) -> list[dict[str, Any]]:
    """Games Rob was winning and did not win (a `faded` event, or a game peak at least the faded
    peak), never a clock-decided one: unreviewed first, then the most recent. Each row carries
    its peak expected score and its turning point."""
    by_game: dict[int, list[Event]] = {}
    for e in events:
        by_game.setdefault(int(e["chess_game_id"]), []).append(e)
    out: list[dict[str, Any]] = []
    for gid, evs in by_game.items():
        meta = evs[0]
        if meta["result"] == "win" or (meta["termination"] or "") in CLOCK_DECIDED_TERMINATIONS:
            continue
        peak = _peak(evs)
        if not any(e["base_route"] == "faded" for e in evs) and (peak is None or peak < faded_peak_es):
            continue
        turn = _turning(evs)
        played = meta["played_at"]
        out.append(
            {
                "chess_game_id": gid,
                "played_at": played.isoformat() if played else None,
                "opponent_username": meta["opponent_username"],
                "opponent_rating": meta["opponent_rating"],
                "result": meta["result"],
                "time_class": meta["time_class"],
                "url": meta["url"],
                "reviewed": meta["reviewed_at"] is not None,
                "peak_es": round(peak, 1) if peak is not None else None,
                "anchor_ply": int(turn["anchor_ply"]),
                "anchor_move": move_label(int(turn["anchor_ply"]), turn["san"]) if turn["san"] else None,
                "cost": round(float(turn["cost"] or 0), 1),
                "_played": played.timestamp() if played else 0.0,
            }
        )
    out.sort(key=lambda r: (1 if r["reviewed"] else 0, -r["_played"], -r["chess_game_id"]))
    for r in out:
        del r["_played"]
    return out


# --- The page ---------------------------------------------------------------------------------


def checked_opening(conn: Connection[Any], config: Settings, time_class: str, key: str) -> tuple[Opening, list[Any]]:
    """The parsed opening and the filter's choices; a key that is not one of them is a
    ReviewParamError saying `unknown opening key`."""
    opening = parse_opening(key)
    options = positions.opening_options(conn, config, time_class)
    if key != OPENING_ALL and key not in {o["key"] for o in options}:
        raise ReviewParamError(f"unknown opening key: {key!r}")
    return opening, options


def page(
    conn: Connection[Any], config: Settings, *, time_class: str = "focus", opening: str = OPENING_ALL
) -> dict[str, Any]:
    parsed, options = checked_opening(conn, config, time_class, opening)
    scope = positions.Scope(config, time_class, parsed)
    meta = positions.meta(conn, scope)
    habit_rows, window_games = habits.habits(conn, config, time_class, parsed)
    lost = select_lost_wins(fetch_events(conn, config, time_class, parsed), config.review_faded_peak_es)
    as_of = meta.get("as_of")
    return {
        "positions": positions.ranked_positions(conn, scope),
        "habits": habit_rows,
        "lost_wins": {"games": lost, "total": len(lost)},
        "filter": {"time_class": time_class, "opening": opening, "openings": options},
        "meta": {
            "as_of": as_of.isoformat() if as_of else None,
            "history_months": config.review_history_months,
            "games_counted": int(meta.get("games_counted") or 0),
            "games_without_prefix": int(meta.get("games_without_prefix") or 0),
            "games_without_ratings": int(meta.get("games_without_ratings") or 0),
            "window_games": window_games,
        },
    }
