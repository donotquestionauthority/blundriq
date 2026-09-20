"""Rerun repertoire matching on every in-window game in the scratch database and
diff game_repertoire_results / game_result_lines / no-match flags against the oracle."""

from __future__ import annotations

import sys

from common import oracle, report, scratch

from core import oracle as q
from core import settings
from core.repertoire import matching


def main() -> int:
    with scratch() as new, oracle() as old:
        window = settings.load(new).analysis_game_limit
        q.forget_match_decisions(new)
        print("rerun:", matching.match_player(new, window))
        new.commit()
        ids = q.decided_game_ids(new)
        mine, theirs = q.match_results(new, ids), q.match_results(old, ids)
        flags_new, flags_old = q.no_match_flags(new, ids), q.no_match_flags(old, ids)

    diffs: list[str] = []
    for gid in ids:
        a, b = mine.get(gid, []), theirs.get(gid, [])
        if len(a) != len(b):
            diffs.append(f"game {gid}: {len(a)} result row(s) new vs {len(b)} old")
            continue
        if a:
            for f in q.RESULT_FIELDS:
                if a[0][f] != b[0][f]:
                    diffs.append(f"game {gid}: {f} new={a[0][f]!r} old={b[0][f]!r}")
            if list(a[0]["lines"] or []) != list(b[0]["lines"] or []):
                diffs.append(f"game {gid}: lines new={a[0]['lines']} old={b[0]['lines']}")
        if flags_new.get(gid) != flags_old.get(gid):
            diffs.append(f"game {gid}: no_repertoire_match new={flags_new.get(gid)} old={flags_old.get(gid)}")
    return report("matching", diffs, len(ids))


if __name__ == "__main__":
    sys.exit(main())
