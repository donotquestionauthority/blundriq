"""`pipeline` — the one command-line entry point for everything that is not the API.

    pipeline db init                 fresh database: load schema.sql, record baseline
    pipeline db upgrade              apply pending migrations
    pipeline db version              print recorded and latest versions
    pipeline settings show           print the current settings row (or defaults)
    pipeline settings seed FILE      load settings from a JSON file (validated), replacing the row
    pipeline settings schema         print the settings JSON schema

Pipeline steps (import, analyze, generate, housekeep) are added in later phases,
each as a subcommand here, each idempotent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core import db, schema, settings


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
