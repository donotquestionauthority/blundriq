-- Blunders and Deviations: when each list was first looked at.

-- NEW is decided per visit (core/blunders.py): a visit marks NEW only if it started after the
-- list's first look, so the first-look visit acknowledges history as it pages without the
-- history turning into news on its next page. That needs the time of the first look, which
-- players.blunders_seen_at / deviations_seen_at (the last look, the date Home shows) stop
-- holding at the second look. An existing database has already had its first look: any time
-- before now is right, and the last look is the one on record.
ALTER TABLE public.players
    ADD COLUMN blunders_first_seen_at timestamp with time zone,
    ADD COLUMN deviations_first_seen_at timestamp with time zone;
UPDATE public.players
   SET blunders_first_seen_at = blunders_seen_at, deviations_first_seen_at = deviations_seen_at;
