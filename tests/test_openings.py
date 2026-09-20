"""Opening name canonicalisation (ported from the old test_openings.py cases)."""

from core.chess.openings import canonical_opening

# (raw name, eco, family, variation or None = family only)
CASES = [
    ("Caro-Kann Defense", "B10", "Caro-Kann Defense", "Caro-Kann Defense"),
    ("Caro Kann Defense", "B10", "Caro-Kann Defense", "Caro-Kann Defense"),
    ("Caro Kann Defense 2.Nf3 d5", "B10", "Caro-Kann Defense", "Caro-Kann Defense"),
    (
        "Caro Kann Defense Advance Short Variation with 4 Nf3...e6",
        "B12",
        "Caro-Kann Defense",
        "Caro-Kann Defense: Advance Short",
    ),
    (
        "Caro Kann Defense Exchange Variation 3...cxd5 4.Nf3 Nc6",
        "B13",
        "Caro-Kann Defense",
        "Caro-Kann Defense: Exchange",
    ),
    ("English Opening: Caro-Kann Defensive System", "A11", "English Opening", None),
    ("Queens Pawn Opening Horwitz Defense 2.Bf4", "A40", "Horwitz Defense", "Horwitz Defense"),
    ("Englund Gambit 2.dxe5 Nc6", "A40", "Englund Gambit", "Englund Gambit"),
    ("Modern Defense with 1 d4...3.Bf4 d6 4.e3 Nf6", "A40", "Modern Defense", "Modern Defense"),
    ("Queen's Pawn Game: Modern Defense", "A40", "Modern Defense", "Modern Defense"),
    ("Queen's Pawn Game", "D00", "Queen's Pawn Game", "Queen's Pawn Game"),
    ("London System, with Bd3", "A48", "London System", None),
    ("Queens Pawn Opening Accelerated London System 2...e6 3.e3", "D00", "London System", None),
    ("Queens Pawn Opening Zukertort Chigorin Variation 3.Bf4 Nf6 4.e3", "D02", "Queen's Pawn Game", None),
    ("Zukertort Opening: Slav Invitation", "A04", "Réti Opening", None),
    ("Sicilian Defense Bowdler Attack", "B20", "Sicilian Defense", "Sicilian Defense: Bowdler"),
    ("Indian Game 2.Bf4 d6 3.e3", "A45", "Indian Defense", "Indian Defense"),
    ("Birds Opening", "A02", "Bird Opening", "Bird Opening"),
    (
        "Nimzowitsch Larsen Attack Classical Variation 2.Bb2 c6",
        "A01",
        "Nimzo-Larsen Attack",
        "Nimzo-Larsen Attack: Classical",
    ),
    ("Nimzowitsch Larsen Attack Dutch Variation 2.Bb2 e6", "A01", "Nimzo-Larsen Attack", None),
    ("English Opening Anglo Dutch Defense 2.Nc3 Nf6 3.g3 e6 4.Bg2", "A10", "English Opening", None),
    ("Van t Kruijs Opening 1...c5", "A00", "Van't Kruijs Opening", "Van't Kruijs Opening"),
    ("Scotch Game...4.Nxd4 Nxd4 5.Qxd4 d6", "C45", "Scotch Game", "Scotch Game"),
    ("Scandinavian Defense 2.e5", "B01", "Scandinavian Defense", "Scandinavian Defense"),
]


def test_family_and_variation() -> None:
    for name, eco, fam, var in CASES:
        f, v = canonical_opening(name, eco)
        assert f == fam, (name, f)
        if var is not None:
            assert v == var, (name, v)


def test_unknown_is_none_pair() -> None:
    for name in (None, "", "   ", "Undefined", "unknown"):
        assert canonical_opening(name, "A00") == (None, None)


def test_eco_guard_blocks_only_contradicting_codes() -> None:
    assert canonical_opening("Caro-Kann Defense", None)[0] == "Caro-Kann Defense"
    assert canonical_opening("Caro-Kann Defense", "")[0] == "Caro-Kann Defense"
    assert canonical_opening("Caro-Kann Defensive System", "A11")[0] != "Caro-Kann Defense"


def test_unclassified_name_is_its_own_family() -> None:
    assert canonical_opening("Amar Opening", "A00") == ("Amar Opening", "Amar Opening")
