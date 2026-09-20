"""Pick the fixed game sample: 50 seeded-random analysed standard games plus the named
edge cases core.oracle knows about."""

from __future__ import annotations

import json
import random
import sys

from common import SAMPLE, oracle

from core import oracle as q


def main() -> int:
    with oracle() as conn:
        chosen = sorted(random.Random(20260920).sample(q.analysed_game_ids(conn), 50))
        edges = q.edge_case_games(conn)
    data = {"seed": 20260920, "random": chosen, "edge_cases": edges}
    if "--write" in sys.argv:
        SAMPLE.write_text(json.dumps(data, indent=2) + "\n")
        print(f"wrote {SAMPLE}")
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
