"""The Scout positions list: boards the player and a scouted opponent both reach, in three tiers.

`page` is one statement. `opp_agg` expands the opponent's games (`opponent_views`, in window)
to every position at or past `SCOUT_MIN_MATCH_PLY` where it is the **player's** turn — the
side to move read from the FEN against `ov.played_as`, so a position where the opponent just
erred is never surfaced — with how often the opponent reached it and how deep. Three
overlays give the tiers: the player's blunder rows at the board (tier 1, ranked by weighted
score), the effectively-active repertoire lines through it (tier 2) and the player's own
games through it (tier 3). Tiers 2 and 3 are collapsed along their chains: a position whose
only surfaced successor is one other surfaced position is an interior point of a line and is
dropped, so what remains is each line's furthest surfaced position and its genuine branch
points; blunders always pass. Totals and tier counts are window aggregates over the collapsed
set, so a page never disagrees with them. Boards dismissed on Blunders or here are excluded.

Windows are "last N games" on each side, applied as `played_at >= cutoff` where the cutoff is
the Nth most recent game's date: the player's over analysable games only (Chess960 never takes
a slot), the opponent's over the profile's views. Every `chess_games` alias carries the
analysable predicate.

`details` fills the page's cards: the player's most recent analysed game through each board
(replay moves, the ply, and the engine's best move there from the same row, so the replay and
the arrow can never come from different games), the player's games at the board (blunder
rows, else the five most recent), the opponent's games at it, and the repertoire lines with
the move they agree on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import BLUNDER_SCORE_WEIGHTS, PLAYER_ID, SCOUT_MIN_MATCH_PLY
from core.repertoire import read

Row = dict[str, Any]

PAGE_SIZE = 20
COLORS = ("both", "white", "black")


@dataclass(frozen=True)
class ScoutFilters:
    profile_id: int
    my_last_n: int = 0  # 0 = all
    opp_last_n: int = 0  # 0 = all
    color: str = "both"  # the player's colour: white = the opponent played black
    min_freq: int = 1


def my_since(conn: Connection[Any], last_n: int) -> datetime | None:
    """The date of the player's Nth most recent analysable game, or None (no window, or fewer
    than N games)."""
    if last_n <= 0:
        return None
    row = conn.execute(
        cast(
            LiteralString,
            f"""
            SELECT cg.played_at FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
            WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")} AND cg.played_at IS NOT NULL
            ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC LIMIT 1 OFFSET %(offset)s
            """,
        ),
        {"pid": PLAYER_ID, "offset": last_n - 1},
    ).fetchone()
    return row["played_at"] if row else None


def opp_since(conn: Connection[Any], profile_id: int, last_n: int) -> datetime | None:
    """The date of the opponent's Nth most recent stored game, or None."""
    if last_n <= 0:
        return None
    row = conn.execute(
        cast(
            LiteralString,
            f"""
            SELECT cg.played_at FROM opponent_views ov JOIN chess_games cg ON cg.id = ov.chess_game_id
            WHERE ov.opponent_profile_id = %(profile_id)s AND {analysable_sql("cg")} AND cg.played_at IS NOT NULL
            ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC LIMIT 1 OFFSET %(offset)s
            """,
        ),
        {"profile_id": profile_id, "offset": last_n - 1},
    ).fetchone()
    return row["played_at"] if row else None


_WEIGHT_CASE = " ".join(f"WHEN b.classification = '{k}' THEN {v}" for k, v in BLUNDER_SCORE_WEIGHTS.items())

# The player's colour decides which of the opponent's games count and which side must be to
# move; "both" keeps every position where the side to move is not the opponent's.
_OPP_PLAYED_AS = {"white": "AND ov.played_as = 'black'", "black": "AND ov.played_as = 'white'", "both": ""}
_ACTIVE_COLOR = {
    "white": "AND SPLIT_PART(elem.fen, ' ', 2) = 'w'",
    "black": "AND SPLIT_PART(elem.fen, ' ', 2) = 'b'",
    "both": "AND SPLIT_PART(elem.fen, ' ', 2) != LEFT(ov.played_as, 1)",
}


