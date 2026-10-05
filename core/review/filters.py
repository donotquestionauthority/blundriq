"""The two filters every Review read applies: time class and opening.

Time class is `focus` (the `time_class_focus` setting's classes, through
`core.chess.eligibility.evidence_sql`) or `all`. The opening key is `__all__`,
`{colour}:{family}` over `chess_games.canonical_family`, or `{colour}:__unclassified__` for
games with no family. It narrows the game set itself, so every section of the page is that
opening's. Whether a well-formed key names an opening the player plays is the page's to judge (it is
one of the options the page lists); a key that is not is a ReviewParamError whose text says
`unknown opening key`, which the client's stale-key recovery reads.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.chess.eligibility import evidence_sql

TIME_CLASSES = ("focus", "all")
OPENING_ALL = "__all__"
UNCLASSIFIED = "__unclassified__"
COLOURS = ("white", "black")


class ReviewParamError(ValueError):
    """A bad request parameter: 422."""


@dataclass(frozen=True)
class Opening:
    """A parsed opening key; `colour` None is every opening."""

    colour: str | None = None
    family: str | None = None  # None with a colour: the games with no family

    @property
    def key(self) -> str:
        if self.colour is None:
            return OPENING_ALL
        return f"{self.colour}:{self.family if self.family is not None else UNCLASSIFIED}"


def opening_key(colour: str, family: str | None) -> str:
    return Opening(colour, family).key


def parse_opening(key: str) -> Opening:
    if key == OPENING_ALL:
        return Opening()
    colour, sep, family = key.partition(":")
    if not sep or colour not in COLOURS or not family:
        raise ReviewParamError(f"unknown opening key: {key!r}")
    return Opening(colour, None if family == UNCLASSIFIED else family)


def time_class_sql(time_class: str, focus: str, cg: str = "cg") -> str:
    if time_class == "focus":
        return evidence_sql(cg, focus)
    if time_class == "all":
        return "TRUE"
    raise ReviewParamError(f"unknown time_class: {time_class!r}")


def opening_sql(opening: Opening, pg: str = "pg", cg: str = "cg") -> str:
    """A predicate over a player_games / chess_games pair. Binds %(op_colour)s and
    %(op_family)s (see `opening_params`)."""
    if opening.colour is None:
        return "TRUE"
    if opening.family is None:
        return f"({pg}.player_color = %(op_colour)s AND {cg}.canonical_family IS NULL)"
    return f"({pg}.player_color = %(op_colour)s AND {cg}.canonical_family = %(op_family)s)"


def opening_params(opening: Opening) -> dict[str, str | None]:
    return {"op_colour": opening.colour, "op_family": opening.family}
