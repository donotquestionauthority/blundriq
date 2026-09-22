-- Home page: "new since your last visit", where "new" is a board the Blunders list has never shown.

-- The list acknowledges exactly the boards it rendered (core/blunders.py mark_seen), so a
-- board that arrived after the list was read stays new, and one that is on a page never
-- opened stays new. The first look declares everything then listed as known. The old marker
-- is cleared: its value would say the list was looked at long before this page existed.
ALTER TABLE public.players RENAME COLUMN home_seen_at TO blunders_seen_at;
UPDATE public.players SET blunders_seen_at = NULL;

CREATE TABLE public.seen_blunder_boards (
    player_id integer NOT NULL,
    canonical_fen text NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT seen_blunder_boards_pkey PRIMARY KEY (player_id, canonical_fen),
    CONSTRAINT seen_blunder_boards_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE
);