def _body(color: str, my_since_at: datetime | None, opp_since_at: datetime | None) -> str:
    opp_since_clause = "AND cg_opp.played_at >= %(opp_since)s" if opp_since_at else ""
    my_since_clause = "AND cg.played_at >= %(my_since)s" if my_since_at else ""
    my_blunder_clause = "AND cg2.played_at >= %(my_since)s" if my_since_at else ""
    return f"""
    WITH
    opp_agg AS (
        SELECT elem.fen,
               MAX((elem.ordinality - 1)::int)             AS max_depth,
               COUNT(DISTINCT cg_opp.id)                   AS opp_frequency,
               MODE() WITHIN GROUP (ORDER BY ov.played_as) AS opp_color
        FROM   opponent_views ov
        JOIN   chess_games cg_opp ON cg_opp.id = ov.chess_game_id
        CROSS  JOIN LATERAL jsonb_array_elements_text(cg_opp.fen_sequence) WITH ORDINALITY AS elem(fen, ordinality)
        WHERE  ov.opponent_profile_id = %(profile_id)s
          AND  {analysable_sql("cg_opp")}
          AND  (elem.ordinality - 1) >= %(min_depth)s
          {opp_since_clause}
          {_OPP_PLAYED_AS[color]}
          {_ACTIVE_COLOR[color]}
        GROUP  BY elem.fen
        HAVING COUNT(DISTINCT cg_opp.id) >= %(min_freq)s
    ),
    blunder_tier AS (
        SELECT b.fen,
               SUM(CASE {_WEIGHT_CASE} ELSE 0 END)              AS blunder_score,
               COUNT(*)                                         AS blunder_count,
               MODE() WITHIN GROUP (ORDER BY b.classification) AS top_classification,
               MODE() WITHIN GROUP (ORDER BY b.move_played)    AS move_played,
               MODE() WITHIN GROUP (ORDER BY b.best_move)      AS best_move
        FROM   blunders b
        JOIN   chess_games cg2 ON cg2.id = b.chess_game_id
        WHERE  b.player_id = %(pid)s
          AND  {analysable_sql("cg2")}
          AND  b.fen IN (SELECT fen FROM opp_agg)
          {my_blunder_clause}
        GROUP  BY b.fen
    ),
    rep_tier AS (
        SELECT DISTINCT ON (rep_fen.fen)
               rep_fen.fen, rl.id AS line_id, rl.line_name,
               ch.title AS chapter_title, bk.title AS book_title, bk.color AS book_color
        FROM   repertoire_lines rl
        JOIN   chapters ch ON ch.id = rl.chapter_id
        JOIN   books    bk ON bk.id = ch.book_id,
        LATERAL jsonb_array_elements_text(rl.fen_sequence) AS rep_fen(fen)
        JOIN   opp_agg ON opp_agg.fen = rep_fen.fen
        WHERE  rl.active AND ch.active AND bk.active AND bk.player_id = %(pid)s
        ORDER  BY rep_fen.fen, rl.id
    ),
    my_fens AS (
        SELECT gf.fen, COUNT(DISTINCT cg.id) AS my_frequency
        FROM   player_games pg
        JOIN   chess_games cg ON cg.id = pg.chess_game_id
        CROSS  JOIN LATERAL jsonb_array_elements_text(cg.fen_sequence) AS gf(fen)
        WHERE  pg.player_id = %(pid)s
          AND  {analysable_sql("cg")}
          {my_since_clause}
          AND  gf.fen IN (SELECT fen FROM opp_agg)
        GROUP  BY gf.fen
    ),
    tiered AS (
        SELECT ofa.fen, ofa.max_depth, ofa.opp_frequency, ofa.opp_color,
               bt.blunder_score, bt.blunder_count, bt.top_classification, bt.move_played, bt.best_move,
               rt.line_id, rt.line_name, rt.chapter_title, rt.book_title, rt.book_color,
               COALESCE(mf.my_frequency, 0) AS my_frequency,
               CASE WHEN bt.fen IS NOT NULL THEN 1 WHEN rt.fen IS NOT NULL THEN 2 ELSE 3 END AS tier,
               CASE
                   WHEN bt.fen IS NOT NULL
                       THEN bt.blunder_score * COALESCE(mf.my_frequency, 1) * 1000 + ofa.opp_frequency
                   WHEN rt.fen IS NOT NULL THEN COALESCE(mf.my_frequency, 0) * ofa.opp_frequency * 10 + ofa.max_depth
                   ELSE COALESCE(mf.my_frequency, 0) * ofa.opp_frequency + ofa.max_depth
               END AS sort_score
        FROM   opp_agg ofa
        LEFT  JOIN blunder_tier bt ON bt.fen = ofa.fen
        LEFT  JOIN rep_tier     rt ON rt.fen = ofa.fen
        LEFT  JOIN my_fens      mf ON mf.fen = ofa.fen
        WHERE  (bt.fen IS NOT NULL OR rt.fen IS NOT NULL OR mf.fen IS NOT NULL)
          AND  NOT EXISTS (SELECT 1 FROM dismissed_blunder_fens d
                           WHERE d.player_id = %(pid)s AND d.canonical_fen = bq_canonical_fen(ofa.fen) || ' 0 1')
    ),
    rep_adj AS (
        SELECT DISTINCT fen, next_fen FROM (
            SELECT elem.fen, LEAD(elem.fen) OVER (PARTITION BY rl.id ORDER BY elem.ordinality) AS next_fen
            FROM   repertoire_lines rl
            JOIN   chapters ch ON ch.id = rl.chapter_id
            JOIN   books    bk ON bk.id = ch.book_id
            CROSS  JOIN LATERAL jsonb_array_elements_text(rl.fen_sequence) WITH ORDINALITY AS elem(fen, ordinality)
            WHERE  rl.active AND ch.active AND bk.active AND bk.player_id = %(pid)s
              AND  rl.fen_sequence ?| ARRAY(SELECT fen FROM tiered WHERE tier = 2)
              AND  elem.fen IN (SELECT fen FROM tiered WHERE tier = 2)
        ) x
        WHERE next_fen IS NOT NULL AND next_fen <> fen
    ),
    shared_adj AS (
        SELECT DISTINCT fen, next_fen FROM (
            SELECT elem.fen, LEAD(elem.fen) OVER (PARTITION BY g.id ORDER BY elem.ordinality) AS next_fen
            FROM (
                SELECT DISTINCT cg.id, cg.fen_sequence
                FROM   player_games pg
                JOIN   chess_games cg ON cg.id = pg.chess_game_id
                WHERE  pg.player_id = %(pid)s
                  AND  {analysable_sql("cg")}
                  AND  cg.position_keys && (SELECT array_agg(bq_position_key(fen)) FROM tiered WHERE tier = 3)
                  {my_since_clause}
            ) g
            CROSS  JOIN LATERAL jsonb_array_elements_text(g.fen_sequence) WITH ORDINALITY AS elem(fen, ordinality)
            WHERE  elem.fen IN (SELECT fen FROM tiered WHERE tier = 3)
        ) x
        WHERE next_fen IS NOT NULL AND next_fen <> fen
    ),
    succ2 AS (SELECT fen, COUNT(DISTINCT next_fen) AS n FROM rep_adj    GROUP BY fen),
    succ3 AS (SELECT fen, COUNT(DISTINCT next_fen) AS n FROM shared_adj GROUP BY fen),
    kept AS (
        SELECT t.*
        FROM   tiered t
        LEFT  JOIN succ2 ON t.tier = 2 AND succ2.fen = t.fen
        LEFT  JOIN succ3 ON t.tier = 3 AND succ3.fen = t.fen
        WHERE  t.tier = 1
           OR (t.tier = 2 AND COALESCE(succ2.n, 0) <> 1)
           OR (t.tier = 3 AND COALESCE(succ3.n, 0) <> 1)
    )
    """


