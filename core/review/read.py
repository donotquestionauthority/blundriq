"""The Review worklist: the stored events aggregated into nodes at read time.

Pools are never materialised. Every request fetches the player's events under the time-class
filter and derives, in memory, the layer-2 displayed route (an opening candidate displays as
"opening" only when its pool clears the floor under *this* filter), the category tree, each
node's severity and representative game, and the drill-down. The writer (`core.review.write`)
stores every window game's events unfiltered; the filter lives here, as it always did.

Identity: a route pool is `v1:route:{base_route}[:{sub_key}]`; an opening family is
`v1:fam:{digest}` and a subgroup `{family}:{var|rep|pos}:{digest}`. Digests are one-way, so a
requested opening id resolves only by membership in the ids this request would emit. Labels
never enter identity: a relabel keeps a node's rotation state, a changed canonical value is a
new node.

Progress is per game: a game is reviewed when `player_games.reviewed_at` is set, whichever of
its events a node holds. Counts are distinct games at each node's own boundary, never summed
from children. Representative rotation ranks distinct games, so the 24-hour cooldown alternates
to a *different* game; `review_pool_state` records only when a node was last shown.

Rule 7: the event fetch joins `analysable_sql('cg')`; a `review_events` row for a Chess960 game
(the writer never produces one) is invisible to every node, count, drill-down and stamp.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql, evidence_sql
from core.constants import CLOCK_DECIDED_TERMINATIONS, PLAYER_ID
from core.settings import Settings

Event = dict[str, Any]


class ReviewParamError(ValueError):
    """A bad request parameter (unknown opening key, group_by or reviewed_scope): 422."""


class PoolNotFound(ValueError):
    """A pool id that does not parse or names no node this request would emit: 404."""


CLUSTERING_VERSION = 1
GROUP_BY_MODES = ("variation", "repertoire", "position")
REVIEWED_SCOPES = ("to_review", "all")
TIME_CLASSES = ("focus", "all")
OPENING_ALL = "__all__"
OPENING_UNCLASSIFIED = "__unclassified__"

# Small-pool shrink: severity = raw × n / (n + prior).
_SHRINK_PRIOR = 3
# A node shown within this window serves its runner-up game instead of the same clearest one.
_REPRESENTATIVE_COOLDOWN = timedelta(hours=24)
_POOL_ID_MAX_LEN = 256
# The evidence-strength badge: "high" from this many events.
_HIGH_CONFIDENCE_EVENTS = 8

_BASE_ROUTES = ("endgame_technique", "lapse_defense", "lapse_offense", "faded")
# Routes split into sub-pools (defense by piece label, offense by motif theme); the other two
# are single pools, and a sub_key on them is rejected rather than ignored: it would mint a
# distinct shown-state row for the one real pool.
_SUBPOOL_ROUTES = ("lapse_defense", "lapse_offense")
_KIND_WORDS = {"var": "variation", "rep": "repertoire", "pos": "position"}
_KIND_OF = {v: k for k, v in _KIND_WORDS.items()}

_DIGEST_HEX = 16
_DOMAIN_SEP = "\x1f"
_UNCLASSIFIED_SRC = "\x00unclassified"
_REP_NONE_SRC = "none"


# --- Identity ----------------------------------------------------------------------------


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:_DIGEST_HEX]


def family_id(canonical_family: str | None) -> str:
    body = canonical_family if canonical_family is not None else _UNCLASSIFIED_SRC
    return f"v{CLUSTERING_VERSION}:fam:{_digest('fam' + _DOMAIN_SEP + body)}"


def subgroup_id(fam_id: str, kind: str, source: str) -> str:
    return f"{fam_id}:{kind}:{_digest(kind + _DOMAIN_SEP + source)}"


def route_pool_id(base_route: str, sub_key: str | None = None) -> str:
    if sub_key:
        return f"v{CLUSTERING_VERSION}:route:{base_route}:{sub_key}"
    return f"v{CLUSTERING_VERSION}:route:{base_route}"


def _variation_source(canonical_variation: str | None) -> str:
    return canonical_variation if canonical_variation is not None else _UNCLASSIFIED_SRC


# --- Knobs ---------------------------------------------------------------------------------


def knobs_of(config: Settings) -> dict[str, int]:
    return {
        "pool_floor_line": config.review_pool_floor_line,
        "pool_floor_eco": config.review_pool_floor_eco,
        "half_life": config.review_recency_half_life_games,
        "faded_peak_es": config.review_faded_peak_es,
    }


def _time_class_sql(time_class: str, focus: str) -> str:
    if time_class == "focus":
        return evidence_sql("cg", focus)
    if time_class == "all":
        return "TRUE"
    raise ReviewParamError(f"unknown time_class: {time_class!r}")


# --- Reads ---------------------------------------------------------------------------------


def fetch_events(conn: Connection[Any], time_class: str, focus: str) -> list[Event]:
    """Every review event under the time-class filter with the game context the aggregation
    needs, plus a per-game recency rank (0 = most recent) over the filtered set's games — the
    severity recency axis. All events of a game share a rank (DENSE_RANK); the game order is
    played_at DESC NULLS LAST, id DESC, the window's own. Clock-decided games need no exclusion
    here: the writer emits no events for them."""
    query = cast(
        LiteralString,
        f"""
        SELECT re.chess_game_id, re.anchor_ply, re.base_route, re.opening_candidate, re.pool_key,
               re.evidence, re.cost, re.phase, re.piece_label, re.book_relation, re.board_key,
               cg.played_at, cg.time_class, cg.termination, cg.url, cg.opening_name,
               cg.canonical_family, cg.canonical_variation,
               pg.result, pg.opponent_username, pg.reviewed_at,
               DENSE_RANK() OVER (ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC) - 1 AS recency_rank
        FROM review_events re
        JOIN chess_games cg ON cg.id = re.chess_game_id
        JOIN player_games pg ON pg.chess_game_id = re.chess_game_id AND pg.player_id = re.player_id
        WHERE re.player_id = %(pid)s AND {analysable_sql("cg")} AND {_time_class_sql(time_class, focus)}
        ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC, re.anchor_ply
        """,
    )
    rows = [dict(r) for r in conn.execute(query, {"pid": PLAYER_ID}).fetchall()]
    for r in rows:
        ev = r.get("evidence")
        if isinstance(ev, str):
            try:
                r["evidence"] = json.loads(ev)
            except ValueError:
                r["evidence"] = {}
        elif not isinstance(ev, dict):
            r["evidence"] = {}
    return rows


def _enrich_opening_events(conn: Connection[Any], rows: list[dict[str, Any]]) -> None:
    """Opening nodes show the correct move: `best_move` / `best_line` from the blunders row at the
    same anchor, `expected_move` / `deviated_at_ply` from the game's repertoire result. Every key
    is always present, NULL when there is nothing; one batched query per call. DISTINCT ON guards
    the unconstrained blunders side (the lowest id wins)."""
    if not rows:
        return
    found = conn.execute(
        """
        SELECT DISTINCT ON (v.chess_game_id, v.anchor_ply)
               v.chess_game_id, v.anchor_ply, b.best_move, b.best_line, grr.expected_move, grr.deviated_at_ply
        FROM (SELECT unnest(%(gids)s::bigint[]) AS chess_game_id, unnest(%(plies)s::int[]) AS anchor_ply) v
        LEFT JOIN blunders b ON b.player_id = %(pid)s AND b.chess_game_id = v.chess_game_id AND b.ply = v.anchor_ply
        LEFT JOIN game_repertoire_results grr ON grr.player_id = %(pid)s AND grr.chess_game_id = v.chess_game_id
        ORDER BY v.chess_game_id, v.anchor_ply, b.id ASC NULLS LAST
        """,
        {
            "gids": [r["chess_game_id"] for r in rows],
            "plies": [r["anchor_ply"] for r in rows],
            "pid": PLAYER_ID,
        },
    ).fetchall()
    by_key = {(r["chess_game_id"], r["anchor_ply"]): r for r in found}
    for row in rows:
        r: dict[str, Any] = by_key.get((row["chess_game_id"], row["anchor_ply"])) or {}
        for k in ("best_move", "best_line", "expected_move", "deviated_at_ply"):
            row[k] = r.get(k)


def _repertoire_ctx(conn: Connection[Any], gids: list[int]) -> dict[int, dict[str, Any] | None]:
    """The line each game is grouped under in "by repertoire": the game's deepest matched line
    (max matched_ply, then the lowest line_id — the matcher's own tie-break, so the subgroup
    agrees with the event's stored `line:` pool key). Every requested id is present (None: no
    match recorded). Labels are LEFT-joined; a match whose labels no longer join is a
    "Retired line", never mistaken for an unprepared game."""
    ctx: dict[int, dict[str, Any] | None] = {gid: None for gid in gids}
    if not gids:
        return ctx
    rows = conn.execute(
        """
        SELECT DISTINCT ON (grr.chess_game_id)
               grr.chess_game_id, grl.line_id, b.title AS book_title, c.title AS chapter_title, rl.line_name
        FROM game_repertoire_results grr
        JOIN game_result_lines grl ON grl.game_repertoire_result_id = grr.id
        LEFT JOIN repertoire_lines rl ON rl.id = grl.line_id
        LEFT JOIN chapters c ON c.id = grr.chapter_id
        LEFT JOIN books b ON b.id = grr.book_id
        WHERE grr.player_id = %(pid)s AND grr.chess_game_id = ANY(%(gids)s)
        ORDER BY grr.chess_game_id, grl.matched_ply DESC, grl.line_id ASC
        """,
        {"pid": PLAYER_ID, "gids": gids},
    ).fetchall()
    for r in rows:
        ctx[r["chess_game_id"]] = {
            "line_id": r["line_id"],
            "book_title": r["book_title"],
            "chapter_title": r["chapter_title"],
            "line_name": r["line_name"],
        }
    return ctx


def _pool_state(conn: Connection[Any]) -> dict[str, datetime]:
    rows = conn.execute("SELECT pool_id, last_shown_at FROM review_pool_state WHERE player_id = %s", (PLAYER_ID,))
    return {r["pool_id"]: r["last_shown_at"] for r in rows.fetchall()}


# --- Layer 2: the displayed route ------------------------------------------------------------


def _floor_for_pool_key(pool_key: str, knobs: dict[str, int]) -> int:
    """Line and canonical-pair keys share the line floor; an ECO key takes the higher ECO floor."""
    if pool_key.startswith("eco:"):
        return knobs["pool_floor_eco"]
    return knobs["pool_floor_line"]


def derive_display(events: list[Event], knobs: dict[str, int], floor_override: int | None = None) -> None:
    """Annotate each event in place with `displayed_route`: "opening" when it is an opening
    candidate whose pool key is carried by at least the floor's worth of opening-candidate
    events in *this* set, else its stored base route. Floor counts are over candidates only: a
    late material event on the same key neither lifts the pool nor displays as opening. Never
    stored; the same event may display differently under another filter. `floor_override`
    (1 under a focused opening) waives the floor so thin lines surface in that view only."""
    counts: dict[str, int] = {}
    for e in events:
        if e["pool_key"] and e["opening_candidate"]:
            counts[e["pool_key"]] = counts.get(e["pool_key"], 0) + 1
    for e in events:
        pk = e["pool_key"]
        if e["opening_candidate"] and pk:
            floor = floor_override if floor_override is not None else _floor_for_pool_key(pk, knobs)
            e["displayed_route"] = "opening" if counts.get(pk, 0) >= floor else e["base_route"]
        else:
            e["displayed_route"] = e["base_route"]


def apply_opening_filter(events: list[Event], opening: str) -> list[Event]:
    """Restrict the set to one family before qualification (the floor is re-evaluated over the
    filtered set, as it is under the time-class filter). NULL family is a first-class bucket."""
    if opening == OPENING_ALL:
        return events
    if opening == OPENING_UNCLASSIFIED:
        return [e for e in events if e["canonical_family"] is None]
    return [e for e in events if e["canonical_family"] == opening]


# --- Scoring and representatives ---------------------------------------------------------------


def _recency_weight(recency_rank: int, half_life: int) -> float:
    if half_life <= 0:
        return 1.0
    return 0.5 ** (float(recency_rank) / float(half_life))


def _score(e: Event, half_life: int) -> float:
    return float(e["cost"] or 0) * _recency_weight(e["recency_rank"], half_life)


def serialize_event(e: Event) -> dict[str, Any]:
    """One game row: the representative anchor with the game context the row renders."""
    return {
        "chess_game_id": e["chess_game_id"],
        "anchor_ply": e["anchor_ply"],
        "cost": float(e["cost"] or 0),
        "phase": e["phase"],
        "piece_label": e["piece_label"],
        "book_relation": e["book_relation"],
        "evidence": e["evidence"],
        "base_route": e["base_route"],
        "displayed_route": e.get("displayed_route"),
        "pool_key": e["pool_key"],
        "board_key": e["board_key"],
        "played_at": e["played_at"],
        "opponent_username": e["opponent_username"],
        "result": e["result"],
        "url": e["url"],
        "time_class": e["time_class"],
        "canonical_family": e["canonical_family"],
        "canonical_variation": e["canonical_variation"],
        "reviewed": e["reviewed_at"] is not None,
    }


def _distinct_games(members: list[Event]) -> int:
    return len({e["chess_game_id"] for e in members})


def _distinct_to_review_games(members: list[Event]) -> int:
    return len({e["chess_game_id"] for e in members if e["reviewed_at"] is None})


def _game_rep_anchor(events_of_game: list[Event], half_life: int) -> Event:
    """A game's representative anchor: its highest cost × recency event; ties to the most recent,
    the later ply, the higher id."""
    return max(
        events_of_game,
        key=lambda e: (_score(e, half_life), -e["recency_rank"], -(e["anchor_ply"] or 0), e["chess_game_id"]),
    )


def collapse_to_games(members: list[Event], half_life: int) -> list[dict[str, Any]]:
    """One entry per game: its representative anchor, that anchor's score, whether the game is
    reviewed, and how many other anchors of the game this membership holds ("+N more")."""
    by_game: dict[int, list[Event]] = {}
    for e in members:
        by_game.setdefault(e["chess_game_id"], []).append(e)
    out: list[dict[str, Any]] = []
    for gid, evs in by_game.items():
        rep = _game_rep_anchor(evs, half_life)
        out.append(
            {
                "game_id": gid,
                "reviewed": rep["reviewed_at"] is not None,
                "rep": rep,
                "score": _score(rep, half_life),
                "extra": len(evs) - 1,
            }
        )
    return out


def _rank_games(collapsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Worst first: score DESC, then most recent, later ply, higher id."""
    return sorted(
        collapsed,
        key=lambda g: (g["score"], -g["rep"]["recency_rank"], -(g["rep"]["anchor_ply"] or 0), g["game_id"]),
        reverse=True,
    )


