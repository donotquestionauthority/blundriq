from core import settings


def test_defaults_match_phase0_values() -> None:
    s = settings.Settings()
    assert s.blunder_threshold == 200 and s.cc0_difficulty_tier == "very_hard" and s.time_class_focus == "rapid_plus"


def test_round_trip(conn) -> None:
    assert settings.load(conn) == settings.Settings()  # no row yet → defaults
    s = settings.Settings(daily_puzzle_target=25)
    settings.save(conn, s)
    assert settings.load(conn).daily_puzzle_target == 25


def test_schema_has_descriptions_for_every_field() -> None:
    props = settings.schema()["properties"]
    missing = [k for k, v in props.items() if not v.get("description")]
    assert not missing, missing
