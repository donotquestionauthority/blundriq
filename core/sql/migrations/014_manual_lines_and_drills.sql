-- Lines the player adds by hand from the line report, and drills of chosen lines.
--
-- repertoire_lines.origin: 'course' for a line the import wrote, 'manual' for one the player
-- added from a gap card. Every existing line is 'course'. A full replacement import never
-- switches off a manual line, nor a chapter that holds one.
--
-- puzzles.drilled_at: when a drill first served this repertoire puzzle. NULL on every existing
-- row means the puzzle was never asked for in a drill, and the deviation gate alone decides
-- whether it exists and is served; set once, never cleared, it keeps the puzzle regardless of
-- that gate.
--
-- puzzle_attempts.attempt_seq: the attempt's order within its load of the solver (1 = the first
-- move the player committed after the puzzle was loaded). NULL means the client did not number
-- its attempts (a row from before this column, or a queued record from an older client): among
-- such rows the row id is the only order there is.
ALTER TABLE public.repertoire_lines ADD COLUMN origin text DEFAULT 'course'::text NOT NULL;
ALTER TABLE public.repertoire_lines ADD CONSTRAINT repertoire_lines_origin_check CHECK ((origin = ANY (ARRAY['course'::text, 'manual'::text])));
ALTER TABLE public.puzzles ADD COLUMN drilled_at timestamp with time zone;
ALTER TABLE public.puzzle_attempts ADD COLUMN attempt_seq integer;
