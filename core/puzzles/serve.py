"""The play queue: batches, what is pending, skipping, and the corpus rotation.

A **batch** is minted per *scope* (the Type/SubType filter, 'all' by default) and persisted
as one `player_puzzle_exposure` row per item; the row's serial id is the serve order. The
batch is composed from the five buckets in `core.constants`: the raw configured
percentages over the buckets that can supply something right now, allocated by largest
remainder, each bucket filled to its target, the residual handed round-robin to whatever
still supplies, and the whole batch shuffled once. A bucket that could not supply this
batch gets no catch-up next time — repaying a missed share is how one bucket floods the
queue.

An item is **acknowledged** when an attempt on it exists at or after it was served, or a
skip row names it in its batch. **Pending** is not acknowledged and still visible,
recomputed on every serve and never stored, ordered oldest first — that order *is* the
carry-over. The next batch is minted when nothing is pending, or when few enough items
are pending and fewer than two batches are; one advisory lock serialises the mint so a
second tab reads the same batch instead of minting another.

Corpus puzzles are drawn inside a rating window placed around the player's latest rating,
uniformly from the most popular candidates the player has not *seen* — attempted, skipped
or dismissed at that position. Ownership is not seenness: a corpus puzzle materialised
earlier but never acknowledged is fresh again and is served from its existing row. A
corpus puzzle is not on the ladder; an attempt moves it to the back of the rotation.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, LiteralString, cast

from psycopg import Connection

from core import constants
from core.chess.eligibility import evidence_sql
from core.constants import (
    BUCKET_MOTIFS_FIRST_CLASS,
    BUCKET_OWN_MISSED_MATE,
    BUCKET_YOUR_PUZZLES,
    LOCK_PUZZLE_QUEUE,
    MINT_AHEAD_THRESHOLD,
    MOTIF_THEME_VOCAB,
    PENDING_BATCH_DEPTH_CAP,
    PLAYER_ID,
    PUZZLE_MIX_BUCKETS,
    ROTATION_BUCKET_THEMES,
    ROTATION_BUCKETS,
    ROTATION_FIRST_CLASS_THEMES,
    ROTATION_ROUTING_ORDER,
    SRS_BUCKETS,
)
from core.puzzles import srs, visibility
from core.puzzles.lines import fen_sequence
from core.settings import Settings

CC0_SOURCE = "lichess_cc0"
OWN_MATE_SOURCE = "own_mate"
PTYPES = ("all", "motif", "repertoire", "blunder", "custom")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# --- classification -----------------------------------------------------------------------


def rotation_bucket_for_themes(themes: list[str] | None) -> str | None:
    """The rotation bucket a corpus puzzle belongs to, by the first theme class it overlaps."""
    have = set(themes or [])
    for bucket in ROTATION_ROUTING_ORDER:
        if have & ROTATION_BUCKET_THEMES[bucket]:
            return bucket
    return None


def _sources(row: dict[str, Any]) -> list[str]:
    return [str(t) for t in cast(list[object], row.get("source_types") or [])]


def _themes(row: dict[str, Any]) -> list[str]:
    return [str(t) for t in cast(list[object], row.get("themes") or [])]


def bucket_of(row: dict[str, Any]) -> str:
    sources = _sources(row)
    if OWN_MATE_SOURCE in sources:
        return BUCKET_OWN_MISSED_MATE
    if CC0_SOURCE in sources:
        return rotation_bucket_for_themes(_themes(row)) or BUCKET_MOTIFS_FIRST_CLASS
    return BUCKET_YOUR_PUZZLES


def matches_type(row: dict[str, Any], ptype: str) -> bool:
    if ptype == "all":
        return True
    sources = _sources(row)
    is_rep = bool(row.get("is_repertoire"))
    if ptype == "repertoire":
        return is_rep
    if ptype == "blunder":
        return not is_rep and "blunder" in sources
    if ptype == "custom":
        return not is_rep and "custom" in sources
    if ptype == "motif":
        return not is_rep and "blunder" not in sources and bool(_themes(row))
    return False


def matches_subtype(row: dict[str, Any], ptype: str, subtype: str | None) -> bool:
    if not subtype:
        return True
    if ptype in ("motif", "blunder"):
        return subtype in _themes(row)
    if ptype == "repertoire":
        return str(row.get("repertoire_line_id")) == str(subtype)
    return True


def scope_of(ptype: str, subtype: str | None) -> str:
    """The batch lifecycle key for a filter: 'all', 'motif', 'motif:fork', 'repertoire:12'."""
    if ptype == "all":
        return "all"
    return f"{ptype}:{subtype}" if subtype else ptype


def lookahead_plies(config: Settings) -> int:
    return 2 * int(config.repertoire_puzzle_lookahead_moves)


def mint_ahead_threshold(config: Settings) -> int:
    """Clamped to [1, batch_size): a batch of one cannot look ahead."""
    return max(1, min(MINT_AHEAD_THRESHOLD, max(1, config.puzzle_mix_batch_size) - 1))


# --- ordering -----------------------------------------------------------------------------

_RANK_BLUNDER, _RANK_DEVIATION, _RANK_MOTIF, _RANK_SCOUT = 0, 1, 2, 3


def _source_rank(row: dict[str, Any]) -> int:
    breakdown = row.get("source_breakdown")
    if isinstance(breakdown, dict):
        if "blunder" in breakdown:
            return _RANK_BLUNDER
        if "deviation" in breakdown:
            return _RANK_DEVIATION
    sources = _sources(row)
    if OWN_MATE_SOURCE in sources:
        return _RANK_BLUNDER
    if CC0_SOURCE in sources:
        return _RANK_MOTIF
    return _RANK_SCOUT


def sort_key(row: dict[str, Any], now: datetime) -> tuple[int, int, int, int, int]:
    """Ready-now before scheduled; own blunders before deviations before corpus before
    scout; lower level first; more frequent first; within deviations, more often wrong first."""
    state = row.get("srs")
    rank = _source_rank(row)
    summary = cast(dict[str, Any], row.get("attempt_summary") or {})
    wrong = max(0, int(summary.get("total", 0)) - int(summary.get("solved", 0)))
    wrong_key = -wrong if rank == _RANK_DEVIATION else 0
    occurrence = int(row.get("occurrence_count") or 0)
    if state is None:
        return (0, rank, 0, -occurrence, 0)
    ready = 0 if (state["next_show_at"] <= now or state["level"] == "king") else 1
    return (ready, rank, srs.LEVEL_INDEX.get(state["level"], 0), -occurrence, wrong_key)


# --- acknowledgement, pending, skip ---------------------------------------------------------

# Bound to an exposure row alias `e`. An attempt acknowledges only from the moment the item
# was served, so a months-old attempt cannot consume a fresh batch item.
_ACKNOWLEDGED = """(
    EXISTS (SELECT 1 FROM puzzle_attempts pa
            WHERE pa.player_id = e.player_id AND pa.puzzle_id = e.puzzle_id AND pa.attempt_at >= e.served_at)
    OR EXISTS (SELECT 1 FROM player_puzzle_skip s
               WHERE s.player_id = e.player_id AND s.scope = e.scope AND s.batch_id = e.batch_id
                 AND s.puzzle_id = e.puzzle_id)
)"""


@dataclass(frozen=True)
class Pending:
    batch_id: int
    exposure_id: int
    puzzle_id: int


def pending_members(conn: Connection[Any], scope: str, visible_ids: set[int]) -> list[Pending]:
    """The scope's pending items across all its batches, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT e.batch_id, e.id AS exposure_id, e.puzzle_id FROM player_puzzle_exposure e"
            f" WHERE e.player_id = %s AND e.scope = %s AND NOT {_ACKNOWLEDGED} ORDER BY e.batch_id, e.id",
            (PLAYER_ID, scope),
        )
        return [
            Pending(int(r["batch_id"]), int(r["exposure_id"]), int(r["puzzle_id"]))
            for r in cur.fetchall()
            if r["puzzle_id"] in visible_ids
        ]


