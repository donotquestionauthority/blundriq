import { useEffect, useState } from "react";
import { api } from "../api";
import { DAY_OPTIONS, LAST_N_OPTIONS, MIN_OCCURRENCE_OPTIONS, TIME_CLASS_LABELS } from "../blunders";
import type { TimeClass } from "../blunders";
import { CreatePuzzleModal } from "../components/CreatePuzzleModal";
import type { CreatePuzzleSource } from "../components/CreatePuzzleModal";
import { PositionList } from "../components/PositionCard/PositionList";
import { defaultFilters, getDeviations, markSeen, toCard } from "../deviations";
import type { DeviationFilters } from "../deviations";
import { useApi } from "../hooks/useApi";
import { lichessAnalyzeUrl } from "../utils/chess";

/**
 * Where I keep leaving my own repertoire, most often first. A pattern is a book, a chapter, the
 * ply and the move the line expected there; it recurs when I leave the line at that point in
 * several games. There is no dismissal: a deviation that should not count is a line that should
 * not be active, and the Repertoire page switches lines off. NEW and its acknowledgement work
 * exactly as on Blunders: the page posts what each accepted response told it to, once rendered.
 */

const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const action = "rounded border border-zinc-300 px-3 py-1.5 text-sm hover:border-zinc-500 disabled:opacity-50 dark:border-zinc-700";

