-- The review step's proof that the published generation is what the current inputs give.
--
-- review_detection_state.fingerprint: a digest of everything the detector was given (the
-- window's games and their stored analysis, the repertoire and motif context, the knobs, the
-- schema version, the code) by the run that published the generation on the table, written in
-- the same upsert as computed_at. fingerprint_at: that upsert's now(), the same instant as its
-- computed_at. A fingerprint is proof only while fingerprint_at = computed_at; a run that finds
-- its own fingerprint proven reads nothing and writes nothing. NULL on the existing row, and on
-- a row a client from before this migration inserts, means no proof; such a client's update
-- keeps the two columns and moves computed_at, so the proof it leaves no longer matches. Either
-- way the next run tags every window game and records one.
ALTER TABLE public.review_detection_state ADD COLUMN fingerprint text;
ALTER TABLE public.review_detection_state ADD COLUMN fingerprint_at timestamp with time zone;
