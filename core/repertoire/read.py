"""The repertoire read side: which lines pass through a position, and what they say to play.

Every reader composes the same predicate for **effectively active**: `rl.active AND ch.active
AND bk.active`. `is_alternative` is a display tag, never part of it.

`rep_lines` answers "which lines contain this board" for a batch of FENs: a GIN probe on
`repertoire_lines.position_keys` (a hash, a prefilter) verified by exact board text
(`bq_canonical_fen`), one row per *occurrence* — a line can reach the same board twice — with
the move the line plays from there and how the player's games have fared at that board. The
result is keyed by the input FEN; a FEN given twice is looked up once.

`book_color` has no default on purpose. A Black book contains the start position at ply 0
with White's first move as its `expected_move` (the move the book answers), so a reader that
did not filter by colour once reported "your prep plays Nf3" as White from 1,294 lines of an
anti-Réti book. `'white'` and `'black'` assert every input FEN's side to move; `'by_turn'`
filters each FEN by its own side to move, with `ELSE NULL` in the CASE — a real colour in that
branch would be a default and bring the defect back.

Reducing many occurrences to one move is `project_ply`, and only `project_ply`. It reads the
raw FEN first — an occurrence whose stored FEN equals the game's, counters included, is an
exact match and outranks any board-only (transposed) occurrence — then canonicalises every
candidate's stored move against the position (two spellings of one move are one move; a
token that is not legal here makes the ply `unreadable`), then fails closed with `conflict`
when two *different* lines prescribe different moves. One line holding the same board twice is
not a conflict (the tie-break's `line_ply` picks the earlier occurrence). `singular_move` is
the thin wrapper every card uses for its repertoire arrow; four hand-rolled versions of it
once disagreed with each other.
"""

from __future__ import annotations

from typing import Any, Literal, LiteralString, cast

import chess
from psycopg import Connection

from core.constants import PLAYER_ID

Row = dict[str, Any]
BookColor = Literal["white", "black", "by_turn"]

STATUS_MATCH = "match"
STATUS_AGREE = "agree"
STATUS_END_OF_LINE = "end_of_line"
STATUS_CONFLICT = "conflict"
STATUS_UNREADABLE = "unreadable"
STATUS_NONE = "none"

_COLOR_CLAUSE: dict[str, str] = {
    "by_turn": (
        "AND bk.color = CASE SPLIT_PART(qk.fen, ' ', 2) WHEN 'w' THEN 'white' WHEN 'b' THEN 'black' ELSE NULL END"
    ),
    "white": "AND bk.color = 'white'",
    "black": "AND bk.color = 'black'",
}

# The GIN probe is a correlated LATERAL with `OFFSET 0`: without the fence the planner pulls
# the subquery up into the join and, past ~120 input FENs, abandons the index and unnests every
# element of every active line (measured 6.4x slower on Rob's repertoire). `AS MATERIALIZED`
# keeps the input normalisation to once per FEN. Both are load-bearing; neither changes a row.
_LINES_SQL = """
WITH qk AS MATERIALIZED (
    SELECT q.fen AS fen, bq_position_key(q.fen) AS key, bq_canonical_fen(q.fen) AS canon
    FROM unnest(%(fens)s::text[]) AS q(fen)
)
SELECT qk.fen, rl.id AS line_id, rl.line_name, rl.is_alternative, ch.id AS chapter_id, ch.title AS chapter_title,
       bk.id AS book_id, bk.title AS book_title,
       rl.moves->>((k.ord - 1)::int)       AS expected_move,
       rl.moves                             AS line_moves,
       (k.ord - 1)::int                     AS line_ply,
       rl.fen_sequence->>((k.ord - 1)::int) AS occurrence_fen
FROM qk
CROSS JOIN LATERAL (
    SELECT rl_i.id, rl_i.line_name, rl_i.is_alternative, rl_i.chapter_id, rl_i.moves, rl_i.fen_sequence,
           rl_i.position_keys
    FROM repertoire_lines rl_i
    WHERE rl_i.position_keys @> ARRAY[qk.key] AND rl_i.active
    OFFSET 0
) rl
JOIN chapters ch ON ch.id = rl.chapter_id
JOIN books bk ON bk.id = ch.book_id,
LATERAL unnest(rl.position_keys) WITH ORDINALITY AS k(key, ord)
WHERE k.key = qk.key
  AND bq_canonical_fen(rl.fen_sequence->>((k.ord - 1)::int)) = qk.canon
  AND bk.player_id = %(pid)s AND ch.active AND bk.active
  {color_clause}
ORDER BY qk.fen, bk.title, ch.title, rl.line_name, rl.id, k.ord
"""

