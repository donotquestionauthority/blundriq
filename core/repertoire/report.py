"""The line report: the repertoire lines the player's games go through, and the boards where
the games are on their own.

One read (`full`) over the matcher's results (`game_repertoire_results`, `game_result_lines`) and
the analyser's stored lines (`chess_games.ply_analysis`), scoped to the analysis window
(`core.chess.eligibility.window_cte`: Chess960 never takes a slot) and the Deviations page's
time-class filter, returning the lines table and both colours' gaps together: the per-(game,
line) rows are reduced once, in SQL (`_REPORT_SQL`), to one row per line, chapter, book and gap
game. The read has no state of its own; a rematch rewrites what it reads.

**Per (game, line) status.** The matcher attaches a game to every line it reached equally far
(`matched_ply`, the same boundary `m` for all of them) and describes the result row from the
first of them only, so the report derives each attached line's own outcome from that line's
moves and the game's, in the order `core.repertoire.matching.who_deviated` stops:

    ran_out   m >= len(line.moves)        the line has no move at m — followed to the end
    ended     m >= len(game.moves)        the game stopped (resigned, timed out, drawn) in book
    me        the player's turn at ply m  the player left the line: expected line.moves[m],
                                          played game.moves[m], at own move own(m) + 1
    opponent  the opponent's turn at m    the opponent left the line: the gap board is the
                                          game's board after their move, game.fens[m + 1]

`own(ply, colour)` — how many of the player's moves fall in the first `ply` plies — is
`player_committed`'s arithmetic: (ply + 1) // 2 for White, ply // 2 for Black. The player is
*faithful* to a line unless the status is `me`; `ran_out` and `ended` have no slip and are
never a gap.

**Reduction to a game.** At chapter and book level a game is one game. Every tied line shares
`m`, so the mover at `m` is the same for all of them and a line is either exhausted (`ran_out`,
`ended`) or carries that one status: a game's status for a chapter is `me` if any attached line
of the chapter says `me`, else `opponent` if any says `opponent`, else `ended` if any says
`ended`, else `ran_out` — "did the player play the book move at every own turn while the
chapter still covered the game". A game is a gap (kind A) when its reduction over *every*
attached line is `opponent`; its context is the continuing line chosen by the read module's
tie-break (book, chapter, line name, line id), never the id the matcher wrote first.

A game tied between two lines of one scope that both say `me` with different expected moves
reduces to the first line under that same order, so the scope's slip never depends on the id
the matcher wrote first.

**One observed tuple.** A line's slip and a gap's book context are the most common whole
`(own move, expected, played)` / `(book, chapter, line, expected, played)` combination with its
count, never a mode per field: independent modes can describe a slip no game made.

**Unmatched games** (`player_games.no_repertoire_match`) are checked at the player's first
own-decision board (the start as White, the board after White's first move as Black) against the
active repertoire through the read module's continuing-move projection: a covered board is
"prepared but not played" (the book prescribes a move there, the player played another), an
uncovered one is a gap (kind B). A line that merely ends at the board is not preparation.

**Puzzles and the stem.** A prepared row's `puzzles` is the positions Practice serves for its
line (`visibility.repertoire_scopes`); the practice numbers per line are `core.puzzles.stats`.
A gap card carries `stem`, the representative game's own moves from the start to the gap board,
re-rendered and checked to reach it, which is what a line added from the card opens with; the
chapters such a line may go into are sent once per colour (`core.repertoire.lines`).

**Scope.** The read describes the player's most recent `games` analysable games (`scope`): the
request's number, 500 when it names none, never more than `analysis_game_limit` — the matcher's
window, so every game in scope has been matched; the options offered are the fixed steps below
the limit and the limit itself. The practice numbers are not game-scoped.

**Decisions and practice** (`core.repertoire.decisions`, `core.repertoire.practice`) add to each
line what the player did at every board the line prescribes a move at, and how its puzzle's
recent first tries went; the Decisions view is the same evidence grouped by board.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, LiteralString, cast

import chess
from psycopg import Connection

from core.chess.eligibility import analysable_sql, time_class_sql, window_cte
from core.chess.san import normalize_san
from core.chess.san import parse as parse_san
from core.constants import (
    CATCH_ALL_TITLE,
    DRILL_MAX_LINES,
    PLAYER_ID,
    REPERTOIRE_LINE_MAX_PLIES,
    REPORT_CONTINUATION_PLIES,
    REPORT_PREFILL_PLIES,
)
from core.puzzles import serve, stats, visibility
from core.repertoire import decisions as decisions_mod
from core.repertoire import lines as lines_by_hand
from core.repertoire import practice, read
from core.settings import Settings

Row = dict[str, Any]

RAN_OUT = "ran_out"
ENDED = "ended"
ME = "me"
OPPONENT = "opponent"
_PRECEDENCE = {ME: 0, OPPONENT: 1, ENDED: 2, RAN_OUT: 3}

# The per-(game, attached line) rows of the window — the line, its chapter and book, the matched
# boundary and the two moves at it — and their reduction to one row per line, chapter, book and
# gap game, all in one statement. The status and own-move arithmetic are written here and in
# Python (`status_of`, `own_moves`): the Python is what the tests check against the matcher, and
# a parity test holds the two together. Every text tie-break orders under COLLATE "C": Python
# compares strings by code point and the database's default collation does not.

# The reduction's session settings, for its transaction: the planner's row estimates for one
# player's small tables cannot be relied on, so nested loops — a probe per (game, line) pair —
# are discouraged, the pairs' hashes and sorts get working memory sized to them rather than the
# server's default, and the statement's many expressions are not JIT-compiled per run. The gap
# boards' lookups by id that follow are nested loops by right, so that one comes back.
READ_SETTINGS = ("SET LOCAL jit = off; SET LOCAL enable_nestloop = off; SET LOCAL work_mem = '32MB'",)
READ_SETTINGS_AFTER = ("SET LOCAL enable_nestloop = on",)

_PRECEDENCE_SQL = "CASE status WHEN 'me' THEN 0 WHEN 'opponent' THEN 1 WHEN 'ended' THEN 2 ELSE 3 END"

# What a (game `g`, line `n`) pair derives: the status in `who_deviated`'s order of stops, the
# player's moves up to the boundary, and the two moves at it when someone left (the slip's, or
# the gap's). Used for every pair and again for a scope's representative pair.
_DERIVED_SQL = """
    CASE WHEN n.color = 'white' THEN (g.m + 1) / 2 ELSE g.m / 2 END AS own,
    CASE WHEN s.status IN ('me', 'opponent') THEN n.moves->>g.m END AS expected,
    CASE WHEN s.status IN ('me', 'opponent') THEN g.played END AS played"""
_STATUS_SQL = """
    CROSS JOIN LATERAL (
        SELECT CASE WHEN g.m >= n.len THEN 'ran_out'
                    WHEN g.m >= g.glen THEN 'ended'
                    WHEN (g.m %% 2 = 0) = (n.color = 'white') THEN 'me'
                    ELSE 'opponent' END AS status
    ) s"""

# A scope's compact rows: one per (scope id, own moves, status, the slip's two moves), with the
# count, the results, the latest game and — for a line — whether every game in the group sits
# under another line of the chapter too. `_summary` adds them up; a line has a few such rows.
_COMPACT = """SELECT '{scope}' AS scope, {key} AS id, own, status,
       CASE WHEN status = 'me' THEN expected END AS expected, CASE WHEN status = 'me' THEN played END AS played,
       count(*) AS n, count(*) FILTER (WHERE result = 'win') AS wins, count(*) FILTER (WHERE result = 'draw') AS draws,
       count(*) FILTER (WHERE result = 'loss') AS losses, max(played_at) AS last_played,
       {shared} AS shared, NULL::jsonb AS ctx
