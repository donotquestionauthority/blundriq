from core.chess import eligibility


def test_standard_is_analysable_and_960_is_not() -> None:
    assert eligibility.is_analysable("standard")
    assert eligibility.is_analysable(None)
    assert not eligibility.is_analysable("chess960")


def test_sql_predicate_names_the_alias() -> None:
    assert eligibility.analysable_sql("cg") == "cg.variant IN ('standard')"