_PAGE_TAIL = """
    SELECT k.*,
           COUNT(*)                         OVER () AS _total,
           COUNT(*) FILTER (WHERE tier = 1) OVER () AS _tc1,
           COUNT(*) FILTER (WHERE tier = 2) OVER () AS _tc2,
           COUNT(*) FILTER (WHERE tier = 3) OVER () AS _tc3
    FROM   kept k
    ORDER  BY tier ASC, sort_score DESC, fen
    LIMIT  %(page_size)s OFFSET %(page_offset)s
"""

_COUNT_TAIL = """
    SELECT COUNT(*)                         AS _total,
           COUNT(*) FILTER (WHERE tier = 1) AS _tc1,
           COUNT(*) FILTER (WHERE tier = 2) AS _tc2,
           COUNT(*) FILTER (WHERE tier = 3) AS _tc3
    FROM   kept
"""


def page_rows(conn: Connection[Any], f: ScoutFilters, page: int) -> tuple[list[Row], int, dict[int, int]]:
    """(the page's rows, total, {tier: count} for the tiers present) over the collapsed set."""
    if f.color not in COLORS:
        raise ValueError(f"color must be one of {COLORS}")
    if f.min_freq < 1 or page < 0:
        raise ValueError("filters out of domain")
    my_at = my_since(conn, f.my_last_n)
    opp_at = opp_since(conn, f.profile_id, f.opp_last_n)
    params: dict[str, Any] = {
        "pid": PLAYER_ID,
        "profile_id": f.profile_id,
        "min_depth": SCOUT_MIN_MATCH_PLY,
        "min_freq": f.min_freq,
        "page_size": PAGE_SIZE,
        "page_offset": page * PAGE_SIZE,
        "my_since": my_at,
        "opp_since": opp_at,
    }
    body = _body(f.color, my_at, opp_at)
    with conn.cursor() as cur:
        cur.execute(cast(LiteralString, body + _PAGE_TAIL), params)
        rows = [dict(r) for r in cur.fetchall()]
    if rows:
        total = int(rows[0]["_total"])
        tcs = (rows[0]["_tc1"], rows[0]["_tc2"], rows[0]["_tc3"])
        for r in rows:
            for key in ("_total", "_tc1", "_tc2", "_tc3"):
                r.pop(key, None)
    else:
        with conn.cursor() as cur:
            cur.execute(cast(LiteralString, body + _COUNT_TAIL), params)
            c = cur.fetchone()
        assert c is not None
        total = int(c["_total"])
        tcs = (c["_tc1"], c["_tc2"], c["_tc3"])
    tier_counts = {t: int(n) for t, n in zip((1, 2, 3), tcs, strict=True) if n}
    return rows, total, tier_counts


