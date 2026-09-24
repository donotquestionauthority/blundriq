-- Deviations page: "new since your last visit", where "new" is a pattern the Deviations list has never shown.

-- The same model as seen_blunder_boards (migration 003): the list acknowledges exactly the
-- patterns it rendered (core/deviations.py mark_seen); the first look declares everything
-- then listed as known. A pattern is a book, a chapter, the ply and the expected move.
ALTER TABLE public.players ADD COLUMN deviations_seen_at timestamp with time zone;

CREATE TABLE public.seen_deviations (
    player_id integer NOT NULL,
    book_id integer NOT NULL,
    chapter_id integer NOT NULL,
    deviated_at_ply integer NOT NULL,
    expected_move text NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT seen_deviations_pkey PRIMARY KEY (player_id, book_id, chapter_id, deviated_at_ply, expected_move),
    CONSTRAINT seen_deviations_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE,
    CONSTRAINT seen_deviations_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE,
    CONSTRAINT seen_deviations_chapter_id_fkey FOREIGN KEY (chapter_id) REFERENCES public.chapters(id) ON DELETE CASCADE
);

-- What a served repertoire puzzle was truncated to when it was served, so an attempt on it is
-- graded against the line the player saw even if the results that set the truncation have
-- since been matched again (core/puzzles/attempts.py). NULL for a standard puzzle.
ALTER TABLE public.player_puzzle_exposure ADD COLUMN presentation_ply integer;
