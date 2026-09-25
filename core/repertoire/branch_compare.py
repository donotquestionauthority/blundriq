"""Branch compare: what the opponent could have played one half-move before a puzzle position.

Given the puzzle position `fen` (the player to move) and its parent `pre_fen` (the opponent to
move; the route has verified that exactly one legal move joins them), every opponent option at
the parent from two sources, merged into one board per branch:

* R — the effectively-active repertoire of `book_color` (the colour to move in `fen`, derived
  by the route, never defaulted): lines through the parent, the move they give the opponent
  there, the child position and the reply the line prescribes. A line ending at the parent has
  nothing to branch. The reply is the only producer of the orange arrow on this surface.
* B — the player's own blunders in reply to an opponent move from the parent. The blunder row
  pins the ordinal (`blunders.fen` is the position before the blundered move, so the parent is
  `fen_sequence[ply - 1]`), and the double exact verify (parent element equals the parent,
  the blunder's own element equals its stored canonical FEN) makes the arithmetic
  self-checking: an off-by-one yields zero rows and the positive-control test goes red. Games
  whose bulk JSON housekeeping has nulled drop out — the recency window is a feature.
* S — scouted opponents' games: phase 6. The payload keeps `sources.scout` (null) and the
  ordering keeps its third tier so the UI ships whole.

Repertoire wins on a board: a branch with a repertoire source carries `blunders: None` by
construction. The branch whose child board is `fen` is `current` — always present (the
validated arriving move defines it, even when no source covers it), never in `branches`,
never capped, never counted as omitted. Alternatives are ordered by tier (repertoire, blunder,
scout), then most lines / worst loss / most games, then board text; the cap counts them.

Grouping of the repertoire replies is `neighbourhood.prep_groups`, so this surface cannot
disagree with Similar positions about what a group is. Counts are distinct games.
"""

from __future__ import annotations

from typing import Any, LiteralString, cast

import chess
from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import PLAYER_ID
from core.repertoire.annotations import normalize_fen
from core.repertoire.neighbourhood import parse_arriving, prep_groups
from core.repertoire.read import tie_break

Row = dict[str, Any]

# `bq_position_key(%(pre_fen)s)` and `bq_canonical_fen(%(pre_fen)s)` are IMMUTABLE over a bind
# literal: the planner folds each once per statement. Index arithmetic on the ordinality: element
# (ord - 1) of fen_sequence is the parent at 0-based ply i; moves->>i leaves it; fen_sequence->>(i+1)
# is the child and moves->>(i+1) the reply from it.
_REPERTOIRE_BRANCHES_SQL = """
SELECT rl.id AS line_id, rl.line_name, rl.is_alternative, ch.title AS chapter_title, bk.title AS book_title,
       (k.ord - 1)::int                     AS parent_ply,
       rl.fen_sequence->>((k.ord - 1)::int) AS parent_fen,
       rl.moves->>((k.ord - 1)::int)        AS branch_move,
       rl.fen_sequence->>(k.ord::int)       AS child_fen,
       rl.moves->>(k.ord::int)              AS reply_move
FROM repertoire_lines rl
JOIN chapters ch ON ch.id = rl.chapter_id
JOIN books bk ON bk.id = ch.book_id,
LATERAL unnest(rl.position_keys) WITH ORDINALITY AS k(key, ord)
WHERE bk.player_id = %(pid)s AND rl.active AND ch.active AND bk.active
  AND bk.color = %(book_color)s
  AND rl.position_keys @> ARRAY[bq_position_key(%(pre_fen)s)]
  AND k.key = bq_position_key(%(pre_fen)s)
  AND bq_canonical_fen(rl.fen_sequence->>((k.ord - 1)::int)) = bq_canonical_fen(%(pre_fen)s)
  AND rl.moves->>((k.ord - 1)::int) IS NOT NULL
ORDER BY bk.title, ch.title, rl.line_name, rl.id, k.ord
"""

