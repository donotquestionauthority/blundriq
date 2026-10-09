"""`pipeline` — the one command-line entry point for everything that is not the API.

    pipeline db init | upgrade [--to N] | version schema
    pipeline settings show | seed FILE | schema  the settings row
    pipeline player set --chesscom U --lichess U the one players row (usernames)
    pipeline import [--platform P] [--all] [--months N]
    pipeline match-repertoire
    pipeline analyze [--workers N] [--limit N] [--game-id ID ...]
    pipeline generate-puzzles
    pipeline srs-maintain                         un-retire mastered puzzles whose pattern recurred
    pipeline import-opponents [--profile ID] [--reset-lichess-cursors]   scouted opponents' games
    pipeline review [--workers N]                 retag the window's review events
    pipeline position-evals [--limit N] [--workers N]  evaluate Review boards (hourly: 40; 0 = all)
    pipeline review-snapshot                      store Review's position sections for every filter
    pipeline backfill-openings [--months N]       opening prefix for games stored before it existed
    pipeline import-corpus --csv FILE             rebuild the Lichess CC0 corpus sample
    pipeline import-repertoire FILE --mode update|scratch [--preserve-manual] [--dry-run]
    pipeline housekeep
    pipeline run [--analyze-limit N]              the hourly chain, logged, alert on failure
    pipeline reprocess [--workers N]              every game and board under the current engine, fail-fast
    pipeline engine-status [--since TIMESTAMP]    read-only: did the reprocess run through? (exit 1 if not)
    pipeline blunder-funnel                       why the blunder generator qualifies what it does
    pipeline migrate --mapping FILE [--only T,T]  old database (ORACLE_DATABASE_URL) → this one

Every step is idempotent and safe to rerun; each records a pipeline_runs row.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import DictRow, dict_row

from core import db, housekeeping, migrate, notify, player, runs, schema, settings
from core.analysis import run as analysis
from core.ingest import backfill
from core.ingest import run as ingest
from core.puzzles import corpus, srs
from core.puzzles.generate import run as puzzles
from core.repertoire import importing, matching
from core.review import evals as position_evals
from core.review import run as review
from core.review import snapshot as review_snapshot
from core.scout import importing as scout_importing


def _db_init(_: argparse.Namespace) -> int:
    with db.connect() as conn:
        version = schema.init(conn)
    print(f"initialised; schema version {version}")
    return 0


def _db_upgrade(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        applied = schema.upgrade(conn, to=getattr(args, "to", None))
    print("applied: " + (", ".join(str(n) for n in applied) if applied else "nothing to do"))
    return 0


def _db_version(_: argparse.Namespace) -> int:
    with db.connect() as conn:
        current = schema.current_version(conn)
    print(f"database: {current if current is not None else 'no schema'}; latest: {schema.latest_version()}")
    return 0


def _settings_show(_: argparse.Namespace) -> int:
    with db.connect() as conn:
        current = settings.load(conn)
    print(json.dumps(current.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def _settings_seed(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.file).read_text())
    values = settings.Settings.model_validate(data)
    refused = settings.save_errors(values)
    if refused:
        raise notify.OperatorError("; ".join(refused))
    with db.connect() as conn:
        settings.save(conn, values)
    print(f"settings saved ({len(data)} keys given, {len(values.model_fields)} fields stored)")
    return 0


def _settings_schema(_: argparse.Namespace) -> int:
    print(json.dumps(settings.schema(), indent=2))
    return 0


def _player_set(args: argparse.Namespace) -> int:
    """Store the handles. The console says which platforms are configured, never the handles
    themselves: output here may be pasted or run under a public log."""
    with db.connect() as conn:
        names = player.set_usernames(conn, args.chesscom, args.lichess)
    given = {"chesscom": args.chesscom, "lichess": args.lichess}
    state = {k: "updated" if given[k] else ("unchanged" if names[k] else "not set") for k in ("chesscom", "lichess")}
    print(f"player saved (chesscom: {state['chesscom']}, lichess: {state['lichess']})")
    return 0


# --- steps ---------------------------------------------------------------------------------
# Each step takes an open connection and returns a JSON-able summary. `_run_step`
# wraps it in a pipeline_runs row; `run` chains the hourly order.

Step = Callable[[psycopg.Connection[Any], argparse.Namespace], dict[str, Any]]


def _step_import(conn: psycopg.Connection[Any], args: argparse.Namespace) -> dict[str, Any]:
    platforms = [args.platform] if getattr(args, "platform", None) else ["chesscom", "lichess"]
    out: dict[str, Any] = {}
    for p in platforms:
        s = ingest.import_platform(
            conn, p, months=getattr(args, "months", None), all_history=bool(getattr(args, "all", False))
        )
        out[p] = {"fetched": s.fetched, "new": s.new, "skipped": s.skipped}
    return out


def _step_match(conn: psycopg.Connection[Any], _: argparse.Namespace) -> dict[str, Any]:
    window = settings.load(conn).analysis_game_limit
    return matching.match_player(conn, window)


def _step_analyze(conn: psycopg.Connection[Any], args: argparse.Namespace) -> dict[str, Any]:
    current = settings.load(conn)
    return analysis.analyze_pending(
        conn,
        current,
        workers=getattr(args, "workers", None),
        game_ids=getattr(args, "game_id", None) or None,
        limit=getattr(args, "limit", None),
    )


def _step_generate_puzzles(conn: psycopg.Connection[Any], _: argparse.Namespace) -> dict[str, Any]:
    return puzzles.generate_all(conn, settings.load(conn))


def _step_import_corpus(conn: psycopg.Connection[Any], args: argparse.Namespace) -> dict[str, Any]:
    return corpus.import_corpus(conn, settings.load(conn), args.csv)


def _step_import_repertoire(conn: psycopg.Connection[Any], args: argparse.Namespace) -> dict[str, Any]:
    window = settings.load(conn).analysis_game_limit
    return importing.import_file(
        conn, Path(args.file), mode=args.mode, window=window, preserve_manual=args.preserve_manual, dry_run=args.dry_run
    )


def _step_srs_maintain(conn: psycopg.Connection[Any], _: argparse.Namespace) -> dict[str, Any]:
    return srs.demote_kings(conn, settings.load(conn))


def _step_import_opponents(conn: psycopg.Connection[Any], args: argparse.Namespace) -> dict[str, Any]:
    """`--reset-lichess-cursors` is the one-time cutover switch (core/scout/importing.py): the
    reset and the walk it triggers run under this step's row. The hourly chain never passes it."""
    reset = 0
    if getattr(args, "reset_lichess_cursors", False):
        reset = scout_importing.reset_lichess_cursors(conn)
    summary = scout_importing.import_profiles(
        conn, window=settings.load(conn).analysis_game_limit, profile_id=getattr(args, "profile", None)
    )
    if reset:
        summary["cursors_reset"] = reset
    return summary


