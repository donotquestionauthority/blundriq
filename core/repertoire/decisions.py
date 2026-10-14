"""Decisions: what the player did at every board where the repertoire prescribes a move.

A **decision** is a board (its canonical FEN — pieces, side to move, castling, en passant; no
counters, the identity `position_keys` hashes) with the player to move at which the effective
repertoire prescribes one continuation, identified by the board that continuation reaches:
`(colour, board, next board)`. Two lines whose tokens differ (`Nf3`, `Ngf3`, `Nf3+`) but reach one
board are one decision; SAN is never compared. An **opportunity** is a game of the scope that
reached the board with a move still to come; it is a **success** when the game's next board is
the decision's next board, otherwise a **slip at that decision**. A board reached twice in one
game is one visit (the first). A decision with no opportunity in scope is **untested**.

Every game of the scope counts, attached by the matcher to that line, to another, or to none: a
game that transposes into a repertoire board reached the same decision. So a decision's
opportunities are not the line's games (the matcher's attachment, the report's Games column),
and a line's slips at the matcher's boundary and its slips per decision can disagree. Nothing
reconciles them.

**Contested boards.** The import gate keys the full FEN, so two effective lines can prescribe
different continuations at one canonical board reached with different counters. The reader
(`read.project_ply`) fails closed on such a board unless the game's FEN matches an occurrence
exactly; this module has no exact-occurrence step and fails closed either way: the board is
*contested*, attributed no opportunity, success or slip, and reported for the page's notice.
The gate, the matcher and the Conflicts page keep their full-FEN identity.

**Per line**, board-level evidence has holes (a transposition can reach move 4's board having
skipped move 2's), so a line carries three separate numbers: decisions *tested* (any
opportunity), the *deepest tested* decision, and *correct through* — the longest prefix of its
decisions each with an opportunity and no slip — plus its *trouble spot*, the decision with the
most slips. The word "through" belongs to the prefix only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, LiteralString, cast

import chess
from psycopg import Connection

from core.chess.eligibility import analysable_sql, time_class_sql, window_cte
from core.chess.san import normalize_san, parse
from core.constants import PLAYER_ID
from core.settings import Settings

Row = dict[str, Any]

# Every player-turn occurrence of every effective line (`occ`), grouped to one row per board
# (`dec`), joined to every in-scope game's boards by key and verified by canonical text (`opp`):
# two unnested sequences joined on their elements, so both sides are MATERIALIZED. The side to
# move is read from the FEN, not from the ply's parity, because a chapter may be rooted at a
# tabiya. `position_keys` is 1-based: the board after `ord - 1` plies is keys[ord], the next
# keys[ord + 1].
_DECISIONS_SQL = """
WITH {window},
eff AS MATERIALIZED (
    -- Every effective line with its arrays as text[] (one conversion per line), so the zip below
    -- is unnest over slices: the board, its key, the next board, its key and the move, by ply.
    SELECT rl.id AS line_id, bk.color, rl.position_keys AS keys,
           ARRAY(SELECT x FROM jsonb_array_elements_text(rl.fen_sequence) AS x) AS fens,
           ARRAY(SELECT x FROM jsonb_array_elements_text(rl.moves) AS x) AS moves,
           jsonb_array_length(rl.moves) AS len,
           CASE bk.color WHEN 'white' THEN 'w' ELSE 'b' END AS side
    FROM repertoire_lines rl
    JOIN chapters ch ON ch.id = rl.chapter_id AND ch.active
    JOIN books bk ON bk.id = ch.book_id AND bk.active AND bk.player_id = %(pid)s
    WHERE rl.active AND rl.fen_sequence IS NOT NULL
      AND jsonb_array_length(rl.fen_sequence) = jsonb_array_length(rl.moves) + 1
),
occ AS MATERIALIZED (
    -- The player's turns only. The board is the FEN's first four fields — what bq_canonical_fen
    -- keeps and position_keys hash — taken by splitting rather than by the regex function, which
    -- costs several times more per call; on the stored single-spaced FENs the two are equal, and
    -- a test holds them equal over every FEN the fixtures store.
    SELECT color, line_id, ply, key, fen, array_to_string((string_to_array(fen, ' '))[1:4], ' ') AS board,
           next_key, array_to_string((string_to_array(next_fen, ' '))[1:4], ' ') AS next_board, raw_move
    FROM (
        SELECT n.color, n.line_id, (z.ord - 1)::int AS ply, z.key, z.fen, z.next_key, z.next_fen, z.raw_move
        FROM eff n
        CROSS JOIN LATERAL unnest(n.keys[1:n.len], n.fens[1:n.len], n.keys[2:n.len + 1], n.fens[2:n.len + 1],
                                  n.moves[1:n.len]) WITH ORDINALITY AS z(key, fen, next_key, next_fen, raw_move, ord)
        WHERE split_part(z.fen, ' ', 2) = n.side
    ) x
),
dec AS MATERIALIZED (
    SELECT color, board, min(key) AS key, count(DISTINCT next_board) AS continuations,
           min(next_board) AS next_board, min(next_key) AS next_key,
           min(fen) AS fen, min(raw_move) AS raw_move,
           jsonb_agg(jsonb_build_array(line_id, ply, split_part(fen, ' ', 6)::int) ORDER BY line_id, ply) AS occurrences
    FROM occ GROUP BY color, board
),
reach AS MATERIALIZED (
    -- No board past the longest line's can be a decision: a game's plies beyond it are never read.
    SELECT COALESCE(max(len), 0) AS plies FROM eff
),
scope AS MATERIALIZED (
    SELECT cg.id AS gid, pg.player_color AS color, cg.played_at, cg.position_keys[1:reach.plies + 1] AS keys,
           ARRAY(SELECT x FROM jsonb_array_elements_text(cg.fen_sequence) WITH ORDINALITY AS e(x, ord)
                 WHERE ord <= reach.plies + 1) AS fens,
           ARRAY(SELECT x FROM jsonb_array_elements_text(cg.moves) WITH ORDINALITY AS e(x, ord)
                 WHERE ord <= reach.plies) AS moves,
           least(jsonb_array_length(cg.moves), reach.plies) AS glen
    FROM player_games pg
    JOIN chess_games cg ON cg.id = pg.chess_game_id
    CROSS JOIN reach
    WHERE pg.player_id = %(pid)s
      AND cg.id IN (SELECT chess_game_id FROM window_games)
      AND cg.fen_sequence IS NOT NULL AND cg.moves IS NOT NULL
      AND {analysable} AND {time_class}
),
gk AS MATERIALIZED (
    -- Every board of every in-scope game with a move still to come, flat (the same zip), so the
    -- join below is one hash on the key.
    SELECT s.gid, s.color, s.played_at, (z.ord - 1)::int AS ply, z.key, z.fen, z.next_key, z.next_fen, z.played
    FROM scope s
    CROSS JOIN LATERAL unnest(s.keys[1:s.glen], s.fens[1:s.glen], s.keys[2:s.glen + 1], s.fens[2:s.glen + 1],
                              s.moves[1:s.glen]) WITH ORDINALITY AS z(key, fen, next_key, next_fen, played, ord)
),
opp AS MATERIALIZED (
    -- The hash is on the key and colour alone; the board text is checked on the matched rows as a
    -- prefix (the canonical board is the FEN up to its fourth field), no function on the game side.
    SELECT DISTINCT ON (g.gid, d.color, d.board)
           g.gid, g.played_at, d.color, d.board,
           (g.next_key = d.next_key AND left(g.next_fen, length(d.next_board) + 1) = d.next_board || ' ') AS success,
           g.played
    FROM gk g
    JOIN dec d ON d.key = g.key AND d.color = g.color AND d.continuations = 1
    WHERE left(g.fen, length(d.board) + 1) = d.board || ' '
    ORDER BY g.gid, d.color, d.board, g.ply
)
-- Two kinds of row, merged in Python by (colour, board): the decisions and their evidence — no
-- join the planner could get wrong. A contested board's continuations are read afterwards from
-- its few lines (`_alternatives`).
SELECT 'dec' AS kind, color, board, fen, raw_move, continuations::int AS n, occurrences AS j,
       NULL::timestamptz AS t1, NULL::timestamptz AS t2, NULL::text[] AS a