def _all_pending_ids(conn: Connection[Any]) -> set[int]:
    """Pending across every scope: a puzzle pending in one queue is not minted into another."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT e.puzzle_id FROM player_puzzle_exposure e WHERE e.player_id = %s AND NOT {_ACKNOWLEDGED}",
            (PLAYER_ID,),
        )
        return {int(r["puzzle_id"]) for r in cur.fetchall()}


def _acknowledged(conn: Connection[Any], scope: str, batch_id: int, puzzle_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT EXISTS (SELECT 1 FROM player_puzzle_exposure e WHERE e.player_id = %s AND e.scope = %s"
            f" AND e.batch_id = %s AND e.puzzle_id = %s AND {_ACKNOWLEDGED}) AS acked",
            (PLAYER_ID, scope, batch_id, puzzle_id),
        )
        row = cur.fetchone()
    return bool(row and row["acked"])


def skip(conn: Connection[Any], scope: str, batch_id: int, puzzle_id: int, config: Settings) -> str:
    """Record a skip and say what happened: DEFERRED (written), ALREADY_CONSUMED (an attempt
    or earlier skip already acknowledges it), STATE_MISS (not a pending member of that
    batch — stale, foreign, or gone invisible; nothing is written)."""
    if _acknowledged(conn, scope, batch_id, puzzle_id):
        return "ALREADY_CONSUMED"
    if visibility.visible_by_id(conn, puzzle_id, lookahead_plies=lookahead_plies(config)) is None:
        return "STATE_MISS"
    with conn.cursor() as cur:
        # Sourced from the exposure log, so a target that was never served writes nothing;
        # a second skip racing the first is a no-op rather than an error.
        cur.execute(
            """
            INSERT INTO player_puzzle_skip (player_id, scope, batch_id, puzzle_id)
            SELECT e.player_id, e.scope, e.batch_id, e.puzzle_id
            FROM player_puzzle_exposure e
            WHERE e.player_id = %s AND e.scope = %s AND e.batch_id = %s AND e.puzzle_id = %s
            LIMIT 1
            ON CONFLICT (player_id, scope, batch_id, puzzle_id) DO NOTHING
            """,
            (PLAYER_ID, scope, batch_id, puzzle_id),
        )
        if cur.rowcount == 1:
            return "DEFERRED"
    return "ALREADY_CONSUMED" if _acknowledged(conn, scope, batch_id, puzzle_id) else "STATE_MISS"


# --- corpus rotation -----------------------------------------------------------------------


def place_window(center: int, lo_off: int, hi_off: int, floor: int, ceiling: int) -> tuple[int, int]:
    """The absolute rating window: the tier band around `center`, intersected with the
    corpus extent; when they do not meet, the band's full width anchored at the nearest
    corpus edge. Always floor <= lo <= hi <= ceiling."""
    width = hi_off - lo_off
    raw_lo, raw_hi = center + lo_off, center + hi_off
    lo, hi = max(raw_lo, floor), min(raw_hi, ceiling)
    if lo > hi:
        if raw_hi < floor:
            lo, hi = floor, min(floor + width, ceiling)
        else:
            lo, hi = max(ceiling - width, floor), ceiling
    return lo, hi


def rating_window(conn: Connection[Any], config: Settings) -> tuple[int | None, int | None]:
    """(lo, hi) for corpus draws, or (None, None) when there is no rated game or no corpus.
    The centre is the latest rated game's rating brought onto the corpus (Lichess) scale by
    the offset for its time class; the extent is what is actually loaded, not the import
    config."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg.player_rating, pg.source, cg.time_class
            FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
            WHERE pg.player_id = %s AND pg.player_rating IS NOT NULL
            ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC LIMIT 1
            """,
            (PLAYER_ID,),
        )
        latest = cur.fetchone()
        cur.execute("SELECT min(rating) AS lo, max(rating) AS hi FROM lichess_puzzles")
        extent = cur.fetchone()
    if not latest or not extent or extent["lo"] is None:
        return None, None
    # The offsets are how far a Chess.com rating sits below the corpus scale (negative), so a
    # Chess.com rating is raised by that much; a Lichess rating is already on the scale.
    center = int(latest["player_rating"])
    if latest["source"] == "chesscom":
        offsets = config.lichess_rating_offsets
        center -= int(offsets.get(str(latest["time_class"] or ""), offsets.get("default", 0)))
    lo_off, hi_off = getattr(config, f"cc0_tier_{config.cc0_difficulty_tier}")
    return place_window(center, int(lo_off), int(hi_off), int(extent["lo"]), int(extent["hi"]))


_SEEN_FENS = """seen_fens AS (
    SELECT p.canonical_fen FROM puzzle_attempts pa JOIN puzzles p ON p.id = pa.puzzle_id WHERE pa.player_id = %(pid)s
    UNION
    SELECT p.canonical_fen FROM player_puzzle_skip s JOIN puzzles p ON p.id = s.puzzle_id WHERE s.player_id = %(pid)s
    UNION
    SELECT canonical_fen FROM dismissed_blunder_fens WHERE player_id = %(pid)s
)"""

_DRAW = f"""
WITH {_SEEN_FENS},
topk AS (
    SELECT lp.puzzle_id, lp.fen, lp.solution_line, lp.color, lp.themes
    FROM lichess_puzzles lp
    WHERE lp.themes && %(themes)s::text[]
      AND (%(lo)s::int IS NULL OR lp.rating BETWEEN %(lo)s AND %(hi)s)
      AND lp.fen <> ALL(%(excluded)s::text[])
      AND NOT EXISTS (SELECT 1 FROM seen_fens sf WHERE sf.canonical_fen = lp.canonical_fen)
    ORDER BY lp.popularity DESC, lp.nb_plays DESC, lp.puzzle_id
    LIMIT %(k)s
)
SELECT * FROM topk ORDER BY random() LIMIT 1
"""


def draw_candidate(
    conn: Connection[Any], themes: list[str], lo: int | None, hi: int | None, excluded: set[str], k: int
) -> dict[str, Any] | None:
    """One fresh corpus puzzle overlapping `themes` inside the window, drawn uniformly from
    the `k` most popular. k = 1 is the deterministic most-popular pick."""
    with conn.cursor() as cur:
        cur.execute(
            _DRAW,
            {
                "pid": PLAYER_ID,
                "themes": list(themes),
                "lo": lo,
                "hi": hi,
                "excluded": sorted(excluded),
                "k": max(1, int(k)),
            },
        )
        row = cur.fetchone()
    return dict(row) if row else None


def materialise(conn: Connection[Any], candidate: dict[str, Any], keep_themes: frozenset[str]) -> int | None:
    """Insert a corpus puzzle as the player's own row. None when the board already has an
    active puzzle (the conflict is a no-op, never a caught exception, because this runs in
    a loop inside the serve transaction)."""
    line = candidate["solution_line"]
    if isinstance(line, str):
        line = json.loads(line)
    themes = sorted(t for t in _themes(candidate) if t in keep_themes)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO puzzles (fen, solution_line, solution_fen_sequence, source_types, color, themes,
                                 is_repertoire, player_id)
            VALUES (%s, %s::jsonb, %s::jsonb, ARRAY['lichess_cc0'], %s, %s, FALSE, %s)
            ON CONFLICT (player_id, canonical_fen) WHERE is_repertoire = FALSE AND active = TRUE DO NOTHING
            RETURNING id
            """,
            (
                candidate["fen"],
                json.dumps(line),
                json.dumps(fen_sequence(candidate["fen"], line)),
                candidate["color"],
                themes,
                PLAYER_ID,
            ),
        )
        row = cur.fetchone()
    return int(row["id"]) if row else None


