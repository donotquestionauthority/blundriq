"""The line report: the repertoire lines the player's games go through, and the boards where
the games are on their own.

Two reads over the matcher's results (`game_repertoire_results`, `game_result_lines`) and the
analyser's stored lines (`chess_games.ply_analysis`), both scoped to the analysis window
(`core.chess.eligibility.window_cte`: Chess960 never takes a slot) and the Deviations page's
time-class filter. Neither read has state of its own; a rematch rewrites what they read.

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

**Puzzles and the stem.** Line, chapter and book puzzle counts are the positions Practice serves
for that scope (`visibility.repertoire_scopes`); the practice numbers per line are
`core.puzzles.stats`. A gap card carries `stem`, the representative game's own moves from the
start to the gap board, re-rendered and checked to reach it, which is what a line added from the
card opens with; and the chapters such a line may go into (`core.repertoire.lines`).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, LiteralString, cast

import chess
from psycopg import Connection

from core.chess.eligibility import analysable_sql, time_class_sql, window_cte
from core.chess.san import normalize_san
from core.chess.san import parse as parse_san
from core.constants import (
    CATCH_ALL_TITLE,
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

# One row per (game, attached line) in the window: the line, its chapter and book, the matched
# boundary and the two moves at it. The status itself is derived in Python (`status_of`), where
# it is tested against the matcher; SQL only fetches what the arithmetic needs.
_PER_LINE_SQL = """
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
)
SELECT g.gid, g.result, g.played_at, g.m, g.glen, g.played,
       rl.id AS line_id, rl.line_name, rl.is_alternative, jsonb_array_length(rl.moves) AS len,
       rl.moves->>g.m AS expected,
       ch.id AS chapter_id, ch.title AS chapter_title,
       bk.id AS book_id, bk.title AS book_title, bk.color
FROM g
JOIN game_result_lines grl ON grl.game_repertoire_result_id = g.rid
JOIN repertoire_lines rl ON rl.id = grl.line_id AND rl.active
JOIN chapters ch ON ch.id = rl.chapter_id AND ch.active
JOIN books bk ON bk.id = ch.book_id AND bk.active AND bk.player_id = %(pid)s
{colour}
"""

# The gap board of a kind-A game and what the card needs from the game: the board after the
# opponent's move at `m`, its canonical key, the next REPORT_CONTINUATION_PLIES moves, the family,
# and the stored engine line from that board (NULL when the game is not analysed).
_GAP_GAMES_SQL = """
WITH {window}
SELECT cg.id AS gid, pg.result, cg.played_at, cg.canonical_family,
       cg.fen_sequence->>(%(gap_ply)s) AS gap_fen,
       bq_canonical_fen(cg.fen_sequence->>(%(gap_ply)s)) || ' 0 1' AS canon,
       cg.moves->>(%(gap_ply)s - 1) AS arrived_by,
       (SELECT array_agg(x ORDER BY ord) FROM jsonb_array_elements_text(cg.moves) WITH ORDINALITY AS t(x, ord)
         WHERE ord > %(gap_ply)s AND ord <= %(gap_ply)s + %(plies)s) AS continuation,
       (SELECT array_agg(x ORDER BY ord) FROM jsonb_array_elements_text(cg.moves) WITH ORDINALITY AS t(x, ord)
         WHERE ord <= %(gap_ply)s) AS stem,
       cg.ply_analysis -> (%(gap_ply)s) AS analysis
FROM chess_games cg
JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
WHERE cg.id = ANY(%(ids)s)
  AND cg.fen_sequence IS NOT NULL AND cg.moves IS NOT NULL
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


def _per_line_rows(conn: Connection[Any], config: Settings, time_class: str, colour: str | None = None) -> list[Row]:
    """The per-(game, line) rows, in the read module's line order within a game (book, chapter,
    line name, id) so that whatever a scope reduces first is the same under any ids."""
    params, tc = _window_params(config, time_class)
    if colour is not None:
        params["colour"] = colour
    sql = _PER_LINE_SQL.format(
        window=window_cte(),
        analysable=analysable_sql("cg"),
        time_class=tc,
        colour="WHERE bk.color = %(colour)s" if colour is not None else "",
    )
    rows: list[Row] = conn.execute(cast(LiteralString, sql), params).fetchall()
    for r in rows:
        r["status"] = status_of(int(r["m"]), int(r["len"]), int(r["glen"]), str(r["color"]))
        if r["status"] not in (ME, OPPONENT):
            r["expected"] = None
            r["played"] = None
    rows.sort(
        key=lambda r: (
            int(r["gid"]),
            str(r["book_title"]),
            str(r["chapter_title"]),
            str(r["line_name"]),
            int(r["line_id"]),
        )
    )
    return rows


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else None