def _step_review(conn: psycopg.Connection[Any], args: argparse.Namespace) -> dict[str, Any]:
    """Reads its settings itself, after its locks (core/review/run.py). `--workers` is for
    a local catch-up; the hourly chain runs serially."""
    return review.run(conn, workers=int(getattr(args, "workers", None) or 1))


def _step_position_evals(conn: psycopg.Connection[Any], args: argparse.Namespace) -> dict[str, Any]:
    """`--limit 0` evaluates every pending board (a local catch-up, with
    `--workers N` engines); the hourly chain passes nothing and evaluates at most
    POSITION_EVALS_PER_RUN with one engine."""
    from core.constants import POSITION_EVALS_PER_RUN

    limit = getattr(args, "evals_limit", None)
    if limit is None:
        limit = POSITION_EVALS_PER_RUN
    workers = int(getattr(args, "workers", None) or 1)
    return position_evals.run(conn, settings.load(conn), limit=limit or None, workers=workers)


def _step_review_snapshot(conn: psycopg.Connection[Any], _: argparse.Namespace) -> dict[str, Any]:
    return review_snapshot.build(conn, settings.load(conn))


def _step_backfill_openings(conn: psycopg.Connection[Any], args: argparse.Namespace) -> dict[str, Any]:
    months = getattr(args, "months", None)
    if months is None:
        months = settings.load(conn).review_history_months
    return backfill.backfill_openings(conn, months=int(months))


def _step_housekeep(conn: psycopg.Connection[Any], _: argparse.Namespace) -> dict[str, Any]:
    return housekeeping.run(conn, settings.load(conn).analysis_game_limit)


class StepFailed(RuntimeError):
    """A step finished but some of its work failed (e.g. analysis workers); the summary
    is kept, the run is recorded as failed and the chain stops."""


