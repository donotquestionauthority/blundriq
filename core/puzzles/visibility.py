"""Which puzzles the player may see and attempt right now.

One rule set, written once as SQL fragments and composed by every reader — the play
queue, the browse list, the deep link, the attempt route and the Home page's due count —
so the surfaces cannot disagree about what is visible.

A standard puzzle (not repertoire) is visible when it is active, is not a missed-mate
puzzle without its acceptance map, is not *conflicted* (the active repertoire prescribes a
different move at one of the puzzle's interior positions, for the puzzle's colour), is not
*redundant* (a blunder-sourced puzzle whose start position the active repertoire already
teaches, colour-matched — a separate rule from conflict, and neither implies the other),
and has not been dismissed on the Blunders page.

A repertoire puzzle is visible when its line, chapter and book are all active and the
player has deviated from that line in at least REPERTOIRE_PUZZLE_MIN_EVENTS distinct games,
all time. It is presented from the furthest deviation the player has made plus the
configured lookahead, snapped so that the truncated line still ends on the player's move,
and one puzzle per presented position is served (the most-deviated line wins, among the
lines of the book, chapter or line the reader asked for). Dismissal
does not apply to repertoire puzzles: their gate is the repertoire itself.

`occurrence_count` and `source_breakdown` are how often the position turned up in the
player's blunders and deviations; they order the queue and are shown on the row, they do
not gate visibility (every puzzle here is the player's own). Scouted opponents' games are
not counted here: that count needs Scout's own indexed shape, and it arrives with Scout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import PLAYER_ID, REPERTOIRE_PUZZLE_MIN_EVENTS

# One row per (line, step) of the active repertoire where the book's side is to move next
# (the last position of a line prescribes nothing). Shared by the conflict and redundancy
# rules and by the attempt route, so "the repertoire's prescribing positions" has one
# definition. Binds %(pid)s.
REP_FEN_STEPS = """
    SELECT rl.id AS line_id,
           CASE bk.color WHEN 'white' THEN 'w' ELSE 'b' END AS book_color,
           step.fen AS position_fen,
           rl.fen_sequence->>(step.ord::int) AS next_fen
    FROM repertoire_lines rl
    JOIN chapters ch ON ch.id = rl.chapter_id
    JOIN books bk ON bk.id = ch.book_id,
    LATERAL jsonb_array_elements_text(rl.fen_sequence) WITH ORDINALITY AS step(fen, ord)
    WHERE bk.player_id = %(pid)s AND bk.active = TRUE AND ch.active = TRUE AND rl.active = TRUE
      AND step.ord < jsonb_array_length(rl.fen_sequence)
"""


def redundancy_predicate(puzzle_alias: str, steps_relation: str) -> str:
    """A blunder-sourced puzzle is hidden when the active repertoire contains its start
    position with the book's colour to move: the player is already being taught it."""
    return f"""NOT ({puzzle_alias}.source_types @> ARRAY['blunder']
      AND EXISTS (SELECT 1 FROM {steps_relation} rfs
                  WHERE rfs.position_fen = {puzzle_alias}.fen AND rfs.book_color = {puzzle_alias}.color))"""


# The window predicate is the player's most recent N analysable games, or nothing.
_WINDOW_CTE = f"""
    window_games AS (
        SELECT pg.chess_game_id
        FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")}
        ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC
        LIMIT %(window)s
    ),"""
_ALL_GAMES_CTE = """
    window_games AS (
        SELECT pg.chess_game_id FROM player_games pg WHERE pg.player_id = %(pid)s
    ),"""


