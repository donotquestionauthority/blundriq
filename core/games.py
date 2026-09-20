"""The Games page's reads: filtered, paged game list with repertoire and issue
columns; summary counts; CSV export; filter values; opponent search.

Filters are applied in one WHERE builder so rows, summary, pages and the
export always agree. `last_n_games` scopes to the most recent N games first
and then the other filters apply; `since_days` is ignored when it is set.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.constants import PLAYER_ID

PAGE_SIZE = 100
DEVIATION_VALUES = {"me", "opponent", "none", "no_match"}


@dataclass(frozen=True)
class GameFilters:
    since_days: int | None = None
    last_n_games: int = 0
    color: str | None = None  # 'white' | 'black'
    result: str | None = None  # 'win' | 'loss' | 'draw'
    platform: str | None = None  # 'chesscom' | 'lichess'
    variant: str | None = None  # 'standard' | 'chess960'
    book: str | None = None  # book title
    chapter: str | None = None  # chapter title
    deviation: str | None = None  # 'me' | 'opponent' | 'none' | 'no_match'
    opponent: str | None = None  # substring, case-insensitive


_FROM = """
    FROM player_games pg
    JOIN chess_games cg ON cg.id = pg.chess_game_id
    LEFT JOIN game_repertoire_results grr ON grr.chess_game_id = pg.chess_game_id AND grr.player_id = pg.player_id
    LEFT JOIN books bk ON bk.id = grr.book_id
    LEFT JOIN chapters ch ON ch.id = grr.chapter_id
"""

_SELECT = (
    """
    SELECT cg.id, cg.url, pg.source, cg.played_at, cg.variant, pg.player_color, pg.opponent_username,
           pg.opponent_rating, pg.player_rating, pg.result, cg.time_control, cg.time_class, cg.opening_name,
           cg.opening_eco, cg.termination, (cg.analysis_status = 'completed') AS analyzed, cg.moves,
           grr.deviated_at_ply, grr.deviation_by, grr.expected_move, grr.played_move,
           bk.title AS book_title, ch.title AS chapter_title, rl.line_name,
           (SELECT count(*) FROM blunders b WHERE b.player_id = pg.player_id AND b.chess_game_id = cg.id)
              AS issue_count,
           (SELECT count(*) FROM blunders b WHERE b.player_id = pg.player_id AND b.chess_game_id = cg.id
              AND b.classification = 'miss') AS miss_count,
           (SELECT count(*) FROM blunders b WHERE b.player_id = pg.player_id AND b.chess_game_id = cg.id
              AND b.classification = 'blunder') AS blunder_count,
           (SELECT count(*) FROM blunders b WHERE b.player_id = pg.player_id AND b.chess_game_id = cg.id
              AND b.classification = 'mistake') AS mistake_count,
           (SELECT count(*) FROM blunders b WHERE b.player_id = pg.player_id AND b.chess_game_id = cg.id
              AND b.classification = 'inaccuracy') AS inaccuracy_count
"""
    + _FROM
    + """
    LEFT JOIN LATERAL (
        SELECT grl.line_id FROM game_result_lines grl
        WHERE grl.game_repertoire_result_id = grr.id ORDER BY grl.matched_ply DESC, grl.line_id LIMIT 1
    ) best ON TRUE
    LEFT JOIN repertoire_lines rl ON rl.id = best.line_id