FROM {src} GROUP BY 2, 3, 4, 5, 6"""

_REPORT_SQL = (
    """
WITH {window},
g AS MATERIALIZED (
    -- One row per matched game in scope: the boundary and the game's move there, computed once
    -- (every line tied on a game shares `matched_ply`, so this is the game's fact, not the line's).
    SELECT grr.id AS rid, grr.chess_game_id AS gid, pg.result, cg.played_at, grr.deviated_at_ply AS m,
           jsonb_array_length(cg.moves) AS glen, cg.moves->>grr.deviated_at_ply AS played
    FROM game_repertoire_results grr
    JOIN chess_games cg ON cg.id = grr.chess_game_id
    JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = grr.player_id
    WHERE grr.player_id = %(pid)s
      AND cg.id IN (SELECT chess_game_id FROM window_games)
      AND cg.moves IS NOT NULL
      AND {analysable} AND {time_class}
),
n AS MATERIALIZED (
    -- Every effectively active line with its rank under the tie-break (book, chapter, line name,
    -- id): the one place text is compared, by code point, as Python compares it.
    SELECT rl.id AS line_id, rl.line_name, rl.moves, jsonb_array_length(rl.moves) AS len,
           ch.id AS chapter_id, ch.title AS chapter_title, bk.id AS book_id, bk.title AS book_title, bk.color,
           row_number() OVER (ORDER BY bk.title COLLATE "C", ch.title COLLATE "C", rl.line_name COLLATE "C", rl.id)
             AS rank
    FROM repertoire_lines rl
    JOIN chapters ch ON ch.id = rl.chapter_id AND ch.active
    JOIN books bk ON bk.id = ch.book_id AND bk.active AND bk.player_id = %(pid)s
    WHERE rl.active
),
r AS MATERIALIZED (
    -- One row per (game, attached line), with its precedence for the reduction and how many
    -- lines of the chapter the game is attached to — a window over the pairs, so no join
    -- multiplies rows. Held once for the two aggregates below.
    SELECT g.gid, g.result, g.played_at, g.m, n.line_id, n.chapter_id, n.book_id, n.rank,
           s.status, """
    + _PRECEDENCE_SQL
    + """ AS prec,"""
    + _DERIVED_SQL
    + """,
           count(*) OVER (PARTITION BY g.rid, n.chapter_id) AS siblings
    FROM g
    JOIN game_result_lines grl ON grl.game_repertoire_result_id = g.rid
    JOIN n ON n.line_id = grl.line_id"""
    + _STATUS_SQL
    + """
),
-- A game is one game per chapter and per book: its representative pair is the first by
-- precedence then rank — the least (precedence, rank) key per (chapter, game) — rebuilt from
-- the game and the line the key names rather than joined back to the pairs, since every attached
-- line shares the game's fields. The book's representative is the first of its chapter
-- representatives, and the game's over every attached line (the gap game, when it is
-- `opponent`) the first of those.
best AS MATERIALIZED (
    SELECT chapter_id, gid, min(prec * 1000000 + rank) AS key FROM r GROUP BY chapter_id, gid
),
rep_ch AS MATERIALIZED (
    SELECT g.gid, g.result, g.played_at, g.m, n.line_id, n.chapter_id, n.book_id, n.rank,
           n.line_name, n.chapter_title, n.book_title, n.color,
           s.status, best.key / 1000000 AS prec,"""
    + _DERIVED_SQL
    + """
    FROM best
    JOIN g ON g.gid = best.gid
    JOIN n ON n.rank = best.key %% 1000000"""
    + _STATUS_SQL
    + """
),
rep_bk AS (SELECT DISTINCT ON (book_id, gid) * FROM rep_ch ORDER BY book_id, gid, prec, rank),
gap AS (SELECT DISTINCT ON (gid) * FROM rep_ch ORDER BY gid, prec, rank)
"""
    + _COMPACT.format(scope="line", key="line_id", src="r", shared="bool_and(siblings > 1)")
    + " UNION ALL "
    + _COMPACT.format(scope="chapter", key="chapter_id", src="rep_ch", shared="NULL::boolean")
    + " UNION ALL "
    + _COMPACT.format(scope="book", key="book_id", src="rep_bk", shared="NULL::boolean")
    + """
