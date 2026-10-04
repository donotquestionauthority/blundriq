"""Mistake habits: the stored review events grouped by what kind of mistake they are.

The events cover the analysis window (`core.chess.eligibility.window_cte`), so the habits do too:
the denominator is the window's analysed games under the time-class and opening filters, and
only those games' events count. Each event belongs to one habit, by its stored route:

- `lapse_offense`: `missed:{theme}` (the motif theme, else the piece label, else `missed_win`);
- `lapse_defense` and `endgame_technique`: `lost:{phase}` (a missing phase is the middlegame);
- `faded`: `faded`.

Per habit: events per 100 games over the window, the same rate weighted toward recent games
(the half-life in days and `as_of` of the position ranking), and the points it costs a month
(each event's cost is expected-score points, so cost / 100 is game points). The trend compares
the weighted rate with the window's; below REVIEW_HABIT_MIN_WEIGHT weighted games nothing is
claimed.
"""

from __future__ import annotations

import math
import re
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql, window_cte
from core.constants import (
    CC0_SERVE_THEMES,
    PLAYER_ID,
    REVIEW_HABIT_BETTER,
    REVIEW_HABIT_MIN_EVENTS,
    REVIEW_HABIT_MIN_WEIGHT,
    REVIEW_HABIT_WORSE,
)
from core.review.filters import Opening, ReviewParamError, opening_params, opening_sql, time_class_sql
from core.review.position import move_label
from core.settings import Settings

PAGE_SIZE = 50
PHASES = ("opening", "middlegame", "endgame")
_HABIT_ID = re.compile(r"^(faded|lost:(opening|middlegame|endgame)|missed:[A-Za-z0-9_-]{1,40})$")
_LN2 = math.log(2)


def offense_theme(e: dict[str, Any]) -> str:
    """A missed win's key: the motif theme on the evidence, else the piece label, else the
    generic bucket."""
    theme = e.get("theme")
    if isinstance(theme, str) and theme:
        return theme
    return e.get("piece_label") or "missed_win"


def habit_id(e: dict[str, Any]) -> str:
    route = e["base_route"]
    if route == "lapse_offense":
        return f"missed:{offense_theme(e)}"
    if route in ("lapse_defense", "endgame_technique"):
        phase = e.get("phase")
        return f"lost:{phase if phase in PHASES else 'middlegame'}"
    return "faded"


