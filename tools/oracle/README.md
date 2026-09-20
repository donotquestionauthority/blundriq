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

Each script prints per-game differences and exits 1 if any field differs.
