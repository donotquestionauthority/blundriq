import json
from pathlib import Path

from core import settings


def test_defaults_match_the_original_deployments_values() -> None:
    """Globals AND the user's page-filter overrides, resolved."""
    s = settings.Settings()
    assert s.blunder_threshold == 200 and s.cc0_difficulty_tier == "very_hard" and s.time_class_focus == "rapid_plus"
    assert s.blunders_default_min_occurrences == 2 and s.blunders_default_classifications == [
        "blunder",
        "miss",
        "mistake",
    ]
    assert s.blunders_default_window_days == 20 and s.deviations_default_min_occurrences == 2
    # The old system had two settings of the same name: a global the generators read and a
    # page filter the original user had set lower. They are separate fields here because they mean
    # different things — what gets built, and what gets shown.
    assert s.blunder_puzzle_min_occurrences == 3 and s.deviation_puzzle_min_occurrences == 3
    assert s.blunders_default_last_n_games == 500 and s.deviations_default_last_n_games == 500
    assert set(s.ai_prompts) == {"a", "b", "c"} and s.ai_prompts["a"].model.startswith("claude-")


def test_nested_prompt_round_trip(conn) -> None:
    s = settings.Settings()
    s.ai_prompts["a"].text = "changed"
    settings.save(conn, s)
    assert settings.load(conn).ai_prompts["a"].text == "changed"


def test_round_trip(conn) -> None:
    assert settings.load(conn) == settings.Settings()  # no row yet → defaults
    s = settings.Settings(daily_puzzle_target=25)
    settings.save(conn, s)
    assert settings.load(conn).daily_puzzle_target == 25


def test_schema_has_descriptions_for_every_field() -> None:
    props = settings.schema()["properties"]
    missing = [k for k, v in props.items() if not v.get("description")]
    assert not missing, missing


GOLDEN_SCHEMA = Path(__file__).resolve().parents[1] / "ui" / "src" / "pages" / "__fixtures__" / "settings-schema.json"


def test_the_preferences_tests_render_the_real_schema_and_defaults() -> None:
    """The UI tests load these files; they must be what GET /settings/schema and a fresh
    GET /settings serve. Regenerate each with `json.dumps(..., indent=2, sort_keys=True)` of
    `settings.schema()` and `settings.Settings().model_dump(mode="json")`."""
    assert json.loads(GOLDEN_SCHEMA.read_text()) == settings.schema()
    defaults = GOLDEN_SCHEMA.with_name("settings-defaults.json")
    assert json.loads(defaults.read_text()) == settings.Settings().model_dump(mode="json")


def test_every_setting_has_a_shape_the_preferences_form_can_edit() -> None:
    """The form dispatches on `type`, an `enum`, or an `anyOf` of consts; anything else would
    fall through to a text box (a model-typed field showed `[object Object]`)."""
    props = settings.schema()["properties"]
    unhandled = [
        k
        for k, v in props.items()
        if "type" not in v and "enum" not in v and not any("const" in a for a in v.get("anyOf", []))
    ]
    assert not unhandled, unhandled
    assert props["ai_line_prompt"]["type"] == "object"


DROPPED = (
    "puzzle_mix_window",
    "weak_motif_target_count",
    "weak_motif_theme_cap_pct",
    "coverage_practice_min_attempts",
    "coverage_mastered_success_pct",
    "coverage_recent_games_window",
    "coverage_weakness_min_occurrences",
    "coverage_weakness_miss_rate_pct",
    "coverage_strength_found_rate_pct",
    "review_pool_floor_line",
    "review_pool_floor_eco",
    "review_recency_half_life_games",
)


def test_the_dropped_settings_are_gone_from_the_model() -> None:
    assert not set(DROPPED) & set(settings.Settings.model_fields)


def test_migration_011_removes_the_twelve_keys_and_keeps_every_other(fresh_db_url: str, tmp_path: Path) -> None:
    """A version-10 database whose row holds the twelve keys and a real setting the player changed:
    the upgrade removes exactly the twelve, the changed value survives, and the row loads."""
    import psycopg
    from psycopg import sql
    from psycopg.rows import DictRow, dict_row

    from core import schema
    from tests.test_schema import FIXTURES, _scratch

    url = _scratch(fresh_db_url, "settings_v10")
    v10 = tmp_path / "v10"
    v10.mkdir()
    for n, path in schema.migration_files():
        if n <= 10:
            (v10 / path.name).write_text(path.read_text())
    stored = settings.Settings().model_dump(mode="json") | {k: 7 for k in DROPPED} | {"daily_puzzle_target": 25}
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row) as c:
        c.execute(sql.SQL((FIXTURES / "schema_baseline.sql").read_text()))  # type: ignore[arg-type]  # repo fixture
        c.execute("INSERT INTO schema_version (version) VALUES (0)")
        assert schema.upgrade(c, v10)[-1] == 10
        c.execute("INSERT INTO settings (id, data) VALUES (1, %s::jsonb)", (json.dumps(stored),))
        c.commit()
        assert schema.upgrade(c, to=11) == [11]
        row = c.execute("SELECT data FROM settings WHERE id = 1").fetchone()
        assert row is not None
        data = row["data"]
        assert not set(DROPPED) & set(data)
        assert set(data) == set(stored) - set(DROPPED)
        assert data["daily_puzzle_target"] == 25
        assert settings.load(c).daily_puzzle_target == 25


def test_a_row_still_holding_the_twelve_keys_loads(conn) -> None:
    """The new code before the migration runs: unknown keys are ignored."""
    stored = settings.Settings(daily_puzzle_target=25).model_dump(mode="json") | {k: 7 for k in DROPPED}
    conn.execute(
        "INSERT INTO settings (id, data) VALUES (1, %s::jsonb) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
        (json.dumps(stored),),
    )
    assert settings.load(conn).daily_puzzle_target == 25
