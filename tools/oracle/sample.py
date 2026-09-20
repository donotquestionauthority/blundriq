"""Pick the fixed game sample: 50 seeded-random analysed standard games plus named edge cases."""

from __future__ import annotations

import json
import random
import sys

from common import SAMPLE, oracle

from core import oracle as q

EDGE_CASES = {
    "checkmate_win": "cg.termination = 'checkmate' AND pg.result = 'win'",
    "promotion": "cg.moves::text LIKE '%%=Q%%'",
    "clean": "NOT EXISTS (SELECT 1 FROM blunders b WHERE b.player_id = pg.player_id AND b.chess_game_id = cg.id)",
    "missed_mate": (
        "EXISTS (SELECT 1 FROM player_motif_events e WHERE e.player_id = pg.player_id AND e.chess_game_id = cg.id"
        " AND e.metric_type = 'mate' AND e.found IS FALSE)"
    ),
    "longest": "TRUE",
    "opponent_deviated": (
        "EXISTS (SELECT 1 FROM game_repertoire_results g WHERE g.player_id = pg.player_id"
        " AND g.chess_game_id = cg.id AND g.deviation_by = 'opponent')"
    ),
    "followed_line": (
        "EXISTS (SELECT 1 FROM game_repertoire_results g WHERE g.player_id = pg.player_id"
        " AND g.chess_game_id = cg.id AND g.deviation_by = 'none')"
    ),
}


def main() -> int:
    with oracle() as conn:
        pool = q.analysed_game_ids(conn)
        chosen = sorted(random.Random(20260920).sample(pool, 50))
        edges = {
            name: q.first_analysed_game(
                conn, pred, "jsonb_array_length(cg.moves) DESC" if name == "longest" else "cg.id"
            )  # type: ignore[arg-type]
            for name, pred in EDGE_CASES.items()
        }
    data = {"seed": 20260920, "random": chosen, "edge_cases": {k: v for k, v in edges.items() if v is not None}}
    if "--write" in sys.argv:
        SAMPLE.write_text(json.dumps(data, indent=2) + "\n")
        print(f"wrote {SAMPLE}")
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
