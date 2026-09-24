"""The Deviations page: where the player keeps leaving his own repertoire, ranked.

The unit is the **pattern** — a book, a chapter, the ply, and the move the line expected there
(`game_repertoire_results` rows with `deviation_by = 'me'`) — and how often it recurs is
counted in **distinct games**. A pattern is about the line, not the board: in a few of them
the same expected move was missed on more than one board (…Nf6 at ply 3 whatever White's
third move was), and the card shows the representative game's board while listing every game.
There is no dismissal: a deviation that should not count is a line that should not be active,
and the Repertoire page switches lines off.

Every filter applies through one WHERE builder shared by the ranked query and the details
query. The window is either a day count or the player's most recent N standard games
(`eligibility.window_cte`: Chess960 never takes a slot); time class narrows inside the window
(docs/decisions/005); `min_ply` is the `deviations_default_min_ply` setting. The details
query orders games most recent first, and the first is the representative: its `deviation_fen`,
`moves` and `chess_game_id` are what the card and its explanation are about.

The repertoire's own view of each card's position comes from `core.repertoire.read`: the lines
through it (`rep_lines`, colour by the side to move) and the one move they agree on
(`singular_move`, None when they do not). That is the card's third arrow authority; the
pattern's own `expected_move` — the pipeline's matched move — is the second and outranks it.

**New** (`mark_new`): a pattern the list has never shown. The page acknowledges exactly the
patterns it rendered (`mark_seen`); the first look ever (`players.deviations_seen_at` still
NULL) marks nothing and acknowledges everything then listed; Home counts the same predicate
and never acknowledges. Order is by count alone, so an acknowledgement between two pages
moves nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql, evidence_sql, window_cte
from core.constants import PLAYER_ID
from core.repertoire import read
from core.settings import Settings

Row = dict[str, Any]
Key = tuple[int, int, int, str]  # book_id, chapter_id, deviated_at_ply, expected_move

PAGE_SIZE = 50
TIME_CLASSES = ("focus", "all", "bullet", "blitz", "rapid", "classical")


@dataclass(frozen=True)
class DeviationFilters:
    min_occurrences: int = 2
    since_days: int | None = None
    last_n_games: int = 0  # > 0 wins over since_days
    time_class: str = "focus"
    color: str | None = None  # 'white' | 'black' | None for both
    min_ply: int = 1
    mark_new: bool = False


def default_filters(config: Settings, mark_new: bool = False) -> DeviationFilters:
    """The filters the page opens on (ui/src/deviations.ts `defaultFilters` builds the same
    from the settings row). Home counts new patterns against exactly these."""
    by_games = config.deviations_default_filter_mode == "games"
    return DeviationFilters(
        min_occurrences=config.deviations_default_min_occurrences,
        since_days=None if by_games else config.deviations_default_window_days,
        last_n_games=config.deviations_default_last_n_games if by_games else 0,
        time_class="focus",
        color=None,
        min_ply=config.deviations_default_min_ply,
        mark_new=mark_new,
    )


def _time_class_sql(time_class: str, focus: str) -> str:
    if time_class == "focus":
        return evidence_sql("cg", focus)
    if time_class == "all":
        return "TRUE"
    if time_class == "classical":
        return "cg.time_class IN ('classical', 'correspondence')"
    if time_class in ("bullet", "blitz", "rapid"):
        return f"cg.time_class = '{time_class}'"
    raise ValueError(f"unknown time class {time_class!r}")


def _scope(f: DeviationFilters, focus: str) -> tuple[str, str, dict[str, Any]]:
    """`(WITH prefix, WHERE body over aliases grr/cg/pg/bk, params)` — which result rows are
    in play. Both queries below are built on it."""
    params: dict[str, Any] = {"pid": PLAYER_ID, "min_ply": f.min_ply}
    clauses = [
        "grr.player_id = %(pid)s",
        "grr.deviation_by = 'me'",
        "grr.deviated_at_ply >= %(min_ply)s",
        analysable_sql("cg"),
        _time_class_sql(f.time_class, focus),
    ]
    if f.color in ("white", "black"):
        clauses.append("bk.color = %(color)s")
        params["color"] = f.color
    prefix = ""
    if f.last_n_games > 0:
        prefix = window_cte() + ", "
        clauses.append("grr.chess_game_id IN (SELECT chess_game_id FROM window_games)")
        params["window"] = f.last_n_games
    elif f.since_days:
        clauses.append("cg.played_at >= now() - make_interval(days => %(days)s)")
        params["days"] = f.since_days
    return prefix, " AND ".join(clauses), params


_FROM = """
FROM game_repertoire_results grr
JOIN chess_games cg ON cg.id = grr.chess_game_id
JOIN player_games pg ON pg.chess_game_id = grr.chess_game_id AND pg.player_id = grr.player_id
JOIN books bk ON bk.id = grr.book_id
"""

_KEY = "grr.book_id, grr.chapter_id, grr.deviated_at_ply, grr.expected_move"
_ORDER = "count DESC, last_played DESC NULLS LAST, book_id, chapter_id, deviated_at_ply, expected_move"
_OUTER_ORDER = ", ".join("f." + c for c in _ORDER.split(", "))


def _ranked_sql(prefix: str, where: str) -> str:
    return f"""
    WITH {prefix}agg AS (
        SELECT {_KEY}, bk.color, bk.title AS book, count(*) AS count, max(cg.played_at) AS last_played,
               count(*) FILTER (WHERE pg.result = 'win') AS wins,
               count(*) FILTER (WHERE pg.result = 'loss') AS losses,
               count(*) FILTER (WHERE pg.result = 'draw') AS draws
        {_FROM}
        WHERE {where} AND grr.expected_move IS NOT NULL
        GROUP BY {_KEY}, bk.color, bk.title
        HAVING count(*) >= %(min_occ)s
    ),
    flagged AS (
        SELECT agg.*, ch.title AS chapter,
               (%(mark_new)s AND NOT EXISTS (
                   SELECT 1 FROM seen_deviations s
                   WHERE s.player_id = %(pid)s AND s.book_id = agg.book_id AND s.chapter_id = agg.chapter_id
                     AND s.deviated_at_ply = agg.deviated_at_ply AND s.expected_move = agg.expected_move)) AS is_new,
               NOT EXISTS (
                   SELECT 1 FROM seen_deviations s
                   WHERE s.player_id = %(pid)s AND s.book_id = agg.book_id AND s.chapter_id = agg.chapter_id
                     AND s.deviated_at_ply = agg.deviated_at_ply AND s.expected_move = agg.expected_move) AS unseen
        FROM agg JOIN chapters ch ON ch.id = agg.chapter_id
    )
    """


def _key_of(r: Row) -> Key:
    return (int(r["book_id"]), int(r["chapter_id"]), int(r["deviated_at_ply"]), str(r["expected_move"]))


def _page_rows(conn: Connection[Any], f: DeviationFilters, focus: str, page: int) -> tuple[list[Row], int, list[Key]]:
    prefix, where, params = _scope(f, focus)
    params |= {"min_occ": f.min_occurrences, "mark_new": f.mark_new, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE}
    body = _ranked_sql(prefix, where)
    with conn.cursor() as cur:
        cur.execute(
            cast(
                LiteralString,
                body
                + f"""
                SELECT f.*, c.total, c.unseen_keys
                FROM (SELECT count(*) AS total,
                             coalesce(jsonb_agg(jsonb_build_array(book_id, chapter_id, deviated_at_ply, expected_move))
                                      FILTER (WHERE unseen), '[]') AS unseen_keys
                      FROM flagged) c
                LEFT JOIN LATERAL (
                    SELECT * FROM flagged ORDER BY {_ORDER} LIMIT %(limit)s OFFSET %(offset)s
                ) f ON TRUE
                ORDER BY {_OUTER_ORDER}
                """,
            ),
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
    counts = rows[0]
    unseen = [(int(k[0]), int(k[1]), int(k[2]), str(k[3])) for k in counts["unseen_keys"]]
    return [r for r in rows if r["book_id"] is not None], int(counts["total"]), unseen


def _details(conn: Connection[Any], f: DeviationFilters, focus: str, keys: list[Key]) -> dict[Key, list[Row]]:
    """Every game of the given patterns under the same scope, most recent first."""
    if not keys:
        return {}
    prefix, where, params = _scope(f, focus)
    params["keys"] = json.dumps([list(k) for k in keys])
    with_prefix = f"WITH {prefix.rstrip(', ')}" if prefix else ""
    with conn.cursor() as cur:
        cur.execute(
            cast(
                LiteralString,
                f"""
                {with_prefix}
                SELECT {_KEY}, grr.played_move, grr.deviation_fen, grr.chess_game_id, cg.moves, cg.url AS game_url,
                       cg.played_at, pg.result,
                       (SELECT array_agg(rl.line_name ORDER BY rl.line_name)
                        FROM game_result_lines grl JOIN repertoire_lines rl ON rl.id = grl.line_id
                        WHERE grl.game_repertoire_result_id = grr.id) AS line_names
                {_FROM}
                WHERE {where}
                  AND (grr.book_id, grr.chapter_id, grr.deviated_at_ply, grr.expected_move) IN (
                      SELECT (k->>0)::int, (k->>1)::int, (k->>2)::int, k->>3 FROM jsonb_array_elements(%(keys)s) k)
                ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC
                """,
            ),
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
    out: dict[Key, list[Row]] = {}
    for r in rows:
        out.setdefault(_key_of(r), []).append(r)
    return out


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def _card(row: Row, games: list[Row], lines: list[Row]) -> Row:
    rep = games[0] if games else None
    played = [str(g["played_move"]) for g in games if g["played_move"]]
    most_common = min(set(played), key=lambda m: (-played.count(m), m)) if played else None
    fen = str(rep["deviation_fen"]) if rep else None
    return {
        "book_id": row["book_id"],
        "chapter_id": row["chapter_id"],
        "ply": row["deviated_at_ply"],
        "expected_move": row["expected_move"],
        "book": row["book"],
        "chapter": row["chapter"],
        "color": row["color"],
        "count": int(row["count"]),
        "wins": int(row["wins"]),
        "losses": int(row["losses"]),
        "draws": int(row["draws"]),
        "win_pct": round(int(row["wins"]) / int(row["count"]) * 100) if int(row["count"]) else 0,
        "is_new": bool(row["is_new"]),
        "last_seen": _iso(row["last_played"]),
        "most_common_played": most_common,
        "deviation_fen": fen,
        "chess_game_id": rep["chess_game_id"] if rep else None,
        "moves": rep["moves"] if rep else None,
        "line_names": rep["line_names"] if rep else None,
        "rep_lines": lines,
        "rep_expected_move": read.singular_move(fen, lines) if fen else None,
        "games": [
            {
                "chess_game_id": g["chess_game_id"],
                "game_url": g["game_url"],
                "played_at": _iso(g["played_at"]),
                "result": g["result"],
                "played_move": g["played_move"],
                "expected_move": row["expected_move"],
                "ply": row["deviated_at_ply"],
            }
            for g in games
        ],
    }


def positions(conn: Connection[Any], f: DeviationFilters, focus: str, page: int = 0) -> Row:
    """One page of the ranked list. `focus` is the `time_class_focus` setting."""
    rows, total, unseen = _page_rows(conn, f, focus, page)
    details = _details(conn, f, focus, [_key_of(r) for r in rows])
    fens = [str(details[_key_of(r)][0]["deviation_fen"]) for r in rows if details.get(_key_of(r))]
    lines_by_fen = read.rep_lines(conn, fens, book_color="by_turn")
    cards: list[Row] = []
    for r in rows:
        games = details.get(_key_of(r), [])
        fen = str(games[0]["deviation_fen"]) if games else None
        cards.append(_card(r, games, lines_by_fen.get(fen, []) if fen else []))
    shown = [_key_of(r) for r in rows if r["is_new"]] if f.mark_new else unseen
    return {
        "positions": cards,
        "total": total,
        "new_count": len(unseen) if f.mark_new else 0,
        "to_acknowledge": [list(k) for k in shown],
        "page": page,
        "page_size": PAGE_SIZE,
        "total_pages": max(1, -(-total // PAGE_SIZE)),
    }


def new_count(conn: Connection[Any], f: DeviationFilters, focus: str) -> int:
    """How many patterns the list has never shown — Home's number. Zero before the first look."""
    if not f.mark_new:
        return 0
    prefix, where, params = _scope(f, focus)
    params |= {"min_occ": f.min_occurrences, "mark_new": True}
    body = _ranked_sql(prefix, where) + "SELECT count(*) AS n FROM flagged WHERE is_new"
    with conn.cursor() as cur:
        cur.execute(cast(LiteralString, body), params)
        row = cur.fetchone()
    assert row is not None
    return int(row["n"])


