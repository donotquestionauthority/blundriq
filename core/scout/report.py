"""The Scout header: how active an opponent is and what they play.

Activity is `core.activity.counts`. Openings are grouped by the canonical family written at
import (`chess_games.canonical_family`; a game the classifier could not name is left out):
per colour the top eight families by count, ties broken by the most recent game, cut where
a family falls under 3 %, each with its ten most recent games; "most played" is the same
over both colours. Best and worst lines group the games that carry a result by (family,
variation) and rank them by a win rate shrunk toward the opponent's overall rate,
`(wins + k * overall) / (games + k)` with `k = scout_bayesian_prior_strength`, so a one-game
100 % line cannot top the list; `overall` is over every result-bearing game in the window, not
the bucketed subset. `line_games` is the drill-down behind a row: the family is required and
the variation narrows within it, so a row's games are the bucket its rate was computed over.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, LiteralString, cast

from psycopg import Connection

from core import activity
from core.chess.eligibility import analysable_sql
from core.scout.positions import game_url

Row = dict[str, Any]

TOP_FAMILIES = 8
MIN_SHARE_PCT = 3
GAMES_PER_FAMILY = 10
TOP_LINES = 5
LINE_GAMES_LIMIT = 50


def _since_clause(opp_since: datetime | None) -> str:
    return "AND cg.played_at >= %(opp_since)s" if opp_since else ""


def _families(conn: Connection[Any], params: dict[str, Any], since: str) -> list[Row]:
    return [
        dict(r)
        for r in conn.execute(
            cast(
                LiteralString,
                f"""
                SELECT ov.played_as, cg.canonical_family AS family, COUNT(*) AS cnt, MAX(cg.played_at) AS last_seen
                FROM opponent_views ov JOIN chess_games cg ON cg.id = ov.chess_game_id
                WHERE ov.opponent_profile_id = %(profile_id)s AND {analysable_sql("cg")}
                  AND cg.canonical_family IS NOT NULL {since}
                GROUP BY ov.played_as, cg.canonical_family
                """,
            ),
            params,
        ).fetchall()
    ]


def _family_games(conn: Connection[Any], params: dict[str, Any], since: str) -> dict[tuple[str, str], list[Row]]:
    games: dict[tuple[str, str], list[Row]] = {}
    rows = conn.execute(
        cast(
            LiteralString,
            f"""
            SELECT played_as, family, played_at, source_type, platform_game_id FROM (
                SELECT ov.played_as, cg.canonical_family AS family, cg.played_at, ov.source_type, cg.platform_game_id,
                       ROW_NUMBER() OVER (PARTITION BY ov.played_as, cg.canonical_family
                                          ORDER BY cg.played_at DESC NULLS LAST) AS rn
                FROM opponent_views ov JOIN chess_games cg ON cg.id = ov.chess_game_id
                WHERE ov.opponent_profile_id = %(profile_id)s AND {analysable_sql("cg")}
                  AND cg.canonical_family IS NOT NULL {since}
            ) t
            WHERE rn <= %(per_family)s
            ORDER BY played_as, family, played_at DESC NULLS LAST
            """,
        ),
        params | {"per_family": GAMES_PER_FAMILY},
    ).fetchall()
    for r in rows:
        games.setdefault((r["played_as"], r["family"]), []).append(
            {
                "date": r["played_at"].strftime("%Y-%m-%d") if r["played_at"] else "",
                "url": game_url(r["source_type"], r["platform_game_id"]),
            }
        )
    return games


def ordered(fam_map: dict[str, tuple[int, datetime | None]]) -> list[tuple[str, int]]:
    """Families by count, then most recent game, NULL dates last."""

    def key(item: tuple[str, tuple[int, datetime | None]]) -> tuple[int, bool, float]:
        _fam, (cnt, last_seen) = item
        return (-cnt, last_seen is None, -last_seen.timestamp() if last_seen is not None else 0.0)

    return [(fam, v[0]) for fam, v in sorted(fam_map.items(), key=key)]


def pct_list(fam_map: dict[str, tuple[int, datetime | None]]) -> list[Row]:
    total = sum(v[0] for v in fam_map.values())
    if not total:
        return []
    out: list[Row] = []
    for family, cnt in ordered(fam_map)[:TOP_FAMILIES]:
        pct = round(cnt / total * 100)
        if pct < MIN_SHARE_PCT:
            break
        out.append({"family": family, "cnt": cnt, "pct": pct})
    return out


def shrink(lines: list[Row], overall_rate: float, k: int) -> list[Row]:
    """Each bucket's shrunk rate added, then the sort the page shows."""
    for line in lines:
        g, w = line["games"], line["wins"]
        line["shrunk_rate"] = float((w + k * overall_rate) / (g + k)) if (g + k) else 0.0
        line["win_pct"] = round(w / g * 100, 1) if g else 0.0
    lines.sort(key=lambda d: (-d["shrunk_rate"], -d["games"], d["variation"], d["family"]))
    return lines


