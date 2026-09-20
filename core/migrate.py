"""One-time migration of the old database (restored locally: ORACLE_DATABASE_URL)
into a freshly initialised new one (DATABASE_URL).

Table by table: the columns are the intersection of the two schemas after the
renames in a mapping file, so dropped columns need no code; a per-table
predicate keeps only the single player's rows, only games the player or a
scouted opponent owns, and for every analysis-derived table only analysable
variants (existing Chess960 blunders, motif events and repertoire results are
dropped: docs/decisions/001 and core/chess/eligibility.py). IDs are preserved
and sequences reset. Rows are streamed with COPY. Runs only into an empty target.

The mapping file (`pipeline migrate --mapping FILE`) carries the old schema's
column names and enum values that the new schema renamed; it lives outside this
repository. Shape:

    {"renames": {"books": {"old_column": "source_book_id"}, ...},
     "values":  {"repertoire_annotations": {"source": {"old_value": "course"}}}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, LiteralString, cast

from psycopg import Connection, sql

from core.chess.eligibility import analysable_sql
from core.constants import PLAYER_ID

_ANALYSABLE_GAME = f"EXISTS (SELECT 1 FROM chess_games cg WHERE cg.id = t.chess_game_id AND {analysable_sql('cg')})"
_MY_BOOK = f"EXISTS (SELECT 1 FROM books b WHERE b.id = t.book_id AND b.player_id = {PLAYER_ID})"
_MY_CHAPTER = (
    "EXISTS (SELECT 1 FROM chapters c JOIN books b ON b.id = c.book_id"
    f" WHERE c.id = t.chapter_id AND b.player_id = {PLAYER_ID})"
)
_MY_PROFILE = (
    f"EXISTS (SELECT 1 FROM opponent_profiles p WHERE p.id = t.opponent_profile_id AND p.player_id = {PLAYER_ID})"
)
_OWNED_GAME = (
    f"(EXISTS (SELECT 1 FROM player_games pg WHERE pg.chess_game_id = t.id AND pg.player_id = {PLAYER_ID})"
    " OR EXISTS (SELECT 1 FROM opponent_views ov JOIN opponent_profiles p ON p.id = ov.opponent_profile_id"
    f" WHERE ov.chess_game_id = t.id AND p.player_id = {PLAYER_ID}))"
)
ANNOTATION_SOURCES = ("course", "manual")  # the new schema's CHECK; other sources are not migrated

# The old system had two tiers of puzzle: the player's own, and a shared tier owned by
# nobody (`player_id IS NULL`) that he could still solve. A puzzle survives here if it is
# his, or if it is one of those shared ones his own history reaches — otherwise the
# migration would quietly drop the attempts and the spaced-repetition progress behind
# them, which is the one thing in this database that cannot be rebuilt. Endgame drills
# do not exist here and do not survive either way.
_HISTORY_REACHES = " OR ".join(
    f"EXISTS (SELECT 1 FROM {table} h WHERE h.puzzle_id = t.id AND h.player_id = {PLAYER_ID})"
    for table in ("puzzle_attempts", "player_puzzle_state", "player_puzzle_exposure", "player_puzzle_skip")
)
_SURVIVING_PUZZLE = (
    f"t.puzzle_kind = 'line' AND (t.player_id = {PLAYER_ID} OR (t.player_id IS NULL AND ({_HISTORY_REACHES})))"
)
# The rows keyed on a puzzle follow whichever puzzles survived.
_MY_PUZZLE = (
    "EXISTS (SELECT 1 FROM puzzles p WHERE p.id = t.puzzle_id AND p.puzzle_kind = 'line'"
    f" AND (p.player_id = {PLAYER_ID} OR p.player_id IS NULL))"
)

# Who made a puzzle decided whether a generator was allowed to take its board away, and
# the old schema said so twice: a 'manual' tag in `source_types`, and a `created_by` that
# only a person ever filled in. This vocabulary has one marker, 'custom', so both signals
# are translated into it here — while `created_by` still exists to read. Translating only
# the tag leaves a hand-made puzzle looking like one the pipeline generated, and the first
# generation run then deactivates it for having no evidence behind it.
_SOURCE_TYPES = """
CASE
    WHEN t.source_types @> ARRAY['manual'] THEN array_replace(t.source_types, 'manual', 'custom')
    WHEN t.created_by IS NOT NULL THEN t.source_types || ARRAY['custom']
    ELSE t.source_types