UNION ALL
SELECT 'gap', gid, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       jsonb_build_object('m', m, 'expected', expected, 'played', played, 'color', color,
                          'book_id', book_id, 'book_title', book_title, 'chapter_id', chapter_id,
                          'chapter_title', chapter_title, 'line_id', line_id, 'line_name', line_name)
FROM gap WHERE status = 'opponent'
"""
)

# The gap board of each kind-A game and what the card needs from the game: the board after the
# opponent's move at `m` (the gap ply is m + 1, per game), its canonical key, the next
# REPORT_CONTINUATION_PLIES moves, the family, and whether the game is analysed. The stored
# engine entry itself — a large value per game — is read afterwards, for a board's candidate
# representatives only (`_ENGINE_SQL`).
_GAP_GAMES_SQL = """
SELECT cg.id AS gid, pg.result, cg.played_at, cg.canonical_family,
       cg.fen_sequence->>t.gap_ply AS gap_fen,
       bq_canonical_fen(cg.fen_sequence->>t.gap_ply) || ' 0 1' AS canon,
       cg.moves->>(t.gap_ply - 1) AS arrived_by,
       (SELECT array_agg(x ORDER BY ord) FROM jsonb_array_elements_text(cg.moves) WITH ORDINALITY AS e(x, ord)
         WHERE ord > t.gap_ply AND ord <= t.gap_ply + %(plies)s) AS continuation,
       (SELECT array_agg(x ORDER BY ord) FROM jsonb_array_elements_text(cg.moves) WITH ORDINALITY AS e(x, ord)
         WHERE ord <= t.gap_ply) AS stem,
       t.gap_ply, cg.ply_analysis IS NOT NULL AS analysed
FROM unnest(%(gids)s::int[], %(plies_at)s::int[]) AS t(gid, gap_ply)
JOIN chess_games cg ON cg.id = t.gid
JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
WHERE cg.fen_sequence IS NOT NULL AND cg.moves IS NOT NULL
"""

# The stored engine entry at a board, for the representative games only.
_ENGINE_SQL = """
SELECT t.gid, cg.ply_analysis -> t.gap_ply AS analysis
FROM unnest(%(gids)s::int[], %(plies_at)s::int[]) AS t(gid, gap_ply)
JOIN chess_games cg ON cg.id = t.gid
"""

# Unmatched games of one colour in the window: the first own-decision board and its context.
_UNMATCHED_SQL = """
WITH {window}
SELECT cg.id AS gid, pg.result, cg.played_at, cg.canonical_family, pg.player_color,
       cg.fen_sequence->>(%(gap_ply)s) AS gap_fen,
       bq_canonical_fen(cg.fen_sequence->>(%(gap_ply)s)) || ' 0 1' AS canon,
       cg.moves->>(%(gap_ply)s) AS played,
       CASE WHEN %(gap_ply)s > 0 THEN cg.moves->>(%(gap_ply)s - 1) END AS arrived_by,
       (SELECT array_agg(x ORDER BY ord) FROM jsonb_array_elements_text(cg.moves) WITH ORDINALITY AS t(x, ord)
         WHERE ord > %(gap_ply)s AND ord <= %(gap_ply)s + %(plies)s) AS continuation,
       (SELECT array_agg(x ORDER BY ord) FROM jsonb_array_elements_text(cg.moves) WITH ORDINALITY AS t(x, ord)
         WHERE ord <= %(gap_ply)s) AS stem,
       %(gap_ply)s AS gap_ply, cg.ply_analysis IS NOT NULL AS analysed
FROM player_games pg
JOIN chess_games cg ON cg.id = pg.chess_game_id
WHERE pg.player_id = %(pid)s AND pg.no_repertoire_match AND pg.player_color = %(colour)s
  AND cg.id IN (SELECT chess_game_id FROM window_games)
  AND cg.fen_sequence IS NOT NULL AND cg.moves IS NOT NULL
  AND jsonb_array_length(cg.moves) > %(gap_ply)s
  AND {analysable} AND {time_class}
"""


def own_moves(ply: int, colour: str) -> int:
    """How many of the player's moves fall in the first `ply` plies (`player_committed`)."""
    return (ply + 1) // 2 if colour == "white" else ply // 2


def status_of(m: int, line_len: int, game_len: int, colour: str) -> str:
    """One attached line's outcome for a game, in `who_deviated`'s order of stops."""
    if m >= line_len:
        return RAN_OUT
    if m >= game_len:
        return ENDED
    mover = "white" if m % 2 == 0 else "black"
    return ME if mover == colour else OPPONENT


def reduce_status(statuses: list[str]) -> str:
    """A game's status over the lines of one scope: me > opponent > ended > ran_out."""
    return min(statuses, key=lambda s: _PRECEDENCE[s])


SCOPE_DEFAULT = 500
SCOPE_MIN = 50
SCOPE_STEPS = (100, 250, 500)


