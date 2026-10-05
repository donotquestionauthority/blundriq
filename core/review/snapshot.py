"""Review's position sections, computed once an hour and read back by the page.

Aggregating every game's opening prefix takes seconds on the production database, and its
inputs change only when games are imported, evaluations are added or the settings change.
`pipeline review-snapshot` (the hourly chain's last step) computes, for each time class and
every opening the page offers there, exactly what the page would: `mistakes.section`,
`positions.ranked_positions`, `positions.meta` and the opening options. The page reads the row
for its filters and uses it only when its fingerprint matches what the page would compute from
now; any difference (a new game, a changed game, a new evaluation, a changed setting, other
code) and the page computes the sections itself. So a snapshot can only be slow to arrive, never out of date: a
failed hourly run (the chain stops at a failed step) leaves the page computing for itself.
"""

from __future__ import annotations

import functools
import hashlib
import importlib
import inspect
import json
import pkgutil
import time
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import PLAYER_ID
from core.review import mistakes, positions
from core.review.filters import OPENING_ALL, TIME_CLASSES, parse_opening, time_class_sql
from core.settings import Settings


def stamped_modules() -> list[str]:
    """Every module of `core.review` (this one included) and `core.chess.board`: whatever the
    sections are computed by is in the stamp, and a module added later is in it without a
    change here."""
    import core.review

    names = sorted(f"core.review.{m.name}" for m in pkgutil.iter_modules(core.review.__path__))
    return ["core.review", *names, "core.chess.board"]


# The settings the sections read, by value. tests/review/test_snapshot.py fails if a field the
# section code reads is missing here.
SETTINGS_READ = (
    "time_class_focus",
    "review_history_months",
    "review_recency_half_life_days",
    "review_position_min_games",
    "review_position_max_ply",
    "review_min_costly_games",
    "review_mistake_floor_es",
)


@functools.cache
def _code_stamp() -> str:
    """A digest of the code and constants the sections are computed by, so a snapshot written by
    other code is never served: the source of `stamped_modules` and every Review and
    opening-prefix constant. Read from the installed modules at the first request."""
    from core import constants

    digest = hashlib.sha256()
    for name in stamped_modules():
        digest.update(inspect.getsource(importlib.import_module(name)).encode())
    for name in sorted(vars(constants)):
        if name.startswith(("REVIEW_", "OPENING_")):
            digest.update(f"{name}={getattr(constants, name)!r}".encode())
    return digest.hexdigest()[:16]


def fingerprint(conn: Connection[Any], config: Settings, time_class: str) -> str:
    """Everything the position sections under `time_class` are computed from, as one string:
    the code (`_code_stamp`), the schema version (a migration that rewrites stored rows), the
    settings they read (`SETTINGS_READ`, by value), `as_of`, a digest of every analysable game
    of the player's as the sections see it (when, which side, time class, family, result, ratings,
    whether it has a prefix; a prefix itself is written once and only a migration rewrites it),
    and a digest of the evaluations (score, best move and terminal outcome). Any change to an
    input changes the fingerprint, so a stored row whose fingerprint matches is what the page
    would compute now."""
    in_class = time_class_sql(time_class, config.time_class_focus)
    query = cast(
        LiteralString,
        f"""
        SELECT max(cg.played_at) FILTER (WHERE {in_class}) AS as_of, count(*) AS games,
               sum(hashtextextended(concat_ws('|', cg.id, cg.played_at, cg.time_class, cg.canonical_family,
                                               cg.opening_keys IS NULL, pg.player_color, pg.result,
                                               pg.player_rating, pg.opponent_rating), 0)) AS games_digest,
               (SELECT sum(hashtextextended(concat_ws('|', board_key, eval_cp, mate_in, best_move, terminal), 0))
                  FROM position_evals) AS evals_digest,
               (SELECT max(version) FROM schema_version) AS schema_version
        FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")}
        """,
    )
    found = conn.execute(query, {"pid": PLAYER_ID}).fetchone()
    row: dict[str, Any] = dict(found) if found else {}
    as_of = row.get("as_of")
    return json.dumps(
        {
            "code": _code_stamp(),
            "schema_version": row.get("schema_version"),
            "time_class": time_class,
            "settings": {name: getattr(config, name) for name in SETTINGS_READ},
            "as_of": as_of.isoformat() if as_of is not None else None,
            "games": row.get("games"),
            "games_digest": str(row.get("games_digest")),
            "evals_digest": str(row.get("evals_digest")),
        },
        sort_keys=True,
    )


def _jsonable(meta: dict[str, Any]) -> dict[str, Any]:
    out = dict(meta)
    if out.get("as_of") is not None:
        out["as_of"] = out["as_of"].isoformat()
    return out


def sections(conn: Connection[Any], config: Settings, time_class: str, opening: str) -> dict[str, Any]:
    """What the page needs for one filter pair, computed now: the opening mistakes, the results
    section's positions and meta (`as_of` as ISO text)."""
    scope = positions.Scope(config, time_class, parse_opening(opening))
    return {
        "mistakes": mistakes.section(conn, scope),
        "positions": positions.ranked_positions(conn, scope),
        "meta": _jsonable(positions.meta(conn, scope)),
    }


def build(conn: Connection[Any], config: Settings) -> dict[str, Any]:
    """`replace`, committed: the hourly step."""
    summary = replace(conn, config)
    conn.commit()
    return summary


def replace(conn: Connection[Any], config: Settings) -> dict[str, Any]:
    """Replace every snapshot row, uncommitted: for each time class, all openings and each
    offered opening. One transaction, so the page sees the old rows or the new ones. Each
    fingerprint is taken before the sections it stamps, so a game that arrives meanwhile makes
    the row read as stale, never as current."""
    started = time.monotonic()
    rows: list[tuple[str, str, str, str]] = []
    for time_class in TIME_CLASSES:
        stamp = fingerprint(conn, config, time_class)
        options = positions.opening_options(conn, config, time_class)
        for opening in [OPENING_ALL, *(o["key"] for o in options)]:
            body = sections(conn, config, time_class, opening)
            if opening == OPENING_ALL:
                body["openings"] = options
            rows.append((time_class, opening, stamp, json.dumps(body)))
    with conn.transaction():
        conn.execute("DELETE FROM review_snapshots")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO review_snapshots (time_class, opening, fingerprint, body) VALUES (%s, %s, %s, %s::jsonb)",
                rows,
            )
    return {"snapshots": len(rows), "seconds": round(time.monotonic() - started, 1)}


def read(conn: Connection[Any], config: Settings, time_class: str, opening: str) -> dict[str, Any] | None:
    """The stored sections for the filter pair, with the time class's opening options, when both
    rows are there and still describe the data; else None (the caller computes them)."""
    rows = conn.execute(
        "SELECT opening, fingerprint, body FROM review_snapshots WHERE time_class = %s AND opening = ANY(%s)",
        (time_class, [OPENING_ALL, opening]),
    ).fetchall()
    by_opening = {r["opening"]: r for r in rows}
    if OPENING_ALL not in by_opening or opening not in by_opening:
        return None
    stamp = fingerprint(conn, config, time_class)
    if by_opening[OPENING_ALL]["fingerprint"] != stamp or by_opening[opening]["fingerprint"] != stamp:
        return None
    body: dict[str, Any] = dict(by_opening[opening]["body"])
    body["openings"] = by_opening[OPENING_ALL]["body"]["openings"]
    return body