def standard_ctes(*, windowed: bool) -> str:
    """The CTE block every standard-visibility query starts from. `windowed` binds
    %(window)s to the player's most recent N games; otherwise all games count."""
    window = _WINDOW_CTE if windowed else _ALL_GAMES_CTE
    return f"""WITH{window}
    blunder_fens AS (
        SELECT b.canonical_fen AS fen, count(DISTINCT b.chess_game_id) AS cnt
        FROM blunders b JOIN window_games w ON w.chess_game_id = b.chess_game_id
        WHERE b.player_id = %(pid)s
        GROUP BY b.canonical_fen
    ),
    deviation_fens AS (
        SELECT grr.canonical_fen AS fen, count(DISTINCT grr.chess_game_id) AS cnt
        FROM game_repertoire_results grr JOIN window_games w ON w.chess_game_id = grr.chess_game_id
        WHERE grr.player_id = %(pid)s AND grr.deviation_by = 'me' AND grr.deviated_at_ply IS NOT NULL
        GROUP BY grr.canonical_fen
    ),
    fen_totals AS (
        SELECT fen, sum(cnt)::int AS total_count, jsonb_object_agg(source, cnt) AS source_breakdown
        FROM (SELECT fen, cnt, 'blunder' AS source FROM blunder_fens
              UNION ALL SELECT fen, cnt, 'deviation' FROM deviation_fens WHERE fen IS NOT NULL) af
        GROUP BY fen
    ),
    -- Both step relations are fenced: left inline, the planner joins the two unnested
    -- sequences before filtering on position and walks millions of pairs (40 s on the
    -- real data); materialised, the join is a hash on the position text (under a second).
    puzzle_fen_steps AS MATERIALIZED (
        SELECT p.id AS puzzle_id, p.color AS puzzle_color, step.fen AS position_fen,
               p.solution_fen_sequence->>(step.ord::int) AS next_fen
        FROM puzzles p, LATERAL jsonb_array_elements_text(p.solution_fen_sequence) WITH ORDINALITY AS step(fen, ord)
        WHERE p.player_id = %(pid)s AND p.active = TRUE AND p.is_repertoire = FALSE
          AND step.ord < jsonb_array_length(p.solution_fen_sequence)
          AND split_part(step.fen, ' ', 2) = p.color
    ),
    rep_fen_steps AS MATERIALIZED ({REP_FEN_STEPS}),
    conflicted AS (
        SELECT DISTINCT pfs.puzzle_id
        FROM puzzle_fen_steps pfs
        JOIN rep_fen_steps rfs ON rfs.position_fen = pfs.position_fen AND rfs.book_color = pfs.puzzle_color
        WHERE rfs.next_fen IS NOT NULL AND pfs.next_fen IS NOT NULL AND rfs.next_fen <> pfs.next_fen
    )"""


STANDARD_PREDICATE = f"""p.player_id = %(pid)s AND p.active = TRUE AND p.is_repertoire = FALSE
      AND NOT (p.source_types @> ARRAY['own_mate'] AND p.acceptance_map IS NULL)
      AND NOT EXISTS (SELECT 1 FROM conflicted c WHERE c.puzzle_id = p.id)
      AND {redundancy_predicate("p", "rep_fen_steps")}
      AND NOT EXISTS (SELECT 1 FROM dismissed_blunder_fens d
                      WHERE d.player_id = %(pid)s AND d.canonical_fen = p.canonical_fen)"""

_STANDARD_ROW = """p.id, p.fen, p.solution_line, p.source_types, p.color, p.title, p.description, p.created_at,
           p.themes, p.acceptance_map, FALSE AS is_repertoire, NULL::int AS presentation_ply,
           NULL::text AS presentation_fen, NULL::int AS repertoire_line_id,
           COALESCE(ft.total_count, 0) AS occurrence_count,
           COALESCE(ft.source_breakdown, '{}'::jsonb) AS source_breakdown,
           (SELECT count(*) FROM puzzle_attempts pa WHERE pa.puzzle_id = p.id AND pa.player_id = %(pid)s)::int
               AS attempt_count"""


def standard_rows_sql(*, windowed: bool, extra_where: str = "") -> LiteralString:
    """Full visible standard rows. `extra_where` narrows the outer WHERE (e.g. to one id)."""
    return cast(
        LiteralString,
        f"""{standard_ctes(windowed=windowed)}
    SELECT {_STANDARD_ROW}
    FROM puzzles p LEFT JOIN fen_totals ft ON ft.fen = p.canonical_fen
    WHERE {STANDARD_PREDICATE} {extra_where}""",
    )


# The presented ply: furthest deviation plus lookahead, capped at the line's end snapped to
# the player's parity so the truncated line still ends on his move.
_PRESENTATION_PLY = """LEAST(ls.furthest_ply + {n},
        (jsonb_array_length(p.solution_line) - 1)
          - ((jsonb_array_length(p.solution_line) - 1 - ls.furthest_ply) %% 2))::int"""


RepKind = Literal["book", "chapter", "line"]


@dataclass(frozen=True)
class RepScope:
    """A repertoire filter: every puzzle under one book, one chapter or one line."""

    kind: RepKind
    id: int


# The column each scope kind narrows on, inside `candidates`. A fixed map, so the SQL text is
# always one of three literals and the id is always a bound parameter.
_SCOPE_PREDICATE: dict[RepKind, LiteralString] = {
    "book": "AND bk.id = %(scope_id)s",
    "chapter": "AND ch.id = %(scope_id)s",
    "line": "AND rl.id = %(scope_id)s",
}