def scope(config: Settings, games: int | None) -> Row:
    """`{"games": effective, "limit", "options"}`: the request's `games` clamped to the analysis
    window, SCOPE_DEFAULT when absent or below SCOPE_MIN; the options are the steps below the
    limit and the limit itself (the "All" of the page)."""
    limit = int(config.analysis_game_limit)
    wanted = SCOPE_DEFAULT if games is None or games < SCOPE_MIN else games
    options = [o for o in SCOPE_STEPS if o < limit] + [limit]
    return {"games": min(wanted, limit), "limit": limit, "options": options}


def _window_params(config: Settings, time_class: str, games: int) -> tuple[dict[str, Any], str]:
    return {"pid": PLAYER_ID, "window": games}, time_class_sql("cg", time_class, config.time_class_focus)


def _reduced(
    conn: Connection[Any], config: Settings, time_class: str, games: int
) -> tuple[dict[str, dict[int, Row]], list[Row]]:
    """One statement: `{"line" | "chapter" | "book": {id: summary}}` and the gap games (a row per
    game whose representative over every attached line is `opponent`, with that line as context)."""
    params, tc = _window_params(config, time_class, games)
    sql = _REPORT_SQL.format(window=window_cte(), analysable=analysable_sql("cg"), time_class=tc)
    for setting in READ_SETTINGS:
        conn.execute(setting)
    compact: dict[tuple[str, int], list[Row]] = defaultdict(list)
    gap_games: list[Row] = []
    for r in conn.execute(cast(LiteralString, sql), params).fetchall():
        if r["scope"] == "gap":
            gap_games.append({"id": int(r["id"]), **cast(Row, r["ctx"])})
            continue
        compact[(str(r["scope"]), int(r["id"]))].append(r)
    for setting in READ_SETTINGS_AFTER:
        conn.execute(setting)
    scopes: dict[str, dict[int, Row]] = {"line": {}, "chapter": {}, "book": {}}
    for (scope, sid), rows in compact.items():
        scopes[scope][sid] = _summary(rows)
    return scopes, gap_games


def _decisions(conn: Connection[Any], config: Settings, time_class: str, games: int) -> list[decisions_mod.Decision]:
    """The decisions statement under the reduction's session settings (two large CTE scans joined,
    the same planner), nested loops back on afterwards for the gap lookups."""
    for setting in READ_SETTINGS:
        conn.execute(setting)
    try:
        return decisions_mod.read(conn, config, time_class=time_class, games=games)
    finally:
        for setting in READ_SETTINGS_AFTER:
            conn.execute(setting)


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else None


def _summary(rows: list[Row]) -> Row:
    """A scope's numbers from its compact rows (one game per row counted `n` times): the slip is
    the most common whole (own move, expected, played) among the games the player left, ties by
    the tuple — Python's code-point order, which the SQL's rank shares."""
    games = sum(int(r["n"]) for r in rows)
    by_status: Counter[str] = Counter()
    depth: Counter[int] = Counter()
    slips: Counter[tuple[int, str, str]] = Counter()
    wins = draws = losses = 0
    for r in rows:
        k = int(r["n"])
        wins, draws, losses = wins + int(r["wins"]), draws + int(r["draws"]), losses + int(r["losses"])
        by_status[str(r["status"])] += k
        depth[int(r["own"])] += k
        if r["status"] == ME:
            slips[(int(r["own"]) + 1, str(r["expected"]), str(r["played"]))] += k
    decided = wins + draws + losses  # a game without a result counts, but scores nothing
    slip: Row | None = None
    if slips:
        (own_move, expected, played), count = min(slips.items(), key=lambda kv: (-kv[1], kv[0]))
        slip = {"own_move": own_move, "expected": expected, "played": played, "count": count}
    me = by_status.get(ME, 0)
    played_at = max((r["last_played"] for r in rows if r["last_played"] is not None), default=None)
    last_slip = max(
        (r["last_played"] for r in rows if r["status"] == ME and r["last_played"] is not None), default=None
    )
    return {
        "games": games,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "score": round((wins + draws / 2) / decided, 3) if decided else None,
        "left_by_me": me,
        "left_by_opponent": by_status.get(OPPONENT, 0),
        "ended": by_status.get(ENDED, 0),
        "ran_out": by_status.get(RAN_OUT, 0),
        "faithful": games - me,
        "depth_reached": {str(k): v for k, v in sorted(depth.items())},
        "slip": slip,
        "last_played": _iso(played_at),
        "last_slip": _iso(last_slip),
        "shared": all(bool(r["shared"]) for r in rows),  # NULL on a chapter or book row, dropped by `_lines`
    }


_EMPTY: Row = {
    "games": 0,
    "wins": 0,
    "draws": 0,
    "losses": 0,
    "score": None,
    "left_by_me": 0,
    "left_by_opponent": 0,
    "ended": 0,
    "ran_out": 0,
    "faithful": 0,
    "depth_reached": {},
    "slip": None,
    "last_played": None,
    "last_slip": None,
    "shared": False,
}


def _line_counts(conn: Connection[Any], config: Settings) -> dict[int, int]:
    """{line_id: n}: the positions Practice serves for the line (the prepared rows' `puzzles`)."""
    lines: dict[int, int] = {}
    for b in visibility.repertoire_scopes(conn, lookahead_plies=serve.lookahead_plies(config)):
        for ch in b["chapters"]:
            for ln in ch["lines"]:
                lines[int(ln["id"])] = int(ln["count"])
    return lines


def _ordered(rows: list[Row], name: str) -> list[Row]:
    """Most games first, then most recently played, then by name."""

    def key(r: Row) -> tuple[int, float, str]:
        lp = r["last_played"]
        return (-int(r["games"]), -datetime.fromisoformat(lp).timestamp() if lp else float("inf"), str(r[name]))

    return sorted(rows, key=key)