def pick_representative(members: list[Event], half_life: int, last_shown: datetime | None, now: datetime) -> Event:
    """The node's representative anchor, game-collapsed so rotation ranks distinct games: the
    worst un-reviewed game (all games once every one is reviewed), or, when the node was shown
    within the cooldown and at least two games are eligible, the runner-up."""
    collapsed = collapse_to_games(members, half_life)
    un_reviewed = [g for g in collapsed if not g["reviewed"]]
    ranked = _rank_games(un_reviewed or collapsed)
    cooled = last_shown is None or (now - last_shown) > _REPRESENTATIVE_COOLDOWN
    chosen = ranked[0] if (cooled or len(ranked) < 2) else ranked[1]
    return chosen["rep"]


def _node_severity(members: list[Event], half_life: int) -> tuple[float, float]:
    """(raw, shrunk) at the node's own boundary: raw = Σ cost × recency weight; shrunk toward
    zero for small nodes by n / (n + prior). Never summed from children."""
    n = len(members)
    raw = sum(_score(e, half_life) for e in members)
    sev = raw * (n / float(n + _SHRINK_PRIOR)) if n else 0.0
    return round(raw, 2), round(sev, 2)


def _book_relation_verdict(members: list[Event]) -> str | None:
    """The most common book relation, ties broken lexically. Opening nodes only."""
    relations = [e["book_relation"] for e in members if e["book_relation"]]
    if not relations:
        return None
    return max(sorted(set(relations)), key=relations.count)