def repertoire_rows_sql(
    lookahead_plies: int, *, collapse: bool = True, scope: RepKind | None = None, by_id: bool = False
) -> LiteralString:
    """Full visible repertoire rows. One row per presented position when `collapse`.

    `scope` narrows the candidates to one book, chapter or line (binds %(scope_id)s) BEFORE the
    collapse, so a scope whose puzzle loses the collapse to another scope's puzzle on the same
    board still has its own representative. `by_id` narrows to one puzzle (binds %(id)s)."""
    n = int(lookahead_plies)
    ply = _PRESENTATION_PLY.format(n=n)
    distinct = "DISTINCT ON (presentation_fen)" if collapse else ""
    order = "ORDER BY presentation_fen, occurrence_count DESC, id ASC" if collapse else "ORDER BY id"
    narrow = (_SCOPE_PREDICATE[scope] if scope else "") + (" AND p.id = %(id)s" if by_id else "")
    return cast(
        LiteralString,
        f"""WITH line_events AS (
        SELECT grl.line_id, grr.chess_game_id, grr.deviated_at_ply AS event_ply
        FROM game_result_lines grl
        JOIN game_repertoire_results grr ON grr.id = grl.game_repertoire_result_id
        WHERE grr.player_id = %(pid)s AND grr.deviation_by = 'me' AND grr.deviated_at_ply IS NOT NULL
    ),
    line_stats AS (
        SELECT line_id, count(DISTINCT chess_game_id) AS event_count, max(event_ply) AS furthest_ply
        FROM line_events GROUP BY line_id
        HAVING count(DISTINCT chess_game_id) >= {REPERTOIRE_PUZZLE_MIN_EVENTS}
    ),
    candidates AS (
        SELECT p.id, p.fen, p.solution_line, p.source_types, p.color, p.title, p.description, p.created_at,
               p.themes, NULL::jsonb AS acceptance_map, TRUE AS is_repertoire,
               {ply} AS presentation_ply,
               p.solution_fen_sequence->>{ply} AS presentation_fen,
               p.repertoire_line_id, rl.chapter_id, ch.book_id,
               ls.event_count::int AS occurrence_count,
               jsonb_build_object('deviation', ls.event_count) AS source_breakdown,
               (SELECT count(*) FROM puzzle_attempts pa WHERE pa.puzzle_id = p.id AND pa.player_id = %(pid)s)::int
                   AS attempt_count
        FROM puzzles p
        JOIN line_stats ls ON ls.line_id = p.repertoire_line_id
        JOIN repertoire_lines rl ON rl.id = p.repertoire_line_id
        JOIN chapters ch ON ch.id = rl.chapter_id
        JOIN books bk ON bk.id = ch.book_id
        WHERE p.player_id = %(pid)s AND p.active = TRUE AND p.is_repertoire = TRUE
          AND bk.player_id = %(pid)s AND rl.active = TRUE AND ch.active = TRUE AND bk.active = TRUE
          {narrow}
    )
    SELECT {distinct} * FROM candidates {order}""",
    )


def normalise(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if out.get("created_at") is not None:
        out["created_at"] = out["created_at"].isoformat()
    if not isinstance(out.get("source_breakdown"), dict):
        out["source_breakdown"] = {}
    # Migrated rows may carry NULL themes; the wire shape is always a list.
    if out.get("themes") is None:
        out["themes"] = []
    return out


def visible_rows(
    conn: Connection[Any], *, last_n_games: int, lookahead_plies: int, rep_scope: RepScope | None = None
) -> list[dict[str, Any]]:
    """Every visible puzzle, repertoire rows first. Callers re-sort; the order here only
    makes the result stable. With `rep_scope`, only the repertoire rows under it, collapsed
    among themselves (a scoped reader asks for repertoire puzzles and nothing else)."""
    params: dict[str, Any] = {"pid": PLAYER_ID, "window": last_n_games}
    out: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        if rep_scope is not None:
            params["scope_id"] = rep_scope.id
        cur.execute(repertoire_rows_sql(lookahead_plies, scope=rep_scope.kind if rep_scope else None), params)
        rep = sorted(cur.fetchall(), key=lambda r: (r["attempt_count"], -r["occurrence_count"], r["id"]))
        out.extend(normalise(r) for r in rep)
        if rep_scope is not None:
            return out
        cur.execute(
            standard_rows_sql(windowed=last_n_games > 0)
            + " ORDER BY attempt_count ASC, COALESCE(ft.total_count, 0) DESC, p.created_at DESC, p.id DESC",
            params,
        )
        out.extend(normalise(r) for r in cur.fetchall())
    return out


def visible_standard_by_id(conn: Connection[Any], ids: list[int], *, last_n_games: int) -> list[dict[str, Any]]:
    """The visible subset of `ids` among standard puzzles — what a mint just materialised,
    without paying for a second full visibility pass. Empty input issues no query."""
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            standard_rows_sql(windowed=last_n_games > 0, extra_where="AND p.id = ANY(%(ids)s::int[])"),
            {"pid": PLAYER_ID, "window": last_n_games, "ids": [int(i) for i in ids]},
        )
        return [normalise(r) for r in cur.fetchall()]