def _run_step(name: str, step: Step, args: argparse.Namespace) -> int:
    """Run one step inside a pipeline_runs row.

    Console output is public (GitHub Actions logs), so an error reaches it only as its
    class chain (core.notify.error_label); the message text goes to pipeline_runs.error.
    A step whose summary reports `failed > 0` is recorded as failed like an exception,
    with the partial summary kept, so worker failures cannot pass as a green hour.
    """
    label = "-"
    try:
        with db.connect() as conn:
            run_id = runs.start(conn, name)
            try:
                summary = step(conn, args)
                if int(summary.get("failed", 0)) > 0:
                    raise StepFailed(json.dumps(summary, sort_keys=True))
            except Exception as exc:
                label = notify.error_label(exc)
                runs.fail(conn, run_id, notify.error_detail(exc))
                print(f"{name}: FAILED ({label})", file=sys.stderr)
                if getattr(args, "alert", False):
                    notify.send_failure(name, run_id, label)
                return 1
            runs.finish(conn, run_id, summary)
    except Exception as exc:  # could not connect, or could not record the run
        label = notify.error_label(exc)
        print(f"{name}: FAILED before it could be recorded ({label})", file=sys.stderr)
        if getattr(args, "alert", False):
            notify.send_failure(name, None, label)
        return 1
    print(f"{name}: {json.dumps(summary, sort_keys=True)}")
    return 0


def _cmd(name: str, step: Step) -> Callable[[argparse.Namespace], int]:
    return lambda args: _run_step(name, step, args)


def hourly_steps() -> dict[str, Step]:
    """The step behind each name in core.runs.HOURLY_STEPS, in that order."""
    steps: dict[str, Step] = {
        "import": _step_import,
        "match": _step_match,
        "analyze": _step_analyze,
        "generate-puzzles": _step_generate_puzzles,
        "srs-maintain": _step_srs_maintain,
        "import-opponents": _step_import_opponents,
        "review": _step_review,
        "housekeep": _step_housekeep,
        "position-evals": _step_position_evals,
        "review-snapshot": _step_review_snapshot,
    }
    return {name: steps[name] for name in runs.HOURLY_STEPS}


def _run_all(args: argparse.Namespace) -> int:
    """The hourly order. A failed step stops the chain (its alert already went out).

    The alert secrets are read before the first step, like the API reads its own at
    startup: a run that could fail without anyone hearing about it does not start. The
    message names the variable (never a value) and is printed in full as an operator error.
    """
    from core import secrets

    try:
        secrets.alerts()
    except secrets.MissingSecret as exc:
        raise notify.OperatorError(f"{exc}; the hourly run does not start without its alert secrets") from exc
    args.alert = True
    args.limit = args.analyze_limit
    for name, step in hourly_steps().items():
        if _run_step(name, step, args) != 0:
            return 1
    return 0


def _reprocess(args: argparse.Namespace) -> int:
    """The engine change's batch (core/reprocess.py): the five steps in order, every game and every
    board, each logged as its own run; the first failure stops it. Hand-run and watched, so no
    alert; rerunning it after a failure picks up what is left."""
    from core.reprocess import REQUIRED_STEPS

    args.limit = None  # every pending game
    args.evals_limit = 0  # every pending board
    args.alert = False
    steps = hourly_steps()
    for name in REQUIRED_STEPS:
        if _run_step(name, steps[name], args) != 0:
            return 1
    return 0


def _engine_status(args: argparse.Namespace) -> int:
    """Read-only. Exit 0 only when nothing is pending under the current engine and every required
    step's latest run is ok, after the boundary, in order. Prints labels, counts, names and times."""
    from datetime import datetime

    from core import reprocess

    since = datetime.fromisoformat(args.since) if args.since else None
    if since is not None and since.tzinfo is None:
        raise notify.OperatorError("--since needs a UTC offset (2026-10-08T20:00:00+00:00): the run times carry one")
    with db.connect() as conn:
        found = reprocess.status(conn, settings.load(conn), since)
    print(f"engine {found.engine}; boundary {found.since.isoformat() if found.since else '-'}")
    print(f"pending: {found.games_pending} games, {found.boards_pending} boards")
    for step in found.steps:
        when = step.started_at.isoformat() if step.started_at else "-"
        print(f"{step.step:18s} {step.status or '-':8s} {when}  {step.problem or 'ok'}")
    if found.ok:
        print("engine-status: complete")
        return 0
    print(f"engine-status: NOT complete ({len(found.problems)} problems)", file=sys.stderr)
    return 1