@dataclass
class _Tally:
    """One scope's games (a line, a chapter or a book), one entry per game."""

    games: dict[int, Row] = field(default_factory=lambda: {})  # gid -> {status, result, played_at, m, slip}

    def add(
        self, gid: int, status: str, result: str | None, played_at: Any, m: int, slip: tuple[Any, ...] | None
    ) -> None:
        cur = self.games.get(gid)
        if cur is None or _PRECEDENCE[status] < _PRECEDENCE[cur["status"]]:
            self.games[gid] = {"status": status, "result": result, "played_at": played_at, "m": m, "slip": slip}

    def summary(self, colour: str) -> Row:
        rows = list(self.games.values())
        results = Counter(r["result"] for r in rows)
        statuses = Counter(r["status"] for r in rows)
        games = len(rows)
        wins, draws, losses = results.get("win", 0), results.get("draw", 0), results.get("loss", 0)
        decided = wins + draws + losses  # a game without a result counts, but scores nothing
        slips = Counter(r["slip"] for r in rows if r["status"] == ME and r["slip"] is not None)
        slip: Row | None = None
        if slips:
            (own_move, expected, played), count = min(slips.items(), key=lambda kv: (-kv[1], kv[0]))
            slip = {"own_move": own_move, "expected": expected, "played": played, "count": count}
        depth = Counter(own_moves(int(r["m"]), colour) for r in rows)
        played_at = max((r["played_at"] for r in rows if r["played_at"] is not None), default=None)
        return {
            "games": games,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "score": round((wins + draws / 2) / decided, 3) if decided else None,
            "left_by_me": statuses.get(ME, 0),
            "left_by_opponent": statuses.get(OPPONENT, 0),
            "ended": statuses.get(ENDED, 0),
            "ran_out": statuses.get(RAN_OUT, 0),
            "faithful": games - statuses.get(ME, 0),
            "depth_reached": {str(k): v for k, v in sorted(depth.items())},
            "slip": slip,
            "last_played": _iso(played_at),
        }


def _scope_counts(conn: Connection[Any], config: Settings) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    """{book_id: n}, {chapter_id: n}, {line_id: n}: the positions Practice serves per scope."""
    books: dict[int, int] = {}
    chapters: dict[int, int] = {}
    lines: dict[int, int] = {}
    for b in visibility.repertoire_scopes(conn, lookahead_plies=serve.lookahead_plies(config)):
        books[int(b["id"])] = int(b["count"])
        for ch in b["chapters"]:
            chapters[int(ch["id"])] = int(ch["count"])
            for ln in ch["lines"]:
                lines[int(ln["id"])] = int(ln["count"])
    return books, chapters, lines


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


def lines(conn: Connection[Any], config: Settings, *, time_class: str) -> Row:
    """`{"books": [...]}`: book › chapter › line rows with games, results, depth, faithfulness,
    the slip and the practice numbers, every level ordered most games first (then most recent,
    then name). Every effectively-active line is a row, with zero games when no game in the
    window reached it, so a line just added can be found and drilled. `time_class` is one of
    `eligibility.TIME_CLASSES`."""
    rows = _per_line_rows(conn, config, time_class)
    book_counts, chapter_counts, line_counts = _scope_counts(conn, config)
    books: dict[int, Row] = {}
    chapters: dict[int, Row] = {}
    lines_: dict[int, Row] = {}

    def see(r: Row) -> None:
        bid, cid, lid, colour = int(r["book_id"]), int(r["chapter_id"]), int(r["line_id"]), str(r["color"])
        if bid not in books:
            books[bid] = {"id": bid, "title": r["book_title"], "color": colour}
        if cid not in chapters:
            chapters[cid] = {"id": cid, "title": r["chapter_title"], "book_id": bid}
        if lid not in lines_:
            lines_[lid] = {
                "id": lid,
                "title": r["line_name"],
                "is_alternative": bool(r["is_alternative"]),
                "origin": r.get("origin", "course"),
                "chapter_id": cid,
                "depth_total": own_moves(int(r["len"]), colour),
            }

    for r in conn.execute(_ACTIVE_LINES_SQL, {"pid": PLAYER_ID}).fetchall():
        see(r)
    tallies: dict[tuple[str, int], _Tally] = defaultdict(_Tally)
    line_games: dict[int, set[int]] = defaultdict(set)
    chapter_game_lines: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for r in rows:
        see(r)
        gid, m, status, colour = int(r["gid"]), int(r["m"]), str(r["status"]), str(r["color"])
        slip = (own_moves(m, colour) + 1, r["expected"], r["played"]) if status == ME else None
        bid, cid, lid = int(r["book_id"]), int(r["chapter_id"]), int(r["line_id"])
        result, played_at = r["result"], r["played_at"]
        tallies[("line", lid)].add(gid, status, result, played_at, m, slip)
        tallies[("chapter", cid)].add(gid, status, result, played_at, m, slip)
        tallies[("book", bid)].add(gid, status, result, played_at, m, slip)
        line_games[lid].add(gid)
        chapter_game_lines[cid][gid].add(lid)
    practice = stats.by_line(conn, list(lines_))
    chapters_of: dict[int, list[Row]] = defaultdict(list)
    for ch in chapters.values():
        chapters_of[int(ch["book_id"])].append(ch)
    lines_of: dict[int, list[Row]] = defaultdict(list)
    for ln in lines_.values():
        lines_of[int(ln["chapter_id"])].append(ln)

    def finish(scope: str, row: Row, counts: dict[int, int], colour: str) -> Row:
        return {**row, **tallies[(scope, int(row["id"]))].summary(colour), "puzzles": counts.get(int(row["id"]), 0)}

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
                row = finish("line", ln, line_counts, colour)
                # Shared: every game under this line also sits under another line of the chapter.
                games_here = line_games[lid]
                row["shared"] = bool(games_here) and all(len(chapter_game_lines[cid][g]) > 1 for g in games_here)
                row["practice"] = practice.get(lid, stats.empty())
                row.update(book_id=bid, book=b["title"], chapter=ch["title"], colour=colour)
                out_lines.append(row)
            out_lines = _ordered(out_lines, "title")
            crow = finish("chapter", dict(ch), chapter_counts, colour)
            crow.update(book=b["title"], colour=colour, lines=out_lines, practice=summed(out_lines))
            out_chapters.append(crow)
        out_chapters = _ordered(out_chapters, "title")
        brow = finish("book", dict(b), book_counts, colour)
        brow.update(colour=colour, chapters=out_chapters, practice=summed(out_chapters))
        out_books.append(brow)
    out_books = _ordered(out_books, "title")
    return {"books": out_books}