PLAYABLE_FIELDS = (
    "id",
    "fen",
    "solution_line",
    "color",
    "acceptance_map",
    "source_types",
    "themes",
    "is_repertoire",
    "presentation_ply",
)


def playable(row: dict[str, Any]) -> dict[str, Any]:
    """The immutable solver payload: what a board needs and nothing about the queue."""
    return {field: row[field] for field in PLAYABLE_FIELDS}


def visible_by_id(conn: Connection[Any], puzzle_id: int, *, lookahead_plies: int) -> dict[str, Any] | None:
    """One visible puzzle's full row, or None when it is missing or not visible (the deep
    link answers 404 for both). A repertoire puzzle is visible when it is an eligible
    candidate: the one-per-position collapse picks a representative for a list, and a
    narrower filter may pick a different one, so a by-id check never depends on it."""
    params = {"pid": PLAYER_ID, "window": 0, "id": puzzle_id}
    with conn.cursor() as cur:
        cur.execute(standard_rows_sql(windowed=False, extra_where="AND p.id = %(id)s"), params)
        row = cur.fetchone()
        if row is not None:
            return normalise(row)
        cur.execute(repertoire_rows_sql(lookahead_plies, collapse=False, by_id=True), params)
        row = cur.fetchone()
    return normalise(row) if row is not None else None


def repertoire_scopes(conn: Connection[Any], *, lookahead_plies: int) -> list[dict[str, Any]]:
    """Books, their chapters and those chapters' lines that have a visible repertoire puzzle,
    each with `count`: how many positions its scope serves, which is how many rows the browse
    list shows for it (distinct presented positions after its own collapse)."""
    query = f"""
        WITH v AS ({repertoire_rows_sql(lookahead_plies, collapse=False)})
        SELECT v.book_id, bk.title AS book_title, bk.color AS book_color,
               v.chapter_id, ch.title AS chapter_title,
               v.repertoire_line_id AS line_id, rl.line_name,
               count(DISTINCT v.presentation_fen) AS n,
               GROUPING(v.chapter_id) AS g_chapter, GROUPING(v.repertoire_line_id) AS g_line
        FROM v
        JOIN books bk ON bk.id = v.book_id AND bk.player_id = %(pid)s
        JOIN chapters ch ON ch.id = v.chapter_id
        JOIN repertoire_lines rl ON rl.id = v.repertoire_line_id
        GROUP BY GROUPING SETS (
            (v.book_id, bk.title, bk.color),
            (v.book_id, bk.title, bk.color, v.chapter_id, ch.title),
            (v.book_id, bk.title, bk.color, v.chapter_id, ch.title, v.repertoire_line_id, rl.line_name)
        )
        ORDER BY g_chapter DESC, g_line DESC, bk.title, v.book_id, ch.title, v.chapter_id,
                 rl.line_name, v.repertoire_line_id"""
    with conn.cursor() as cur:
        cur.execute(query, {"pid": PLAYER_ID})
        rows = cur.fetchall()
    # Books arrive first, then chapters, then lines, each in display order.
    books: list[dict[str, Any]] = []
    book_chapters: dict[int, list[dict[str, Any]]] = {}
    chapter_lines: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        n = int(r["n"])
        if r["g_chapter"]:
            chapters: list[dict[str, Any]] = []
            book_chapters[int(r["book_id"])] = chapters
            books.append(
                {
                    "id": int(r["book_id"]),
                    "title": r["book_title"],
                    "color": r["book_color"],
                    "count": n,
                    "chapters": chapters,
                }
            )
        elif r["g_line"]:
            lines: list[dict[str, Any]] = []
            chapter_lines[int(r["chapter_id"])] = lines
            book_chapters[int(r["book_id"])].append(
                {"id": int(r["chapter_id"]), "title": r["chapter_title"], "count": n, "lines": lines}
            )
        else:
            chapter_lines[int(r["chapter_id"])].append({"id": int(r["line_id"]), "title": r["line_name"], "count": n})
    return books