# Per (board, book): games containing the board, by what their result row for that book says.
# A game with no result row groups under book NULL and so counts for nothing.
_STATS_SQL = """
WITH qk AS MATERIALIZED (
    SELECT q.fen AS fen, bq_position_key(q.fen) AS key, bq_canonical_fen(q.fen) AS canon
    FROM unnest(%(fens)s::text[]) AS q(fen)
)
SELECT qk.fen, grr.book_id,
    COUNT(DISTINCT cg.id) FILTER (WHERE grr.deviation_by IS NULL OR grr.deviation_by = 'none') AS followed,
    MAX(cg.played_at)     FILTER (WHERE grr.deviation_by IS NULL OR grr.deviation_by = 'none') AS last_followed,
    COUNT(DISTINCT cg.id) FILTER (WHERE grr.deviation_by = 'me') AS deviated_by_me,
    MAX(cg.played_at)     FILTER (WHERE grr.deviation_by = 'me') AS last_deviated,
    MODE() WITHIN GROUP (ORDER BY CASE WHEN grr.deviation_by = 'me' THEN grr.expected_move END NULLS LAST)
        AS me_dev_expected,
    MODE() WITHIN GROUP (ORDER BY CASE WHEN grr.deviation_by = 'me' THEN grr.played_move END NULLS LAST)
        AS me_dev_played,
    MODE() WITHIN GROUP (ORDER BY CASE WHEN grr.deviation_by = 'me' THEN grr.deviated_at_ply END NULLS LAST)
        AS me_dev_ply,
    COUNT(DISTINCT cg.id) FILTER (WHERE grr.deviation_by = 'opponent') AS deviated_by_opp,
    MODE() WITHIN GROUP (ORDER BY CASE WHEN grr.deviation_by = 'opponent' THEN grr.expected_move END NULLS LAST)
        AS opp_dev_expected,
    MODE() WITHIN GROUP (ORDER BY CASE WHEN grr.deviation_by = 'opponent' THEN grr.played_move END NULLS LAST)
        AS opp_dev_played,
    MODE() WITHIN GROUP (ORDER BY CASE WHEN grr.deviation_by = 'opponent' THEN grr.deviated_at_ply END NULLS LAST)
        AS opp_dev_ply
FROM qk
JOIN chess_games cg ON cg.position_keys @> ARRAY[qk.key]
JOIN player_games pg ON pg.chess_game_id = cg.id
CROSS JOIN LATERAL unnest(cg.position_keys) WITH ORDINALITY AS k(key, ord)
LEFT JOIN game_repertoire_results grr ON grr.chess_game_id = pg.chess_game_id AND grr.player_id = pg.player_id
WHERE pg.player_id = %(pid)s
  AND k.key = qk.key
  AND bq_canonical_fen(cg.fen_sequence->>((k.ord - 1)::int)) = qk.canon
  AND (grr.book_id = ANY(%(book_ids)s) OR grr.book_id IS NULL)
GROUP BY qk.fen, grr.book_id
"""

_NO_STATS: dict[str, Any] = {
    "followed": 0,
    "last_followed": None,
    "deviated_by_me": 0,
    "last_deviated": None,
    "me_dev_expected": None,
    "me_dev_played": None,
    "me_dev_ply": None,
    "deviated_by_opp": 0,
    "opp_dev_expected": None,
    "opp_dev_played": None,
    "opp_dev_ply": None,
}