def _confidence(n: int) -> str:
    return "high" if n >= _HIGH_CONFIDENCE_EVENTS else "low"


def _build_pool(
    pool_id: str,
    label: str,
    members: list[Event],
    knobs: dict[str, int],
    now: datetime,
    pool_state: dict[str, datetime],
) -> dict[str, Any]:
    """One route pool (defense, offense, endgame, faded). `pool_key` and the verdict are None:
    opening identity lives in the family layer."""
    raw_sev, sev = _node_severity(members, knobs["half_life"])
    rep = pick_representative(members, knobs["half_life"], pool_state.get(pool_id), now)
    return {
        "pool_id": pool_id,
        "pool_key": None,
        "label": label,
        "severity": sev,
        "raw_severity": raw_sev,
        "event_count": len(members),
        "confidence": _confidence(len(members)),
        "book_relation_verdict": None,
        "total_games": _distinct_games(members),
        "to_review_games": _distinct_to_review_games(members),
        "representative_game": serialize_event(rep),
    }


def offense_theme(e: Event) -> str:
    """The offense sub-pool key: the motif theme on the evidence, else the piece label, else the
    generic bucket."""
    evidence: dict[str, Any] = e["evidence"] or {}
    theme = evidence.get("theme")
    if isinstance(theme, str) and theme:
        return theme
    return e["piece_label"] or "missed_win"