_ATTEMPTABLE = cast(
    LiteralString,
    f"""
    WITH target AS (
        SELECT id, fen, canonical_fen, solution_line, solution_fen_sequence, color, is_repertoire,
               acceptance_map, source_types, repertoire_line_id
        FROM puzzles
        WHERE id = %(id)s AND player_id = %(pid)s AND active = TRUE
          AND NOT (source_types @> ARRAY['own_mate'] AND acceptance_map IS NULL)
    )
    SELECT t.id, t.fen, t.solution_line, t.color, t.acceptance_map, t.source_types, t.is_repertoire,
           t.repertoire_line_id
    FROM target t
    WHERE (t.is_repertoire = TRUE AND EXISTS (
              SELECT 1 FROM repertoire_lines rl
              JOIN chapters ch ON ch.id = rl.chapter_id
              JOIN books bk ON bk.id = ch.book_id
              WHERE rl.id = t.repertoire_line_id AND bk.player_id = %(pid)s
                AND rl.active = TRUE AND ch.active = TRUE AND bk.active = TRUE))
       OR (t.is_repertoire = FALSE
           AND NOT EXISTS (
               SELECT 1
               FROM jsonb_array_elements_text(t.solution_fen_sequence) WITH ORDINALITY AS pstep(fen, ord)
               JOIN ({REP_FEN_STEPS}) rfs ON rfs.position_fen = pstep.fen AND rfs.book_color = t.color
               WHERE pstep.ord < jsonb_array_length(t.solution_fen_sequence)
                 AND split_part(pstep.fen, ' ', 2) = t.color
                 AND (t.solution_fen_sequence->>(pstep.ord::int)) IS NOT NULL
                 AND rfs.next_fen IS NOT NULL
                 AND (t.solution_fen_sequence->>(pstep.ord::int)) <> rfs.next_fen)
           AND NOT EXISTS (SELECT 1 FROM dismissed_blunder_fens d
                           WHERE d.player_id = %(pid)s AND d.canonical_fen = t.canonical_fen)
           AND {redundancy_predicate("t", "(" + REP_FEN_STEPS + ")")})
    """,
)


def attemptable(conn: Connection[Any], puzzle_id: int) -> dict[str, Any] | None:
    """The row the attempt route grades against, or None when the puzzle is not visible.
    A single-row probe of the same rules as `standard_rows_sql`, without the counts."""
    with conn.cursor() as cur:
        cur.execute(_ATTEMPTABLE, {"id": puzzle_id, "pid": PLAYER_ID})
        row = cur.fetchone()
    return dict(row) if row else None


def presentation_ply(conn: Connection[Any], puzzle_id: int, *, lookahead_plies: int) -> int | None:
    """The truncation point the player was shown for a repertoire puzzle; None for a
    standard puzzle or a line without enough deviations. Serve and grading derive it the
    same way, so the player is graded against the line he saw."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT is_repertoire, repertoire_line_id, jsonb_array_length(solution_line) AS sol_len"
            " FROM puzzles WHERE id = %(id)s AND player_id = %(pid)s",
            {"id": puzzle_id, "pid": PLAYER_ID},
        )
        p = cur.fetchone()
        if not p or not p["is_repertoire"] or not p["repertoire_line_id"] or not p["sol_len"]:
            return None
        cur.execute(
            """
            SELECT count(DISTINCT grr.chess_game_id) AS event_count, max(grr.deviated_at_ply) AS furthest_ply
            FROM game_result_lines grl
            JOIN game_repertoire_results grr ON grr.id = grl.game_repertoire_result_id
            WHERE grr.player_id = %(pid)s AND grl.line_id = %(line)s
              AND grr.deviation_by = 'me' AND grr.deviated_at_ply IS NOT NULL
            """,
            {"pid": PLAYER_ID, "line": p["repertoire_line_id"]},
        )
        s = cur.fetchone()
    if not s or s["event_count"] < REPERTOIRE_PUZZLE_MIN_EVENTS or s["furthest_ply"] is None:
        return None
    furthest = int(s["furthest_ply"])
    cap = (int(p["sol_len"]) - 1) - ((int(p["sol_len"]) - 1 - furthest) % 2)
    return min(furthest + int(lookahead_plies), cap)
