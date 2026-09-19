"""The one place that decides which games are eligible for analysis-derived work.

Rule (plan §4.5): Chess960 games are kept as history and counted, but never
analysed, matched against the repertoire, turned into puzzles, or reviewed.
Every worklist, page query, and migration script applies this rule through
this module, never through its own literal, so the rule cannot be applied in
two places and forgotten in a third.
"""

from __future__ import annotations

from core.constants import ANALYSABLE_VARIANTS

# SQL fragment for a chess_games alias. Usage: f"WHERE {analysable_sql('cg')}".
# Rendered as a literal list so it can be used in any query, including ones
# assembled without parameters (migration scripts, CLI reports).
_VARIANT_LIST = ", ".join(f"'{v}'" for v in ANALYSABLE_VARIANTS)


def analysable_sql(alias: str = "chess_games") -> str:
    """Predicate selecting games the pipeline may analyse, match, puzzle or review."""
    return f"{alias}.variant IN ({_VARIANT_LIST})"


def is_analysable(variant: str | None) -> bool:
    """Python-side check for the same rule (importers, matcher)."""
    return (variant or "standard") in ANALYSABLE_VARIANTS
