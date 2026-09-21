#!/usr/bin/env python3
"""Compare what Practice would serve and how it grades against the old database.

**Attempt replay.** Every attempt the old system recorded as solved is replayed through
the current grader — the stored moves against the stored solution, or the acceptance map
for a missed mate — and must come out solved. A disagreement is a grading regression.
Two things are not regressions and are listed apart: an attempt older than the puzzle's
last change was graded against a different puzzle (a missed-mate puzzle acquired its map
after some early attempts), and a repertoire attempt was graded up to the ply presented
at the time, which is not stored, so it is replayed up to the ply its own move count
implies. Attempts recorded as failed are not replayed: the old system took a failed claim
at its word, so those moves may be anything.

**Due eligibility.** The set of puzzles the new code counts as due in the scratch
database is compared with the old rules applied to the archive, restricted to the puzzles
that came across. Puzzles adopted from the old shared tier are listed apart rather than
counted: the old system showed one only while the player's sources still reached its
position, and now it is his and always visible.

    python tools/oracle/diff_practice.py [--replay] [--due]

Reads both databases; writes nothing. Exits 1 if anything differs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import oracle, scratch  # noqa: E402

from core import oracle as q  # noqa: E402
from core import settings  # noqa: E402
from core.puzzles import attempts, serve, srs  # noqa: E402


def _implied_ply(fen: str, line: list[str], color: str, submitted: int) -> int | None:
    """The ply at which `submitted` player moves end in the line; None if the line is shorter."""
    side = fen.split(" ")[1] if len(fen.split(" ")) > 1 else "w"
    player_plies = [i for i in range(len(line)) if (i % 2 == 0) == (side == color)]
    if submitted < 1 or submitted > len(player_plies):
        return None
    return player_plies[submitted - 1]


def diff_replay() -> int:
    diffs: list[str] = []
    older: list[str] = []
    with scratch() as db:
        rows = q.solved_attempts(db)
        for row in rows:
            line = [str(m) for m in row["solution_line"]] if isinstance(row["solution_line"], list) else []
            ply = None
            if row["is_repertoire"]:
                submitted = len(attempts.submitted_moves(row["moves_played"]))
                ply = _implied_ply(str(row["fen"]), line, str(row["color"]), submitted)
            ok = attempts.resolve_solved(
                fen=str(row["fen"]),
                solution_line=line,
                color=str(row["color"]),
                presentation_ply=ply,
                claimed=True,
                moves_played=row["moves_played"],
                acceptance_map=row["acceptance_map"],
                is_own_mate="own_mate" in (row["source_types"] or []),
            )
            if ok:
                continue
            kind = "repertoire" if row["is_repertoire"] else "standard"
            note = f"attempt {row['id']} on puzzle {row['puzzle_id']} ({kind}): stored solved, replays unsolved"
            if row["attempt_at"] < row["puzzle_updated_at"]:
                older.append(note + f" (predates the puzzle's last change, {row['puzzle_updated_at']:%Y-%m-%d})")
            else:
                diffs.append(note)
    print(
        f"\n== attempt replay: {len(rows)} solved attempts replayed, {len(diffs)} disagree,"
        f" {len(older)} predate their puzzle"
    )
    for d in diffs[:200]:
        print("  " + d)
    for d in older[:50]:
        print("  " + d)
    return 1 if diffs else 0


def diff_due() -> int:
    with scratch() as db, oracle() as old:
        config = settings.load(db)
        new_count = serve.count_eligible(db, config)
        new_rows = serve.browse(db, config, last_n_games=0)
        now = db.execute("SELECT NOW() AS now").fetchone()
        assert now is not None
        new_due = {int(r["id"]) for r in new_rows if srs.is_due(r.get("srs"), now["now"])}
        migrated = {int(r["id"]) for r in db.execute("SELECT id FROM puzzles").fetchall()}
        old_visible = q.old_visible_ids(old, serve.lookahead_plies(config))
        old_due = q.old_due_ids(old, old_visible)
        shared = {int(r["id"]) for r in old.execute("SELECT id FROM puzzles WHERE player_id IS NULL").fetchall()}
        adopted = shared & migrated
        old_due_kept = old_due & migrated
    print(
        f"\n== due eligibility: new {new_count} due ({len(new_due)} listed), old {len(old_due)} due,"
        f" {len(adopted)} puzzles adopted from the shared tier"
    )
    diffs = [f"puzzle {pid}: due only in the new system" for pid in sorted(new_due - old_due_kept - adopted)]
    diffs += [f"puzzle {pid}: due only in the old system" for pid in sorted(old_due_kept - new_due - adopted)]
    for d in diffs[:200]:
        print("  " + d)
    for pid in sorted(adopted & (new_due ^ old_due_kept)):
        print(f"  puzzle {pid}: adopted from the shared tier, so visible on ownership now (was gated on sources)")
    if new_count != len(new_due):
        print(f"  count_eligible ({new_count}) and the due list ({len(new_due)}) disagree")
        return 1
    return 1 if diffs else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--due", action="store_true")
    args = parser.parse_args()
    run_all = not (args.replay or args.due)
    rc = 0
    if run_all or args.replay:
        rc |= diff_replay()
    if run_all or args.due:
        rc |= diff_due()
    return rc


if __name__ == "__main__":
    sys.exit(main())
