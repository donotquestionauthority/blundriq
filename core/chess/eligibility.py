"""The one place that decides which games are eligible for analysis-derived work.

Rule (docs/decisions/001 and 003 cover the related choices): Chess960 games are kept as history and counted, but never
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


def window_cte() -> str:
    """The training window: the player's most recent `%(window)s` ANALYSABLE games (Chess960
    is history, not training material, so it never takes a window slot). A CTE body for
    `WITH window_games AS (...)`; binds %(pid)s and %(window)s."""
    return (
        "window_games AS ("
        " SELECT pg.chess_game_id FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id"
        f" WHERE pg.player_id = %(pid)s AND {analysable_sql('cg')}"
        " ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC LIMIT %(window)s)"
    )


# Time classes that count as evidence when the focus is the player's study time controls.
FOCUS_TIME_CLASSES = ("rapid", "classical", "correspondence")


def evidence_sql(alias: str, focus: str) -> str:
    """Predicate selecting games whose findings may become puzzles.

    Distinct from the window above, and applied *after* it on purpose. A Chess960 game
    is not training material at all, so it never takes a window slot; a blitz game is
    still one of the player's recent games, it just is not what they are studying.
    Filtering it out before the window would silently reach further back in time.

    `focus` is the `time_class_focus` setting: "rapid_plus" is rapid and slower,
    "all" is every time class, including games whose class the platform did not give.
    """
    if focus == "all":
        return "TRUE"
    classes = ", ".join(f"'{c}'" for c in FOCUS_TIME_CLASSES)
    return f"{alias}.time_class IN ({classes})"
