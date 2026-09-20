"""Canonical opening names from the dirty (name, ECO) pairs the platforms send.

`canonical_opening(name, eco)` -> (family, variation) or (None, None) when the
game has no usable opening name. `family` groups games (e.g. "Caro-Kann Defense");
`variation` is the actionable label ("Caro-Kann Defense: Advance"). FAMILY_RULES
and _ALIASES are reference data — a chess-opening standard, not a setting.
"""

from __future__ import annotations

import re
import unicodedata

# ── Move-sequence tail ────────────────────────────────────────────────────────
# chess.com appends the played moves into the name. Cut at the FIRST move token:
# a move number ("2.", "3...", "4 ."), a bare ellipsis, a castling token, or a
# "with <n>" connector ("Modern Defense with 1 d4"). Everything from there on is
# move text, not opening identity. Names with no such token (most lichess names,
# "London System, with Bd3") survive intact.
_MOVE_TAIL = re.compile(
    r"\s*(?:\d{1,2}\s*\.{1,3}|\.{2,3}|O-?O\b|with\s+\d).*$",
    re.IGNORECASE,
)

_WS = re.compile(r"\s+")

# ── Punctuation / spelling aliases ────────────────────────────────────────────
# Applied in order to the move-stripped name to produce the canonical display
# spelling. Matching (FAMILY_RULES) runs against the lower-cased alias output, so
# both the label and the grouping see one spelling per opening.
_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcaro[\s-]?kann\b", re.I), "Caro-Kann"),
    (re.compile(r"\bnimzo(?:witsch)?[\s-]?larsen\b", re.I), "Nimzo-Larsen"),
    (re.compile(r"\bnimzo(?:witsch)?[\s-]?indian\b", re.I), "Nimzo-Indian"),
    (re.compile(r"\bbogo[\s-]?indian\b", re.I), "Bogo-Indian"),
    (re.compile(r"\bqueens\s+pawn\s+(?:opening|game)\b", re.I), "Queen's Pawn Game"),
    (re.compile(r"\bkings\s+pawn\s+(?:opening|game)\b", re.I), "King's Pawn Game"),
    (re.compile(r"\bqueens\s+gambit\b", re.I), "Queen's Gambit"),
    (re.compile(r"\bkings\s+indian\b", re.I), "King's Indian"),
    (re.compile(r"\bkings\s+gambit\b", re.I), "King's Gambit"),
    (re.compile(r"\bkings\s+fianchetto\b", re.I), "King's Fianchetto"),
    (re.compile(r"\bbirds?\s+opening\b", re.I), "Bird Opening"),
    (re.compile(r"\b(?:r[eé]ti)\b", re.I), "Réti"),
    (re.compile(r"\bgr[uü]nfeld\b", re.I), "Grünfeld"),
    (re.compile(r"\bst\.?\s+george\b", re.I), "St. George"),
    (re.compile(r"\bvan'?\s*t\s+kruijs\b", re.I), "Van't Kruijs"),
    (re.compile(r"\bdefence\b", re.I), "Defense"),
]