function List({ filters, setFilters }: { filters: DeviationFilters; setFilters: (f: DeviationFilters) => void }) {
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState<CreatePuzzleSource | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const key = JSON.stringify(filters);
  const { data, isLoading, error, isStale } = useApi(async () => {
    const r = await getDeviations(filters, page);
    if (page > 0 && page > r.total_pages - 1) setPage(r.total_pages - 1);
    return r;
  }, [key, page]);

  // Acknowledge a response only once it is on screen: after commit, and only if useApi accepted it.
  useEffect(() => {
    if (!data || isStale) return;
    markSeen(data.to_acknowledge).catch((e: unknown) => console.warn("could not acknowledge the list:", e));
  }, [data, isStale]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const change = (patch: Partial<DeviationFilters>) => {
    setPage(0);
    setFilters({ ...filters, ...patch });
  };

  const cards = (data?.positions ?? []).map(toCard);
  const window_ = filters.last_n_games > 0 ? `n${filters.last_n_games}` : filters.since_days ? `d${filters.since_days}` : "all";

  return (
    <div>
      <div className="flex flex-wrap items-end gap-3 border-b border-zinc-200 pb-3 dark:border-zinc-800">
        <label className="text-xs text-zinc-500">
          Window
          <select
            aria-label="Window"
            className={`${select} mt-1 block`}
            value={window_}
            onChange={(e) => {
              const v = e.target.value;
              change(v === "all" ? { since_days: null, last_n_games: 0 } : v[0] === "n" ? { since_days: null, last_n_games: Number(v.slice(1)) } : { since_days: Number(v.slice(1)), last_n_games: 0 });
            }}
          >
            {!DAY_OPTIONS.includes(filters.since_days ?? DAY_OPTIONS[0]) && <option value={window_}>Last {filters.since_days} days</option>}
            {filters.last_n_games > 0 && !LAST_N_OPTIONS.includes(filters.last_n_games) && <option value={window_}>Last {filters.last_n_games} games</option>}
            {DAY_OPTIONS.map((d) => (
              <option key={`d${d}`} value={`d${d}`}>
                Last {d} days
              </option>
            ))}
            {LAST_N_OPTIONS.map((n) => (
              <option key={`n${n}`} value={`n${n}`}>
                Last {n} games
              </option>
            ))}
            <option value="all">All time</option>
          </select>
        </label>
        <label className="text-xs text-zinc-500">
          Min seen
          <select aria-label="Min seen" className={`${select} mt-1 block`} value={filters.min_occurrences} onChange={(e) => change({ min_occurrences: Number(e.target.value) })}>
            {[...new Set([...MIN_OCCURRENCE_OPTIONS, filters.min_occurrences])].sort((a, b) => a - b).map((n) => (
              <option key={n} value={n}>
                {n}+
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-zinc-500">
          Color
          <select aria-label="Color" className={`${select} mt-1 block`} value={filters.color ?? "all"} onChange={(e) => change({ color: e.target.value === "all" ? null : (e.target.value as "white" | "black") })}>
            <option value="all">Both</option>
            <option value="white">White</option>
            <option value="black">Black</option>
          </select>
        </label>
        <label className="text-xs text-zinc-500">
          Time class
          <select aria-label="Time class" className={`${select} mt-1 block`} value={filters.time_class} onChange={(e) => change({ time_class: e.target.value as TimeClass })}>
            {(Object.keys(TIME_CLASS_LABELS) as TimeClass[]).map((t) => (
              <option key={t} value={t}>
                {TIME_CLASS_LABELS[t]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="space-y-3 pt-4">
        {error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        )}
        {isLoading && !data && <p className="text-sm text-zinc-500">Loading…</p>}
        {data && (
          <div className={isStale ? "opacity-50" : ""}>
            <div className="mb-2 flex items-center justify-between text-sm text-zinc-500">
              <span>
                {data.total} deviation patterns{data.new_count > 0 ? ` · ${data.new_count} new` : ""}
              </span>
              {data.total_pages > 1 && (
                <span className="flex items-center gap-2">
                  <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} className="disabled:opacity-30">
                    ← Prev
                  </button>
                  <span className="text-xs">
                    {data.page + 1} / {data.total_pages}
                  </span>
                  <button type="button" disabled={page >= data.total_pages - 1} onClick={() => setPage(page + 1)} className="disabled:opacity-30">
                    Next →
                  </button>
                </span>
              )}
            </div>
            {cards.length === 0 ? (
              <p className="py-8 text-center text-sm text-zinc-500">No deviations for these filters. Your games are matched against the active lines on the Repertoire page.</p>
            ) : (
              <PositionList
                items={cards}
                overlaySuspended={creating !== null}
                accent={() => "border-l-amber-500"}
                overlayActions={(item) => (
                  <>
                    <a href={lichessAnalyzeUrl(item.moves, item.ply, item.color)} target="_blank" rel="noreferrer" className={action}>
                      Analyze on Lichess
                    </a>
                    <button type="button" className={action} onClick={() => setCreating({ source: "deviation", fen: item.fen, color: item.color === "white" ? "w" : "b", movePlayed: item.mostCommonPlayed, bestMove: item.expectedMove, lineNames: item.lineNames, moves: item.moves, ply: item.ply })}>
                      Create puzzle
                    </button>
                  </>
                )}
              />
            )}
          </div>
        )}
      </div>

      {creating && <CreatePuzzleModal key={creating.fen} source={creating} onClose={() => setCreating(null)} onCreated={(made) => setToast(made.visible ? "Puzzle created" : "Puzzle created, but hidden for now: your repertoire already covers the position")} />}
      {toast && (
        <div role="status" className="fixed bottom-6 left-1/2 z-[60] max-w-md -translate-x-1/2 rounded-lg border border-zinc-200 bg-white px-4 py-2 text-sm shadow-2xl dark:border-zinc-800 dark:bg-zinc-900">
          ✓ {toast}
        </div>
      )}
    </div>
  );
}

export default function Deviations() {
  const [filters, setFilters] = useState<DeviationFilters | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Record<string, unknown>>("/settings")
      .then((s) => setFilters(defaultFilters(s)))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight">Deviations</h1>
      <p className="mb-4 mt-1 text-sm text-zinc-500">Where I keep leaving my own repertoire, most often first. A pattern marked NEW has not been shown here before.</p>
      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
      {filters && <List filters={filters} setFilters={setFilters} />}
    </div>
  );
}
