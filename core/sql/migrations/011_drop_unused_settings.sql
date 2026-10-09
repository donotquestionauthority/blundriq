-- Twelve settings carried over from the earlier version had no reader: the mix catch-up window,
-- the weak-motif pool's size and theme cap, the six thresholds of a Stats page that was not
-- rebuilt, and the old Review worklist's two pool floors and half-life in games. The model no
-- longer defines them (core/settings.py ignores unknown keys, so either order of deploy and
-- migration is safe); this removes them from the stored row. Every other key is untouched.
UPDATE public.settings
SET data = data - ARRAY[
        'puzzle_mix_window',
        'weak_motif_target_count',
        'weak_motif_theme_cap_pct',
        'coverage_practice_min_attempts',
        'coverage_mastered_success_pct',
        'coverage_recent_games_window',
        'coverage_weakness_min_occurrences',
        'coverage_weakness_miss_rate_pct',
        'coverage_strength_found_rate_pct',
        'review_pool_floor_line',
        'review_pool_floor_eco',
        'review_recency_half_life_games'
    ]::text[],
    updated_at = now()
WHERE id = 1;