def _strings(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x) for x in cast(list[Any], v)]


def side_to_move(fen: str) -> str:
    parts = fen.split(" ")
    if len(parts) < 2 or parts[1] not in ("w", "b"):
        raise ValueError("FEN has no readable side to move")
    return parts[1]


def rep_lines(
    conn: Connection[Any], fens: list[str], *, book_color: BookColor, with_stats: bool = True
) -> dict[str, list[Row]]:
    """{input FEN: occurrences} for every effectively-active line containing that board, keyed
    by the exact input string, with the per-(board, book) statistics unless `with_stats` is
    False (then the keys are present with their empty values, so no caller branches)."""
    if book_color not in _COLOR_CLAUSE:
        raise ValueError(f"book_color must be white, black or by_turn, not {book_color!r}")
    if not fens:
        return {}
    if book_color in ("white", "black"):
        want = "w" if book_color == "white" else "b"
        for fen in fens:
            if side_to_move(fen) != want:
                raise ValueError(f"book_color={book_color!r} but a FEN is not {book_color} to move")
    distinct = list(dict.fromkeys(fens))
    params: dict[str, Any] = {"pid": PLAYER_ID, "fens": distinct}
    with conn.cursor() as cur:
        cur.execute(cast(LiteralString, _LINES_SQL.format(color_clause=_COLOR_CLAUSE[book_color])), params)
        line_rows = [dict(r) for r in cur.fetchall()]
    stats: dict[tuple[str, int], dict[str, Any]] = {}
    if with_stats and line_rows:
        book_ids = sorted({int(r["book_id"]) for r in line_rows})
        with conn.cursor() as cur:
            cur.execute(cast(LiteralString, _STATS_SQL), params | {"book_ids": book_ids})
            for r in cur.fetchall():
                if r["book_id"] is None:
                    continue
                stats[(str(r["fen"]), int(r["book_id"]))] = {
                    **{k: r[k] for k in _NO_STATS},
                    "last_followed": r["last_followed"].isoformat() if r["last_followed"] else None,
                    "last_deviated": r["last_deviated"].isoformat() if r["last_deviated"] else None,
                }
    out: dict[str, list[Row]] = {f: [] for f in fens}
    for r in line_rows:
        fen = str(r["fen"])
        out[fen].append(
            {
                "book": r["book_title"],
                "chapter": r["chapter_title"],
                "line_name": r["line_name"],
                "line_id": r["line_id"],
                "is_alternative": bool(r["is_alternative"]),
                "expected_move": r["expected_move"],
                "occurrence_fen": r["occurrence_fen"],
                "line_moves": _strings(r["line_moves"]),
                "line_ply": r["line_ply"],
                **(stats.get((fen, int(r["book_id"])), dict(_NO_STATS))),
            }
        )
    return out


# --- reducing occurrences to a move ---------------------------------------------


def tie_break(c: Row) -> tuple[str, str, str, int, int]:
    """The total order over occurrences: book, chapter, line name, line id, and `line_ply` as
    the terminator, because one line can hold the same board twice."""
    return (
        c.get("book") or "",
        c.get("chapter") or "",
        c.get("line_name") or "",
        c["line_id"] if c.get("line_id") is not None else -1,
        c["line_ply"] if c.get("line_ply") is not None else -1,
    )


def canonicalise(fen: str, pool: list[Row]) -> list[Row]:
    """Each candidate's stored `expected_move` parsed in this position and re-emitted as the
    one canonical SAN, in `canonical_move`. Raises ValueError (python-chess's invalid, illegal
    and ambiguous errors all are) so the caller can mark the whole ply unreadable."""
    board = chess.Board(fen)
    out: list[Row] = []
    for c in pool:
        move = board.parse_san(str(c["expected_move"]))
        out.append({**c, "canonical_move": board.san(move)})
    return out