def _owned_id_at(conn: Connection[Any], fen: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM puzzles WHERE player_id = %s AND canonical_fen = bq_canonical_fen(%s) || ' 0 1'"
            " AND is_repertoire = FALSE AND active = TRUE LIMIT 1",
            (PLAYER_ID, fen),
        )
        row = cur.fetchone()
    return int(row["id"]) if row else None


def _weak_theme_order(conn: Connection[Any], themes: list[str], config: Settings) -> list[str]:
    """The first-class themes the player misses most, most-missed first, counting misses in
    the games he is studying (the time-class focus). When nothing clears the threshold,
    every theme in alphabetical order."""
    with conn.cursor() as cur:
        cur.execute(
            cast(
                LiteralString,
                f"""
            SELECT pme.theme, count(*) AS misses
            FROM player_motif_events pme JOIN chess_games cg ON cg.id = pme.chess_game_id
            WHERE pme.player_id = %s AND pme.found = FALSE AND pme.metric_type = 'motif' AND pme.theme = ANY(%s)
              AND {evidence_sql("cg", config.time_class_focus)}
            GROUP BY pme.theme HAVING count(*) >= %s ORDER BY misses DESC, pme.theme
            """,
            ),
            (PLAYER_ID, themes, config.weak_motif_min_occurrences),
        )
        weak = [str(r["theme"]) for r in cur.fetchall()]
    return weak or themes