def defense_piece(e: Event) -> str:
    return e["piece_label"] or "material"


# --- Opening families and subgroups ----------------------------------------------------------


def _family_label(canonical_family: str | None) -> str:
    return canonical_family if canonical_family is not None else "Unclassified openings"


def subgroup_partition(
    family_members: list[Event], group_by: str, rep_ctx: dict[int, dict[str, Any] | None]
) -> tuple[str, list[dict[str, Any]]]:
    """Partition a family's opening-displayed events under the group-by mode into
    `[{source, label, members}]` in first-seen order, with the id kind. Keys are canonical:
    the variation, the game's selected repertoire line (or "none"), the board key."""
    buckets: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def bucket(source: str, label: str, e: Event) -> None:
        if source not in buckets:
            buckets[source] = {"source": source, "label": label, "members": []}
            order.append(source)
        buckets[source]["members"].append(e)

    if group_by == "variation":
        for e in family_members:
            cv = e["canonical_variation"]
            bucket(_variation_source(cv), cv if cv is not None else "Unclassified", e)
        kind = "var"
    elif group_by == "repertoire":
        for e in family_members:
            ctx = rep_ctx.get(e["chess_game_id"])
            if ctx is None:
                bucket(_REP_NONE_SRC, "Not in your repertoire", e)
            else:
                if ctx["line_name"]:
                    parts = [p for p in (ctx["chapter_title"], ctx["line_name"]) if p]
                    label = " — ".join(parts) if parts else "Retired line"
                else:
                    label = "Retired line"
                bucket(str(ctx["line_id"]), label, e)
        kind = "rep"
    else:
        for e in family_members:
            bucket(str(e["board_key"]), e["opening_name"] or "Recurring position", e)
        kind = "pos"
    return kind, [buckets[s] for s in order]