def _words(token: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", token.replace("_", " ")).lower()


def habit_label(hid: str) -> str:
    if hid == "faded":
        return "Let a winning position fade"
    kind, _, sub = hid.partition(":")
    if kind == "lost":
        return f"Lost material in the {sub}"
    if sub == "mate":
        return "Missed mate"
    if sub in ("material", "missed_win"):
        return "Missed winning material"
    words = _words(sub)
    return f"Missed {'an' if words[:1] in ('a', 'e', 'i', 'o', 'u') else 'a'} {words}"


def practice_theme(hid: str) -> str | None:
    """The Practice motif a habit drills, when Practice serves it."""
    kind, _, sub = hid.partition(":")
    return sub if kind == "missed" and sub in CC0_SERVE_THEMES else None


def habit_trend(rate: float, current: float, weighted_games: float) -> str | None:
    """`worse` when the weighted rate is at least REVIEW_HABIT_WORSE times the window's,
    `improving` at most REVIEW_HABIT_BETTER times, else `steady`; nothing on too few games."""
    if weighted_games < REVIEW_HABIT_MIN_WEIGHT:
        return None
    if current >= REVIEW_HABIT_WORSE * rate:
        return "worse"
    if current <= REVIEW_HABIT_BETTER * rate:
        return "improving"
    return "steady"


def check_habit_id(hid: str) -> None:
    if not _HABIT_ID.match(hid):
        raise ReviewParamError(f"unknown habit: {hid!r}")


def _window_games(conn: Connection[Any], config: Settings, time_class: str, opening: Opening) -> list[dict[str, Any]]:
    """The window's analysed games under both filters, each with its recency weight."""
    time_sql = time_class_sql(time_class, config.time_class_focus)
    query = cast(
        LiteralString,
        f"""
        WITH {window_cte()},
        asof AS (
            SELECT max(cg.played_at) AS t FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
            WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")} AND {time_sql}
        )
        SELECT cg.id,
               power(0.5::float8, greatest(0.0, extract(epoch FROM (a.t - cg.played_at)) / 86400.0)
                     / %(half_life)s) AS w
        FROM window_games wg JOIN chess_games cg ON cg.id = wg.chess_game_id
        JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
        CROSS JOIN asof a
        WHERE pg.analyzed_at_depth IS NOT NULL AND {time_sql} AND {opening_sql(opening)}
        """,
    )
    params = {
        "pid": PLAYER_ID,
        "window": config.analysis_game_limit,
        "half_life": float(config.review_recency_half_life_days),
        **opening_params(opening),
    }
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def _events(conn: Connection[Any], ids: list[int]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT re.chess_game_id, re.anchor_ply, re.base_route, re.phase, re.piece_label, re.cost,
               re.evidence ->> 'theme' AS theme, cg.moves ->> re.anchor_ply AS san, cg.played_at,
               pg.opponent_username, pg.opponent_rating, pg.result, pg.player_color,
               pg.reviewed_at IS NOT NULL AS reviewed
        FROM review_events re JOIN chess_games cg ON cg.id = re.chess_game_id
        JOIN player_games pg ON pg.chess_game_id = re.chess_game_id AND pg.player_id = re.player_id
        WHERE re.player_id = %s AND re.chess_game_id = ANY(%s)
        """,
        (PLAYER_ID, ids),
    ).fetchall()
    return [dict(r) for r in rows]


def habits(
    conn: Connection[Any], config: Settings, time_class: str, opening: Opening
) -> tuple[list[dict[str, Any]], int]:
    """(the habits worth showing, ordered by points a month; the window's game count)."""
    games = _window_games(conn, config, time_class, opening)
    if not games:
        return [], 0
    weight = {int(g["id"]): float(g["w"]) for g in games}
    total_w = sum(weight.values())
    n_games = len(games)
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in _events(conn, list(weight)):
        groups.setdefault(habit_id(e), []).append(e)
    per_month = 30 * _LN2 / config.review_recency_half_life_days
    out: list[dict[str, Any]] = []
    for hid, evs in groups.items():
        if len(evs) < REVIEW_HABIT_MIN_EVENTS:
            continue
        rate = 100 * len(evs) / n_games
        w_events = sum(weight[int(e["chess_game_id"])] for e in evs)
        current = 100 * w_events / total_w if total_w > 0 else 0.0
        trend = habit_trend(rate, current, total_w)
        points = sum(weight[int(e["chess_game_id"])] * float(e["cost"] or 0) / 100 for e in evs) * per_month
        out.append(
            {
                "id": hid,
                "label": habit_label(hid),
                "events": len(evs),
                "games": len({e["chess_game_id"] for e in evs}),
                "rate_per_100": round(rate, 1),
                "current_rate_per_100": round(current, 1),
                "points_per_month": round(points, 2),
                "trend": trend,
                "practice_theme": practice_theme(hid),
            }
        )
    out.sort(key=lambda h: (-h["points_per_month"], h["id"]))
    return out, n_games


def habit_games(
    conn: Connection[Any], config: Settings, time_class: str, opening: Opening, hid: str, page: int
) -> dict[str, Any]:
    """One habit's games, one row each at its costliest event of the habit (ties to the earlier
    move): unreviewed first, then the most recent."""
    check_habit_id(hid)
    games = _window_games(conn, config, time_class, opening)
    best: dict[int, dict[str, Any]] = {}
    for e in _events(conn, [int(g["id"]) for g in games]):
        if habit_id(e) != hid:
            continue
        gid = int(e["chess_game_id"])
        cur = best.get(gid)
        if cur is None or (float(e["cost"] or 0), -int(e["anchor_ply"])) > (
            float(cur["cost"] or 0),
            -int(cur["anchor_ply"]),
        ):
            best[gid] = e
    rows = sorted(
        best.values(),
        key=lambda e: (
            1 if e["reviewed"] else 0,
            -(e["played_at"].timestamp() if e["played_at"] else 0.0),
            -int(e["chess_game_id"]),
        ),
    )
    total = len(rows)
    start = (page - 1) * PAGE_SIZE
    return {
        "rows": [
            {
                "chess_game_id": int(e["chess_game_id"]),
                "played_at": e["played_at"].isoformat() if e["played_at"] else None,
                "opponent_username": e["opponent_username"],
                "opponent_rating": e["opponent_rating"],
                "result": e["result"],
                "anchor_ply": int(e["anchor_ply"]),
                "anchor_move": move_label(int(e["anchor_ply"]), e["san"]) if e["san"] else None,
                "cost": round(float(e["cost"] or 0), 1),
                "reviewed": bool(e["reviewed"]),
            }
            for e in rows[start : start + PAGE_SIZE]
        ],
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "total_pages": max(1, -(-total // PAGE_SIZE)),
    }