def last_seen(conn: Connection[Any], puzzle_ids: list[int]) -> dict[int, datetime]:
    """When each puzzle was last acknowledged (attempted or skipped); absent = never."""
    if not puzzle_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT puzzle_id, max(at) AS at FROM (
                SELECT puzzle_id, attempt_at AS at FROM puzzle_attempts
                WHERE player_id = %(pid)s AND puzzle_id = ANY(%(ids)s)
                UNION ALL
                SELECT puzzle_id, skipped_at FROM player_puzzle_skip
                WHERE player_id = %(pid)s AND puzzle_id = ANY(%(ids)s)
            ) seen GROUP BY puzzle_id
            """,
            {"pid": PLAYER_ID, "ids": puzzle_ids},
        )
        return {int(r["puzzle_id"]): r["at"] for r in cur.fetchall()}


def largest_remainder(weights: dict[str, float], total: int) -> dict[str, int]:
    """Integer allocation proportional to `weights`, summing to `total`."""
    out = {k: 0 for k in weights}
    keys = [k for k in weights if weights[k] > 0]
    if total <= 0 or not keys:
        return out
    s = sum(weights[k] for k in keys)
    raw = {k: weights[k] / s * total for k in keys}
    alloc = {k: int(raw[k]) for k in keys}
    by_remainder = sorted(keys, key=lambda k: raw[k] - alloc[k], reverse=True)
    i = 0
    while sum(alloc.values()) < total:
        alloc[by_remainder[i % len(by_remainder)]] += 1
        i += 1
    out.update(alloc)
    return out


def _allowed_themes(ptype: str, subtype: str | None, bucket: str) -> set[str]:
    """Which of a rotation bucket's themes a scope may draw: everything for 'all' and
    'motif' (narrowed to the SubType theme), nothing for the other types."""
    full = ROTATION_BUCKET_THEMES[bucket]
    if ptype == "all":
        return set(full)
    if ptype == "motif":
        return ({subtype} & full) if subtype else set(full)
    return set()


# --- minting ---------------------------------------------------------------------------------


def _max_batch_id(conn: Connection[Any], scope: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(max(batch_id), 0) AS m FROM player_puzzle_exposure WHERE player_id = %s AND scope = %s",
            (PLAYER_ID, scope),
        )
        row = cur.fetchone()
    return int(row["m"]) if row else 0


def mint_batch(
    conn: Connection[Any],
    by_bucket: dict[str, list[dict[str, Any]]],
    config: Settings,
    scope: str,
    ptype: str,
    subtype: str | None,
    exclude_ids: set[int],
    now: datetime,
) -> int:
    """Compose and persist the scope's next batch; returns its batch id (possibly with no
    rows, when nothing in the scope can supply). Runs under the queue lock."""
    batch_size = max(1, config.puzzle_mix_batch_size)
    pct = {b: max(0, int(getattr(config, f"puzzle_mix_{b}_pct"))) for b in PUZZLE_MIX_BUCKETS}
    lo, hi = rating_window(conn, config)
    k = config.cc0_candidate_pool_size
    batch_id = _max_batch_id(conn, scope) + 1

    def match(row: dict[str, Any]) -> bool:
        return matches_type(row, ptype) and matches_subtype(row, ptype, subtype)

    allowed = {b: _allowed_themes(ptype, subtype, b) for b in ROTATION_BUCKETS}
    due = {
        b: [
            r for r in by_bucket.get(b, []) if srs.is_due(r.get("srs"), now) and match(r) and r["id"] not in exclude_ids
        ]
        for b in SRS_BUCKETS
    }
    suppliable = {b for b in SRS_BUCKETS if due[b]}
    for b in ROTATION_BUCKETS:
        if not allowed[b]:
            continue
        if any(match(r) for r in by_bucket.get(b, [])) or draw_candidate(conn, sorted(allowed[b]), lo, hi, set(), 1):
            suppliable.add(b)
    if not suppliable:
        return batch_id

    targets = largest_remainder({b: float(pct[b]) for b in suppliable}, batch_size)
    served: list[tuple[int, str]] = []
    already = set(exclude_ids)
    lost_races: set[str] = set()

    def srs_puller(b: str):
        rows = iter(sorted(due[b], key=lambda r: sort_key(r, now)))

        def pull() -> bool:
            for r in rows:
                if r["id"] not in already:
                    served.append((int(r["id"]), b))
                    already.add(int(r["id"]))
                    return True
            return False

        return pull

    def rotation_puller(b: str):
        if b == BUCKET_MOTIFS_FIRST_CLASS:
            order = _weak_theme_order(conn, sorted(ROTATION_FIRST_CLASS_THEMES & allowed[b]), config)
            keep = MOTIF_THEME_VOCAB
        else:
            order = sorted(ROTATION_BUCKET_THEMES[b] & allowed[b])
            keep = frozenset(constants.CC0_SERVE_THEMES)
        cursor = [0]
        dead_themes: set[str] = set()
        fallback: list[dict[str, Any]] | None = None
        fallback_i = [0]

        def pull() -> bool:
            nonlocal fallback
            while len(dead_themes) < len(order):
                theme = order[cursor[0] % len(order)]
                cursor[0] += 1
                if theme in dead_themes:
                    continue
                cand = draw_candidate(conn, [theme], lo, hi, lost_races, k)
                if cand is None:
                    dead_themes.add(theme)
                    continue
                new_id = materialise(conn, cand, keep)
                if new_id is None:
                    new_id = _owned_id_at(conn, cand["fen"])
                    if new_id is None or new_id in already:
                        lost_races.add(cand["fen"])
                        continue
                served.append((new_id, b))
                already.add(new_id)
                return True
            # The corpus is exhausted for this scope: fall back to what is owned, least
            # recently acknowledged first (never acknowledged goes first).
            if fallback is None:
                rows = [r for r in by_bucket.get(b, []) if r["id"] not in already and match(r)]
                seen = last_seen(conn, [int(r["id"]) for r in rows])
                fallback = sorted(rows, key=lambda r: (seen.get(int(r["id"])) or _EPOCH, int(r["id"])))
            while fallback_i[0] < len(fallback):
                r = fallback[fallback_i[0]]
                fallback_i[0] += 1
                if r["id"] not in already:
                    served.append((int(r["id"]), b))
                    already.add(int(r["id"]))
                    return True
            return False

        return pull

    pullers = {b: (srs_puller(b) if b in SRS_BUCKETS else rotation_puller(b)) for b in suppliable}
    for b in PUZZLE_MIX_BUCKETS:
        if b not in suppliable:
            continue
        for _ in range(targets.get(b, 0)):
            if len(served) >= batch_size or not pullers[b]():
                break
    dead: set[str] = set()
    while len(served) < batch_size and len(dead) < len(suppliable):
        progressed = False
        for b in PUZZLE_MIX_BUCKETS:
            if b not in suppliable or b in dead or len(served) >= batch_size:
                continue
            if pullers[b]():
                progressed = True
            else:
                dead.add(b)
        if not progressed:
            break
    random.shuffle(served)
    with conn.cursor() as cur:
        for puzzle_id, bucket in served:
            cur.execute(
                "INSERT INTO player_puzzle_exposure (player_id, puzzle_id, bucket, batch_id, scope, served_at)"
                " VALUES (%s, %s, %s, %s, %s, clock_timestamp())",
                (PLAYER_ID, puzzle_id, bucket, batch_id, scope),
            )
    return batch_id


def _batch_ids(conn: Connection[Any], scope: str, batch_id: int) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT puzzle_id FROM player_puzzle_exposure"
            " WHERE player_id = %s AND scope = %s AND batch_id = %s",
            (PLAYER_ID, scope, batch_id),
        )
        return [int(r["puzzle_id"]) for r in cur.fetchall()]


def _now(conn: Connection[Any]) -> datetime:
    row = conn.execute("SELECT NOW() AS now").fetchone()
    assert row is not None
    return row["now"]


def _attach(conn: Connection[Any], rows: list[dict[str, Any]]) -> None:
    ids = [int(r["id"]) for r in rows]
    state = srs.states(conn, ids)
    summaries = srs.attempt_summaries(conn, ids)
    for r in rows:
        r["srs"] = state.get(int(r["id"]))
        r["attempt_summary"] = summaries.get(int(r["id"]), srs.EMPTY_SUMMARY)


@dataclass
class Served:
    rows: list[dict[str, Any]]
    batch_id: int | None
    scope: str | None
    mint_ahead: int | None


def play_batch(
    conn: Connection[Any], config: Settings, *, last_n_games: int, ptype: str, subtype: str | None
) -> Served:
    """The play queue for one scope: its pending items oldest first, minting the next batch
    under the queue lock when the queue is empty or running low. Writes exposure rows
    (and materialised corpus puzzles); the caller commits."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_PUZZLE_QUEUE, PLAYER_ID))
    scope = scope_of(ptype, subtype)
    now = _now(conn)
    lookahead = lookahead_plies(config)
    visible = visibility.visible_rows(conn, last_n_games=last_n_games, lookahead_plies=lookahead)
    _attach(conn, visible)
    by_id = {int(r["id"]): r for r in visible}
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for r in visible:
        by_bucket.setdefault(bucket_of(r), []).append(r)

    threshold = mint_ahead_threshold(config)
    pending = pending_members(conn, scope, set(by_id))
    batches = {p.batch_id for p in pending}
    if not pending or (len(pending) <= threshold and len(batches) < PENDING_BATCH_DEPTH_CAP):
        exclude = {p.puzzle_id for p in pending} | _all_pending_ids(conn)
        minted = mint_batch(conn, by_bucket, config, scope, ptype, subtype, exclude, now)
        new_ids = [i for i in _batch_ids(conn, scope, minted) if i not in by_id]
        delta = visibility.visible_standard_by_id(conn, new_ids, last_n_games=last_n_games)
        _attach(conn, delta)
        for r in delta:
            by_id.setdefault(int(r["id"]), r)
        pending = pending_members(conn, scope, set(by_id))

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    latest: int | None = None
    for p in pending:
        if p.puzzle_id in seen:
            continue
        seen.add(p.puzzle_id)
        row = dict(by_id[p.puzzle_id])
        row["play_batch_id"] = p.batch_id
        latest = p.batch_id if latest is None else max(latest, p.batch_id)
        rows.append(row)
    return Served(rows, latest, scope, threshold)


