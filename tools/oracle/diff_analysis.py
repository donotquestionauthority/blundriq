"""Diff the new analysis against the oracle's blunders and motif events.

Default (engine-free): for every analysed standard game, replay the oracle's
stored per-ply evals and best lines through the new classifier and tagger and
compare the derived rows. This checks the code, not Stockfish.

--stockfish: run Stockfish 18 at depth 18 on the fixed sample (tools/oracle/
sample.json, or --limit N of it) in the scratch database and compare the
stored rows, which checks engine determinism across machines as well.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import chess
import chess.engine
from common import oracle, report, sample_ids, scratch

from core import oracle as q
from core import settings
from core.analysis.game import analyze_game
from core.analysis.motifs import tag_game
from core.analysis.run import analyze_pending


class ReplayEngine:
    """Serves the oracle's stored eval/best line for each position, so analyze_game
    reproduces the old classification without Stockfish."""

    def __init__(self, ply_analysis: list[dict[str, Any]], moves: list[str]) -> None:
        self.by_ply = {int(e["ply"]): e for e in ply_analysis}
        board = chess.Board()
        fens = [board.fen()]
        for m in moves:
            board.push_san(m)
            fens.append(board.fen())
        self.by_fen = {f: i for i, f in enumerate(fens)}

    def analyse(self, board: chess.Board, limit: Any, game: Any = None) -> dict[str, Any]:
        e = self.by_ply[self.by_fen[board.fen()]]
        ev = e.get("eval")
        mate = e.get("mate_in_moves")
        score = chess.engine.Mate(mate) if mate else chess.engine.Cp(ev if ev is not None else 0)
        pv: list[chess.Move] = []
        if e.get("best_line"):
            b = board.copy()
            for tok in str(e["best_line"]).split():
                try:
                    mv = b.parse_san(tok)
                except Exception:
                    break
                pv.append(mv)
                b.push(mv)
        info: dict[str, Any] = {"score": chess.engine.PovScore(score, chess.WHITE), "pv": pv}
        if ev is None:
            info["score"] = None
        return info


def _norm(v: Any) -> Any:
    return sorted(v) if isinstance(v, list) else v


def compare(
    mine: dict[int, list[dict[str, Any]]],
    theirs: dict[int, list[dict[str, Any]]],
    ids: list[int],
    fields: tuple[str, ...],
    label: str,
) -> list[str]:
    diffs: list[str] = []
    for gid in ids:
        a, b = mine.get(gid, []), theirs.get(gid, [])
        if len(a) != len(b):
            diffs.append(
                f"game {gid}: {len(a)} {label} row(s) new vs {len(b)} old;"
                f" plies new={[r['ply'] for r in a]} old={[r['ply'] for r in b]}"
            )
            continue
        for ra, rb in zip(a, b, strict=True):
            for f in fields:
                if _norm(ra.get(f)) != _norm(rb.get(f)):
                    diffs.append(f"game {gid} ply {ra['ply']} ({label}): {f} new={ra.get(f)!r} old={rb.get(f)!r}")
    return diffs


def engine_free(limit: int | None) -> int:
    with scratch() as new, oracle() as old:
        s = settings.load(new)
        games = q.games_for_replay(old, q.analysed_game_ids(old))
        if limit:
            games = games[:limit]
        ids = [int(g["id"]) for g in games]
        mine_b: dict[int, list[dict[str, Any]]] = {}
        mine_e: dict[int, list[dict[str, Any]]] = {}
        for g in games:
            moves = list(g["moves"])
            engine = ReplayEngine(list(g["ply_analysis"]), moves)
            res = analyze_game(engine, moves, g["player_color"], s, 18)  # type: ignore[arg-type]
            events = tag_game(
                res.ply_analysis,
                moves,
                g["player_color"],
                "standard",
                None,
                motif_min_material_gain=s.motif_min_material_gain,
                motif_found_material_tolerance=s.motif_found_material_tolerance,
            )
            themes_by_ply: dict[int, list[str]] = {}
            for e in events:
                if e["found"] is False:
                    themes_by_ply.setdefault(int(e["ply"]), []).append(str(e["theme"]))
            mine_b[int(g["id"])] = [
                {
                    "ply": b.ply,
                    "move_played": b.move_played,
                    "best_move": b.best_move,
                    "best_line": b.best_line,
                    "post_blunder_line": b.post_blunder_line,
                    "centipawn_loss": b.centipawn_loss,
                    "classification": b.classification,
                    "phase": b.phase,
                    "themes": sorted(themes_by_ply[b.ply]) if b.ply in themes_by_ply else None,
                }
                for b in res.blunders
            ]
            mine_e[int(g["id"])] = sorted(events, key=lambda e: (e["ply"], e["metric_type"], e["theme"]))
        theirs_b, theirs_e = q.blunders(old, ids), q.motif_events(old, ids)
    code = report("engine-free blunders", compare(mine_b, theirs_b, ids, q.BLUNDER_FIELDS, "blunder"), len(ids))
    code |= report("engine-free motif events", compare(mine_e, theirs_e, ids, q.EVENT_FIELDS, "event"), len(ids))
    return code


def with_stockfish(limit: int | None, workers: int | None) -> int:
    """Every requested game must be analysed afresh: its rows are deleted first, the run
    must report no failures, and every id must carry a new depth-18 analysis before the
    comparison counts. Retained migrated rows can never satisfy the diff."""
    ids = sample_ids()
    if limit:
        ids = ids[:limit]
    with scratch() as new:
        s = settings.load(new)
        q.forget_analysis(new, ids)
        new.commit()
        summary = analyze_pending(new, s, workers=workers, game_ids=ids)
        print("rerun:", summary)
    with scratch() as new, oracle() as old:
        fresh = q.freshly_analysed(new, ids)
        missing = sorted(set(ids) - set(fresh))
        mine_b, theirs_b = q.blunders(new, ids), q.blunders(old, ids)
        mine_e, theirs_e = q.motif_events(new, ids), q.motif_events(old, ids)
    code = 0
    if summary["failed"] or summary["pending"] != len(ids) or missing:
        print(
            f"\n== coverage: requested {len(ids)} distinct games, worklist {summary['pending']},"
            f" analysed {summary['analyzed']}, failed {summary['failed']}, without a fresh analysis: {missing}"
        )
        code = 1
    else:
        print(f"\n== coverage: all {len(ids)} distinct games analysed afresh")
    code |= report("stockfish blunders", compare(mine_b, theirs_b, ids, q.BLUNDER_FIELDS, "blunder"), len(ids))
    code |= report("stockfish motif events", compare(mine_e, theirs_e, ids, q.EVENT_FIELDS, "event"), len(ids))
    return code


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stockfish", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--workers", type=int)
    a = p.parse_args()
    return with_stockfish(a.limit, a.workers) if a.stockfish else engine_free(a.limit)


if __name__ == "__main__":
    sys.exit(main())
