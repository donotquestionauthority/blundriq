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
from core.repertoire import lines as lines_by_hand
from core.repertoire import read
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
_PRECEDENCE_SQL = "CASE status WHEN 'me' THEN 0 WHEN 'opponent' THEN 1 WHEN 'ended' THEN 2 ELSE 3 END"

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
c AS (
    -- How many lines of each chapter a game is attached to: a line is shared when no game is alone.
    SELECT grl.game_repertoire_result_id AS rid, n.chapter_id, count(*) AS siblings
    FROM game_result_lines grl JOIN n ON n.line_id = grl.line_id
    WHERE grl.game_repertoire_result_id IN (SELECT rid FROM g)
    GROUP BY 1, 2
),
r AS MATERIALIZED (
    -- One row per (game, attached line): its status in `who_deviated`'s order of stops, its
    -- precedence for the reduction, the player's moves up to the boundary, the two moves at it
    -- when someone left (the slip's, or the gap's), and how many lines of the chapter the game
    -- is attached to.
    SELECT g.gid, g.result, g.played_at, g.m, n.line_id, n.chapter_id, n.book_id, n.rank,
           s.status, """
    + _PRECEDENCE_SQL
    + """ AS prec,
           CASE WHEN n.color = 'white' THEN (g.m + 1) / 2 ELSE g.m / 2 END AS own,
           CASE WHEN s.status IN ('me', 'opponent') THEN n.moves->>g.m END AS expected,
           CASE WHEN s.status IN ('me', 'opponent') THEN g.played END AS played,
           c.siblings
    FROM g
    JOIN game_result_lines grl ON grl.game_repertoire_result_id = g.rid
    JOIN n ON n.line_id = grl.line_id
    JOIN c ON c.rid = g.rid AND c.chapter_id = n.chapter_id
    CROSS JOIN LATERAL (
        SELECT CASE WHEN g.m >= n.len THEN 'ran_out'
                    WHEN g.m >= g.glen THEN 'ended'
                    WHEN (g.m %% 2 = 0) = (n.color = 'white') THEN 'me'
                    ELSE 'opponent' END AS status
    ) s
),
-- A game is one game per chapter and per book: its representative row by precedence then rank.
-- The book's representative is the first of its chapter representatives, and the game's over
-- every attached line (the gap game, when it is `opponent`) the first of those.
rep_ch AS MATERIALIZED (
    SELECT DISTINCT ON (chapter_id, gid) * FROM r ORDER BY chapter_id, gid, prec, rank
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
SELECT 'gap', gap.gid, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       jsonb_build_object('m', gap.m, 'expected', gap.expected, 'played', gap.played, 'color', n.color,
                          'book_id', n.book_id, 'book_title', n.book_title, 'chapter_id', n.chapter_id,
                          'chapter_title', n.chapter_title, 'line_id', n.line_id, 'line_name', n.line_name)
FROM gap JOIN n USING (line_id) WHERE gap.status = 'opponent'
"""
)

# The gap board of each kind-A game and what the card needs from the game: the board after the
# opponent's move at `m` (the gap ply is m + 1, per game), its canonical key, the next
# REPORT_CONTINUATION_PLIES moves, the family, and the stored engine line from that board (NULL
# when the game is not analysed).
_GAP_GAMES_SQL = """
SELECT cg.id AS gid, pg.result, cg.played_at, cg.canonical_family,
       cg.fen_sequence->>t.gap_ply AS gap_fen,
       bq_canonical_fen(cg.fen_sequence->>t.gap_ply) || ' 0 1' AS canon,
       cg.moves->>(t.gap_ply - 1) AS arrived_by,
       (SELECT array_agg(x ORDER BY ord) FROM jsonb_array_elements_text(cg.moves) WITH ORDINALITY AS e(x, ord)
         WHERE ord > t.gap_ply AND ord <= t.gap_ply + %(plies)s) AS continuation,
       (SELECT array_agg(x ORDER BY ord) FROM jsonb_array_elements_text(cg.moves) WITH ORDINALITY AS e(x, ord)
         WHERE ord <= t.gap_ply) AS stem,
       cg.ply_analysis -> t.gap_ply AS analysis
FROM unnest(%(gids)s::int[], %(plies_at)s::int[]) AS t(gid, gap_ply)
JOIN chess_games cg ON cg.id = t.gid
JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
WHERE cg.fen_sequence IS NOT NULL AND cg.moves IS NOT NULL
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
       cg.ply_analysis -> (%(gap_ply)s) AS analysis
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


def _window_params(config: Settings, time_class: str) -> tuple[dict[str, Any], str]:
    return {"pid": PLAYER_ID, "window": config.analysis_game_limit}, time_class_sql(
        "cg", time_class, config.time_class_focus
    )


def _reduced(conn: Connection[Any], config: Settings, time_class: str) -> tuple[dict[str, dict[int, Row]], list[Row]]:
    """One statement: `{"line" | "chapter" | "book": {id: summary}}` and the gap games (a row per
    game whose representative over every attached line is `opponent`, with that line as context)."""
    params, tc = _window_params(config, time_class)
    sql = _REPORT_SQL.format(window=window_cte(), analysable=analysable_sql("cg"), time_class=tc)
    compact: dict[tuple[str, int], list[Row]] = defaultdict(list)
    gap_games: list[Row] = []
    for r in conn.execute(cast(LiteralString, sql), params).fetchall():
        if r["scope"] == "gap":
            gap_games.append({"id": int(r["id"]), **cast(Row, r["ctx"])})
            continue
        compact[(str(r["scope"]), int(r["id"]))].append(r)
    scopes: dict[str, dict[int, Row]] = {"line": {}, "chapter": {}, "book": {}}
    for (scope, sid), rows in compact.items():
        scopes[scope][sid] = _summary(rows)
    return scopes, gap_games


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


def _lines(conn: Connection[Any], scopes: dict[str, dict[int, Row]]) -> Row:
    """`{"books": [...], "drill_max_lines"}`: book › chapter › line rows with games, results, depth,
    faithfulness, the slip and the practice numbers, every level ordered most games first (then
    most recent, then name). Every effectively-active line is a row, with zero games when no game
    in the window reached it, so a line just added can be found and drilled. A line and a chapter
    carry the ids of what holds them; the titles are on the holder, once."""
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
        }
    practice = stats.by_line(conn, list(lines_))
    chapters_of: dict[int, list[Row]] = defaultdict(list)
    for ch in chapters.values():
        chapters_of[int(ch["book_id"])].append(ch)
    lines_of: dict[int, list[Row]] = defaultdict(list)
    for ln in lines_.values():
        lines_of[int(ln["chapter_id"])].append(ln)

    def finish(scope: str, row: Row) -> Row:
        return {**row, **scopes[scope].get(int(row["id"]), _EMPTY)}

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
                row = finish("line", ln)
                row["practice"] = practice.get(int(ln["id"]), stats.empty())
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
    return {"books": out_books, "drill_max_lines": DRILL_MAX_LINES}


