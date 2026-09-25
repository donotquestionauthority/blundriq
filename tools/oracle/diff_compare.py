"""Similar positions and branch compare: the new modules against the old ones, on the same inputs.

The old `db/neighbourhood.py` and `db/branch_compare.py` are executed from the archived source
with their imports satisfied by the old `_rep_canonicalise`, `_rep_tie_break` (lifted from the old
`db/games.py` by AST) and `normalize_fen`, and stubbed settings reads (module defaults, which equal
the new settings' defaults). They run against the old database; the new `similar_positions` and
`branch_compare` run against the migrated scratch database. Compared as JSON per query.

Inputs: every blunder board of the sample games and a seeded slice of all blunders (the move
played as the queried move), a seeded slice of repertoire nodes; for branch compare the
(position, parent) pair of every such blunder and repertoire node. Both caps are raised to 50 so
the scout leg — phase 6 here, so `sources.scout` is stripped and scout-only branches are dropped
from the old output — cannot move the cap. Chess960 games are left out of the inputs: the old
legs did not filter variants, here their blunders count for nothing (a known difference).

    python tools/oracle/diff_compare.py --old-src /path/to/old-src   (the extracted archive)
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import sys
import types
from pathlib import Path
from typing import Any

import chess
from common import oracle, report, sample_ids, scratch

from core.constants import PLAYER_ID
from core.repertoire import branch_compare as new_bc
from core.repertoire import neighbourhood as new_nb
from core.repertoire.annotations import normalize_fen

CAP = 50


def lift(old_src: Path) -> tuple[Any, Any]:
    """(old neighbourhood module, old branch_compare module), executed from the archive."""
    api = old_src / "blundriq-api"
    games = ast.parse((api / "db" / "games.py").read_text())
    wanted = {"_rep_canonicalise", "_rep_tie_break"}
    nodes = [n for n in games.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert len(nodes) == 2, "old authorities not found"
    db_games = types.ModuleType("db.games")
    db_games.__dict__["chess"] = chess
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "old-games", "exec"),
        db_games.__dict__,
    )  # noqa: S102
    db_prefs = types.ModuleType("db.preferences")
    db_prefs.__dict__["_coerce_domain_int"] = lambda v: None
    db_prefs.__dict__["_read_app_setting"] = lambda conn, key: None
    fen_utils = types.ModuleType("fen_utils")
    fen_utils.__dict__["normalize_fen"] = normalize_fen
    db_pkg = types.ModuleType("db")
    sys.modules.update({"db": db_pkg, "db.games": db_games, "db.preferences": db_prefs, "fen_utils": fen_utils})
    old_nb = types.ModuleType("db.neighbourhood")
    exec(compile((api / "db" / "neighbourhood.py").read_text(), "old-neighbourhood", "exec"), old_nb.__dict__)  # noqa: S102
    sys.modules["db.neighbourhood"] = old_nb
    old_bc = types.ModuleType("db.branch_compare")
    exec(compile((api / "db" / "branch_compare.py").read_text(), "old-branch-compare", "exec"), old_bc.__dict__)  # noqa: S102
    return old_nb, old_bc


def queries(old: Any) -> tuple[list[tuple[str, str | None]], list[tuple[str, str]]]:
    """(similar queries as (fen, move), branch pairs as (fen, pre_fen))."""
    rng = random.Random(402)
    with old.cursor() as cur:
        cur.execute(
            "SELECT b.fen, b.move_played, b.ply, cg.fen_sequence FROM blunders b JOIN chess_games cg ON cg.id = b.chess_game_id"
            " WHERE b.player_id = %s AND cg.fen_sequence IS NOT NULL AND cg.variant = 'standard' ORDER BY b.id",
            (PLAYER_ID,),
        )
        blunders = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT rl.moves, rl.fen_sequence FROM repertoire_lines rl JOIN chapters ch ON ch.id = rl.chapter_id"
            " JOIN books bk ON bk.id = ch.book_id WHERE bk.player_id = %s AND rl.active ORDER BY rl.id",
            (PLAYER_ID,),
        )
        lines = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT b.fen FROM blunders b WHERE b.chess_game_id = ANY(%s)", (sample_ids(),))
        sample_fens = [str(r["fen"]) for r in cur.fetchall()]
    picked = rng.sample(blunders, min(300, len(blunders)))
    similar: list[tuple[str, str | None]] = [(chess.Board(f).fen(), None) for f in sample_fens]
    pairs: list[tuple[str, str]] = []
    for b in picked:
        fen = chess.Board(str(b["fen"])).fen()
        similar.append((fen, b["move_played"]))
        seq = b["fen_sequence"] if isinstance(b["fen_sequence"], list) else json.loads(b["fen_sequence"])
        if b["ply"] >= 1 and b["ply"] < len(seq):
            pairs.append((chess.Board(seq[b["ply"]]).fen(), chess.Board(seq[b["ply"] - 1]).fen()))
    for line in rng.sample(lines, min(150, len(lines))):
        seq = line["fen_sequence"] if isinstance(line["fen_sequence"], list) else json.loads(line["fen_sequence"])
        if len(seq) < 3:
            continue
        i = rng.randrange(1, len(seq))
        similar.append((chess.Board(seq[i]).fen(), None))
        pairs.append((chess.Board(seq[i]).fen(), chess.Board(seq[i - 1]).fen()))
    return list(dict.fromkeys(similar)), list(dict.fromkeys(pairs))


def canonical_move(fen: str, move: str | None) -> str | None:
    if not move:
        return None
    board = chess.Board(fen)
    try:
        return board.san(board.parse_san(move))
    except ValueError:
        return None


def arriving_for(fen: str, pre: str) -> dict[str, Any] | None:
    """The route's validation: exactly one legal move joins the pair."""
    pre_board = chess.Board(pre)
    target = normalize_fen(fen)
    found = [m for m in list(pre_board.legal_moves) if _reaches(pre_board, m, target)]
    if len(found) != 1:
        return None
    return new_nb.parse_arriving(pre, pre_board.san(found[0]))