# --- details ------------------------------------------------------------------------------

# The replay game per board: the player's most recent analysed game through it (the most recent
# with moves when none is analysed), with the engine's best move at that ply from the same row,
# so the replay and the arrow can never come from different games — and Compare's scout column
# reads the engine's move at a board the same way. `ply_analysis` is a list of per-ply entries
# whose `ply` is the 0-based index of the position they evaluate, the same index
# `elem.ordinality - 1` gives the FEN; the entry is read only when the two agree, so a shifted
# array yields no move rather than a wrong one. The GIN prefilter keeps the expansion to the
# games that hold one of the boards.
_REPLAY_SQL = """
SELECT DISTINCT ON (elem.fen)
       elem.fen, cg.moves, cg.starting_fen, (elem.ordinality - 1)::int AS ply, cg.played_at,
       CASE WHEN (cg.ply_analysis -> (elem.ordinality - 1)::int ->> 'ply')::int = (elem.ordinality - 1)::int
            THEN cg.ply_analysis -> (elem.ordinality - 1)::int ->> 'best_move' END AS best_move
FROM   player_games pg
JOIN   chess_games cg ON cg.id = pg.chess_game_id,
       jsonb_array_elements_text(cg.fen_sequence) WITH ORDINALITY AS elem(fen, ordinality)
WHERE  pg.player_id = %(pid)s AND {analysable} AND cg.moves IS NOT NULL
  AND  cg.position_keys && (SELECT array_agg(bq_position_key(f)) FROM unnest(%(fens)s::text[]) AS f)
  AND  elem.fen = ANY(%(fens)s)
ORDER  BY elem.fen, (cg.ply_analysis IS NOT NULL) DESC, cg.played_at DESC NULLS LAST, cg.id DESC
"""

_MY_BLUNDER_GAMES_SQL = """
SELECT DISTINCT b.fen, cg.url, cg.played_at, pg.opponent_username, pg.player_color, b.classification
FROM   blunders b
JOIN   chess_games cg ON cg.id = b.chess_game_id
JOIN   player_games pg ON pg.chess_game_id = b.chess_game_id AND pg.player_id = b.player_id
WHERE  b.player_id = %(pid)s AND {analysable} AND b.fen = ANY(%(fens)s)
ORDER  BY b.fen, cg.played_at DESC NULLS LAST
"""

_MY_RECENT_GAMES_SQL = """
SELECT cg.url, cg.played_at, pg.opponent_username, pg.player_color
FROM   player_games pg
JOIN   chess_games cg ON cg.id = pg.chess_game_id
WHERE  pg.player_id = %(pid)s AND {analysable} AND cg.position_keys @> ARRAY[bq_position_key(%(fen)s::text)]
ORDER  BY cg.played_at DESC NULLS LAST LIMIT 5
"""

_OPP_GAMES_SQL = """
SELECT DISTINCT ON (target.fen, cg.id)
       target.fen, cg.played_at, cg.opening_name, ov.source_type, cg.platform_game_id, ov.played_as
FROM   opponent_views ov
JOIN   chess_games cg ON cg.id = ov.chess_game_id,
       unnest(%(fens)s::text[]) AS target(fen)
WHERE  ov.opponent_profile_id = %(profile_id)s AND {analysable}
  AND  cg.position_keys @> ARRAY[bq_position_key(target.fen)]
ORDER  BY target.fen, cg.id, cg.played_at DESC NULLS LAST
"""