"""
)


def _where(f: GameFilters) -> tuple[str, list[Any]]:
    params: list[Any] = [PLAYER_ID]
    clauses = ["pg.player_id = %s"]
    if f.last_n_games > 0:
        clauses.append(
            "cg.id IN (SELECT pg2.chess_game_id FROM player_games pg2"
            " JOIN chess_games cg2 ON cg2.id = pg2.chess_game_id"
            " WHERE pg2.player_id = %s ORDER BY cg2.played_at DESC NULLS LAST LIMIT %s)"
        )
        params += [PLAYER_ID, f.last_n_games]
    elif f.since_days is not None:
        clauses.append("cg.played_at >= now() - make_interval(days => %s)")
        params.append(f.since_days)
    for column, value in (
        ("pg.player_color", f.color),
        ("pg.result", f.result),
        ("pg.source", f.platform),
        ("cg.variant", f.variant),
        ("bk.title", f.book),
        ("ch.title", f.chapter),
    ):
        if value:
            clauses.append(f"{column} = %s")
            params.append(value)
    if f.deviation == "no_match":
        clauses.append("grr.id IS NULL")
    elif f.deviation in DEVIATION_VALUES:
        clauses.append("grr.deviation_by = %s")
        params.append(f.deviation)
    if f.opponent:
        clauses.append("pg.opponent_username ILIKE %s")
        params.append(f"%{f.opponent}%")
    return " WHERE " + " AND ".join(clauses), params


def _q(text: str) -> LiteralString:
    return cast(LiteralString, text)  # assembled from literals above; values are bound parameters


def list_games(conn: Connection[Any], f: GameFilters, page: int = 1) -> list[dict[str, Any]]:
    where, params = _where(f)
    return conn.execute(
        _q(_SELECT + where + " ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC LIMIT %s OFFSET %s"),
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ).fetchall()


def export_games(conn: Connection[Any], f: GameFilters) -> list[dict[str, Any]]:
    where, params = _where(f)
    return conn.execute(_q(_SELECT + where + " ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC"), params).fetchall()


def summary(conn: Connection[Any], f: GameFilters) -> dict[str, Any]:
    where, params = _where(f)
    row = conn.execute(
        _q(
            "SELECT count(*) AS total, count(*) FILTER (WHERE pg.result = 'win') AS wins,"
            " count(*) FILTER (WHERE pg.result = 'loss') AS losses, count(*) FILTER (WHERE pg.result = 'draw') AS draws"
            + _FROM
            + where
        ),
        params,
    ).fetchone()
    assert row is not None
    total = int(row["total"])
    wins = int(row["wins"])
    return {
        "total": total,
        "wins": wins,
        "losses": int(row["losses"]),
        "draws": int(row["draws"]),
        "win_pct": round(wins / total * 100) if total else 0,
        "pages": max(1, -(-total // PAGE_SIZE)),
    }


def filter_values(conn: Connection[Any]) -> dict[str, Any]:
    """Book and chapter titles for the dropdowns (active or not: old results may reference them)."""
    books = conn.execute(
        "SELECT id, title, color FROM books WHERE player_id = %s ORDER BY title", (PLAYER_ID,)
    ).fetchall()
    chapters = conn.execute(
        "SELECT ch.id, ch.title, ch.book_id FROM chapters ch JOIN books bk ON bk.id = ch.book_id"
        " WHERE bk.player_id = %s ORDER BY ch.book_id, ch.title",
        (PLAYER_ID,),
    ).fetchall()
    return {"books": books, "chapters": chapters}


def search_opponents(conn: Connection[Any], q: str, limit: int = 10) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT opponent_username FROM player_games WHERE player_id = %s AND opponent_username ILIKE %s"
        " ORDER BY opponent_username LIMIT %s",
        (PLAYER_ID, f"%{q}%", limit),
    ).fetchall()
    return [r["opponent_username"] for r in rows]


def to_csv(rows: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "date",
            "platform",
            "variant",
            "color",
            "result",
            "opponent",
            "opponent_rating",
            "my_rating",
            "opening",
            "eco",
            "time_control",
            "book",
            "chapter",
            "line",
            "deviation_by",
            "deviated_at_ply",
            "expected_move",
            "played_move",
            "misses",
            "blunders",
            "mistakes",
            "inaccuracies",
            "issues",
            "analyzed",
            "url",
        ]
    )
    for g in rows:
        w.writerow(
            [
                g["played_at"].strftime("%Y-%m-%d") if g.get("played_at") else "",
                g.get("source", ""),
                g.get("variant", ""),
                g.get("player_color", ""),
                g.get("result", ""),
                g.get("opponent_username") or "",
                g.get("opponent_rating") or "",
                g.get("player_rating") or "",
                g.get("opening_name") or "",
                g.get("opening_eco") or "",
                g.get("time_control") or "",
                g.get("book_title") or "",
                g.get("chapter_title") or "",
                g.get("line_name") or "",
                g.get("deviation_by") or "",
                g.get("deviated_at_ply") or "",
                g.get("expected_move") or "",
                g.get("played_move") or "",
                g["miss_count"],
                g["blunder_count"],
                g["mistake_count"],
                g["inaccuracy_count"],
                g["issue_count"],
                "yes" if g["analyzed"] else "no",
                g.get("url") or "",
            ]
        )
    return out.getvalue()