# ── Family taxonomy ───────────────────────────────────────────────────────────
# Ordered priority rules: (keyword phrases, canonical family, eco_guard).
# First rule whose keyword appears in the alias-normalized lower-cased name wins.
# ORDER ENCODES TWO PRINCIPLES:
#   1. Strong full-board openings (English, Réti, Nimzo-Larsen, ...) before the
#      specific d4 systems, so "English Opening: Caro-Kann Defensive System"
#      (an English, ECO A11) resolves to English Opening, not Caro-Kann.
#   2. Specific defenses/systems before the weak generic qualifiers
#      (Queen's Pawn Game / Indian / King's Pawn), so "Queens Pawn Opening
#      Horwitz Defense" resolves to Horwitz, while a bare "Queen's Pawn Game"
#      (nothing more specific) stays Queen's Pawn Game.
# eco_guard, when set, is (letter, lo, hi): the rule is SKIPPED only when the ECO
# is present AND falls outside [letter+lo .. letter+hi]. A missing/blank ECO
# never blocks a name match (so a Caro-Kann with no ECO still classifies). The
# guard exists to break known name collisions (English "Caro-Kann system" A11 vs
# the real Caro-Kann Defense B10–B19).
#
# The London / Zukertort / Queen's-Pawn boundary (memo): "London System" and
# "Accelerated London" route to London System; the d4-Bf4 "Queens Pawn ...
# Zukertort Chigorin" cluster has no specific keyword and falls to Queen's Pawn
# Game. This is the documented judgment surface — tweak the list to move it.
FAMILY_RULES: list[tuple[tuple[str, ...], str, tuple[str, int, int] | None]] = [
    # Strong full-board openings (own the game even with a sub-structure)
    (("english opening",), "English Opening", None),
    (("english defense",), "English Defense", None),
    (("nimzo-larsen",), "Nimzo-Larsen Attack", None),
    (("réti", "reti"), "Réti Opening", None),
    (("zukertort opening",), "Réti Opening", ("A", 4, 9)),
    (("king's indian attack",), "King's Indian Attack", None),
    (("bird opening",), "Bird Opening", None),
    (("polish opening", "sokolsky"), "Polish Opening", None),
    (("grob",), "Grob Opening", None),
    (("van't kruijs",), "Van't Kruijs Opening", None),
    (("mieses opening",), "Mieses Opening", None),
    (("van geet", "dunst"), "Van Geet Opening", None),
    (("vienna",), "Vienna Game", None),
    # Specific d4 / e4 systems and defenses
    (("london system", "accelerated london"), "London System", None),
    (("trompowsky",), "Trompowsky Attack", None),
    (("englund",), "Englund Gambit", None),
    (("horwitz",), "Horwitz Defense", None),
    (("modern defense",), "Modern Defense", None),
    (("pirc",), "Pirc Defense", None),
    (("alekhine",), "Alekhine Defense", None),
    (("caro-kann",), "Caro-Kann Defense", ("B", 10, 19)),
    (("scandinavian", "center counter"), "Scandinavian Defense", None),
    (("sicilian", "alapin"), "Sicilian Defense", None),
    (("french defense",), "French Defense", None),
    (("philidor",), "Philidor Defense", None),
    (("petrov", "petroff", "russian game"), "Petrov Defense", None),
    (("ruy lopez", "spanish game"), "Ruy Lopez", None),
    (("italian game", "giuoco"), "Italian Game", None),
    (("scotch",), "Scotch Game", None),
    (("ponziani",), "Ponziani Opening", None),
    (("four knights",), "Four Knights Game", None),
    (("nimzo-indian",), "Nimzo-Indian Defense", None),
    (("bogo-indian",), "Bogo-Indian Defense", None),
    (("queen's indian", "queens indian"), "Queen's Indian Defense", None),
    (("grünfeld",), "Grünfeld Defense", None),
    (("benoni",), "Benoni Defense", None),
    (("benko", "volga"), "Benko Gambit", None),
    (("catalan",), "Catalan Opening", None),
    (("queen's gambit",), "Queen's Gambit", None),
    (("semi-slav",), "Semi-Slav Defense", None),
    (("slav defense",), "Slav Defense", None),
    (("dutch defense",), "Dutch Defense", None),
    # Weak generic qualifiers — only when nothing more specific matched
    (("hungarian opening",), "Hungarian Opening", None),
    (("king's fianchetto",), "King's Fianchetto Opening", None),
    (("queen's pawn game",), "Queen's Pawn Game", None),
    (("king's pawn game",), "King's Pawn Game", None),
    (("nimzowitsch defense",), "Nimzowitsch Defense", None),
    (("indian game", "indian defense"), "Indian Defense", None),
]