def conflict_groups(pool: list[Row]) -> list[Row]:
    """One entry per distinct canonical move, ordered by each group's representative under the
    tie-break; `more_lines` counts other distinct lines prescribing the same move."""
    by_move: dict[str, list[Row]] = {}
    for c in pool:
        by_move.setdefault(str(c["canonical_move"]), []).append(c)
    ordered: list[tuple[tuple[str, str, str, int, int], Row]] = []
    for move, members in by_move.items():
        rep = min(members, key=tie_break)
        ordered.append(
            (
                tie_break(rep),
                {
                    "move": move,
                    "book": rep.get("book"),
                    "chapter": rep.get("chapter"),
                    "line_name": rep.get("line_name"),
                    "more_lines": len({m.get("line_id") for m in members}) - 1,
                },
            )
        )
    ordered.sort(key=lambda pair: pair[0])
    return [g for _, g in ordered]


def _entry(
    status: str,
    transposed: bool | None,
    selected: Row | None = None,
    more_lines: int = 0,
    conflict: list[Row] | None = None,
) -> Row:
    entry: Row = {
        "status": status,
        "book_move": None,
        "book": None,
        "chapter": None,
        "line_name": None,
        "line_ply": None,
        "plan": [],
        "more_lines": more_lines,
        "transposed": transposed,  # None is meaningful: no line was selected at all
        "conflict": conflict,
    }
    if selected is not None:
        moves = _strings(selected.get("line_moves"))
        line_ply = selected.get("line_ply")
        entry["book"] = selected.get("book")
        entry["chapter"] = selected.get("chapter")
        entry["line_name"] = selected.get("line_name")
        entry["line_ply"] = line_ply
        if status in (STATUS_MATCH, STATUS_AGREE):
            entry["book_move"] = selected.get("canonical_move")
            entry["plan"] = moves[int(line_ply or 0) :]
    return entry


def _divergent_across_lines(pool: list[Row]) -> bool:
    return any(
        a["canonical_move"] != b["canonical_move"] and a.get("line_id") != b.get("line_id")
        for i, a in enumerate(pool)
        for b in pool[i + 1 :]
    )


def project_ply(fen: str, candidates: list[Row]) -> Row:
    """What the repertoire says at `fen` (the game's raw FEN, counters included), given the
    occurrences `rep_lines` returned for its board. The order of the branches is the contract."""
    if not candidates:
        return _entry(STATUS_NONE, None)
    exact = [c for c in candidates if c.get("occurrence_fen") is not None and c["occurrence_fen"] == fen]
    more_lines = max(len({c.get("line_id") for c in candidates}) - 1, 0)
    if exact:
        continuing = [c for c in exact if c.get("expected_move") is not None]
        if continuing:
            try:
                pool = canonicalise(fen, continuing)
            except ValueError:
                return _entry(STATUS_UNREADABLE, None)
            if _divergent_across_lines(pool):
                return _entry(STATUS_CONFLICT, None, conflict=conflict_groups(pool))
            return _entry(STATUS_MATCH, False, min(pool, key=tie_break), more_lines)
        return _entry(STATUS_END_OF_LINE, False, min(exact, key=tie_break), more_lines)
    continuing = [c for c in candidates if c.get("expected_move") is not None]
    if continuing:
        try:
            pool = canonicalise(fen, continuing)
        except ValueError:
            return _entry(STATUS_UNREADABLE, None)
        if len({c["canonical_move"] for c in pool}) == 1:
            return _entry(STATUS_AGREE, True, min(pool, key=tie_break), more_lines)
        return _entry(STATUS_CONFLICT, None, conflict=conflict_groups(pool))
    return _entry(STATUS_END_OF_LINE, True, min(candidates, key=tie_break), more_lines)


def singular_move(fen: str, lines: list[Row]) -> str | None:
    """The one move the repertoire prescribes at `fen`, or None when it does not agree on one."""
    if not lines:
        return None
    return project_ply(fen, lines).get("book_move")
