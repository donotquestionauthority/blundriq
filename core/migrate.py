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
from pathlib import Path
from typing import Any

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


def migrate(src: Connection[Any], dst: Connection[Any], mapping: Mapping) -> dict[str, int]:
    """Copy every step in order inside one target transaction. Returns row counts."""
    plan = steps(mapping)
    for step in plan:
        row = dst.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(step.table))).fetchone()
        if row and int(row["n"]) > 0:
            raise RuntimeError(f"target table {step.table} is not empty; migrate only into a fresh database")
    counts: dict[str, int] = {}
    with dst.transaction():
        for step in plan:
            counts[step.table] = copy_table(src, dst, step, mapping)
        reset_sequences(dst, plan)
    return counts
