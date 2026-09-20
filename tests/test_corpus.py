"""Sampling the Lichess corpus, storing it, and the index the serve path needs.

The published CSV is about six million rows. What matters here is that the sample is
the best puzzles per theme and rating rather than an arbitrary slice, that it is the
same sample every time, that the stored position is the one the solver sees, and that a
reload can never wipe a working corpus or quietly lose the covering index.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import DictRow

from core.puzzles import corpus
from core.settings import Settings

HEADER = ["PuzzleId", "FEN", "Moves", "Rating", "RatingDeviation", "Popularity", "NbPlays", "Themes", "GameUrl", "Tags"]

# A real corpus row: the FEN is before White's setup move e2e4, so the puzzle the solver
# sees begins after it, with Black to move.
SETUP_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

ORIGINAL_TOPK_INDEX = (
    f"CREATE INDEX {corpus.TOPK_INDEX} ON public.lichess_puzzles"
    " USING btree (popularity DESC, nb_plays DESC, puzzle_id)"
    " INCLUDE (fen, rating, themes, color, solution_line)"
)


def _row(
    puzzle_id: str,
    *,
    rating: int = 1500,
    popularity: int = 90,
    nb_plays: int = 1000,
    themes: str = "fork",
    moves: str = "e2e4 e7e5 g1f3",
    fen: str = SETUP_FEN,
) -> list[str]:
    return [puzzle_id, fen, moves, str(rating), "75", str(popularity), str(nb_plays), themes, "url", ""]


def _config(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_the_sample_keeps_the_most_popular_per_cell() -> None:
    stats = corpus.ImportStats()
    rows = [HEADER] + [_row(f"p{i:02d}", popularity=i) for i in range(20)]
    kept = corpus.sample(rows, _config(cc0_import_cap_per_cell=10), stats)
    assert sorted(kept) == [f"p{i}" for i in range(10, 20)]
    assert stats.scanned == 20 and stats.sampled == 10 and stats.cells == 1


def test_ties_break_the_same_way_every_time() -> None:
    """The sample has to be reproducible, or a reload silently changes the pool."""
    rows = [HEADER] + [_row(f"p{i:02d}", popularity=50, nb_plays=100) for i in range(20)]
    first = corpus.sample(rows, _config(cc0_import_cap_per_cell=10), corpus.ImportStats())
    second = corpus.sample(rows, _config(cc0_import_cap_per_cell=10), corpus.ImportStats())
    assert sorted(first) == sorted(second)


def test_each_theme_and_rating_band_gets_its_own_cell() -> None:
    stats = corpus.ImportStats()
    rows = [
        HEADER,
        _row("a", rating=1100, themes="fork"),
        _row("b", rating=1100, themes="pin"),
        _row("c", rating=1900, themes="fork"),
    ]
    kept = corpus.sample(rows, _config(cc0_import_cap_per_cell=10), stats)
    assert sorted(kept) == ["a", "b", "c"] and stats.cells == 3


def test_a_puzzle_in_several_themes_is_kept_once(clean: psycopg.Connection[DictRow]) -> None:
    kept = corpus.sample([HEADER, _row("a", themes="fork pin skewer")], _config(), corpus.ImportStats())
    assert list(kept) == ["a"]
    assert kept["a"].themes == ["fork", "pin", "skewer"]


@pytest.mark.parametrize(
    "bad",
    [
        _row("a", rating=100),  # below the imported range
        _row("a", rating=9000),  # above it
        _row("a", themes="crushing"),  # no theme this system serves
        _row("a", moves="e2e4"),  # a setup move and nothing to solve
        ["a"],  # a short line
    ],
)
def test_rows_that_cannot_be_served_are_dropped(bad: list[str]) -> None:
    assert corpus.sample([HEADER, bad], _config(), corpus.ImportStats()) == {}


def test_the_stored_position_is_the_one_the_solver_sees() -> None:
    stats = corpus.ImportStats()
    kept = corpus.sample([HEADER, _row("a")], _config(), stats)
    rows = corpus.materialise(kept, stats)
    assert len(rows) == 1
    stored = rows[0]
    # After White's e4, Black is to move and the solution is the rest, in SAN.
    assert stored.fen.split()[1] == "b" and stored.color == "b"
    assert stored.solution_line == ["e5", "Nf3"]


def test_an_unreplayable_row_is_counted_and_skipped() -> None:
    stats = corpus.ImportStats()
    kept = corpus.sample([HEADER, _row("a", moves="e2e4 h8h1")], _config(), stats)
    assert corpus.materialise(kept, stats) == []
    assert stats.unreplayable == 1


def test_an_empty_sample_never_deletes_the_corpus(clean: psycopg.Connection[DictRow]) -> None:
    stats = corpus.ImportStats()
    kept = corpus.sample([HEADER, _row("a")], _config(), stats)
    corpus.load(clean, corpus.materialise(kept, stats), stats)
    assert stats.loaded == 1

    empty = corpus.ImportStats()
    corpus.load(clean, [], empty)
    assert empty.failed == 1 and empty.loaded == 0
    remaining = clean.execute("SELECT count(*) AS n FROM lichess_puzzles").fetchone()
    assert remaining and remaining["n"] == 1
    clean.execute("DELETE FROM lichess_puzzles")
    clean.commit()


def test_the_index_check_reads_structure_not_words(clean: psycopg.Connection[DictRow]) -> None:
    """An index with the same column names in a different order contains every substring
    a naive check would look for, and is still the wrong index."""
    corpus.verify_topk_index(clean)
    clean.execute(f"DROP INDEX {corpus.TOPK_INDEX}")
    clean.execute(
        f"CREATE INDEX {corpus.TOPK_INDEX} ON lichess_puzzles"
        " (nb_plays DESC, popularity DESC, puzzle_id)"
        " INCLUDE (fen, rating, themes, color, solution_line)"
    )
    clean.commit()
    with pytest.raises(RuntimeError, match="does not match"):
        corpus.verify_topk_index(clean)
    clean.execute(f"DROP INDEX {corpus.TOPK_INDEX}")
    clean.commit()
    with pytest.raises(RuntimeError, match="missing"):
        corpus.verify_topk_index(clean)
    # Put the real index back: this test shares one scratch database with the rest.
    clean.execute(ORIGINAL_TOPK_INDEX)
    clean.commit()
    corpus.verify_topk_index(clean)


def test_a_puzzle_evicted_from_one_cell_survives_in_another() -> None:
    """A puzzle sits in one cell per theme it matches, and the cells fill independently.
    Dropping its payload the first time any cell lets go would lose a puzzle that is still
    among the best in another theme — and would leave the survivor set referring to it."""
    cap = 10
    rows = [HEADER]
    # A crowd of popular pure-fork puzzles, enough to push the cell over the cap.
    rows += [_row(f"fork{i:02d}", popularity=90 - i, themes="fork") for i in range(cap)]
    # Unpopular for fork, but the only thing in the pin cell.
    rows.append(_row("both", popularity=1, themes="fork pin"))

    stats = corpus.ImportStats()
    kept = corpus.sample(rows, _config(cc0_import_cap_per_cell=cap), stats)

    assert "both" in kept, "evicted from the fork cell, still the best pin puzzle there"
    assert kept["both"].themes == ["fork", "pin"], "its payload came through intact"
    assert corpus.materialise(kept, stats), "a survivor must still be materialisable"
