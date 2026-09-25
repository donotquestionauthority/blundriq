# Oracle diffs

Compare the new pipeline against what the old system stored: the new code must match the old
results before it is allowed to differ, and deliberate differences go in docs/decisions/. Needs two
databases: `DATABASE_URL` = a scratch database that was `pipeline db init` + `settings
seed` + `pipeline migrate`d from the oracle, and `ORACLE_DATABASE_URL` = the restored
old database. Nothing here touches a production database; the scratch database is
disposable and is modified.

    python tools/oracle/sample.py --write        # choose the fixed game sample (sample.json)
    python tools/oracle/diff_matching.py         # wipe + rerun matching on every in-window game, diff
    python tools/oracle/diff_analysis.py         # engine-free: replay stored evals through the new
                                                 # classifier + tagger for every analysed game, diff
    python tools/oracle/diff_analysis.py --stockfish [--limit N]
                                                 # real Stockfish 18 depth 18 on the sample, diff
    python tools/oracle/diff_puzzles.py          # regenerate puzzles and diff; rebuild every
                                                 # acceptance map and diff
    python tools/oracle/diff_practice.py         # replay every solved attempt through the grader;
                                                 # diff the due set against the old rules
    python tools/oracle/diff_blunders.py         # the Blunders page's ranked list against the old
                                                 # ranking query (core/oracle.py, weights frozen there)
    python tools/oracle/diff_deviations.py       # the Deviations page's patterns against the old
                                                 # route's grouping (run before any rematch: 003's
                                                 # known differences would otherwise show)
    python tools/oracle/diff_annotations.py --old-src DIR
                                                 # the line walk-through's notes, every line, against
                                                 # the old projection lifted from the archived source
    python tools/oracle/diff_compare.py --old-src DIR
                                                 # Similar positions and branch compare against the old
                                                 # modules executed from the archive, on the same queries
    python tools/oracle/diff_conflicts.py --old-src DIR
                                                 # the Conflicts page's listing and the activation gate on
                                                 # every line not in play, against the old functions lifted
                                                 # from the archive and run over its rows
    python tools/oracle/diff_scout.py --old-src DIR
                                                 # Scout's positions, decision nodes and report against the
                                                 # old route's queries lifted from the archive (all-time
                                                 # windows; the scratch copy's dismissals are emptied first)

`diff_puzzles.py --generation` deletes the generated puzzles before remaking them, and
deleting a puzzle cascades its SRS row away, so it refuses to run unless the database name
contains `scratch`. Make one by migrating from the oracle into a local database rather than
pointing it at the database Rob practises against.

Each script prints per-game differences and exits 1 if any field differs.