def _build_subgroup(
    fam_id: str,
    kind: str,
    part: dict[str, Any],
    knobs: dict[str, int],
    now: datetime,
    pool_state: dict[str, datetime],
) -> dict[str, Any]:
    members = part["members"]
    sid = subgroup_id(fam_id, kind, part["source"])
    raw_sev, sev = _node_severity(members, knobs["half_life"])
    rep = pick_representative(members, knobs["half_life"], pool_state.get(sid), now)
    label = part["label"]
    if kind == "pos":
        # A position has no name of its own: the label is a hint from the chosen representative.
        label = rep.get("opening_name") or "Recurring position"
    return {
        "subgroup_id": sid,
        "label": label,
        "kind": _KIND_WORDS[kind],
        "severity": sev,
        "raw_severity": raw_sev,
        "event_count": len(members),
        "confidence": _confidence(len(members)),
        "book_relation_verdict": _book_relation_verdict(members),
        "total_games": _distinct_games(members),
        "to_review_games": _distinct_to_review_games(members),
        "representative_game": serialize_event(rep),
    }


def _build_family(
    canonical_family: str | None,
    members: list[Event],
    group_by: str,
    rep_ctx: dict[int, dict[str, Any] | None],
    knobs: dict[str, int],
    now: datetime,
    pool_state: dict[str, datetime],
) -> dict[str, Any]:
    fid = family_id(canonical_family)
    raw_sev, sev = _node_severity(members, knobs["half_life"])
    rep = pick_representative(members, knobs["half_life"], pool_state.get(fid), now)
    kind, parts = subgroup_partition(members, group_by, rep_ctx)
    subgroups = [_build_subgroup(fid, kind, p, knobs, now, pool_state) for p in parts]
    subgroups.sort(key=lambda s: (-s["severity"], s["subgroup_id"]))
    return {
        "family_id": fid,
        "family_key": canonical_family if canonical_family is not None else OPENING_UNCLASSIFIED,
        "label": _family_label(canonical_family),
        "severity": sev,
        "raw_severity": raw_sev,
        "event_count": len(members),
        "confidence": _confidence(len(members)),
        "total_games": _distinct_games(members),
        "to_review_games": _distinct_to_review_games(members),
        "subgroups": subgroups,
        "representative_game": serialize_event(rep),
    }


def _assemble_families(
    conn: Connection[Any],
    events: list[Event],
    group_by: str,
    knobs: dict[str, int],
    now: datetime,
    pool_state: dict[str, datetime],
) -> list[dict[str, Any]]:
    opening_events = [e for e in events if e["displayed_route"] == "opening"]
    if not opening_events:
        return []
    by_family: dict[str | None, list[Event]] = {}
    for e in opening_events:
        by_family.setdefault(e["canonical_family"], []).append(e)
    rep_ctx: dict[int, dict[str, Any] | None] = {}
    if group_by == "repertoire":
        rep_ctx = _repertoire_ctx(conn, sorted({e["chess_game_id"] for e in opening_events}))
    families = [_build_family(fam, m, group_by, rep_ctx, knobs, now, pool_state) for fam, m in by_family.items()]
    families.sort(key=lambda f: (-f["severity"], f["family_id"]))
    reps = [f["representative_game"] for f in families]
    reps += [s["representative_game"] for f in families for s in f["subgroups"]]
    _enrich_opening_events(conn, reps)
    return families


# --- Lost wins --------------------------------------------------------------------------------


def _lost_win_game_ids(events: list[Event], knobs: dict[str, int]) -> set[int]:
    """Games with winning-peak evidence (a `faded` event, or any event whose
    `evidence.game_peak_es` reaches the faded peak) that were not won and not clock-decided."""
    qualifying: set[int] = set()
    by_game: dict[int, list[Event]] = {}
    for e in events:
        by_game.setdefault(e["chess_game_id"], []).append(e)
    for gid, evs in by_game.items():
        meta = evs[0]
        if meta["result"] == "win" or (meta["termination"] or "") in CLOCK_DECIDED_TERMINATIONS:
            continue
        for e in evs:
            if e["base_route"] == "faded":
                qualifying.add(gid)
                break
            evidence: dict[str, Any] = e["evidence"] or {}
            peak = evidence.get("game_peak_es")
            try:
                if peak is not None and float(peak) >= knobs["faded_peak_es"]:
                    qualifying.add(gid)
                    break
            except (TypeError, ValueError):
                pass
    return qualifying


