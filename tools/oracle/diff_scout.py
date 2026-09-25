"""Scout: the new positions list, decision nodes and report against the old route's queries.

The old `_fetch_scout_page`, `_fetch_scout_decision_nodes` and `_fetch_scouting_report` (from
`api/routes/scout.py`) and `get_scout_best_worst_lines` (from `db/scout.py`) are lifted out of the
archived source by their AST and run verbatim against the restored old database, with the old
constants (`MIN_MATCH_PLY = 6`, the classification weights) and the old settings rows. The new
`core.scout.positions.page_rows`, `nodes.decision_nodes` and `report.report` run on the migrated
scratch database, whose dismissed-boards table is emptied first (the old queries never read it).

Compared under all-time windows, for every profile × colour × min_freq ∈ {1, 2}, every page:
the ordered FEN list per page, tier, both frequencies, sort score, totals and tier counts; for
nodes the ordered FEN list, replies, lead-in and my_frequency; for the report the most-played,
as-white / as-black, best and worst lists. Timings per query are printed.

    python tools/oracle/diff_scout.py --old-src /path/to/old-src   (the extracted archive)
"""

from __future__ import annotations

import argparse
import ast
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common import oracle, report, scratch

from core import oracle as q
from core import settings
from core.scout import nodes, positions
from core.scout import report as scout_report
from core.scout.positions import ScoutFilters

OLD_COLORS = {"both": "Both colors", "white": "As White", "black": "As Black"}
SETTING_KEYS = ["branch_min", "reply_min_freq", "reply_cap", "scout_bayesian_prior_strength"]


