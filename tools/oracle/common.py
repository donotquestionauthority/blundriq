"""Shared helpers for the oracle diff scripts (connections, sample, reporting)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import DictRow, dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import secrets  # noqa: E402

SAMPLE = Path(__file__).with_name("sample.json")


def scratch() -> psycopg.Connection[DictRow]:
    return psycopg.Connection[DictRow].connect(secrets.database().database_url, row_factory=dict_row)


def oracle() -> psycopg.Connection[DictRow]:
    return psycopg.Connection[DictRow].connect(secrets.oracle().oracle_database_url, row_factory=dict_row)


def sample_ids() -> list[int]:
    """Distinct sample ids in a stable order (an edge case may repeat a random pick)."""
    data = json.loads(SAMPLE.read_text())
    seen: dict[int, None] = {}
    for i in [*data["random"], *data["edge_cases"].values()]:
        seen[int(i)] = None
    return list(seen)


def report(title: str, diffs: list[str], checked: int) -> int:
    games = {d.split(":", 1)[0].split(" ply", 1)[0] for d in diffs}
    print(f"\n== {title}: {checked} entries checked, {len(games)} with {len(diffs)} field differences")
    for d in diffs[:200]:
        print("  " + d)
    if len(diffs) > 200:
        print(f"  ... {len(diffs) - 200} more")
    return 1 if diffs else 0