_ACTIVE_LINES_SQL = """
SELECT rl.id AS line_id, rl.line_name, rl.is_alternative, rl.origin, jsonb_array_length(rl.moves) AS len,
       ch.id AS chapter_id, ch.title AS chapter_title,
       bk.id AS book_id, bk.title AS book_title, bk.color
FROM repertoire_lines rl
JOIN chapters ch ON ch.id = rl.chapter_id AND ch.active
JOIN books bk ON bk.id = ch.book_id AND bk.active AND bk.player_id = %(pid)s
WHERE rl.active
"""


def _rank(lines_: dict[int, Row], chapters: dict[int, Row], books: dict[int, Row]) -> dict[int, int]:
    """The report's tie-break order over lines — book › chapter › line title by code point, then
    id — as a rank per line id (the decisions' pick and the ordering share it)."""
    order = sorted(
        lines_,
        key=lambda lid: (
            str(books[int(lines_[lid]["book_id"])]["title"]),
            str(chapters[int(lines_[lid]["chapter_id"])]["title"]),
            str(lines_[lid]["title"]),
            lid,
        ),
    )
    return {lid: i for i, lid in enumerate(order)}


def _lines(
    conn: Connection[Any], scopes: dict[str, dict[int, Row]], decisions: list[decisions_mod.Decision] | None = None
) -> tuple[Row, Row]:
    """`({"books": [...], "drill_max_lines"}, decisions view)`: book › chapter › line rows with
    games, results, depth, faithfulness, the slip, the practice numbers and, from the decisions,
    what each line's boards say (`tested`, `deepest_tested`, `correct_through`, `trouble_spot`,
    `decisions`) and the recent first tries of its puzzle (`recent`, `unknown_loads`); every level
    ordered most games first (then most recent, then name). Every effectively-active line is a
    row, with zero games when no game in the window reached it, so a line just added can be found
    and drilled. A line and a chapter carry the ids of what holds them; the titles are on the
    holder, once. The decisions view is `_decision_rows`."""
    books: dict[int, Row] = {}
    chapters: dict[int, Row] = {}
    lines_: dict[int, Row] = {}
    for r in conn.execute(_ACTIVE_LINES_SQL, {"pid": PLAYER_ID}).fetchall():
        bid, cid, lid, colour = int(r["book_id"]), int(r["chapter_id"]), int(r["line_id"]), str(r["color"])
        if bid not in books:
            books[bid] = {"id": bid, "title": r["book_title"], "color": colour}
        if cid not in chapters:
            chapters[cid] = {"id": cid, "title": r["chapter_title"], "book_id": bid}
        lines_[lid] = {
            "id": lid,
            "title": r["line_name"],
            "is_alternative": bool(r["is_alternative"]),
            "origin": r["origin"],
            "chapter_id": cid,
            "book_id": bid,
            "depth_total": own_moves(int(r["len"]), colour),
            "len": int(r["len"]),
        }
    practice_totals = stats.by_line(conn, list(lines_))
    loads = practice.read(conn, list(lines_))
    recent = practice.recent_by_line(loads)
    decisions = decisions or []
    per_line = decisions_mod.by_line(decisions)
    colour_of_line = {lid: str(books[int(ln["book_id"])]["color"]) for lid, ln in lines_.items()}
    rank = _rank(lines_, chapters, books)
    chapters_of: dict[int, list[Row]] = defaultdict(list)
    for ch in chapters.values():
        chapters_of[int(ch["book_id"])].append(ch)
    lines_of: dict[int, list[Row]] = defaultdict(list)
    for ln in lines_.values():
        lines_of[int(ln["chapter_id"])].append(ln)

    def finish(scope_: str, row: Row) -> Row:
        return {**row, **scopes[scope_].get(int(row["id"]), _EMPTY)}

    def summed(rows_: list[Row]) -> dict[str, int]:
        out = stats.empty()
        for r in rows_:
            for k in out:
                out[k] += int(r["practice"][k])
        return out

    out_books: list[Row] = []
    for bid, b in books.items():
        colour = str(b["color"])
        out_chapters: list[Row] = []
        for ch in chapters_of[bid]:
            cid = int(ch["id"])
            out_lines: list[Row] = []
            for ln in lines_of[cid]:
                lid = int(ln["id"])
                row = finish("line", {k: v for k, v in ln.items() if k != "len"})
                row["practice"] = practice_totals.get(lid, stats.empty())
                row.update(decisions_mod.line_evidence(lid, per_line.get(lid, [])))
                rec = recent.get(lid, {"recent": [], "unknown": 0})
                row["recent"] = rec["recent"]
                row["unknown_loads"] = rec["unknown"]
                out_lines.append(row)
            out_lines = _ordered(out_lines, "title")
            crow = finish("chapter", dict(ch))
            del crow["shared"]
            crow.update(lines=out_lines, practice=summed(out_lines))
            out_chapters.append(crow)
        out_chapters = _ordered(out_chapters, "title")
        brow = finish("book", dict(b))
        del brow["shared"]
        brow.update(colour=colour, chapters=out_chapters, practice=summed(out_chapters))
        out_books.append(brow)
    out_books = _ordered(out_books, "title")
    line_games = {lid: int(scopes["line"].get(lid, _EMPTY)["games"]) for lid in lines_}
    line_len = {lid: int(ln["len"]) for lid, ln in lines_.items()}
    view = _decision_rows(decisions, loads, colour_of_line, line_games, line_len, rank)
    return {"books": out_books, "drill_max_lines": DRILL_MAX_LINES}, view


