"""The Home page: what to do today, and what changed since the last visit.

Everything here is read from tables other pages own; Home writes nothing. "New since your
last visit" is the number of recurring boards the Blunders list has never shown
(`seen_blunder_boards`, filled by that page, never by Home), so a new board keeps waiting on
the tile until it has been looked at. The predicate is core/blunders.py's, the same one that
marks the page's NEW chips; `players.blunders_seen_at` is only the "last looked" date. New
deviation patterns are the same model on core/deviations.py (`seen_deviations`).

**A day** is a calendar day in the `timezone` setting, for counts and streaks alike. A puzzle is
solved today when it has a correct attempt today; retries of the same puzzle count once. A
game counts when it was played today, whatever the variant — Chess960 counts here, because
Home measures playing, not analysis. The week starts on Monday. A streak
is the run of consecutive days that met the target, ending today if today already has,
otherwise ending yesterday: an unfinished day never breaks a streak.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, LiteralString

from psycopg import Connection

from core import blunders, deviations, runs
from core.constants import PLAYER_ID
from core.puzzles import serve
from core.settings import Settings


def streak(days: dict[date, int], target: int, today: date) -> int:
    """Consecutive days at or above `target`, counted back from today if today qualifies,
    otherwise from yesterday."""
    if target <= 0:
        return 0
    d = today if days.get(today, 0) >= target else today - timedelta(days=1)
    n = 0
    while days.get(d, 0) >= target:
        n += 1
        d -= timedelta(days=1)
    return n


def week_total(days: dict[date, int], today: date) -> int:
    """This week's total: Monday through today."""
    monday = today - timedelta(days=today.weekday())
    return sum(n for d, n in days.items() if monday <= d <= today)


def _per_day(conn: Connection[Any], sql: LiteralString, tz: str) -> dict[date, int]:
    return {r["d"]: int(r["n"]) for r in conn.execute(sql, {"pid": PLAYER_ID, "tz": tz}).fetchall()}


_PUZZLE_DAYS: LiteralString = """
SELECT (attempt_at AT TIME ZONE %(tz)s)::date AS d, count(DISTINCT puzzle_id) AS n
FROM puzzle_attempts WHERE player_id = %(pid)s AND solved GROUP BY 1
"""

_GAME_DAYS: LiteralString = """
SELECT (cg.played_at AT TIME ZONE %(tz)s)::date AS d, count(*) AS n
FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
WHERE pg.player_id = %(pid)s AND cg.played_at IS NOT NULL GROUP BY 1
"""


def _today(conn: Connection[Any], tz: str) -> date:
    row = conn.execute("SELECT (now() AT TIME ZONE %s)::date AS today", (tz,)).fetchone()
    assert row is not None
    return row["today"]


def page(conn: Connection[Any], config: Settings) -> dict[str, Any]:
    """Everything Home shows. Reads only."""
    since = blunders.seen_at(conn)
    dev_since = deviations.seen_at(conn)
    tz = config.timezone
    today = _today(conn, tz)
    puzzle_days = _per_day(conn, _PUZZLE_DAYS, tz)
    game_days = _per_day(conn, _GAME_DAYS, tz)
    return {
        "since": since.isoformat() if since is not None else None,
        "today": today.isoformat(),
        "timezone": tz,
        "new_blunders": blunders.new_count(
            conn, blunders.default_filters(config, mark_new=since is not None), config.time_class_focus
        ),
        "deviations_since": dev_since.isoformat() if dev_since is not None else None,
        "new_deviations": deviations.new_count(
            conn, deviations.default_filters(config, mark_new=dev_since is not None), config.time_class_focus
        ),
        "puzzles": {
            "due": serve.count_eligible(conn, config),
            "solved_today": puzzle_days.get(today, 0),
            "target": config.daily_puzzle_target,
            "streak": streak(puzzle_days, config.daily_puzzle_target, today),
        },
        "games": {
            "today": game_days.get(today, 0),
            "week": week_total(game_days, today),
            "target": config.daily_game_target,
            "streak": streak(game_days, config.daily_game_target, today),
        },
        "pipeline": runs.hourly_status(conn),
    }