def _played_sort_key(e: Event) -> tuple[int, float, int]:
    """played_at DESC NULLS LAST, chess_game_id DESC, as an ascending key."""
    played = e["played_at"]
    if played is None:
        return (1, 0.0, -e["chess_game_id"])
    return (0, -played.timestamp(), -e["chess_game_id"])


def select_lost_wins(events: list[Event], knobs: dict[str, int]) -> list[dict[str, Any]]:
    """One selector for both the count and the rows: game-collapsed, un-reviewed first, then
    most recent."""
    gids = _lost_win_game_ids(events, knobs)
    if not gids:
        return []
    collapsed = collapse_to_games([e for e in events if e["chess_game_id"] in gids], knobs["half_life"])
    collapsed.sort(key=lambda g: (0 if not g["reviewed"] else 1, _played_sort_key(g["rep"])))
    return collapsed


# --- The category tree ---------------------------------------------------------------------------


def _wrap(members: list[Event], payload: dict[str, Any]) -> dict[str, Any]:
    payload["total_games"] = _distinct_games(members)
    payload["to_review_games"] = _distinct_to_review_games(members)
    return payload


def assemble_categories(
    conn: Connection[Any],
    base_events: list[Event],
    opening_events: list[Event],
    group_by: str,
    knobs: dict[str, int],
    now: datetime,
    pool_state: dict[str, datetime],
) -> dict[str, Any]:
    """The tree. The non-opening categories come from `base_events` (all openings, normal
    floor); Opening problems from `opening_events` (the same objects at `__all__`, else a
    floor-waived copy of the focused family). Under a focus a below-floor event can therefore
    appear as an opening node and in its base-route category: the two views overlap by design."""
    defense: dict[str, list[Event]] = {}
    offense: dict[str, list[Event]] = {}
    endgame: list[Event] = []
    faded: list[Event] = []
    for e in base_events:
        r = e["displayed_route"]
        if r == "lapse_defense":
            defense.setdefault(defense_piece(e), []).append(e)
        elif r == "lapse_offense":
            offense.setdefault(offense_theme(e), []).append(e)
        elif r == "endgame_technique":
            endgame.append(e)
        elif r == "faded":
            faded.append(e)

    families = _assemble_families(conn, opening_events, group_by, knobs, now, pool_state)
    defense_pools = [
        _build_pool(route_pool_id("lapse_defense", k), k, m, knobs, now, pool_state) for k, m in defense.items()
    ]
    offense_pools = [
        _build_pool(route_pool_id("lapse_offense", k), k, m, knobs, now, pool_state) for k, m in offense.items()
    ]
    for pools in (defense_pools, offense_pools):
        pools.sort(key=lambda p: (-p["severity"], p["pool_id"]))
    endgame_pools = (
        [_build_pool(route_pool_id("endgame_technique"), "Endgame technique", endgame, knobs, now, pool_state)]
        if endgame
        else []
    )
    faded_pools = (
        [_build_pool(route_pool_id("faded"), "Faded advantage", faded, knobs, now, pool_state)] if faded else []
    )

    defense_events = [e for e in base_events if e["displayed_route"] == "lapse_defense"]
    offense_events = [e for e in base_events if e["displayed_route"] == "lapse_offense"]
    lost = select_lost_wins(base_events, knobs)
    return {
        "opening": _wrap([e for e in opening_events if e["displayed_route"] == "opening"], {"families": families}),
        "oversights": _wrap(
            defense_events + offense_events,
            {
                "defense": _wrap(defense_events, {"pools": defense_pools}),
                "offense": _wrap(offense_events, {"pools": offense_pools}),
            },
        ),
        "endgame": _wrap(endgame, {"pools": endgame_pools}),
        "faded": _wrap(faded, {"pools": faded_pools}),
        "lost_wins": {
            "games": [serialize_event(g["rep"]) for g in lost],
            "total_games": len(lost),
            "to_review_games": sum(1 for g in lost if not g["reviewed"]),
        },
    }


def opening_options(events: list[Event]) -> tuple[list[dict[str, Any]], set[str]]:
    """The "Focus opening" choices over the time-class-filtered corpus: exactly the families
    with at least one pool-keyed opening-candidate event (a focus waives the floor, so each of
    them surfaces at least one node), worst first by un-reviewed games; `__all__` first and
    `__unclassified__` last when any candidate has no family. The second value is the set of
    keys the opening paths accept."""
    oc = [e for e in events if e["opening_candidate"] and e["pool_key"] is not None]
    by_family: dict[str, list[Event]] = {}
    for e in oc:
        by_family.setdefault(e["canonical_family"] or OPENING_UNCLASSIFIED, []).append(e)
    options: list[dict[str, Any]] = [
        {"key": OPENING_ALL, "label": "All openings", "to_review_games": _distinct_to_review_games(oc)}
    ]
    fam_rows: list[dict[str, Any]] = [
        {"key": k, "label": k, "to_review_games": _distinct_to_review_games(m)}
        for k, m in by_family.items()
        if k != OPENING_UNCLASSIFIED
    ]
    fam_rows.sort(key=lambda o: (-o["to_review_games"], o["label"]))
    options += fam_rows
    valid: set[str] = {OPENING_ALL} | {r["key"] for r in fam_rows}
    if OPENING_UNCLASSIFIED in by_family:
        options.append(
            {
                "key": OPENING_UNCLASSIFIED,
                "label": "Unclassified openings",
                "to_review_games": _distinct_to_review_games(by_family[OPENING_UNCLASSIFIED]),
            }
        )
        valid.add(OPENING_UNCLASSIFIED)
    return options, valid


