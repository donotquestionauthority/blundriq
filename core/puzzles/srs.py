"""Spaced repetition: the six-level ladder, one attempt's transition, and king demotion.

`pawn → knight → bishop → rook → queen → king`. King is mastery and is encoded only by
`next_show_at = 9999-12-31`, which is what makes the due predicate false for it. A puzzle
with no state row has never been attempted and is due; rows are created lazily on the
first attempt.

One attempt: the result is appended to a sliding window of the last few attempts. A solve
credits progress at the current level only once per local day (the anti-farm rule — the
attempt is still recorded), and at the threshold the level advances and the window and
counter reset. A fail demotes one level when the window holds enough fails (pawn floors,
king is exempt) and reschedules the puzzle soon. Only the first attempt of a play-through
scores; a retry within the same session leaves the ladder alone.

King demotion: a mastered puzzle un-retires when its pattern recurs in enough distinct
analysed games played after mastery. It depends only on newly analysed games and is
idempotent, so it is a pipeline step (`pipeline srs-maintain`), not a read-time prelude.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import LOCK_SRS_ATTEMPT, PLAYER_ID, SRS_LEVELS
from core.settings import Settings

LEVEL_INDEX = {level: i for i, level in enumerate(SRS_LEVELS)}
KING_SENTINEL = datetime(9999, 12, 31, tzinfo=UTC)


def due_predicate(state_alias: str = "pps") -> str:
    """SQL: the puzzle is due — never attempted, or not king and its time has come. Over a
    LEFT JOIN to player_puzzle_state. The Home page count and the serve filter both use it."""
    return (
        f"({state_alias}.puzzle_id IS NULL OR ({state_alias}.level <> 'king' AND {state_alias}.next_show_at <= NOW()))"
    )


def is_due(state: dict[str, Any] | None, now: datetime) -> bool:
    """The same rule in Python, for rows already fetched."""
    if state is None:
        return True
    return state["level"] != "king" and state["next_show_at"] <= now


def states(conn: Connection[Any], puzzle_ids: list[int]) -> dict[int, dict[str, Any]]:
    """{puzzle_id: state} for the ids that have a row. Never-attempted puzzles are absent."""
    if not puzzle_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT puzzle_id, level, correct_at_level, last_3_attempts, last_correct_date, next_show_at, updated_at"
            " FROM player_puzzle_state WHERE player_id = %s AND puzzle_id = ANY(%s)",
            (PLAYER_ID, puzzle_ids),
        )
        return {
            r["puzzle_id"]: {
                "level": r["level"],
                "correct_at_level": r["correct_at_level"],
                "last_3_attempts": list(r["last_3_attempts"] or []),
                "last_correct_date": r["last_correct_date"],
                "next_show_at": r["next_show_at"],
                "updated_at": r["updated_at"],
            }
            for r in cur.fetchall()
        }


def serialise(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    out = dict(state)
    for key in ("next_show_at", "last_correct_date", "updated_at"):
        if out.get(key) is not None:
            out[key] = out[key].isoformat()
    return out


def mastered_count(conn: Connection[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM player_puzzle_state pps JOIN puzzles p ON p.id = pps.puzzle_id"
            " WHERE pps.player_id = %s AND pps.level = 'king' AND p.active = TRUE AND p.player_id = %s",
            (PLAYER_ID, PLAYER_ID),
        )
        row = cur.fetchone()
    assert row is not None
    return int(row["n"])


def lock_attempt(conn: Connection[Any], puzzle_id: int) -> None:
    """The per-puzzle transaction lock. Taken before the attempt row is inserted, so a
    concurrent same-session request blocks before its row could be invisible to the
    session check under READ COMMITTED."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_SRS_ATTEMPT, puzzle_id))


def interval_hours(level: str, config: Settings) -> int:
    return int(getattr(config, f"srs_{level}_interval_hours"))