def _decision_rows(
    decisions: list[decisions_mod.Decision],
    loads: list[practice.Load],
    colour_of_line: dict[int, str],
    line_games: dict[int, int],
    line_len: dict[int, int],
    rank: dict[int, int],
) -> Row:
    """`{"rows": [...], "contested": [...], "unknown_loads": n}`: one row per decision with an
    opportunity in scope — the move, missed k of n, the most played move, the last slip, the lines
    through it grouped by the ply they reach it at (`[[ply, [line ids]], ...]`, rank order within),
    the line a Drill queues, and the recent first tries that reached it — in Study order (most
    slips, latest slip, lowest move, colour); a row's identity for the page is the board, which its
    first line and that line's ply name without a FEN or a hash per row; the contested boards for
    the notice; and how many loads' traces did not replay."""
    by_board = practice.by_decision(loads, colour_of_line)
    rows: list[Row] = []
    contested: list[Row] = []
    for d in decisions:
        if d.contested:
            contested.append(
                {
                    "colour": d.colour,
                    "fen": d.fen,
                    "own_move": d.own_move,
                    "alternatives": d.alternatives,
                    "line_ids": d.line_ids,
                }
            )
            continue
        if d.opportunities == 0:
            continue
        by_ply: dict[int, list[int]] = {}
        for lid, ply, _ in sorted(d.occurrences, key=lambda t: (rank.get(t[0], 1 << 30), t[0], t[1])):
            if lid not in by_ply.setdefault(ply, []):
                by_ply[ply].append(lid)
        rows.append(
            {
                "colour": d.colour,
                "own_move": d.own_move,
                "move": d.move,
                "unreadable": d.unreadable,
                "opportunities": d.opportunities,
                "slips": d.slips,
                "most_played": d.most_played,
                "last_slip": _iso(d.last_slip),
                "last_played": _iso(d.last_played),
                "lines": [[ply, ids] for ply, ids in sorted(by_ply.items())],
                "pick": decisions_mod.pick(d, line_games, line_len, rank),
                "practice": by_board.get((d.colour, d.board), {"failed": 0, "of": 0}),
            }
        )
    rows.sort(
        key=lambda r: (
            -int(r["slips"]),
            -(datetime.fromisoformat(r["last_slip"]).timestamp() if r["last_slip"] else float("-inf")),
            int(r["own_move"]),
            str(r["colour"]),
        )
    )
    return {
        "rows": rows,
        "contested": contested,
        "unknown_loads": sum(1 for ld in loads if ld.verdict == practice.UNKNOWN),
    }


def full(conn: Connection[Any], config: Settings, *, time_class: str, games: int | None = None) -> Row:
    """The whole report for one time class (`eligibility.TIME_CLASSES`) and scope: `{"lines":
    _lines(), "gaps": {"white": _gaps(), "black": _gaps()}, "decisions": _decision_rows(), "scope":
    scope()}`, from one reduction of the per-line rows, one decisions read and one scope count."""
    sc = scope(config, games)
    n = int(sc["games"])
    scopes, gap_games = _reduced(conn, config, time_class, n)
    decisions = _decisions(conn, config, time_class, n)
    line_counts = _line_counts(conn, config)
    lines_, view = _lines(conn, scopes, decisions)
    return {
        "lines": lines_,
        "gaps": {c: _gaps(conn, config, time_class, c, gap_games, line_counts, n) for c in ("white", "black")},
        "decisions": view,
        "scope": sc,
    }


def lines(conn: Connection[Any], config: Settings, *, time_class: str, games: int | None = None) -> Row:
    """The lines half of `full`, computed alone (the tests' convenience; the API reads `full`)."""
    n = int(scope(config, games)["games"])
    return _lines(conn, _reduced(conn, config, time_class, n)[0], _decisions(conn, config, time_class, n))[0]


def gaps(conn: Connection[Any], config: Settings, *, time_class: str, colour: str, games: int | None = None) -> Row:
    """One colour's half of `full`'s gaps, computed alone (the tests' convenience)."""
    n = int(scope(config, games)["games"])
    _, gap_games = _reduced(conn, config, time_class, n)
    return _gaps(conn, config, time_class, colour, gap_games, _line_counts(conn, config), n)


# --- gaps -------------------------------------------------------------------------


def _recency(r: Row) -> tuple[bool, Any, int]:
    return (r["played_at"] is not None, r["played_at"], int(r["gid"]))


def _engine_entries(conn: Connection[Any], games: list[Row]) -> dict[int, Row | None]:
    """{gid: the stored engine entry at the game's gap board, or None when the analysis has none}."""
    if not games:
        return {}
    rows = conn.execute(
        _ENGINE_SQL, {"gids": [int(r["gid"]) for r in games], "plies_at": [int(r["gap_ply"]) for r in games]}
    ).fetchall()
    return {int(r["gid"]): cast(Row, r["analysis"]) if isinstance(r["analysis"], dict) else None for r in rows}


def _cards(conn: Connection[Any], groups: list[list[Row]], *, colour: str, context: dict[int, Row] | None) -> list[Row]:
    """One card per group of games reaching one board. A board's representative is the most
    recent game whose stored analysis has an entry at the board, else its most recent game; the
    entries are read for the candidates in that order, a batch per round, until each board has
    its answer — an analysed game may stop short of the board, and the next candidate then
    stands — so the card's board, stem and engine line come from one game."""
    candidates = [sorted((r for r in g if r["analysed"]), key=_recency, reverse=True) for g in groups]
    chosen: list[tuple[Row, Row | None] | None] = [None] * len(groups)
    while True:
        probe = [(i, c[0]) for i, c in enumerate(candidates) if chosen[i] is None and c]
        if not probe:
            break
        entries = _engine_entries(conn, [r for _, r in probe])
        for i, r in probe:
            entry = entries.get(int(r["gid"]))
            if entry is not None:
                chosen[i] = (r, entry)
            else:
                candidates[i] = candidates[i][1:]
    return [
        _group(g, *(chosen[i] or (max(g, key=_recency), None)), colour=colour, context=context)
        for i, g in enumerate(groups)
    ]