# --- Entry points --------------------------------------------------------------------------------


def _check_group_by(group_by: str) -> None:
    if group_by not in GROUP_BY_MODES:
        raise ReviewParamError(f"unknown group_by: {group_by!r}")


def page(
    conn: Connection[Any],
    config: Settings,
    *,
    time_class: str = "focus",
    opening: str = OPENING_ALL,
    group_by: str = "variation",
    now: datetime | None = None,
) -> dict[str, Any]:
    """The worklist. `opening` narrows Opening problems only (with the floor waived); the other
    categories and the page totals are over the whole time-class-filtered set, so a focus never
    empties the page. The reviewed scope is the client's: every node carries both counts."""
    _check_group_by(group_by)
    knobs = knobs_of(config)
    now = now or datetime.now(UTC)
    all_events = fetch_events(conn, time_class, config.time_class_focus)
    options, valid = opening_options(all_events)
    if opening not in valid:
        raise ReviewParamError(f"unknown opening key: {opening!r}")
    pool_state = _pool_state(conn)
    derive_display(all_events, knobs)
    if opening == OPENING_ALL:
        opening_events = all_events
    else:
        # Copies, so the waived floor never reaches the base derivation's routing.
        opening_events = [dict(e) for e in apply_opening_filter(all_events, opening)]
        derive_display(opening_events, knobs, floor_override=1)
    categories = assemble_categories(conn, all_events, opening_events, group_by, knobs, now, pool_state)
    return {
        "categories": categories,
        "page": {"total_games": _distinct_games(all_events), "to_review_games": _distinct_to_review_games(all_events)},
        "filter": {"time_class": time_class, "opening": opening, "openings": options, "group_by": group_by},
    }


def parse_pool_id(pool_id: str) -> tuple[str, str, Any]:
    """`("family", fh, None)`, `("subgroup", fh, (kind, sh))` or `("route", base_route, sub_key)`.
    Shape only; a digest resolves by membership later. A sub-pooled route needs its sub key and a
    single pool takes none: either way round is a PoolNotFound, never a silently different pool."""
    if not (1 <= len(pool_id) <= _POOL_ID_MAX_LEN):
        raise PoolNotFound("pool id length out of bounds")
    prefix = f"v{CLUSTERING_VERSION}:"
    s = pool_id[len(prefix) :] if pool_id.startswith(prefix) else pool_id
    if s.startswith("fam:"):
        parts = s[len("fam:") :].split(":")
        if len(parts) == 1 and parts[0]:
            return ("family", parts[0], None)
        if len(parts) == 3 and parts[0] and parts[2] and parts[1] in _KIND_WORDS:
            return ("subgroup", parts[0], (parts[1], parts[2]))
        raise PoolNotFound("malformed opening pool id")
    if s.startswith("route:"):
        s = s[len("route:") :]
    elif ":" in s:
        raise PoolNotFound("unparseable pool id")
    base_route, _, sub_key = s.partition(":")
    if base_route not in _BASE_ROUTES:
        raise PoolNotFound("unknown base route")
    if (sub_key != "") != (base_route in _SUBPOOL_ROUTES):
        raise PoolNotFound("sub key does not fit the route")
    return ("route", base_route, sub_key or None)


def canonical_pool_id(parsed: tuple[str, str, Any]) -> str:
    kind, key, sub = parsed
    if kind == "family":
        return f"v{CLUSTERING_VERSION}:fam:{key}"
    if kind == "subgroup":
        return f"v{CLUSTERING_VERSION}:fam:{key}:{sub[0]}:{sub[1]}"
    return route_pool_id(key, sub)


def _events_for_node(all_events: list[Event], kind: str, opening: str, knobs: dict[str, int]) -> list[Event]:
    """The derived set a node resolves against. An opening node honours the focus and its waived
    floor (an unknown key is a ReviewParamError); a route pool ignores `opening` entirely: it is
    always its full, normal-floor membership."""
    if kind not in ("family", "subgroup"):
        derive_display(all_events, knobs)
        return all_events
    _, valid = opening_options(all_events)
    if opening not in valid:
        raise ReviewParamError(f"unknown opening key: {opening!r}")
    if opening == OPENING_ALL:
        derive_display(all_events, knobs)
        return all_events
    events = apply_opening_filter(all_events, opening)
    derive_display(events, knobs, floor_override=1)
    return events