def _blunder_funnel(_: argparse.Namespace) -> int:
    """Not a pipeline step: a report, printed as counts only."""
    from core.puzzles.generate import blunder

    with db.connect() as conn:
        config = settings.load(conn)
        rows = blunder.funnel(conn, config)
    print(
        f"window {config.blunders_default_last_n_games} games, threshold {config.blunder_puzzle_min_occurrences},"
        f" focus {config.time_class_focus}"
    )
    for gate, boards in rows:
        print(f"{boards:8d}  {gate}")
    return 0


def _migrate(args: argparse.Namespace) -> int:
    from core import secrets

    src_url = secrets.oracle().oracle_database_url
    mapping = migrate.Mapping.load(Path(args.mapping))
    only = [t.strip() for t in args.only.split(",") if t.strip()] if args.only else None
    with psycopg.Connection[DictRow].connect(src_url, row_factory=dict_row) as src, db.connect() as dst:
        counts = migrate.migrate(src, dst, mapping, only=only)
    for table, n in counts.items():
        print(f"{table:28s} {n}")
    return 0


# A failure outside the chain that still has to reach the operator: name -> the fixed label the
# email carries. Never anything the failing tool printed: the label is all the email says.
ALERTS = {"keepalive": "KeepaliveFailed"}


def _alert(args: argparse.Namespace) -> int:
    """Send the failure email for `args.what`. 0 when the provider accepted it; 1 otherwise,
    missing alert secrets included, so an alert that could not go out is itself a failure."""
    sent = notify.send_failure(args.what, None, ALERTS[args.what])
    print(f"alert {args.what}: {'sent' if sent else 'NOT SENT'}", file=sys.stdout if sent else sys.stderr)
    return 0 if sent else 1