def apply_attempt(
    conn: Connection[Any],
    puzzle_id: int,
    solved: bool,
    *,
    attempt_row_id: int,
    session_id: str | None,
    config: Settings,
) -> dict[str, Any]:
    """Apply one attempt to the ladder and return the transition. Does not commit.

    `attempt_row_id` is the row the caller just inserted; a session that already has
    another row scored on its first attempt, and this one is a no-op ('unchanged').
    """
    lock_attempt(conn, puzzle_id)
    with conn.cursor() as cur:
        cur.execute("SELECT (NOW() AT TIME ZONE %s)::date AS today, NOW() AS now", (config.timezone,))
        clock = cur.fetchone()
        assert clock is not None
        today: date = clock["today"]
        now: datetime = clock["now"]
        cur.execute(
            "INSERT INTO player_puzzle_state (player_id, puzzle_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (PLAYER_ID, puzzle_id),
        )
        cur.execute(
            "SELECT level, correct_at_level, last_3_attempts, last_correct_date"
            " FROM player_puzzle_state WHERE player_id = %s AND puzzle_id = %s",
            (PLAYER_ID, puzzle_id),
        )
        state = cur.fetchone()
        assert state is not None
        already_scored = False
        if session_id is not None:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM puzzle_attempts WHERE player_id = %s AND puzzle_id = %s"
                " AND session_id = %s AND id <> %s) AS scored",
                (PLAYER_ID, puzzle_id, session_id, attempt_row_id),
            )
            scored = cur.fetchone()
            already_scored = bool(scored and scored["scored"])

    level = str(state["level"])
    correct = int(state["correct_at_level"])
    window: list[bool] = [bool(a) for a in cast(list[object], state["last_3_attempts"] or [])]
    last_correct_date: date | None = state["last_correct_date"]
    threshold = config.srs_advance_threshold

    def transition(new_level: str, new_correct: int) -> dict[str, Any]:
        if LEVEL_INDEX[new_level] > LEVEL_INDEX[level]:
            outcome = "promoted"
        elif LEVEL_INDEX[new_level] < LEVEL_INDEX[level]:
            outcome = "demoted"
        elif new_correct > correct:
            outcome = "advanced"
        else:
            outcome = "unchanged"
        return {
            "prev_level": level,
            "new_level": new_level,
            "prev_correct_at_level": correct,
            "new_correct_at_level": new_correct,
            "advance_threshold": threshold,
            "outcome": outcome,
        }

    if already_scored:
        return transition(level, correct)

    window.append(solved)
    window = window[-config.srs_drop_window :]
    new_level, new_correct, new_window = level, correct, window
    new_last_correct: date | None = last_correct_date
    next_show: datetime
    if solved:
        if last_correct_date is None or last_correct_date < today:
            new_correct = correct + 1
            new_last_correct = today
        if new_correct >= threshold and level != "king":
            new_level = SRS_LEVELS[LEVEL_INDEX[level] + 1]
            new_correct = 0
            new_window = []
        next_show = KING_SENTINEL if new_level == "king" else now + timedelta(hours=interval_hours(new_level, config))
    else:
        if sum(1 for a in new_window if not a) >= config.srs_drop_wrongs and level not in ("pawn", "king"):
            new_level = SRS_LEVELS[LEVEL_INDEX[level] - 1]
            new_correct = 0
            new_window = []
        next_show = KING_SENTINEL if new_level == "king" else now + timedelta(hours=config.srs_wrong_retry_hours)

    conn.execute(
        "UPDATE player_puzzle_state SET level = %s, correct_at_level = %s, last_3_attempts = %s,"
        " last_correct_date = %s, next_show_at = %s, updated_at = NOW() WHERE player_id = %s AND puzzle_id = %s",
        (new_level, new_correct, new_window, new_last_correct, next_show, PLAYER_ID, puzzle_id),
    )
    return transition(new_level, new_correct)


# --- king demotion (pipeline step) ------------------------------------------------------

_KING_HITS = cast(
    LiteralString,
    f"""
WITH kings AS (
    SELECT pps.puzzle_id, pps.updated_at, p.canonical_fen, p.is_repertoire, p.repertoire_line_id
    FROM player_puzzle_state pps JOIN puzzles p ON p.id = pps.puzzle_id
    WHERE pps.player_id = %(pid)s AND pps.level = 'king' AND p.active = TRUE AND p.player_id = %(pid)s
),
lookback AS (
    SELECT pg.chess_game_id, cg.played_at
    FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
    WHERE pg.player_id = %(pid)s AND pg.analyzed_at_depth IS NOT NULL AND {analysable_sql("cg")}
    ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC
    LIMIT %(lookback)s
),
standard_hits AS (
    SELECT k.puzzle_id, count(DISTINCT h.chess_game_id) AS hits
    FROM kings k
    JOIN (SELECT canonical_fen, chess_game_id FROM blunders WHERE player_id = %(pid)s
          UNION ALL
          SELECT canonical_fen, chess_game_id FROM game_repertoire_results
          WHERE player_id = %(pid)s AND deviation_by = 'me') h ON h.canonical_fen = k.canonical_fen
    JOIN lookback lb ON lb.chess_game_id = h.chess_game_id
    WHERE k.is_repertoire = FALSE AND lb.played_at > k.updated_at
    GROUP BY k.puzzle_id
),
repertoire_hits AS (
    SELECT k.puzzle_id, count(DISTINCT grr.chess_game_id) AS hits
    FROM kings k
    JOIN game_result_lines grl ON grl.line_id = k.repertoire_line_id
    JOIN game_repertoire_results grr ON grr.id = grl.game_repertoire_result_id
         AND grr.player_id = %(pid)s AND grr.deviation_by = 'me' AND grr.deviated_at_ply IS NOT NULL
    JOIN lookback lb ON lb.chess_game_id = grr.chess_game_id
    WHERE k.is_repertoire = TRUE AND lb.played_at > k.updated_at
    GROUP BY k.puzzle_id
)
SELECT k.puzzle_id, k.updated_at, COALESCE(s.hits, r.hits, 0) AS hits
FROM kings k
LEFT JOIN standard_hits s ON s.puzzle_id = k.puzzle_id
LEFT JOIN repertoire_hits r ON r.puzzle_id = k.puzzle_id
WHERE COALESCE(s.hits, r.hits, 0) >= %(min_hits)s
ORDER BY k.puzzle_id
""",
)