FROM dec
UNION ALL
SELECT 'agg', color, board, NULL, NULL, count(*)::int,
       to_jsonb(count(*) FILTER (WHERE success)::int),
       max(played_at), max(played_at) FILTER (WHERE NOT success),
       array_agg(played) FILTER (WHERE NOT success)
FROM opp GROUP BY color, board
"""


@dataclass
class Decision:
    colour: str
    board: str  # canonical FEN, the identity
    fen: str  # one occurrence's full FEN, to render moves on
    own_move: int  # the FEN's fullmove number
    move: str  # the prescribed move as canonical SAN (the raw token if it does not parse)
    unreadable: bool
    contested: bool
    opportunities: int
    successes: int
    last_played: datetime | None
    last_slip: datetime | None
    most_played: str | None  # the mode of the slips' moves, canonical SAN when it parses
    occurrences: list[tuple[int, int, int]]  # (line_id, ply, own move number there), by line then ply
    alternatives: list[dict[str, Any]]  # contested only: {move, line_ids}; empty otherwise

    @property
    def slips(self) -> int:
        return self.opportunities - self.successes

    @property
    def line_ids(self) -> list[int]:
        return sorted({lid for lid, _, _ in self.occurrences})


def _moves(conn: Connection[Any], line_ids: list[int]) -> dict[int, list[str]]:
    """{line_id: moves} for the few lines through contested boards."""
    if not line_ids:
        return {}
    rows = conn.execute(
        "SELECT id, moves FROM repertoire_lines WHERE id = ANY(%s)", (sorted(set(line_ids)),)
    ).fetchall()
    return {int(r["id"]): [str(m) for m in cast(list[Any], r["moves"])] for r in rows}


@lru_cache(maxsize=32768)
def _render(fen: str, token: str) -> tuple[str, bool]:
    """The canonical SAN of `token` on the board `fen`, or the token itself and False when it is
    not a move there. A pure function of its two strings, memoised: the repertoire's boards and
    tokens are the same from one read to the next, and a parse costs more than the statement."""
    try:
        board = chess.Board(fen)
    except ValueError:
        return token, False
    move = parse(board, token)
    if move is None or not move:
        return token, False
    return board.san(move), True


def read(conn: Connection[Any], config: Settings, *, time_class: str, games: int) -> list[Decision]:
    """Every decision of the effective repertoire with its evidence over the scope's games, the
    contested boards included (with no evidence, by construction)."""
    sql = _DECISIONS_SQL.format(
        window=window_cte(),
        analysable=analysable_sql("cg"),
        time_class=time_class_sql("cg", time_class, config.time_class_focus),
    )
    rows = conn.execute(cast(LiteralString, sql), {"pid": PLAYER_ID, "window": games}).fetchall()
    decs: dict[tuple[str, str], Row] = {}
    aggs: dict[tuple[str, str], Row] = {}
    for r in rows:
        key = (str(r["color"]), str(r["board"]))
        if r["kind"] == "dec":
            decs[key] = r
        else:
            aggs[key] = r
    moves = _moves(conn, [int(o[0]) for r in decs.values() if int(r["n"]) > 1 for o in cast(list[Any], r["j"])])
    out: list[Decision] = []
    for key, r in decs.items():
        fen = str(r["fen"])
        contested = int(r["n"]) > 1
        a = aggs.get(key)
        opportunities = int(a["n"]) if a else 0
        occurrences = [(int(o[0]), int(o[1]), int(o[2])) for o in cast(list[Any], r["j"])]
        # A board's move number for its own row: the lowest among its occurrences (a repeated or
        # transposed board is reached at several); a line's row uses each occurrence's own.
        own_move = min((m for _, _, m in occurrences), default=0)
        # The move is rendered canonically (parsed on its board) for every decision the page shows
        # on its own — one with evidence, or contested; an untested decision shows its token with
        # the decorations stripped, which is the course's SAN already (a parse per board costs more
        # than the whole statement over thousands of untested boards).
        shown = opportunities > 0 or contested
        move, readable = _render(fen, str(r["raw_move"])) if shown else (normalize_san(str(r["raw_move"])), True)
        successes = int(a["j"]) if a else 0
        most_played: str | None = None
        slipped_raw = cast(list[Any] | None, a["a"]) if a else None
        slipped = [str(x) for x in (slipped_raw or []) if x is not None]
        if slipped:
            # The mode by count, ties by the raw text under code-point order — the SQL's rank.
            token, _ = min(Counter(slipped).items(), key=lambda kv: (-kv[1], kv[0]))
            most_played = _render(fen, token)[0]
        alternatives: list[dict[str, Any]] = []
        if contested:
            by_move: dict[str, list[int]] = {}
            for lid, ply, _ in occurrences:
                line_moves = moves.get(lid)
                if line_moves is None or ply >= len(line_moves):
                    continue
                by_move.setdefault(_render(fen, line_moves[ply])[0], []).append(lid)
            alternatives = [{"move": m, "line_ids": sorted(ids)} for m, ids in sorted(by_move.items())]
        out.append(
            Decision(
                colour=key[0],
                board=key[1],
                fen=fen,
                own_move=own_move,
                move=move,
                unreadable=not readable,
                contested=contested,
                opportunities=opportunities,
                successes=successes,
                last_played=a["t1"] if a else None,
                last_slip=a["t2"] if a else None,
                most_played=most_played,
                occurrences=occurrences,
                alternatives=alternatives,
            )
        )
    return out


# --- per line -------------------------------------------------------------------------

UNTESTED = "untested"
CONTESTED = "contested"
CLEAN = "clean"  # tested, never slipped
SLIPPED = "slipped"


def line_evidence(line_id: int, decisions: list[Decision]) -> Row:
    """What a line's decisions say, in ply order: each decision as `[ply, own move, move, state,
    opportunities, slips]` (compact: a line has many and the page has thousands of lines; the own
    move is the occurrence's own — a board the line reaches twice, or a shared board reached at
    another move count, keeps its number in this line), the
    tested count, the deepest tested own move, the continuously tested successful prefix
    (`correct_through`, an own move or None) and the trouble spot (the decision with the most
    slips, ties the earliest; None when nothing slipped). `decisions` is the line's own, any order."""
    owned: list[tuple[int, int, Decision]] = []  # (ply, the line's own move number there, decision)
    for d in decisions:
        for lid, ply, own in d.occurrences:
            if lid == line_id:
                owned.append((ply, own, d))
    owned.sort(key=lambda t: t[0])
    rows: list[list[Any]] = []
    tested = 0
    deepest: int | None = None
    through: int | None = None
    prefix_intact = True
    trouble: tuple[int, int, int, Decision] | None = None  # (-slips, ply, own move, decision)
    for ply, own, d in owned:
        if d.contested:
            state = CONTESTED
        elif d.opportunities == 0:
            state = UNTESTED
        elif d.slips == 0:
            state = CLEAN
        else:
            state = SLIPPED
        if state in (CLEAN, SLIPPED):
            tested += 1
            deepest = own
            if prefix_intact and state == CLEAN:
                through = own
            else:
                prefix_intact = False
        else:
            prefix_intact = False
        if state == SLIPPED and (trouble is None or (-d.slips, ply) < (trouble[0], trouble[1])):
            trouble = (-d.slips, ply, own, d)
        rows.append([ply, own, d.move, state, d.opportunities, d.slips])
    return {
        "decisions": rows,
        "decisions_total": len(rows),
        "tested": tested,
        "deepest_tested": deepest,
        "correct_through": through,
        "trouble_spot": None
        if trouble is None
        else {
            "own_move": trouble[2],
            "move": trouble[3].move,
            "slips": trouble[3].slips,
            "opportunities": trouble[3].opportunities,
        },
    }


def by_line(decisions: list[Decision]) -> dict[int, list[Decision]]:
    """{line_id: its decisions} over every occurrence."""
    out: dict[int, list[Decision]] = {}
    for d in decisions:
        for lid in d.line_ids:
            out.setdefault(lid, []).append(d)
    return out


def pick(d: Decision, line_games: dict[int, int], line_len: dict[int, int], rank: dict[int, int]) -> int | None:
    """The one line a decision's Drill queues: most games attached by the matcher in scope, ties
    by fewest plies after the decision in that line, then the report's tie-break rank."""
    best: tuple[int, int, int, int] | None = None
    for lid, ply, _ in d.occurrences:
        key = (-line_games.get(lid, 0), line_len.get(lid, 0) - ply, rank.get(lid, 1 << 30), lid)
        if best is None or key < best:
            best = key
    return best[3] if best else None