END
"""

# Merging the two tiers can put two active rows on one board, which the partial unique
# index forbids — and it forbids it row by row during the copy, before anything has had a
# chance to reconcile. So the index is dropped for the length of the merge and rebuilt
# before the transaction commits: if the reconciliation below ever misses a case, the
# rebuild fails and the whole migration rolls back rather than leaving a broken invariant.
_BOARD_INDEX = "ix_puzzles_player_standard_fen"
_DROP_BOARD_INDEX: LiteralString = f"DROP INDEX {_BOARD_INDEX}"

# Keep serving the row the player actually worked on — most progress, then most attempts,
# then the older row — and retire the rest. Nothing is deleted: an inactive puzzle keeps
# its attempts and its SRS row.
_RESOLVE_BOARD_COLLISIONS = """
WITH ranked AS (
    SELECT p.id,
           row_number() OVER (
               PARTITION BY p.canonical_fen
               ORDER BY (SELECT count(*) FROM player_puzzle_state s WHERE s.puzzle_id = p.id) DESC,
                        (SELECT count(*) FROM puzzle_attempts a WHERE a.puzzle_id = p.id) DESC,
                        p.id ASC
           ) AS rank
    FROM puzzles p
    WHERE p.is_repertoire = FALSE AND p.active = TRUE
)
UPDATE puzzles SET active = FALSE, updated_at = now()
FROM ranked WHERE puzzles.id = ranked.id AND ranked.rank > 1
"""


@dataclass(frozen=True)
class Mapping:
    renames: dict[str, dict[str, str]]  # table -> {old column: new column}
    values: dict[str, dict[str, dict[str, str]]]  # table -> column -> {old: new}

    @classmethod
    def load(cls, path: Path) -> Mapping:
        data: dict[str, Any] = json.loads(path.read_text())
        renames: dict[str, dict[str, str]] = {
            str(t): {str(k): str(v) for k, v in cols.items()} for t, cols in dict(data.get("renames", {})).items()
        }
        values: dict[str, dict[str, dict[str, str]]] = {
            str(t): {str(c): {str(k): str(v) for k, v in m.items()} for c, m in cols.items()}
            for t, cols in dict(data.get("values", {})).items()
        }
        return cls(renames=renames, values=values)


@dataclass(frozen=True)
class Step:
    table: str
    where: str  # predicate over alias t in the SOURCE database
    order: str = "1"
    after: str = ""  # statement run on the TARGET once the table is copied
    overrides: dict[str, str] = dataclass_field(default_factory=lambda: {})  # target column -> SQL expression


def steps(mapping: Mapping) -> list[Step]:
    source_map = mapping.values.get("repertoire_annotations", {}).get("source", {})
    kept_sources = sorted(set(ANNOTATION_SOURCES) | set(source_map))
    sources_sql = ", ".join(sql.Literal(s).as_string() for s in kept_sources)
    return [
        Step("players", f"t.id = {PLAYER_ID}"),
        Step("chess_games", _OWNED_GAME, order="t.id"),
        Step("player_games", f"t.player_id = {PLAYER_ID}", order="t.chess_game_id"),
        Step("books", f"t.player_id = {PLAYER_ID}", order="t.id"),
        Step("chapters", _MY_BOOK, order="t.id"),
        Step("repertoire_lines", _MY_CHAPTER, order="t.id"),
        Step("repertoire_annotations", f"t.player_id = {PLAYER_ID} AND t.source IN ({sources_sql})", order="t.id"),
        Step("blunders", f"t.player_id = {PLAYER_ID} AND {_ANALYSABLE_GAME}", order="t.id"),
        Step(
            "player_motif_events",
            f"t.player_id = {PLAYER_ID} AND t.metric_type <> 'endgame' AND {_ANALYSABLE_GAME}",
            order="t.id",
        ),
        Step("game_repertoire_results", f"t.player_id = {PLAYER_ID} AND {_ANALYSABLE_GAME}", order="t.id"),
        Step(
            "game_result_lines",
            "EXISTS (SELECT 1 FROM game_repertoire_results g WHERE g.id = t.game_repertoire_result_id"
            f" AND g.player_id = {PLAYER_ID}"
            f" AND EXISTS (SELECT 1 FROM chess_games cg WHERE cg.id = g.chess_game_id AND {analysable_sql('cg')}))",
            order="t.id",
        ),
        # Scout's tables come across too, so retention keeps the opponents' games payload-loaded.
        Step("opponent_profiles", f"t.player_id = {PLAYER_ID}", order="t.id"),
        Step("opponent_sources", _MY_PROFILE, order="t.id"),
        Step("opponent_views", _MY_PROFILE, order="t.chess_game_id"),
        # Puzzles and everything keyed on them. IDs are preserved: attempts, SRS state,
        # exposure and skips all reference them, and the SRS row is the irreplaceable
        # part of the whole migration.
        Step(
            "puzzles",
            _SURVIVING_PUZZLE,
            order="t.id",
            # There is one player here, so an adopted shared puzzle becomes his.
            overrides={"player_id": str(PLAYER_ID), "source_types": _SOURCE_TYPES},
        ),
        Step("puzzle_attempts", f"t.player_id = {PLAYER_ID} AND {_MY_PUZZLE}", order="t.id"),
        Step("player_puzzle_state", f"t.player_id = {PLAYER_ID} AND {_MY_PUZZLE}", order="t.puzzle_id"),
        # Drill items were served alongside puzzles and have no counterpart here.
        Step(
            "player_puzzle_exposure",
            f"t.player_id = {PLAYER_ID} AND t.item_kind = 'puzzle' AND {_MY_PUZZLE}",
            order="t.id",
        ),
        Step(
            "player_puzzle_skip",
            f"t.player_id = {PLAYER_ID} AND t.item_kind = 'puzzle' AND {_MY_PUZZLE}",
            order="t.id",
        ),
        Step("dismissed_blunder_fens", f"t.player_id = {PLAYER_ID}", order="t.id"),
    ]


def _columns(conn: Connection[Any], table: str) -> list[str]:
    """Ordinary (non-generated) columns of a table."""
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s"
        " AND is_generated = 'NEVER' ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [r["column_name"] for r in rows]


def plan_step(src: Connection[Any], dst: Connection[Any], step: Step, mapping: Mapping) -> tuple[list[str], list[str]]:
    """(source expressions, target columns) for one table."""
    renames = mapping.renames.get(step.table, {})
    values = mapping.values.get(step.table, {})
    dst_cols = set(_columns(dst, step.table))
    src_exprs: list[str] = []
    dst_names: list[str] = []
    for c in _columns(src, step.table):
        target = renames.get(c, c)
        if target not in dst_cols:
            continue
        override = step.overrides.get(target)
        if override is not None:
            src_exprs.append(override)
            dst_names.append(target)
            continue
        ident = sql.Identifier(c).as_string(src)
        value_map = values.get(target)
        if value_map:
            cases = " ".join(
                f"WHEN {ident} = {sql.Literal(k).as_string(src)} THEN {sql.Literal(v).as_string(src)}"
                for k, v in value_map.items()
            )
            src_exprs.append(f"CASE {cases} ELSE {ident} END")
        else:
            src_exprs.append(ident)
        dst_names.append(target)
    return src_exprs, dst_names


def copy_table(src: Connection[Any], dst: Connection[Any], step: Step, mapping: Mapping) -> int:
    exprs, names = plan_step(src, dst, step, mapping)
    table = sql.Identifier(step.table).as_string(src)
    select = f"SELECT {', '.join(exprs)} FROM {table} t WHERE {step.where} ORDER BY {step.order}"
    target = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(step.table), sql.SQL(", ").join(sql.Identifier(n) for n in names)
    )
    with src.cursor() as scur, dst.cursor() as dcur:
        with scur.copy(sql.SQL("COPY ({}) TO STDOUT").format(sql.SQL(select))) as out, dcur.copy(target) as into:  # type: ignore[arg-type]
            for data in out:
                into.write(data)
    row = dst.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(step.table))).fetchone()
    return int(row["n"]) if row else 0


def reset_sequences(dst: Connection[Any], plan: list[Step]) -> None:
    for step in plan:
        if "id" not in _columns(dst, step.table):
            continue
        dst.execute(
            sql.SQL(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE((SELECT max(id) FROM {}), 0) + 1, false)"
            ).format(sql.Identifier(step.table)),
            (step.table,),
        )


def migrate(
    src: Connection[Any],
    dst: Connection[Any],
    mapping: Mapping,
    only: list[str] | None = None,
) -> dict[str, int]:
    """Copy every step in order inside one target transaction. Returns row counts.

    `only` restricts the run to the named tables, and the empty-target precondition to
    those tables too. That is how a phase adds its tables to a database earlier phases
    already populated; the cutover runs the whole plan into a clean database.
    """
    plan = steps(mapping)
    if only is not None:
        wanted = set(only)
        unknown = wanted - {s.table for s in plan}
        if unknown:
            raise RuntimeError(f"no migration step for: {', '.join(sorted(unknown))}")
        plan = [s for s in plan if s.table in wanted]
    for step in plan:
        row = dst.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(step.table))).fetchone()
        if row and int(row["n"]) > 0:
            raise RuntimeError(f"target table {step.table} is not empty; migrate only into a fresh database")
    counts: dict[str, int] = {}
    merging_puzzles = "puzzles" in {s.table for s in plan}
    with dst.transaction():
        board_index = _drop_board_index(dst) if merging_puzzles else None
        for step in plan:
            counts[step.table] = copy_table(src, dst, step, mapping)
            if step.table == "puzzles":
                counts["puzzles adopted from the shared tier"] = _count_adopted(src)
            if step.after:
                dst.execute(cast(LiteralString, step.after))
        if board_index is not None:
            with dst.cursor() as cur:
                cur.execute(cast(LiteralString, _RESOLVE_BOARD_COLLISIONS))
                counts["puzzles retired to keep one per board"] = cur.rowcount
            dst.execute(board_index)  # fails here if a board still has two active puzzles
        reset_sequences(dst, plan)
    return counts


def _drop_board_index(dst: Connection[Any]) -> LiteralString:
    """Take the one-active-puzzle-per-board index out of the way, returning the statement
    that puts it back. The definition is read from the database rather than written out
    here, so it cannot drift from schema.sql."""
    row = dst.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s", (_BOARD_INDEX,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"expected index {_BOARD_INDEX} to exist before migrating puzzles")
    definition = cast(LiteralString, row["indexdef"])  # read from pg_indexes, never from input
    dst.execute(_DROP_BOARD_INDEX)
    return definition


_COUNT_ADOPTED = cast(
    LiteralString,
    "SELECT count(*) AS n FROM puzzles t WHERE t.puzzle_kind = 'line'"
    f" AND t.player_id IS NULL AND ({_HISTORY_REACHES})",
)


def _count_adopted(src: Connection[Any]) -> int:
    """How many shared puzzles came over because the player's own history reaches them."""
    with src.cursor() as cur:
        cur.execute(_COUNT_ADOPTED)
        row = cur.fetchone()
    return int(row["n"]) if row else 0