_BLUNDER_BRANCHES_SQL = """
SELECT b.id AS blunder_id, b.chess_game_id, b.fen AS child_fen, b.move_played, b.best_move, b.centipawn_loss,
       cg.fen_sequence->>((b.ply - 1)::int) AS parent_fen,
       cg.moves->>((b.ply - 1)::int)        AS branch_move
FROM blunders b
JOIN chess_games cg ON cg.id = b.chess_game_id
WHERE b.player_id = %(pid)s AND {analysable}
  AND b.ply >= 1 AND cg.fen_sequence IS NOT NULL AND cg.moves IS NOT NULL
  AND cg.position_keys @> ARRAY[bq_position_key(%(pre_fen)s)]
  AND bq_canonical_fen(cg.fen_sequence->>((b.ply - 1)::int)) = bq_canonical_fen(%(pre_fen)s)
  AND b.canonical_fen = bq_canonical_fen(cg.fen_sequence->>(b.ply::int)) || ' 0 1'
ORDER BY b.centipawn_loss DESC NULLS LAST, b.id DESC
"""


def _reply_squares(child_fen: str | None, san: str | None) -> tuple[str | None, Row | None]:
    """(canonical SAN, {from, to}) of a move at the child, or (None, None): an arrow is never
    invented from a token that does not parse."""
    if not child_fen or not san:
        return None, None
    try:
        board = chess.Board(child_fen)
        move = board.parse_san(san)
    except (ValueError, IndexError):
        return None, None
    return board.san(move), {"from": chess.square_name(move.from_square), "to": chess.square_name(move.to_square)}


def repertoire_by_board(rows: list[Row]) -> dict[str, Row]:
    """Leg R rows to {child board: branch with a repertoire source}. `line_ply` is the child's.
    `reply_san` is set only when the move groups agree across lines."""
    by_board: dict[str, list[Row]] = {}
    for r in rows:
        arriving = parse_arriving(r["parent_fen"], r["branch_move"])
        child_fen = r["child_fen"]
        if arriving is None or not child_fen:
            continue
        try:
            board_key = normalize_fen(str(child_fen))
        except ValueError:
            continue
        by_board.setdefault(board_key, []).append(
            {
                "book": r["book_title"],
                "chapter": r["chapter_title"],
                "line_name": r["line_name"],
                "line_id": r["line_id"],
                "line_ply": int(r["parent_ply"]) + 1,
                "is_alternative": bool(r["is_alternative"]),
                "expected_move": r["reply_move"],
                "fen": child_fen,
                "arriving": arriving,
            }
        )
    out: dict[str, Row] = {}
    for board_key, carriers in by_board.items():
        rep = min(carriers, key=tie_break)
        groups, divergent = prep_groups(str(rep["fen"]), carriers)
        move_groups = [g for g in groups if g["prep_status"] == "move"]
        terminal = any(c["expected_move"] is None for c in carriers)
        reply_san, reply_squares = (None, None)
        if move_groups and not divergent:
            reply_san, reply_squares = _reply_squares(str(rep["fen"]), move_groups[0]["prep_move"])
        out[board_key] = {
            "child_fen": rep["fen"],
            "arriving": rep["arriving"],
            "source": {
                "reply_san": reply_san,
                "reply_squares": reply_squares,
                "end_of_line": terminal and not move_groups,
                "line_count": len({c["line_id"] for c in carriers}),
                "board_prep_divergent": divergent,
                "groups": groups,
            },
        }
    return out


def blunders_by_board(rows: list[Row]) -> dict[str, Row]:
    """Leg B rows (worst first) to {child board: branch with a blunder source}. The
    representative is the first row whose arriving and played moves parse; `games` counts
    distinct games over every row, so a parse failure loses an arrow, never a count. A board
    with no parseable representative has no blunder source."""
    games_by_board: dict[str, set[int]] = {}
    rep_by_board: dict[str, Row] = {}
    for r in rows:
        try:
            board_key = normalize_fen(str(r["child_fen"]))
        except ValueError:
            continue
        games_by_board.setdefault(board_key, set()).add(int(r["chess_game_id"]))
        if board_key in rep_by_board:
            continue
        arriving = parse_arriving(r["parent_fen"], r["branch_move"])
        played_san, played_squares = _reply_squares(r["child_fen"], r["move_played"])
        if arriving is None or played_san is None:
            continue
        best_san, best_squares = _reply_squares(r["child_fen"], r["best_move"])
        rep_by_board[board_key] = {
            "child_fen": r["child_fen"],
            "arriving": arriving,
            "worst": {
                "move_played_san": played_san,
                "move_played_squares": played_squares,
                "best_move_san": best_san,
                "best_move_squares": best_squares,
                "centipawn_loss": r["centipawn_loss"],
            },
        }
    return {
        key: {
            "child_fen": rep["child_fen"],
            "arriving": rep["arriving"],
            "source": {"games": len(games_by_board[key]), "worst": rep["worst"]},
        }
        for key, rep in rep_by_board.items()
    }