def browse(conn: Connection[Any], config: Settings, *, last_n_games: int) -> list[dict[str, Any]]:
    """Every visible puzzle with its state, sorted; no minting."""
    visible = visibility.visible_rows(conn, last_n_games=last_n_games, lookahead_plies=lookahead_plies(config))
    _attach(conn, visible)
    now = _now(conn)
    visible.sort(key=lambda r: sort_key(r, now))
    for r in visible:
        r["play_batch_id"] = None
    return visible


def retired(conn: Connection[Any]) -> list[dict[str, Any]]:
    """Mastered puzzles, regardless of current relevance: the trophy view."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, p.fen, p.solution_line, p.source_types, p.color, p.title, p.description, p.created_at,
                   p.themes, p.acceptance_map, p.is_repertoire, NULL::int AS presentation_ply,
                   NULL::text AS presentation_fen, p.repertoire_line_id,
                   0 AS occurrence_count, '{}'::jsonb AS source_breakdown,
                   (SELECT count(*) FROM puzzle_attempts pa WHERE pa.puzzle_id = p.id AND pa.player_id = %(pid)s)::int
                       AS attempt_count
            FROM player_puzzle_state pps JOIN puzzles p ON p.id = pps.puzzle_id
            WHERE pps.player_id = %(pid)s AND pps.level = 'king' AND p.active = TRUE AND p.player_id = %(pid)s
            ORDER BY pps.updated_at DESC, p.id
            """,
            {"pid": PLAYER_ID},
        )
        rows = [visibility.normalise(r) for r in cur.fetchall()]
    _attach(conn, rows)
    for r in rows:
        r["play_batch_id"] = None
    return rows