# --- gaps -------------------------------------------------------------------------


def _context_key(r: Row) -> tuple[str, str, str, int]:
    return (str(r["book_title"] or ""), str(r["chapter_title"] or ""), str(r["line_name"] or ""), int(r["line_id"]))


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


def _kind_a(conn: Connection[Any], config: Settings, time_class: str, colour: str, params: dict[str, Any]) -> list[Row]:
    rows = _per_line_rows(conn, config, time_class, colour)
    by_game: dict[int, list[Row]] = defaultdict(list)
    for r in rows:
        by_game[int(r["gid"])].append(r)
    context: dict[int, Row] = {}
    by_m: dict[int, list[int]] = defaultdict(list)
    for gid, attached in by_game.items():
        if reduce_status([str(r["status"]) for r in attached]) != OPPONENT:
            continue
        continuing = [r for r in attached if r["status"] == OPPONENT]
        context[gid] = min(continuing, key=_context_key)
        by_m[int(attached[0]["m"])].append(gid)
    members: list[Row] = []
    for m, ids in by_m.items():
        sql = _GAP_GAMES_SQL.format(window=window_cte())
        members += [
            dict(r)
            for r in conn.execute(
                cast(LiteralString, sql), {**params, "ids": ids, "gap_ply": m + 1, "plies": REPORT_CONTINUATION_PLIES}
            ).fetchall()
        ]
    groups: dict[str, list[Row]] = defaultdict(list)
    for r in members:
        if r["gap_fen"]:
            groups[str(r["canon"])].append(r)
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


def gaps(conn: Connection[Any], config: Settings, *, time_class: str, colour: str) -> Row:
    """`{"colour", "no_book", "prepared": [...], "gaps": [...]}` for one colour: the boards after
    the opponent left a line (kind A) and the uncovered first boards of unmatched games (kind B),
    most games first; the prepared-but-not-played rows of unmatched games whose board the
    repertoire covers; `no_book` (White only) when no White line is effectively active; and
    `prefill_plies`, how much of a card's engine line a new puzzle opens with."""
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
            "prefill_plies": REPORT_PREFILL_PLIES,
            "max_plies": REPERTOIRE_LINE_MAX_PLIES,
        }
    _, _, line_counts = _scope_counts(conn, config)
    cards = _kind_a(conn, config, time_class, colour, params)
    prepared, cards_b = _kind_b(conn, config, time_class, colour, params, line_counts)
    cards += cards_b
    cards = _ordered(cards, "fen")
    chapters = _destinations(conn, colour)
    for c in cards:
        del c["canon"]
        ctx = c.get("context")
        first = [ch for ch in chapters if ctx is not None and ch["id"] == ctx["chapter_id"]]
        c["chapters"] = first + [ch for ch in chapters if not first or ch["id"] != first[0]["id"]]
        c["catch_all"] = {
            "book": f"{CATCH_ALL_TITLE} ({colour})",
            "chapter": c["family"] or lines_by_hand.CATCH_ALL_FAMILY_FALLBACK,
        }
    return {
        "colour": colour,
        "no_book": False,
        "prepared": prepared,
        "gaps": cards,
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