def merge_branches(
    current_key: str, rep_map: dict[str, Row], blunder_map: dict[str, Row], scout_map: dict[str, Row]
) -> tuple[Row | None, list[Row]]:
    """(the current branch or None, the ordered alternatives). Repertoire wins over blunders
    per board; scout survives an overlap."""
    branches: dict[str, Row] = {}
    for key in set(rep_map) | set(blunder_map) | set(scout_map):
        rep, blu, sco = rep_map.get(key), blunder_map.get(key), scout_map.get(key)
        primary = rep or blu or sco
        assert primary is not None
        branches[key] = {
            "child_fen": primary["child_fen"],
            "opponent_move": primary["arriving"],
            "sources": {
                "repertoire": rep["source"] if rep else None,
                "blunders": None if rep else (blu["source"] if blu else None),
                "scout": sco["source"] if sco else None,
            },
        }
    current = branches.pop(current_key, None)

    def sort_key(item: tuple[str, Row]) -> tuple[int, int, str]:
        key, b = item
        s = b["sources"]
        if s["repertoire"] is not None:
            return (0, -int(s["repertoire"]["line_count"]), key)
        if s["blunders"] is not None:
            cp = s["blunders"]["worst"]["centipawn_loss"]
            return (1, -(int(cp) if cp is not None else -1), key)
        return (2, -int(s["scout"]["total_games"]), key)

    return current, [b for _, b in sorted(branches.items(), key=sort_key)]


def branch_compare(
    conn: Connection[Any], fen: str, pre_fen: str, *, arriving: Row, book_color: str, max_boards: int
) -> Row:
    """The whole comparison. The route owns the inputs: both FENs python-chess-serialised,
    opposite sides to move, `arriving` the one legal move from `pre_fen` to `fen`'s board;
    `book_color` is `fen`'s side to move, given here without a default on purpose."""
    if book_color not in ("white", "black"):
        raise ValueError(f"book_color must be white or black, not {book_color!r}")
    if max_boards < 1:
        raise ValueError("max_boards out of domain")
    params = {"pid": PLAYER_ID, "pre_fen": pre_fen}
    with conn.cursor() as cur:
        cur.execute(_REPERTOIRE_BRANCHES_SQL, params | {"book_color": book_color})
        rep_rows = [dict(r) for r in cur.fetchall()]
        cur.execute(cast(LiteralString, _BLUNDER_BRANCHES_SQL.format(analysable=analysable_sql("cg"))), params)
        blunder_rows = [dict(r) for r in cur.fetchall()]
    scout_map: dict[str, Row] = {}  # phase 6
    current, ordered = merge_branches(
        normalize_fen(fen), repertoire_by_board(rep_rows), blunders_by_board(blunder_rows), scout_map
    )
    if current is None:
        current = {
            "child_fen": fen,
            "opponent_move": arriving,
            "sources": {"repertoire": None, "blunders": None, "scout": None},
        }
    else:
        # The pinned board is the real query position, not a source occurrence's serialisation.
        current["child_fen"] = fen
        current["opponent_move"] = arriving
    retained = ordered[:max_boards]
    omitted = len(ordered) - len(retained)
    return {
        "query": {"fen": fen, "pre_fen": pre_fen, "book_color": book_color, "max_boards": max_boards},
        "current": current,
        "truncated": omitted > 0,
        "boards_omitted": omitted,
        "branches": retained,
    }
