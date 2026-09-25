"""Where they choose: positions where the opponent, to move, has picked between replies.

`opp_moves` expands the opponent's games to every position where it is the **opponent's**
turn (the inverse of the positions list) with the move played from it and how often;
`opp_nodes` keeps the positions with at least `branch_min` distinct replies above the
`reply_min_freq` noise floor and at least `min_freq` games in total — a forced or habitual
continuation never qualifies, so a run of obvious moves yields no card and the first real
fan-out does. Coverage is colour-aligned: the node must lie on a line the player plays
against them, from the repertoire (a book of the side *not* to move) or the player's own games
(the player's colour is not the side to move), so the same board reached in the opponent's
seat is never surfaced. The lead-in is the one move that reaches the node across that
coverage, exactly one distinct SAN or none. Ranked by how much the two sides actually meet
there. Dismissed boards are excluded as on the positions list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import PLAYER_ID, SCOUT_MIN_MATCH_PLY
from core.scout.positions import ScoutFilters, my_since, opp_since
from core.settings import Settings

Row = dict[str, Any]


def _query(my_since_at: datetime | None, opp_since_at: datetime | None) -> str:
    opp_since_clause = "AND cg_opp.played_at >= %(opp_since)s" if opp_since_at else ""
    my_since_clause = "AND cg.played_at >= %(my_since)s" if my_since_at else ""
    return f"""
    WITH
    opp_moves AS (
        SELECT elem.fen,
               cg_opp.moves->>(elem.ordinality - 1)::int AS opp_move,
               COUNT(DISTINCT cg_opp.id)                 AS move_freq
        FROM   opponent_views ov
        JOIN   chess_games cg_opp ON cg_opp.id = ov.chess_game_id
        CROSS  JOIN LATERAL jsonb_array_elements_text(cg_opp.fen_sequence) WITH ORDINALITY AS elem(fen, ordinality)
        WHERE  ov.opponent_profile_id = %(profile_id)s
          AND  {analysable_sql("cg_opp")}
          AND  elem.ordinality <= jsonb_array_length(cg_opp.moves)
          AND  (elem.ordinality - 1) >= %(min_depth)s
          {opp_since_clause}
          AND  SPLIT_PART(elem.fen, ' ', 2) = LEFT(ov.played_as, 1)
        GROUP  BY elem.fen, opp_move
    ),
    opp_nodes AS (
        SELECT fen,
               COUNT(*)       AS distinct_replies,
               SUM(move_freq) AS node_freq,
               jsonb_agg(jsonb_build_object('move', opp_move, 'cnt', move_freq) ORDER BY move_freq DESC, opp_move)
                              AS replies
        FROM   opp_moves
        WHERE  opp_move IS NOT NULL
          AND  move_freq >= %(reply_min_freq)s
        GROUP  BY fen
        HAVING COUNT(*) >= %(branch_min)s AND SUM(move_freq) >= %(min_freq)s
    ),
    my_fens AS (
        SELECT n.fen, LEFT(pg.player_color, 1) AS my_side, COUNT(DISTINCT cg.id) AS my_frequency
        FROM   opp_nodes n
        JOIN   chess_games  cg ON cg.position_keys @> ARRAY[bq_position_key(n.fen)]
        JOIN   player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
        WHERE  {analysable_sql("cg")}
          {my_since_clause}
        GROUP  BY n.fen, my_side
    ),
    rep_tier AS (
        SELECT DISTINCT ON (n.fen, LEFT(bk.color, 1))
               n.fen, LEFT(bk.color, 1) AS my_side, rl.line_name, ch.title AS chapter_title, bk.title AS book_title
        FROM   opp_nodes n
        JOIN   repertoire_lines rl ON rl.fen_sequence ? n.fen
        JOIN   chapters ch ON ch.id = rl.chapter_id
        JOIN   books    bk ON bk.id = ch.book_id
        WHERE  rl.active AND ch.active AND bk.active AND bk.player_id = %(pid)s
        ORDER  BY n.fen, LEFT(bk.color, 1), rl.id
    ),
    lead_in_moves AS (
        SELECT rep_fen.fen AS fen,
               rl.moves->>(rep_fen.ordinality - 2)::int        AS san,
               rl.fen_sequence->>(rep_fen.ordinality - 2)::int AS pre_fen
        FROM   repertoire_lines rl
        JOIN   chapters ch ON ch.id = rl.chapter_id
        JOIN   books    bk ON bk.id = ch.book_id,
        LATERAL jsonb_array_elements_text(rl.fen_sequence) WITH ORDINALITY AS rep_fen(fen, ordinality)
        WHERE  rl.active AND ch.active AND bk.active AND bk.player_id = %(pid)s
          AND  rl.fen_sequence ?| (SELECT array_agg(fen) FROM opp_nodes)
          AND  rep_fen.ordinality >= 2
          AND  rep_fen.fen IN (SELECT fen FROM opp_nodes)
          AND  LEFT(bk.color, 1) <> SPLIT_PART(rep_fen.fen, ' ', 2)
        UNION ALL
        SELECT gf.fen AS fen,
               cg.moves->>(gf.ordinality - 2)::int        AS san,
               cg.fen_sequence->>(gf.ordinality - 2)::int AS pre_fen
        FROM   player_games pg
        JOIN   chess_games  cg ON cg.id = pg.chess_game_id,
        LATERAL jsonb_array_elements_text(cg.fen_sequence) WITH ORDINALITY AS gf(fen, ordinality)
        WHERE  pg.player_id = %(pid)s
          AND  {analysable_sql("cg")}
          {my_since_clause}
          AND  cg.position_keys && (SELECT array_agg(bq_position_key(fen)) FROM opp_nodes)
          AND  gf.ordinality >= 2
          AND  gf.fen IN (SELECT fen FROM opp_nodes)
          AND  LEFT(pg.player_color, 1) <> SPLIT_PART(gf.fen, ' ', 2)
    ),
    lead_ins AS (
        SELECT fen,
               CASE WHEN COUNT(DISTINCT san) = 1 THEN MIN(san)     ELSE NULL END AS lead_in,
               CASE WHEN COUNT(DISTINCT san) = 1 THEN MIN(pre_fen) ELSE NULL END AS lead_pre_fen
        FROM   lead_in_moves
        WHERE  san IS NOT NULL
        GROUP  BY fen
    )
    SELECT n.fen, n.replies, n.node_freq, n.distinct_replies,
           COALESCE(mf.my_frequency, 0) AS my_frequency,
           li.lead_in, li.lead_pre_fen, rt.line_name, rt.book_title, rt.chapter_title
    FROM   opp_nodes n
    LEFT   JOIN my_fens  mf ON mf.fen = n.fen AND mf.my_side <> SPLIT_PART(n.fen, ' ', 2)
    LEFT   JOIN rep_tier rt ON rt.fen = n.fen AND rt.my_side <> SPLIT_PART(n.fen, ' ', 2)
    LEFT   JOIN lead_ins li ON li.fen = n.fen
    WHERE  (mf.fen IS NOT NULL OR rt.fen IS NOT NULL)
      AND  NOT EXISTS (SELECT 1 FROM dismissed_blunder_fens d
                       WHERE d.player_id = %(pid)s AND d.canonical_fen = bq_canonical_fen(n.fen) || ' 0 1')
    ORDER  BY n.node_freq * COALESCE(mf.my_frequency, 0) DESC, n.node_freq DESC, n.fen
    """


def decision_nodes(conn: Connection[Any], f: ScoutFilters, config: Settings) -> list[Row]:
    """One row per node, replies capped to `reply_cap` with the overflow counted, the board's
    orientation (`my_color`) being the side not to move."""
    if f.min_freq < 1:
        raise ValueError("min_freq out of domain")
    my_at = my_since(conn, f.my_last_n)
    opp_at = opp_since(conn, f.profile_id, f.opp_last_n)
    params: dict[str, Any] = {
        "pid": PLAYER_ID,
        "profile_id": f.profile_id,
        "min_depth": SCOUT_MIN_MATCH_PLY,
        "min_freq": f.min_freq,
        "branch_min": config.branch_min,
        "reply_min_freq": config.reply_min_freq,
        "my_since": my_at,
        "opp_since": opp_at,
    }
    with conn.cursor() as cur:
        cur.execute(cast(LiteralString, _query(my_at, opp_at)), params)
        rows = [dict(r) for r in cur.fetchall()]
    out: list[Row] = []
    for row in rows:
        replies = list(row.pop("replies") or [])
        capped = replies[: config.reply_cap]
        side = row["fen"].split(" ")[1] if " " in row["fen"] else "w"
        out.append(
            {
                **row,
                "node_freq": int(row["node_freq"]),
                "distinct_replies": int(row["distinct_replies"]),
                "my_frequency": int(row["my_frequency"]),
                "opp_replies": capped,
                "replies_more": max(0, len(replies) - len(capped)),
                "my_color": "white" if side == "b" else "black",
            }
        )
    return out