def _reaches(board: chess.Board, move: chess.Move, target: str) -> bool:
    board.push(move)
    try:
        return normalize_fen(board.fen()) == target
    finally:
        board.pop()


def strip_scout(resp: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(resp))
    out["current"]["sources"]["scout"] = None
    kept = []
    for b in out["branches"]:
        if b["sources"]["repertoire"] is None and b["sources"]["blunders"] is None:
            continue  # scout-only: phase 6
        b["sources"]["scout"] = None
        kept.append(b)
    out["branches"] = kept
    out["boards_omitted"] = 0
    out["truncated"] = False
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-src", required=True)
    args = ap.parse_args()
    old_nb, old_bc = lift(Path(args.old_src))
    diffs: list[str] = []
    checked = 0
    with oracle() as old, scratch() as new:
        similar, pairs = queries(old)
        print(f"{len(similar)} similar queries, {len(pairs)} branch pairs")
        for fen, move in similar:
            cm = canonical_move(fen, move)
            for dist in (4, 8):
                expected = old_nb.get_similar_positions(old, PLAYER_ID, fen, cm, dist, CAP)
                got = new_nb.similar_positions(new, fen, cm, max_distance=dist, max_positions=CAP)
                checked += 1
                if json.dumps(expected, sort_keys=True) != json.dumps(got, sort_keys=True):
                    diffs.append(f"similar {fen} move={cm} d={dist}: {_first_diff(expected, got)}")
        for fen, pre in pairs:
            arriving = arriving_for(fen, pre)
            if arriving is None:
                continue
            book_color = "white" if chess.Board(fen).turn == chess.WHITE else "black"
            expected = strip_scout(
                old_bc.get_branch_compare(
                    old, PLAYER_ID, fen, pre, arriving=arriving, book_color=book_color, max_boards=CAP
                )
            )
            got = strip_scout(
                new_bc.branch_compare(new, fen, pre, arriving=arriving, book_color=book_color, max_boards=CAP)
            )
            checked += 1
            if json.dumps(expected, sort_keys=True) != json.dumps(got, sort_keys=True):
                diffs.append(f"branch {fen} from {pre}: {_first_diff(expected, got)}")
    return report("similar positions + branch compare", diffs, checked)


def _first_diff(a: Any, b: Any, path: str = "") -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k} missing on one side"
            if a[k] != b[k]:
                return _first_diff(a[k], b[k], f"{path}.{k}")
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path} length old {len(a)} new {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            if x != y:
                return _first_diff(x, y, f"{path}[{i}]")
    return f"{path}: old {a!r} new {b!r}"


if __name__ == "__main__":
    sys.exit(main())
