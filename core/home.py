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
otherwise ending yesterday: an unfinished day never breaks a streak. The activity strip
(games in the last 24 h / 7 d / 30 d / ever) is `core.activity`, the same counts Scout shows
for an opponent. The Practice page's count of today's puzzles (`puzzles_today`) is the same
solved number, plus the puzzles tried, and Home reads its `solved_today` from it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, LiteralString

from psycopg import Connection

from core import activity, blunders, deviations, runs
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


# A day is the set of instants whose local date (in `tz`) is that day, exactly as `_PUZZLE_DAYS`
# buckets them, so Home's streak and these counts never disagree. The time range is only a
# prefilter, widened because a clock change can repeat or skip local midnight. The next day
# starts at the EARLIEST instant whose local date is tomorrow: when clocks go back across
# midnight, `(tomorrow)::timestamp AT TIME ZONE tz` names the later of the two midnights, so the
# 15-minute steps before it are searched too (every zone's offsets differ in quarter hours).
_TODAY_COUNTS: LiteralString = """
WITH d AS (
    SELECT COALESCE(%(at)s::timestamptz, now()) AS at,
           (COALESCE(%(at)s::timestamptz, now()) AT TIME ZONE %(tz)s)::date AS today
),
b AS (
    SELECT at, today,
           today::timestamp AT TIME ZONE %(tz)s - interval '3 hours' AS lo,
           (today + 1)::timestamp AT TIME ZONE %(tz)s AS late_end
    FROM d
),
e AS (
    SELECT b.*, (
        SELECT min(t) FROM generate_series(late_end - interval '3 hours', late_end, interval '15 minutes') t
        WHERE (t AT TIME ZONE %(tz)s)::date = today + 1
    ) AS next_day_at
    FROM b
)
SELECT e.today, e.next_day_at, e.at,
       count(DISTINCT a.puzzle_id) FILTER (WHERE a.solved) AS solved,
       count(DISTINCT a.puzzle_id) AS tried
FROM e LEFT JOIN puzzle_attempts a
  ON a.player_id = %(pid)s
 AND a.attempt_at >= e.lo AND a.attempt_at < e.late_end + interval '3 hours'
 AND (a.attempt_at AT TIME ZONE %(tz)s)::date = e.today
GROUP BY e.today, e.next_day_at, e.at
"""


def _today_counts(conn: Connection[Any], tz: str, at: datetime | None = None) -> dict[str, Any]:
    """Today's distinct puzzles solved and tried, the day taken at `at` (default: now) in `tz`,
    the instant the next day starts there, and the server's own clock (`now`), so a client
    can time the rollover without trusting its own."""
    row = conn.execute(_TODAY_COUNTS, {"at": at, "tz": tz, "pid": PLAYER_ID}).fetchone()
    assert row is not None
    return {
        "date": row["today"].isoformat(),
        "solved": int(row["solved"]),
        "tried": int(row["tried"]),
        "next_day_at": row["next_day_at"].astimezone(UTC).isoformat(),
        "now": row["at"].astimezone(UTC).isoformat(),
    }


def puzzles_today(conn: Connection[Any], config: Settings) -> dict[str, Any]:
    """Today's puzzle count for the Practice page: distinct puzzles solved (Home's number) and
    tried (any attempt, right or wrong), the daily target, when the day rolls over, and the
    server's clock."""
    return {**_today_counts(conn, config.timezone), "target": config.daily_puzzle_target}


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
            "solved_today": _today_counts(conn, tz)["solved"],
            "target": config.daily_puzzle_target,
            "streak": streak(puzzle_days, config.daily_puzzle_target, today),
        },
        "games": {
            "today": game_days.get(today, 0),
            "week": week_total(game_days, today),
            "target": config.daily_game_target,
            "streak": streak(game_days, config.daily_game_target, today),
        },
        "activity": activity.counts(conn, activity.Player()),
        "pipeline": runs.hourly_status(conn),
    }