def _group(members: list[Row], rep: Row, analysis: Row | None, *, colour: str, context: dict[int, Row] | None) -> Row:
    """One gap card from the games reaching one canonical board, `rep` their representative and
    `analysis` its stored engine entry. `context`, when given, maps a game to its continuing
    line's (book, chapter, line, expected, played) — kind A only."""
    results = Counter(str(r["result"]) for r in members if r["result"])
    games = len(members)
    wins, draws, losses = results.get("win", 0), results.get("draw", 0), results.get("loss", 0)
    families = Counter(str(r["canonical_family"]) for r in members if r["canonical_family"])
    family = min(families.items(), key=lambda kv: (-kv[1], kv[0]))[0] if families else None
    conts: Counter[tuple[str, ...]] = Counter(tuple(str(x) for x in (r["continuation"] or ())) for r in members)
    continuations = [
        {"moves": list(moves), "games": n, "score": _score(members, moves)}
        for moves, n in sorted(((k, v) for k, v in conts.items() if k), key=lambda kv: (-kv[1], kv[0]))[:5]
    ]
    engine: Row | None = None
    if analysis is not None:
        line = analysis.get("best_line")  # the PV as one SAN string (core/analysis/game.py pv_san)
        engine = {
            "best_move": analysis.get("best_move"),
            "line": str(line).split() if isinstance(line, str) and line else [],
            "eval": analysis.get("eval"),
            "mate_in": analysis.get("mate_in_moves"),
        }
    ctx: Row | None = None
    if context is not None:
        tuples = Counter(
            (
                c["book_id"],
                c["book_title"],
                c["chapter_id"],
                c["chapter_title"],
                c["line_id"],
                c["line_name"],
                c["expected"],
                c["played"],
            )
            for r in members
            if (c := context.get(int(r["gid"]))) is not None
        )
        if tuples:
            (bid, bt, cid, ct, lid, ln, exp, pl), n = min(
                tuples.items(), key=lambda kv: (-kv[1], str(kv[0][1]), str(kv[0][3]), str(kv[0][5]), kv[0][4])
            )
            ctx = {
                "book_id": bid,
                "book": bt,
                "chapter_id": cid,
                "chapter": ct,
                "line_id": lid,
                "line_name": ln,
                "expected": exp,
                "played": pl,
                "count": n,
            }
    played_at = max((r["played_at"] for r in members if r["played_at"] is not None), default=None)
    return {
        "fen": rep["gap_fen"],
        "canon": rep["canon"],
        "colour": colour,
        "arrived_by": rep["arrived_by"],
        "games": games,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "score": round((wins + draws / 2) / (wins + draws + losses), 3) if wins + draws + losses else None,
        "family": family,
        "context": ctx,
        "continuations": continuations,
        "engine": engine,
        "stem": _stem(rep),
        "last_played": _iso(played_at),
    }


def _stem(rep: Row) -> list[str] | None:
    """The representative game's moves from the start to the gap board, re-rendered as the
    server's SAN and checked to reach the card's board; None when they do not replay there."""
    board = chess.Board()
    out: list[str] = []
    for token in rep["stem"] or ():
        move = parse_san(board, normalize_san(str(token)))
        if not move:
            return None
        # Chess960 games never reach here (the window is analysable games), so the start is the start.
        out.append(board.san(move))
        board.push(move)
    canon = str(rep["canon"]).split(" ")[:4]
    return out if board.fen().split(" ")[:4] == canon else None


def _score(members: list[Row], moves: tuple[str, ...]) -> float | None:
    """The score over the decided games with this continuation; None when none is decided."""
    sub = [r for r in members if tuple(r["continuation"] or ()) == moves and r["result"]]
    pts = sum(1.0 if r["result"] == "win" else 0.5 if r["result"] == "draw" else 0.0 for r in sub)
    return round(pts / len(sub), 3) if sub else None


def _kind_a(conn: Connection[Any], colour: str, gap_games: list[Row], params: dict[str, Any]) -> list[Row]:
    """The gap cards of one colour from the reduced gap games: the board after the opponent's
    move at `m`, grouped by canonical board, each game's context the representative line."""
    mine = [r for r in gap_games if r["color"] == colour]
    if not mine:
        return []
    context = {int(r["id"]): r for r in mine}
    rows = conn.execute(
        _GAP_GAMES_SQL,
        {
            **params,
            "gids": [int(r["id"]) for r in mine],
            "plies_at": [int(r["m"]) + 1 for r in mine],
            "plies": REPORT_CONTINUATION_PLIES,
        },
    ).fetchall()
    groups: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r["gap_fen"]:
            groups[str(r["canon"])].append(dict(r))
    return _cards(conn, list(groups.values()), colour=colour, context=context)