def lift(old_src: Path, old_settings: dict[str, str]) -> dict[str, Any]:
    """The old functions, compiled from the source files alone, with the names they reach for."""
    routes = ast.parse((old_src / "blundriq-api" / "api" / "routes" / "scout.py").read_text())
    dbmod = ast.parse((old_src / "blundriq-api" / "db" / "scout.py").read_text())
    wanted = {"_fetch_scout_page", "_fetch_scout_decision_nodes", "_fetch_scouting_report"}
    nodes_: list[ast.stmt] = [n for n in routes.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    nodes_ += [n for n in dbmod.body if isinstance(n, ast.FunctionDef) and n.name == "get_scout_best_worst_lines"]
    assert len(nodes_) == 4, "old functions not found"
    module = ast.Module(body=nodes_, type_ignores=[])
    old_weights = q.OLD_BLUNDER_WEIGHTS

    class _Db:
        """`db.` as the old route sees it: settings and the best/worst read."""

        @staticmethod
        def get_app_settings(_conn: Any) -> dict[str, int]:
            return {k: int(v) for k, v in old_settings.items()}

        @staticmethod
        def get_scout_best_worst_lines(conn: Any, profile_id: int, k: int, opp_since: Any) -> dict[str, Any]:
            return ns["get_scout_best_worst_lines"](conn, profile_id, k, opp_since)

    ns: dict[str, Any] = {
        "CLASSIFICATION_WEIGHTS": old_weights,
        "MIN_MATCH_PLY": 6,
        "db": _Db,
        "defaultdict": defaultdict,
        "Counter": Counter,
        "datetime": datetime,
        "timezone": timezone,
        "timedelta": timedelta,
    }
    exec(compile(ast.fix_missing_locations(module), "old-scout", "exec"), ns)  # noqa: S102
    return ns


def _pages(
    old: dict[str, Any],
    old_db: Any,
    new: Any,
    profile_id: int,
    color: str,
    min_freq: int,
    diffs: list[str],
    timings: dict[str, list[float]],
) -> int:
    checked = 0
    page = 0
    while True:
        t0 = time.perf_counter()
        old_rows, old_total, old_tc = old["_fetch_scout_page"](
            old_db, 1, profile_id, None, None, OLD_COLORS[color], min_freq, page, positions.PAGE_SIZE
        )
        timings["old positions"].append(time.perf_counter() - t0)
        f = ScoutFilters(profile_id=profile_id, my_last_n=0, opp_last_n=0, color=color, min_freq=min_freq)
        t0 = time.perf_counter()
        new_rows, new_total, new_tc = positions.page_rows(new, f, page)
        timings["new positions"].append(time.perf_counter() - t0)
        label = f"profile {profile_id} {color} min_freq {min_freq} page {page}"
        checked += 1
        if (old_total, old_tc) != (new_total, new_tc):
            diffs.append(f"{label}: totals old {old_total} {old_tc} new {new_total} {new_tc}")
        if [r["fen"] for r in old_rows] != [r["fen"] for r in new_rows]:
            diffs.append(
                f"{label}: fen order old {[r['fen'][:30] for r in old_rows]} new {[r['fen'][:30] for r in new_rows]}"
            )
        for o, n in zip(old_rows, new_rows, strict=False):
            if o["fen"] != n["fen"]:
                break
            checked += 1
            for k in ("tier", "my_frequency", "opp_frequency", "sort_score", "max_depth", "blunder_score", "line_id"):
                if o.get(k) != n.get(k):
                    diffs.append(f"{label} {o['fen']}: {k} old {o.get(k)} new {n.get(k)}")
        if not old_rows and not new_rows:
            break
        page += 1
        if page * positions.PAGE_SIZE >= max(old_total, new_total):
            break
    return checked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-src", required=True)
    args = ap.parse_args()
    diffs: list[str] = []
    checked = 0
    timings: dict[str, list[float]] = defaultdict(list)
    with oracle() as old_db, scratch() as new:
        old_settings = q.old_app_settings(old_db, SETTING_KEYS)
        old = lift(Path(args.old_src), old_settings)
        config = settings.load(new)
        for k in SETTING_KEYS:
            if str(getattr(config, k)) != old_settings.get(k, str(getattr(config, k))):
                print(f"note: setting {k} old {old_settings.get(k)} new {getattr(config, k)}")
        forgotten = q.forget_dismissals(new)
        print(f"{forgotten} dismissed boards forgotten in the scratch copy")
        for profile in q.old_opponent_profiles(old_db):
            pid = int(profile["id"])
            for color in ("both", "white", "black"):
                for min_freq in (1, 2):
                    checked += _pages(old, old_db, new, pid, color, min_freq, diffs, timings)
            for min_freq in (1, 2):
                t0 = time.perf_counter()
                old_nodes = old["_fetch_scout_decision_nodes"](
                    old_db,
                    1,
                    pid,
                    None,
                    None,
                    min_freq,
                    int(old_settings["branch_min"]),
                    int(old_settings["reply_min_freq"]),
                    int(old_settings["reply_cap"]),
                )
                timings["old nodes"].append(time.perf_counter() - t0)
                f = ScoutFilters(profile_id=pid, my_last_n=0, opp_last_n=0, color="both", min_freq=min_freq)
                t0 = time.perf_counter()
                new_nodes = nodes.decision_nodes(new, f, config)
                timings["new nodes"].append(time.perf_counter() - t0)
                label = f"profile {pid} nodes min_freq {min_freq}"
                checked += 1
                if [r["fen"] for r in old_nodes] != [r["fen"] for r in new_nodes]:
                    diffs.append(f"{label}: fen order old {len(old_nodes)} new {len(new_nodes)}")
                for o, n in zip(old_nodes, new_nodes, strict=False):
                    if o["fen"] != n["fen"]:
                        break
                    checked += 1
                    for k in (
                        "opp_replies",
                        "replies_more",
                        "lead_in",
                        "lead_pre_fen",
                        "my_frequency",
                        "node_freq",
                        "my_color",
                    ):
                        if o.get(k) != n.get(k):
                            diffs.append(f"{label} {o['fen']}: {k} old {o.get(k)} new {n.get(k)}")
            t0 = time.perf_counter()
            old_rep = old["_fetch_scouting_report"](old_db, pid, None)
            timings["old report"].append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            new_rep = scout_report.report(new, pid, prior_strength=config.scout_bayesian_prior_strength, opp_since=None)
            timings["new report"].append(time.perf_counter() - t0)
            checked += 1
            for k in ("most_played", "as_white", "as_black"):
                o = [(r["family"], r["cnt"], r["pct"], [g["url"] for g in r.get("games", [])]) for r in old_rep[k]]
                n = [(r["family"], r["cnt"], r["pct"], [g["url"] for g in r.get("games", [])]) for r in new_rep[k]]
                if o != n:
                    diffs.append(f"profile {pid} report {k}: old {o} new {n}")
            for k in ("best_lines", "worst_lines"):
                o = [(r["variation"], r["games"], r["wins"], r["eco"], round(r["shrunk_rate"], 9)) for r in old_rep[k]]
                n = [(r["variation"], r["games"], r["wins"], r["eco"], round(r["shrunk_rate"], 9)) for r in new_rep[k]]
                if o != n:
                    diffs.append(f"profile {pid} report {k}: old {o} new {n}")
            o_act = {k: v for k, v in old_rep["activity"].items() if k in ("total", "chesscom_total", "lichess_total")}
            n_act = {k: v for k, v in new_rep["activity"].items() if k in ("total", "chesscom_total", "lichess_total")}
            if o_act != n_act:
                diffs.append(f"profile {pid} activity: old {o_act} new {n_act}")
    for name, times in sorted(timings.items()):
        print(f"{name:14s} n={len(times):3d} mean {sum(times) / len(times):.3f} s max {max(times):.3f} s")
    return report("scout", diffs, checked)


if __name__ == "__main__":
    sys.exit(main())