def seen_at(conn: Connection[Any]) -> datetime | None:
    row = conn.execute("SELECT deviations_seen_at FROM players WHERE id = %s", (PLAYER_ID,)).fetchone()
    if row is None:
        raise RuntimeError("no players row; run `pipeline player set` first")
    return row["deviations_seen_at"]


def mark_seen(conn: Connection[Any], keys: list[Key]) -> datetime:
    """The page has shown these patterns (as `to_acknowledge` gave them)."""
    row = conn.execute(
        "UPDATE players SET deviations_seen_at = now() WHERE id = %s RETURNING deviations_seen_at", (PLAYER_ID,)
    ).fetchone()
    if row is None:
        raise RuntimeError("no players row; run `pipeline player set` first")
    if keys:
        # Only patterns that exist: a key the client made up must not be a server error.
        conn.execute(
            """
            INSERT INTO seen_deviations (player_id, book_id, chapter_id, deviated_at_ply, expected_move)
            SELECT %(pid)s, (k->>0)::int, (k->>1)::int, (k->>2)::int, k->>3
            FROM jsonb_array_elements(%(keys)s) k
            WHERE EXISTS (SELECT 1 FROM chapters ch JOIN books bk ON bk.id = ch.book_id
                          WHERE ch.id = (k->>1)::int AND bk.id = (k->>0)::int AND bk.player_id = %(pid)s)
            ON CONFLICT DO NOTHING
            """,
            {"pid": PLAYER_ID, "keys": json.dumps([list(k) for k in keys])},
        )
    return row["deviations_seen_at"]
