"""`pipeline` — the one command-line entry point for everything that is not the API.

    pipeline db init | upgrade | version         schema
    pipeline settings show | seed FILE | schema  the settings row
    pipeline player set --chesscom U --lichess U the one players row (usernames)
    pipeline import [--platform P] [--all] [--months N]
    pipeline match-repertoire
    pipeline analyze [--workers N] [--limit N] [--game-id ID ...]
    pipeline housekeep
    pipeline run [--analyze-limit N]              import → match → analyze → housekeep, logged, alert on failure
    pipeline migrate --mapping FILE               old database (ORACLE_DATABASE_URL) → this one (once)

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
from core.ingest import run as ingest
from core.repertoire import matching


def _db_init(_: argparse.Namespace) -> int:
    with db.connect() as conn:
        version = schema.init(conn)
    print(f"initialised; schema version {version}")
    return 0


def _db_upgrade(_: argparse.Namespace) -> int:
    with db.connect() as conn:
        applied = schema.upgrade(conn)
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
    with db.connect() as conn:
        settings.save(conn, values)
    print(f"settings saved ({len(data)} keys given, {len(values.model_fields)} fields stored)")
    return 0


def _settings_schema(_: argparse.Namespace) -> int:
    print(json.dumps(settings.schema(), indent=2))
    return 0


def _player_set(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        names = player.set_usernames(conn, args.chesscom, args.lichess)
    print(f"player: chesscom={names['chesscom']} lichess={names['lichess']}")
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


def _run_all(args: argparse.Namespace) -> int:
    """The hourly order. A failed step stops the chain (its alert already went out)."""
    args.alert = True
    args.limit = args.analyze_limit
    for name, step in (
        ("import", _step_import),
        ("match", _step_match),
        ("analyze", _step_analyze),
        ("housekeep", _step_housekeep),
    ):
        if _run_step(name, step, args) != 0:
            return 1
    return 0


def _migrate(args: argparse.Namespace) -> int:
    from core import secrets

    src_url = secrets.oracle().oracle_database_url
    mapping = migrate.Mapping.load(Path(args.mapping))
    with psycopg.Connection[DictRow].connect(src_url, row_factory=dict_row) as src, db.connect() as dst:
        counts = migrate.migrate(src, dst, mapping)
    for table, n in counts.items():
        print(f"{table:28s} {n}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="group", required=True)

    p_db = sub.add_parser("db", help="schema install and upgrade")
    db_sub = p_db.add_subparsers(dest="cmd", required=True)
    db_sub.add_parser("init").set_defaults(func=_db_init)
    db_sub.add_parser("upgrade").set_defaults(func=_db_upgrade)
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

    sub.add_parser("housekeep", help="retention outside the analysis window").set_defaults(
        func=_cmd("housekeep", _step_housekeep)
    )
    p_run = sub.add_parser("run", help="the hourly chain with an alert on failure")
    p_run.add_argument("--analyze-limit", type=int, help="cap on games analysed per run (runners have a time limit)")
    p_run.set_defaults(func=_run_all)
    p_mig = sub.add_parser("migrate", help="copy the old database into this one (once)")
    p_mig.add_argument("--mapping", required=True, help="JSON file with the old schema's column renames and value maps")
    p_mig.set_defaults(func=_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # never a traceback on a public console
        print(f"pipeline {args.group}: FAILED ({notify.error_label(exc)})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