def full(conn: Connection[Any], config: Settings, *, time_class: str) -> Row:
    """The whole report for one time class (`eligibility.TIME_CLASSES`): `{"lines": _lines(),
    "gaps": {"white": _gaps(), "black": _gaps()}}`, from one reduction of the per-line rows and
    one scope count."""
    scopes, gap_games = _reduced(conn, config, time_class)
    line_counts = _line_counts(conn, config)
    return {
        "lines": _lines(conn, scopes),
        "gaps": {c: _gaps(conn, config, time_class, c, gap_games, line_counts) for c in ("white", "black")},
    }


def lines(conn: Connection[Any], config: Settings, *, time_class: str) -> Row:
    """The lines half of `full`, computed alone (the tests' convenience; the API reads `full`)."""
    return _lines(conn, _reduced(conn, config, time_class)[0])


def gaps(conn: Connection[Any], config: Settings, *, time_class: str, colour: str) -> Row:
    """One colour's half of `full`'s gaps, computed alone (the tests' convenience)."""
    _, gap_games = _reduced(conn, config, time_class)
    return _gaps(conn, config, time_class, colour, gap_games, _line_counts(conn, config))


# --- gaps -------------------------------------------------------------------------


def _group(members: list[Row], *, colour: str, context: dict[int, Row] | None) -> Row:
    """One gap card from the games reaching one canonical board. `context`, when given, maps a
    game to its continuing line's (book, chapter, line, expected, played) — kind A only."""
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
    # The representative: the most recent analysed game, else the most recent game. Its board is
    # the card's FEN and its stored line the prefill; both from one row.
    rep = max(
        members, key=lambda r: (r["analysis"] is not None, r["played_at"] is not None, r["played_at"], int(r["gid"]))
    )
    analysis = cast(Row, rep["analysis"]) if isinstance(rep["analysis"], dict) else None
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
    return [_group(g, colour=colour, context=context) for g in groups.values()]


def _kind_b(
    conn: Connection[Any],
    config: Settings,
    time_class: str,
    colour: str,
    params: dict[str, Any],
    line_counts: dict[int, int],
) -> tuple[list[Row], list[Row]]:
    """(prepared-but-not-played rows, uncovered gap cards) from the unmatched games."""
    _, tc = _window_params(config, time_class)
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
    cards = [_group(g, colour=colour, context=None) for g in uncovered.values()]
    return out_prepared, cards


def _gaps(
    conn: Connection[Any],
    config: Settings,
    time_class: str,
    colour: str,
    gap_games: list[Row],
    line_counts: dict[int, int],
) -> Row:
    """`{"colour", "no_book", "prepared": [...], "gaps": [...], "destinations": [...]}` for one
    colour: the boards after the opponent left a line (kind A) and the uncovered first boards of
    unmatched games (kind B), most games first; the prepared-but-not-played rows of unmatched
    games whose board the repertoire covers; `no_book` (White only) when no White line is
    effectively active; the chapters a line added from a card may go into, once; and
    `prefill_plies`, how much of a card's engine line a new line opens with."""
    params, _ = _window_params(config, time_class)
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
