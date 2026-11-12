-- Which attempts count, and which path they were played from.
--
-- puzzle_attempts.counted: whether the attempt counts toward the day's solved total, the
-- streak and the spaced-repetition ladder. A session's first attempt counts when the puzzle is
-- due as it is recorded; every later attempt of the session inherits that. Every row recorded
-- before this migration is true: it counted where it counted, and history is not recounted.
--
-- puzzle_attempts.mode: the path the solver was on — 'practice' (the queue, the list, a link),
-- 'drill', or 'extra' (practice past a cleared queue). NULL means recorded before this
-- migration, from any path but extra practice, which did not exist; every reader treats NULL as
-- not 'extra'.
-- It decides only whether the attempt is practice evidence on the line report.
ALTER TABLE public.puzzle_attempts ADD COLUMN mode text;
ALTER TABLE public.puzzle_attempts ADD COLUMN counted boolean DEFAULT true NOT NULL;
ALTER TABLE public.puzzle_attempts ADD CONSTRAINT puzzle_attempts_mode_check
    CHECK ((mode = ANY (ARRAY['practice'::text, 'drill'::text, 'extra'::text])));