def game_url(platform: str, platform_game_id: str | None) -> str:
    if not platform_game_id:
        return ""
    if platform == "lichess":
        return f"https://lichess.org/{platform_game_id}"
    if platform == "chesscom":
        return f"https://www.chess.com/game/live/{platform_game_id}"
    return ""


def _date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def details(conn: Connection[Any], profile_id: int, fens: list[str]) -> dict[str, Row]:
    """{fen: {moves, starting_fen, ply, best_move, best_move_date, my_games, opp_games, rep_lines,
    rep_expected_move}} for the page's boards."""
    out: dict[str, Row] = {
        fen: {
            "moves": None,
            "starting_fen": None,
            "ply": None,
            "best_move": None,
            "best_move_date": None,
            "my_games": [],
            "opp_games": [],
            "rep_lines": [],
            "rep_expected_move": None,
        }
        for fen in fens
    }
    if not fens:
        return out
    params: dict[str, Any] = {"pid": PLAYER_ID, "profile_id": profile_id, "fens": list(dict.fromkeys(fens))}
    with conn.cursor() as cur:
        cur.execute(cast(LiteralString, _REPLAY_SQL.format(analysable=analysable_sql("cg"))), params)
        for r in cur.fetchall():
            out[r["fen"]].update(
                {
                    "moves": r["moves"],
                    "starting_fen": r["starting_fen"],
                    "ply": int(r["ply"]),
                    "best_move": r["best_move"] or None,
                    "best_move_date": _date(r["played_at"]) if r["best_move"] else None,
                }
            )
        cur.execute(cast(LiteralString, _MY_BLUNDER_GAMES_SQL.format(analysable=analysable_sql("cg"))), params)
        for r in cur.fetchall():
            out[r["fen"]]["my_games"].append(
                {
                    "date": _date(r["played_at"]),
                    "opponent": r["opponent_username"] or "",
                    "color": r["player_color"] or "",
                    "class": r["classification"] or "",
                    "url": r["url"] or "",
                }
            )
        for fen in fens:
            if out[fen]["my_games"]:
                continue
            cur.execute(
                cast(LiteralString, _MY_RECENT_GAMES_SQL.format(analysable=analysable_sql("cg"))),
                {"pid": PLAYER_ID, "fen": fen},
            )
            for r in cur.fetchall():
                out[fen]["my_games"].append(
                    {
                        "date": _date(r["played_at"]),
                        "opponent": r["opponent_username"] or "",
                        "color": r["player_color"] or "",
                        "class": "",
                        "url": r["url"] or "",
                    }
                )
        cur.execute(cast(LiteralString, _OPP_GAMES_SQL.format(analysable=analysable_sql("cg"))), params)
        for r in cur.fetchall():
            out[r["fen"]]["opp_games"].append(
                {
                    "date": _date(r["played_at"]),
                    "opening": r["opening_name"] or "",
                    "as": r["played_as"] or "",
                    "url": game_url(r["source_type"], r["platform_game_id"]),
                }
            )
    lines = read.rep_lines(conn, list(out), book_color="by_turn")
    for fen, occurrences in lines.items():
        out[fen]["rep_lines"] = occurrences
        out[fen]["rep_expected_move"] = read.singular_move(fen, occurrences)
    return out


def page(conn: Connection[Any], f: ScoutFilters, page_number: int = 0) -> dict[str, Any]:
    """The route's payload: the page's positions with their details, and the totals."""
    rows, total, tier_counts = page_rows(conn, f, page_number)
    extra = details(conn, f.profile_id, [r["fen"] for r in rows])
    positions: list[Row] = []
    for r in rows:
        row = {k: (int(v) if isinstance(v, int) else v) for k, v in r.items()}
        detail = dict(extra[r["fen"]])
        if int(r["tier"]) == 1 and r["best_move"]:
            detail["best_move"], detail["best_move_date"] = r["best_move"], None  # the blunder row's
        row.update(detail)
        positions.append(row)
    return {
        "positions": positions,
        "total": total,
        "page": page_number,
        "page_size": PAGE_SIZE,
        "total_pages": max(1, -(-total // PAGE_SIZE)),
        "tier_counts": {str(t): n for t, n in tier_counts.items()},
    }
