from core import settings


def test_defaults_match_effective_phase0_values() -> None:
    """Globals AND the user's page-filter overrides, resolved (see ship 001 settings-mapping.py)."""
    s = settings.Settings()
    assert s.blunder_threshold == 200 and s.cc0_difficulty_tier == "very_hard" and s.time_class_focus == "rapid_plus"
    assert s.blunders_default_min_occurrences == 2 and s.blunders_default_classifications == [
        "blunder",
        "miss",
        "mistake",
    ]
    assert s.blunders_default_window_days == 20 and s.deviations_default_min_occurrences == 2
    # The old system had two settings of the same name: a global the generators read and a
    # page filter Rob had set lower. They are separate fields here because they mean
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
