-- Drill mode, endgame drills and the admin/global puzzle tier do not exist in this
-- system; their columns, constraints, indexes and one table came over with the schema
-- harvested from the old database. This removes them, so the puzzle tables describe
-- what is actually served: one player, one kind of puzzle, one row per exposure.

-- Exposure: every served item is a puzzle. `repertoire_drill` items were the old
-- drought-fill drill, which went with drill mode.
ALTER TABLE public.player_puzzle_exposure DROP CONSTRAINT ppe_item_kind_xor_chk;
ALTER TABLE public.player_puzzle_exposure DROP CONSTRAINT ppe_item_kind_chk;
DROP INDEX public.ix_ppe_player_line;
ALTER TABLE public.player_puzzle_exposure DROP COLUMN item_kind;
ALTER TABLE public.player_puzzle_exposure DROP COLUMN repertoire_line_id;
DELETE FROM public.player_puzzle_exposure WHERE puzzle_id IS NULL;
ALTER TABLE public.player_puzzle_exposure ALTER COLUMN puzzle_id SET NOT NULL;

-- Skip: the same, and the partial unique index becomes a plain one.
ALTER TABLE public.player_puzzle_skip DROP CONSTRAINT pps_item_kind_xor_chk;
ALTER TABLE public.player_puzzle_skip DROP CONSTRAINT pps_item_kind_chk;
DROP INDEX public.ux_pps_line;
DROP INDEX public.ux_pps_puzzle;
ALTER TABLE public.player_puzzle_skip DROP COLUMN item_kind;
ALTER TABLE public.player_puzzle_skip DROP COLUMN repertoire_line_id;
DELETE FROM public.player_puzzle_skip WHERE puzzle_id IS NULL;
ALTER TABLE public.player_puzzle_skip ALTER COLUMN puzzle_id SET NOT NULL;
CREATE UNIQUE INDEX ux_pps_puzzle ON public.player_puzzle_skip USING btree (player_id, scope, batch_id, puzzle_id);

-- Puzzles: 'endgame_drill' was the only other puzzle_kind, and there is no admin tier,
-- so player_id is never NULL and the two conflict targets lose a key column each.
DELETE FROM public.puzzles WHERE puzzle_kind <> 'line';
DROP INDEX public.ix_puzzles_admin_standard_fen;
DROP INDEX public.ix_puzzles_player_standard_fen;
ALTER TABLE public.puzzles DROP CONSTRAINT puzzles_line_requires_solution;
ALTER TABLE public.puzzles DROP CONSTRAINT puzzles_puzzle_kind_check;
ALTER TABLE public.puzzles DROP COLUMN puzzle_kind;
ALTER TABLE public.puzzles ALTER COLUMN solution_line SET NOT NULL;
ALTER TABLE public.puzzles ALTER COLUMN active SET NOT NULL;
ALTER TABLE public.puzzles ALTER COLUMN player_id SET NOT NULL;
CREATE UNIQUE INDEX ix_puzzles_player_standard_fen ON public.puzzles USING btree (player_id, canonical_fen)
    WHERE ((is_repertoire = false) AND (active = true));

-- Fed only the admin "puzzle candidates" mining panel, which is not ported. The
-- Blunders page's own dismiss list is dismissed_blunder_fens and is untouched.
DROP TABLE public.dismissed_puzzle_candidates;