def _opening_node_members(
    conn: Connection[Any], events: list[Event], kind: str, fh: str, sub: tuple[str, str] | None
) -> list[Event]:
    """Resolve a family or subgroup digest by membership in the ids this set emits; [] otherwise."""
    fam_members: dict[str | None, list[Event]] = {}
    for e in events:
        if e["displayed_route"] == "opening":
            fam_members.setdefault(e["canonical_family"], []).append(e)
    wanted = f"v{CLUSTERING_VERSION}:fam:{fh}"
    target = next(((fam, m) for fam, m in fam_members.items() if family_id(fam) == wanted), None)
    if target is None:
        return []
    fam, members = target
    if kind == "family" or sub is None:
        return members
    sub_kind, sh = sub
    rep_ctx: dict[int, dict[str, Any] | None] = {}
    if sub_kind == "rep":
        rep_ctx = _repertoire_ctx(conn, sorted({e["chess_game_id"] for e in members}))
    fid = family_id(fam)
    _, parts = subgroup_partition(members, _KIND_WORDS[sub_kind], rep_ctx)
    for p in parts:
        if subgroup_id(fid, sub_kind, p["source"]) == f"{fid}:{sub_kind}:{sh}":
            return p["members"]
    return []


def pool_members(conn: Connection[Any], events: list[Event], parsed: tuple[str, str, Any]) -> list[Event]:
    """The membership of one parsed selector over derived events — one authority for the
    drill-down and the shown stamp, so a stampable node is exactly a drillable node."""
    kind, key, sub = parsed
    if kind in ("family", "subgroup"):
        return _opening_node_members(conn, events, kind, key, sub)
    members = [e for e in events if e["displayed_route"] == key]
    if sub is not None and key == "lapse_defense":
        members = [e for e in members if defense_piece(e) == sub]
    elif sub is not None and key == "lapse_offense":
        members = [e for e in members if offense_theme(e) == sub]
    return members


def pool_events(
    conn: Connection[Any],
    config: Settings,
    pool_id: str,
    *,
    time_class: str = "focus",
    opening: str = OPENING_ALL,
    reviewed_scope: str = "to_review",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """The drill-down: one row per game, un-reviewed first then most recent, over the same
    fetch + derivation the page uses, so `total` reconciles with the page's counts. A selector
    the page could not emit is a PoolNotFound; a real node whose games are all reviewed is an
    honestly empty list under `to_review`."""
    if reviewed_scope not in REVIEWED_SCOPES:
        raise ReviewParamError(f"unknown reviewed_scope: {reviewed_scope!r}")
    parsed = parse_pool_id(pool_id)
    knobs = knobs_of(config)
    all_events = fetch_events(conn, time_class, config.time_class_focus)
    events = _events_for_node(all_events, parsed[0], opening, knobs)
    members = pool_members(conn, events, parsed)
    if not members:
        raise PoolNotFound("not a current node")
    collapsed = collapse_to_games(members, knobs["half_life"])
    if reviewed_scope == "to_review":
        collapsed = [g for g in collapsed if not g["reviewed"]]
    collapsed.sort(key=lambda g: (0 if not g["reviewed"] else 1, _played_sort_key(g["rep"])))
    rows: list[dict[str, Any]] = []
    for g in collapsed[offset : offset + limit]:
        row = serialize_event(g["rep"])
        row["extra_in_game"] = g["extra"]
        rows.append(row)
    if parsed[0] in ("family", "subgroup"):
        _enrich_opening_events(conn, rows)
    return {"total": len(collapsed), "rows": rows}


def touch_shown(
    conn: Connection[Any], config: Settings, pool_id: str, *, time_class: str = "focus", opening: str = OPENING_ALL
) -> None:
    """Stamp `review_pool_state.last_shown_at` for the canonical id of a node this request would
    emit. Anything else — malformed, stale, a bare sub-pooled route, a node with no members
    under the active filter — is a PoolNotFound and writes nothing."""
    parsed = parse_pool_id(pool_id)
    knobs = knobs_of(config)
    all_events = fetch_events(conn, time_class, config.time_class_focus)
    events = _events_for_node(all_events, parsed[0], opening, knobs)
    if not pool_members(conn, events, parsed):
        raise PoolNotFound("not a current node")
    conn.execute(
        "INSERT INTO review_pool_state (player_id, pool_id, last_shown_at) VALUES (%s, %s, now())"
        " ON CONFLICT (player_id, pool_id) DO UPDATE SET last_shown_at = now()",
        (PLAYER_ID, canonical_pool_id(parsed)),
    )