def count_eligible(conn: Connection[Any], config: Settings) -> int:
    """How many puzzles are due right now: the Home page number, the same population the
    'due' view shows."""
    lookahead = lookahead_plies(config)
    params = {"pid": PLAYER_ID, "window": 0}
    with conn.cursor() as cur:
        cur.execute(
            cast(
                LiteralString,
                f"""
            {visibility.standard_ctes(windowed=False)}
            SELECT count(*) AS n FROM puzzles p
            LEFT JOIN player_puzzle_state pps ON pps.player_id = %(pid)s AND pps.puzzle_id = p.id
            WHERE {visibility.STANDARD_PREDICATE} AND {srs.due_predicate("pps")}
            """,
            ),
            params,
        )
        standard_row = cur.fetchone()
        assert standard_row is not None
        # The repertoire rows collapse to one per presented position, so they are counted
        # after that collapse, as a subquery.
        cur.execute(
            cast(
                LiteralString,
                f"""
            SELECT count(*) AS n FROM ({visibility.repertoire_rows_sql(lookahead)}) v
            LEFT JOIN player_puzzle_state pps ON pps.player_id = %(pid)s AND pps.puzzle_id = v.id
            WHERE {srs.due_predicate("pps")}
            """,
            ),
            params,
        )
        repertoire_row = cur.fetchone()
        assert repertoire_row is not None
    return int(standard_row["n"]) + int(repertoire_row["n"])