# Leading generic qualifiers stripped from the VARIATION label when a more
# specific family was matched ("Queen's Pawn Game Horwitz Defense" -> the Horwitz
# variation should read "Horwitz Defense", not carry the d4 qualifier).
_QUALIFIER_PREFIXES = ("Queen's Pawn Game", "King's Pawn Game", "Indian Game")

# Trailing filler dropped from a variation descriptor.
_FILLER = {"variation", "system", "line", "main", "the", "defense", "attack", "opening", "game"}


def _clean(name: str | None) -> str:
    """Move-strip + alias-normalize + whitespace-collapse a raw opening name."""
    if not name:
        return ""
    s = unicodedata.normalize("NFC", str(name)).strip()
    s = s.replace("’", "'").replace("‘", "'")  # curly -> straight apostrophe
    if not s or s.lower() in ("undefined", "unknown"):
        return ""
    s = _MOVE_TAIL.sub("", s)
    for pat, repl in _ALIASES:
        s = pat.sub(repl, s)
    s = _WS.sub(" ", s).strip(" ,:-")
    return s


def _eco_contradicts(eco: str | None, guard: tuple[str, int, int] | None) -> bool:
    """True only when ``eco`` is present, parseable, and outside the guard range.

    A blank or unparseable ECO never blocks a name match; a parseable ECO in a
    different letter band or outside [lo, hi] DOES (that's what breaks the
    English "Caro-Kann system" A11 vs real Caro-Kann B10–19 collision).
    """
    if guard is None or not eco:
        return False
    eco = str(eco).strip().upper()
    if len(eco) < 3 or not eco[1:3].isdigit():
        return False  # unparseable ECO never blocks a name match
    letter, lo, hi = guard
    return not (eco[0] == letter and lo <= int(eco[1:3]) <= hi)


def _match_family(cleaned: str, eco: str | None) -> str | None:
    """Return the canonical family for a cleaned name, or None to fall back."""
    low = cleaned.lower()
    for keywords, family, guard in FAMILY_RULES:
        if _eco_contradicts(eco, guard):
            continue
        for kw in keywords:
            if kw in low:
                return family
    return None


def _format_variation(cleaned: str, family: str) -> str:
    """Build the actionable variation label from a cleaned name + its family."""
    rest = cleaned
    # Strip a leading generic qualifier when the real family is more specific.
    if family not in _QUALIFIER_PREFIXES:
        for q in _QUALIFIER_PREFIXES:
            if rest.startswith(q):
                rest = rest[len(q) :].strip(" ,:-")
                break
    if not rest:
        return family
    # Reduce "<family> <descriptor...>" / "<family>: <descriptor...>" to
    # "<family>: <descriptor>" with at most two non-filler descriptor words.
    head, sep, tail = rest.partition(":")
    if sep and head.strip() == family:
        rest = tail.strip()
    elif rest.startswith(family):
        rest = rest[len(family) :].strip(" ,:-")
    elif rest != family and family in rest:
        return rest  # family appears mid-string; keep the cleaned name as-is
    if not rest or rest == family:
        return family
    words = [w for w in rest.replace(":", " ").split() if w.lower().strip(",.:-") not in _FILLER]
    descriptor = " ".join(words[:2]).strip(" ,:-")
    return f"{family}: {descriptor}" if descriptor else family


def canonical_opening(name: str | None, eco: str | None = None) -> tuple[str | None, str | None]:
    """(family, variation), or (None, None) for a nameless/unclassifiable game."""
    cleaned = _clean(name)
    if not cleaned:
        return None, None
    family = _match_family(cleaned, eco)
    if family is None:
        # No taxonomy hit: the cleaned name is its own family + variation. Never
        # worse than the prior SPLIT_PART proxy, and the long tail (A00 oddities)
        # stays individually named.
        return cleaned, cleaned
    return family, _format_variation(cleaned, family)
