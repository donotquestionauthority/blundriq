-- Home page: "new since your last visit", where the visit is a look at the Blunders list.

-- The marker moves only when the Blunders page has shown the list (core/blunders.py
-- mark_seen), never when Home renders, so a new board waits until it has actually been looked
-- at. The old app's value is cleared: the first look is a first look, not "since last spring".
ALTER TABLE public.players RENAME COLUMN home_seen_at TO blunders_seen_at;
UPDATE public.players SET blunders_seen_at = NULL;

-- A game is imported hours before its blunders exist (analysis is capped per run), so a
-- board's newness is dated by when its games' analysis first completed, never by import.
-- Set once, on the first completion: reprocessing at a deeper depth is not news.
ALTER TABLE public.chess_games ADD COLUMN analyzed_at timestamp with time zone;
UPDATE public.chess_games SET analyzed_at = created_at WHERE analysis_status = 'completed';

-- Likewise a deviation is news when the match landed, and a rematch keeps the first date
-- (phase 5's Deviations page reads this the same way).
ALTER TABLE public.game_repertoire_results ADD COLUMN created_at timestamp with time zone DEFAULT now() NOT NULL;