def _kind_b(
    conn: Connection[Any],
    config: Settings,
    time_class: str,
    colour: str,
    params: dict[str, Any],
    line_counts: dict[int, int],
) -> tuple[list[Row], list[Row]]:
    """(prepared-but-not-played rows, uncovered gap cards) from the unmatched games."""
    _, tc = _window_params(config, time_class, int(params["window"]))
    gap_ply = 0 if colour == "white" else 1
    sql = _UNMATCHED_SQL.format(window=window_cte(), analysable=analysable_sql("cg"), time_class=tc)
    rows = [
        dict(r)
        for r in conn.execute(
            cast(LiteralString, sql),
            {**params, "colour": colour, "gap_ply": gap_ply, "plies": REPORT_CONTINUATION_PLIES},
        ).fetchall()
    ]
    rows = [r for r in rows if r["gap_fen"]]
    if not rows:
        return [], []
    fens = sorted({str(r["gap_fen"]) for r in rows})
    occurrences = read.rep_lines(conn, fens, book_color=cast(read.BookColor, colour), with_stats=False)
    prepared: dict[tuple[str, str, str], list[Row]] = defaultdict(list)
    prepared_line: dict[tuple[str, str, str], Row] = {}
    uncovered: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        fen = str(r["gap_fen"])
        entry = read.project_ply(fen, occurrences.get(fen) or [])
        status = entry["status"]
        if status in (read.STATUS_MATCH, read.STATUS_AGREE):
            book_move, line_id, line_name, book, chapter = (
                entry["book_move"],
                entry["line_id"],
                entry["line_name"],
                entry["book"],
                entry["chapter"],
            )
        elif status == read.STATUS_CONFLICT and entry["conflict"]:
            g = entry["conflict"][0]
            book_move, line_id, line_name, book, chapter = (
                g["move"],
                g["line_id"],
                g["line_name"],
                g["book"],
                g["chapter"],
            )
        else:
            uncovered[str(r["canon"])].append(r)
            continue
        key = (fen, str(book_move), str(r["played"]))
        prepared[key].append(r)
        prepared_line.setdefault(key, {"line_id": line_id, "line_name": line_name, "book": book, "chapter": chapter})
    out_prepared: list[Row] = []
    for (fen, book_move, played), members in prepared.items():
        results = Counter(str(r["result"]) for r in members if r["result"])
        info = prepared_line[(fen, book_move, played)]
        lid = info["line_id"]
        out_prepared.append(
            {
                "fen": fen,
                "book_move": book_move,
                "played": played,
                "games": len(members),
                "wins": results.get("win", 0),
                "draws": results.get("draw", 0),
                "losses": results.get("loss", 0),
                "line_id": lid,
                "line_name": info["line_name"],
                "book": info["book"],
                "chapter": info["chapter"],
                "puzzles": line_counts.get(int(lid), 0) if lid is not None else 0,
                "last_played": _iso(max((r["played_at"] for r in members if r["played_at"] is not None), default=None)),
            }
        )
    out_prepared.sort(key=lambda r: (-int(r["games"]), str(r["fen"]), str(r["book_move"]), str(r["played"])))
    cards = _cards(conn, list(uncovered.values()), colour=colour, context=None)
    return out_prepared, cards


def _gaps(
    conn: Connection[Any],
    config: Settings,
    time_class: str,
    colour: str,
    gap_games: list[Row],
    line_counts: dict[int, int],
    games: int,
) -> Row:
    """`{"colour", "no_book", "prepared": [...], "gaps": [...], "destinations": [...]}` for one
    colour: the boards after the opponent left a line (kind A) and the uncovered first boards of
    unmatched games (kind B), most games first; the prepared-but-not-played rows of unmatched
    games whose board the repertoire covers; `no_book` (White only) when no White line is
    effectively active; the chapters a line added from a card may go into, once; and
    `prefill_plies`, how much of a card's engine line a new line opens with."""
    params, _ = _window_params(config, time_class, games)
    # As White every line starts at the start position, so with no effectively active White line
    # every White game is unmatched and "uncovered" at the start: that is one fact, not a card per
    # first move. As Black an uncovered first move is a gap whether or not a Black line is active.
    if colour == "white" and not read.has_active_repertoire(conn, colour):
        return {
            "colour": colour,
            "no_book": True,
            "prepared": [],
            "gaps": [],
            "destinations": [],
            "prefill_plies": REPORT_PREFILL_PLIES,
            "max_plies": REPERTOIRE_LINE_MAX_PLIES,
        }
    cards = _kind_a(conn, colour, gap_games, params)
    prepared, cards_b = _kind_b(conn, config, time_class, colour, params, line_counts)
    cards += cards_b
    cards = _ordered(cards, "fen")
    for c in cards:
        del c["canon"]
        c["catch_all"] = {
            "book": f"{CATCH_ALL_TITLE} ({colour.capitalize()})",
            "chapter": c["family"] or lines_by_hand.CATCH_ALL_FAMILY_FALLBACK,
        }
    return {
        "colour": colour,
        "no_book": False,
        "prepared": prepared,
        "gaps": cards,
        "destinations": _destinations(conn, colour),
        "prefill_plies": REPORT_PREFILL_PLIES,
        "max_plies": REPERTOIRE_LINE_MAX_PLIES,
    }


def _destinations(conn: Connection[Any], colour: str) -> list[Row]:
    """The chapters a manual line of this colour may go into: start-rooted, in an active book
    of the colour, the chapter itself active; the Repertoire page's order."""
    rows = conn.execute(
        "SELECT ch.id, ch.title, bk.id AS book_id, bk.title AS book FROM chapters ch"
        " JOIN books bk ON bk.id = ch.book_id"
        " WHERE bk.player_id = %s AND bk.color = %s AND bk.active AND ch.active AND ch.root_fen IS NULL"
        " ORDER BY bk.title, bk.id, ch.source_chapter_id NULLS LAST, ch.title, ch.id",
        (PLAYER_ID, colour),
    ).fetchall()
    return [{"id": int(r["id"]), "title": r["title"], "book_id": int(r["book_id"]), "book": r["book"]} for r in rows]