def _at_least(low: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        value = int(text)
        if value < low:
            raise argparse.ArgumentTypeError(f"must be {low} or more")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="group", required=True)

    p_db = sub.add_parser("db", help="schema install and upgrade")
    db_sub = p_db.add_subparsers(dest="cmd", required=True)
    db_sub.add_parser("init").set_defaults(func=_db_init)
    p_up = db_sub.add_parser("upgrade")
    p_up.add_argument("--to", type=int, help="stop after this migration (default: apply every pending one)")
    p_up.set_defaults(func=_db_upgrade)
    db_sub.add_parser("version").set_defaults(func=_db_version)

    p_settings = sub.add_parser("settings", help="the settings row")
    s_sub = p_settings.add_subparsers(dest="cmd", required=True)
    s_sub.add_parser("show").set_defaults(func=_settings_show)
    p_seed = s_sub.add_parser("seed")
    p_seed.add_argument("file")
    p_seed.set_defaults(func=_settings_seed)
    s_sub.add_parser("schema").set_defaults(func=_settings_schema)

    p_player = sub.add_parser("player", help="the players row")
    pl_sub = p_player.add_subparsers(dest="cmd", required=True)
    p_set = pl_sub.add_parser("set")
    p_set.add_argument("--chesscom")
    p_set.add_argument("--lichess")
    p_set.set_defaults(func=_player_set)

    p_import = sub.add_parser("import", help="fetch new games")
    p_import.add_argument("--platform", choices=["chesscom", "lichess"])
    p_import.add_argument("--all", action="store_true", help="every archive, not just recent months")
    p_import.add_argument("--months", type=int, help="Chess.com look-back in months (default 3)")
    p_import.set_defaults(func=_cmd("import", _step_import))

    sub.add_parser("match-repertoire", help="match unmatched games against the repertoire").set_defaults(
        func=_cmd("match", _step_match)
    )

    p_an = sub.add_parser("analyze", help="Stockfish analysis of games needing it")
    p_an.add_argument("--workers", type=int)
    p_an.add_argument("--limit", type=int, help="analyse at most N games this run")
    p_an.add_argument("--game-id", type=int, action="append", help="only these games (may repeat)")
    p_an.set_defaults(func=_cmd("analyze", _step_analyze))

    sub.add_parser("generate-puzzles", help="reconcile generated puzzles with the evidence").set_defaults(
        func=_cmd("generate-puzzles", _step_generate_puzzles)
    )

    sub.add_parser("srs-maintain", help="un-retire mastered puzzles whose pattern has recurred").set_defaults(
        func=_cmd("srs-maintain", _step_srs_maintain)
    )

    p_opp = sub.add_parser("import-opponents", help="fetch scouted opponents' games (onboards new profiles)")
    p_opp.add_argument("--profile", type=int, help="only this profile id")
    p_opp.add_argument(
        "--reset-lichess-cursors",
        action="store_true",
        help="cutover only: walk every initialised profile's Lichess history once more, from the start",
    )
    p_opp.set_defaults(func=_cmd("import-opponents", _step_import_opponents))

    p_rev = sub.add_parser("review", help="retag the window's review events (the Review pages read them)")
    p_rev.add_argument("--workers", type=int, help="tagging processes (default 1)")
    p_rev.set_defaults(func=_cmd("review", _step_review))

    p_ev = sub.add_parser("position-evals", help="engine evaluation of each board Review's opening mistakes read")
    p_ev.add_argument(
        "--limit", dest="evals_limit", type=_at_least(0), help="at most N positions (default 40; 0 = all)"
    )
    p_ev.add_argument("--workers", type=_at_least(1), help="engine processes (default 1)")
    p_ev.set_defaults(func=_cmd("position-evals", _step_position_evals))
    p_snap = sub.add_parser("review-snapshot", help="store Review's position sections for every time class and opening")
    p_snap.set_defaults(func=_cmd("review-snapshot", _step_review_snapshot))

    p_bf = sub.add_parser("backfill-openings", help="opening prefix for stored games that have none (re-fetches)")
    p_bf.add_argument("--months", type=_at_least(1), help="how far back (default: review_history_months)")
    p_bf.set_defaults(func=_cmd("backfill-openings", _step_backfill_openings))

    p_corpus = sub.add_parser("import-corpus", help="rebuild the Lichess CC0 corpus sample from the published CSV")
    p_corpus.add_argument("--csv", required=True, help="the decompressed lichess_db_puzzle.csv")
    p_corpus.set_defaults(func=_cmd("import-corpus", _step_import_corpus))

    p_rep = sub.add_parser("import-repertoire", help="load a repertoire file (books, chapters, lines, notes)")
    p_rep.add_argument("file")
    p_rep.add_argument("--mode", choices=["update", "scratch"], required=True)
    p_rep.add_argument("--preserve-manual", action="store_true", help="never overwrite a note written by hand")
    p_rep.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")
    p_rep.set_defaults(func=_cmd("import-repertoire", _step_import_repertoire))

    sub.add_parser("housekeep", help="retention outside the analysis window").set_defaults(
        func=_cmd("housekeep", _step_housekeep)
    )
    p_run = sub.add_parser("run", help="the hourly chain with an alert on failure")
    p_run.add_argument("--analyze-limit", type=int, help="cap on games analysed per run (runners have a time limit)")
    p_run.set_defaults(func=_run_all)
    p_re = sub.add_parser("reprocess", help="every game and board under the current engine; stops at a failure")
    p_re.add_argument("--workers", type=_at_least(1), help="engine processes for analyze, review and position-evals")
    p_re.set_defaults(func=_reprocess)
    p_es = sub.add_parser("engine-status", help="read-only: is the reprocess complete? (exit 1 when not)")
    p_es.add_argument("--since", help="batch boundary as an ISO timestamp (default: the latest schema change)")
    p_es.set_defaults(func=_engine_status)
    sub.add_parser("blunder-funnel", help="boards surviving each gate of the blunder generator").set_defaults(
        func=_blunder_funnel
    )
    p_alert = sub.add_parser("alert", help="send the failure email for a failure outside the chain")
    p_alert.add_argument("what", choices=sorted(ALERTS))
    p_alert.set_defaults(func=_alert)
    p_mig = sub.add_parser("migrate", help="copy the old database into this one (once)")
    p_mig.add_argument("--mapping", required=True, help="JSON file with the old schema's column renames and value maps")
    p_mig.add_argument("--only", help="comma-separated tables to migrate; the emptiness check applies to just those")
    p_mig.set_defaults(func=_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except notify.OperatorError as exc:  # written for the operator; safe to print in full
        print(f"pipeline {args.group}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # never a traceback on a public console
        print(f"pipeline {args.group}: FAILED ({notify.error_label(exc)})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