def best_worst(conn: Connection[Any], profile_id: int, k: int, opp_since: datetime | None) -> dict[str, list[Row]]:
    params: dict[str, Any] = {"profile_id": profile_id, "opp_since": opp_since}
    since = _since_clause(opp_since)
    overall = conn.execute(
        cast(
            LiteralString,
            f"""
            SELECT COUNT(*) AS games, COUNT(*) FILTER (WHERE ov.result = 'win') AS wins
            FROM opponent_views ov JOIN chess_games cg ON cg.id = ov.chess_game_id
            WHERE ov.opponent_profile_id = %(profile_id)s AND {analysable_sql("cg")} AND ov.result IS NOT NULL {since}
            """,
        ),
        params,
    ).fetchone()
    assert overall is not None
    if not overall["games"]:
        return {"best": [], "worst": []}
    overall_rate = int(overall["wins"]) / int(overall["games"])
    rows = conn.execute(
        cast(
            LiteralString,
            f"""
            SELECT cg.canonical_family AS family, cg.canonical_variation AS variation,
                   COUNT(*) AS games,
                   COUNT(*) FILTER (WHERE ov.result = 'win')  AS wins,
                   COUNT(*) FILTER (WHERE ov.result = 'draw') AS draws,
                   COUNT(*) FILTER (WHERE ov.result = 'loss') AS losses,
                   COALESCE(MODE() WITHIN GROUP (ORDER BY UPPER(TRIM(cg.opening_eco)))
                                FILTER (WHERE NULLIF(TRIM(cg.opening_eco), '') IS NOT NULL), '') AS eco
            FROM opponent_views ov JOIN chess_games cg ON cg.id = ov.chess_game_id
            WHERE ov.opponent_profile_id = %(profile_id)s AND {analysable_sql("cg")}
              AND ov.result IS NOT NULL AND cg.canonical_variation IS NOT NULL {since}
            GROUP BY cg.canonical_family, cg.canonical_variation
            """,
        ),
        params,
    ).fetchall()
    lines = shrink(
        [
            {
                "eco": r["eco"],
                "family": r["family"],
                "variation": r["variation"],
                "games": int(r["games"]),
                "wins": int(r["wins"]),
                "draws": int(r["draws"]),
                "losses": int(r["losses"]),
            }
            for r in rows
        ],
        overall_rate,
        k,
    )
    return {"best": lines[:TOP_LINES], "worst": lines[::-1][:TOP_LINES]}


def report(conn: Connection[Any], profile_id: int, *, prior_strength: int, opp_since: datetime | None) -> Row:
    params: dict[str, Any] = {"profile_id": profile_id, "opp_since": opp_since}
    since = _since_clause(opp_since)
    color_counts: dict[str, dict[str, tuple[int, datetime | None]]] = {"white": {}, "black": {}}
    most: dict[str, tuple[int, datetime | None]] = {}
    for r in _families(conn, params, since):
        cnt, last_seen = int(r["cnt"]), r["last_seen"]
        color_counts.setdefault(r["played_as"], {})[r["family"]] = (cnt, last_seen)
        m = most.get(r["family"], (0, None))
        newest = last_seen if m[1] is None or (last_seen is not None and last_seen > m[1]) else m[1]
        most[r["family"]] = (m[0] + cnt, newest)
    games = _family_games(conn, params, since)
    as_color = {
        color: [{**row, "games": games.get((color, row["family"]), [])} for row in pct_list(color_counts[color])]
        for color in ("white", "black")
    }
    lines = best_worst(conn, profile_id, prior_strength, opp_since)
    return {
        "activity": activity.counts(conn, activity.Opponent(profile_id)),
        "as_white": as_color["white"],
        "as_black": as_color["black"],
        "most_played": pct_list(most),
        "best_lines": lines["best"],
        "worst_lines": lines["worst"],
    }


def line_games(
    conn: Connection[Any], profile_id: int, *, family: str, variation: str | None, opp_since: datetime | None
) -> list[Row]:
    """The opponent's most recent games in one family (and variation), newest first."""
    fam = family.strip()
    if not fam:
        raise ValueError("family required")
    params: dict[str, Any] = {
        "profile_id": profile_id,
        "family": fam,
        "variation": (variation or "").strip() or None,
        "limit": LINE_GAMES_LIMIT,
        "opp_since": opp_since,
    }
    rows = conn.execute(
        cast(
            LiteralString,
            f"""
            SELECT cg.opening_name, cg.played_at, ov.result, ov.played_as, ov.source_type, cg.platform_game_id
            FROM opponent_views ov JOIN chess_games cg ON cg.id = ov.chess_game_id
            WHERE ov.opponent_profile_id = %(profile_id)s AND {analysable_sql("cg")}
              AND cg.canonical_family = %(family)s
              AND (%(variation)s::text IS NULL OR cg.canonical_variation = %(variation)s)
              {_since_clause(opp_since)}
            ORDER BY cg.played_at DESC NULLS LAST
            LIMIT %(limit)s
            """,
        ),
        params,
    ).fetchall()
    return [
        {
            "date": r["played_at"].strftime("%Y-%m-%d") if r["played_at"] else "",
            "result": r["result"] or "",
            "color": r["played_as"] or "",
            "opening": r["opening_name"] or "",
            "url": game_url(r["source_type"], r["platform_game_id"]),
        }
        for r in rows
    ]