def demote_kings(conn: Connection[Any], config: Settings) -> dict[str, int]:
    """Un-retire mastered puzzles whose pattern has recurred since mastery. Idempotent: a
    demoted row is no longer king, and a king whose count is unchanged is left alone. The
    write is guarded on `updated_at` so a concurrent attempt is never clobbered."""
    with conn.cursor() as cur:
        cur.execute(
            _KING_HITS,
            {
                "pid": PLAYER_ID,
                "lookback": config.srs_king_demotion_lookback_games,
                "min_hits": config.srs_king_demotion_min_hits,
            },
        )
        candidates = cur.fetchall()
        demoted = 0
        for row in candidates:
            lock_attempt(conn, int(row["puzzle_id"]))
            cur.execute(
                "UPDATE player_puzzle_state SET level = 'pawn', correct_at_level = 0, last_3_attempts = '{}',"
                " next_show_at = NOW(), updated_at = NOW()"
                " WHERE player_id = %s AND puzzle_id = %s AND level = 'king' AND updated_at = %s",
                (PLAYER_ID, row["puzzle_id"], row["updated_at"]),
            )
            demoted += cur.rowcount
    return {"candidates": len(candidates), "demoted": demoted}


# --- attempt summaries -------------------------------------------------------------------

_SESSIONS = """
    WITH sessions AS (
        SELECT DISTINCT ON (puzzle_id, COALESCE(session_id::text, id::text))
               puzzle_id, solved, attempt_at, id
        FROM puzzle_attempts
        WHERE player_id = %(pid)s AND puzzle_id = ANY(%(ids)s)
        ORDER BY puzzle_id, COALESCE(session_id::text, id::text), attempt_at ASC, id ASC
    )
"""


def attempt_summaries(conn: Connection[Any], puzzle_ids: list[int]) -> dict[int, dict[str, Any]]:
    """{puzzle_id: {total, solved, last_attempt_at, streak}} over play-throughs.

    A play-through is one session_id, and its first outcome is the one that counts: a
    right answer after a wrong guess is not a solve. `streak` is signed — the run of
    consecutive same-result play-throughs ending with the latest, positive for solved.
    """
    if not puzzle_ids:
        return {}
    params = {"pid": PLAYER_ID, "ids": puzzle_ids}
    with conn.cursor() as cur:
        cur.execute(
            _SESSIONS + "SELECT puzzle_id, count(*) AS total, count(*) FILTER (WHERE solved) AS solved,"
            " max(attempt_at) AS last_attempt_at FROM sessions GROUP BY puzzle_id",
            params,
        )
        totals = cur.fetchall()
        cur.execute(
            _SESSIONS
            + """,
            ordered AS (
                SELECT puzzle_id, solved,
                       row_number() OVER (PARTITION BY puzzle_id ORDER BY attempt_at DESC, id DESC) AS rn
                FROM sessions
            ),
            tagged AS (
                SELECT puzzle_id, solved, rn,
                       CASE WHEN lag(solved) OVER (PARTITION BY puzzle_id ORDER BY rn) IS DISTINCT FROM solved
                                 AND rn > 1 THEN 1 ELSE 0 END AS boundary
                FROM ordered
            ),
            grouped AS (
                SELECT puzzle_id, solved,
                       sum(boundary) OVER (PARTITION BY puzzle_id ORDER BY rn ROWS UNBOUNDED PRECEDING) AS grp
                FROM tagged
            )
            SELECT puzzle_id, count(*) AS streak_len, bool_and(solved) AS streak_solved
            FROM grouped WHERE grp = 0 GROUP BY puzzle_id
            """,
            params,
        )
        streaks = {
            int(r["puzzle_id"]): (int(r["streak_len"]) if r["streak_solved"] else -int(r["streak_len"]))
            for r in cur.fetchall()
        }
    return {
        r["puzzle_id"]: {
            "total": int(r["total"]),
            "solved": int(r["solved"]),
            "last_attempt_at": r["last_attempt_at"].isoformat() if r["last_attempt_at"] else None,
            "streak": streaks.get(r["puzzle_id"], 0),
        }
        for r in totals
    }


EMPTY_SUMMARY: dict[str, Any] = {"total": 0, "solved": 0, "last_attempt_at": None, "streak": 0}


def post_attempt_state(conn: Connection[Any], puzzle_id: int, config: Settings) -> dict[str, Any]:
    """What the attempt response reports after the write: level, progress and the summary.
    Read inside the same transaction as the write it reports on."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT level, correct_at_level FROM player_puzzle_state WHERE player_id = %s AND puzzle_id = %s",
            (PLAYER_ID, puzzle_id),
        )
        state = cur.fetchone()
    summary = attempt_summaries(conn, [puzzle_id]).get(puzzle_id, EMPTY_SUMMARY)
    return {
        "level": state["level"] if state else "pawn",
        "correct_at_level": int(state["correct_at_level"]) if state else 0,
        "advance_threshold": config.srs_advance_threshold,
        "total": summary["total"],
        "solved": summary["solved"],
        "streak": summary["streak"],
    }
