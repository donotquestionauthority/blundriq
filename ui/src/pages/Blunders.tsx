import { useEffect, useState } from "react";
import { api } from "../api";
import { BLUNDER_CLASSES, DAY_OPTIONS, LAST_N_OPTIONS, MIN_OCCURRENCE_OPTIONS, TIME_CLASS_LABELS, defaultFilters, dismissBoard, getBlunders, markSeen, restoreBoard, toCard } from "../blunders";
import type { BlunderClass, BlunderFilters, TimeClass } from "../blunders";
import { CreatePuzzleModal } from "../components/CreatePuzzleModal";
import type { CreatePuzzleSource } from "../components/CreatePuzzleModal";
import { PositionList } from "../components/PositionCard/PositionList";
import { useApi } from "../hooks/useApi";
import { lichessAnalyzeUrl } from "../utils/chess";

/**
 * Recurring positions where I go wrong, worst first. A position is a board; it recurs when it
 * turns up in several games. Filters open on the settings row's defaults and are not
 * remembered. Dismissing hides a board here and from Practice; the Dismissed view restores it.
 * A board this list has never shown carries a NEW chip (the server's predicate, the same one
 * Home counts with). Once a response is on screen the page acknowledges exactly the boards that
 * response told it to — and every rendered response, an empty list included, records the look —
 * so Home's count is spent only for what was actually shown. A response that lost to a newer
 * request, or arrived after the page was left, is never acknowledged.
 */

const ACCENT: Record<BlunderClass, string> = { miss: "border-l-rose-700", blunder: "border-l-red-500", mistake: "border-l-orange-500", inaccuracy: "border-l-yellow-500" };

const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const chip = (on: boolean) => `rounded border px-2.5 py-1 text-xs font-medium ${on ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900" : "border-zinc-300 text-zinc-500 dark:border-zinc-700"}`;
const action = "rounded border border-zinc-300 px-3 py-1.5 text-sm hover:border-zinc-500 disabled:opacity-50 dark:border-zinc-700";

function List({ filters, setFilters }: { filters: BlunderFilters; setFilters: (f: BlunderFilters) => void }) {
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [creating, setCreating] = useState<CreatePuzzleSource | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const key = JSON.stringify(filters);
  const { data, isLoading, error, refetch, isStale } = useApi(async () => {
    const r = await getBlunders(filters, page);
    // A dismissal can empty the last page: step back onto the new last page.
    if (page > 0 && page > r.total_pages - 1) setPage(r.total_pages - 1);
    return r;
  }, [key, page]);

  // Acknowledge a response only once it is on screen: after commit, and only if useApi accepted
  // it (a response that lost to a newer request never becomes `data`; one that arrives after
  // unmount never reaches an effect). `data` changes exactly once per accepted response.
  useEffect(() => {
    if (!data || isStale) return;
    markSeen(data.to_acknowledge).catch((e: unknown) => console.warn("could not acknowledge the list:", e));
  }, [data, isStale]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const change = (patch: Partial<BlunderFilters>) => {
    setPage(0);
    setFilters({ ...filters, ...patch });
  };

  async function toggleDismissed(fen: string, dismissed: boolean) {
    setBusy(fen);
    setFailure(null);
    try {
      await (dismissed ? restoreBoard(fen) : dismissBoard(fen));
      refetch();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

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
          Time class
          <select aria-label="Time class" className={`${select} mt-1 block`} value={filters.time_class} onChange={(e) => change({ time_class: e.target.value as TimeClass })}>
            {(Object.keys(TIME_CLASS_LABELS) as TimeClass[]).map((t) => (
              <option key={t} value={t}>
                {TIME_CLASS_LABELS[t]}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Classifications">
          {BLUNDER_CLASSES.map((c) => {
            const on = filters.classifications.includes(c);
            // The last chip cannot be turned off: an empty set would mean "the defaults" to the server.
            return (
              <button key={c} type="button" aria-pressed={on} disabled={on && filters.classifications.length === 1} className={chip(on)} onClick={() => change({ classifications: on ? filters.classifications.filter((x) => x !== c) : [...filters.classifications, c] })}>
                {c}
              </button>
            );
          })}
        </div>
        <button type="button" aria-pressed={filters.show_dismissed} className={chip(filters.show_dismissed)} onClick={() => change({ show_dismissed: !filters.show_dismissed })}>
          {filters.show_dismissed ? `← Active (${data?.active_count ?? "…"})` : `Dismissed (${data?.dismissed_count ?? "…"})`}
        </button>
      </div>

      <div className="space-y-3 pt-4">
        {failure && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {failure}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        )}
        {isLoading && !data && <p className="text-sm text-zinc-500">Loading…</p>}
        {data && (
          <div className={isStale ? "opacity-50" : ""}>
            <div className="mb-2 flex items-center justify-between text-sm text-zinc-500">
              <span>{filters.show_dismissed ? `${data.dismissed_count} dismissed positions` : `${data.active_count} positions${data.new_count > 0 ? ` · ${data.new_count} new` : ""} · ${data.dismissed_count} dismissed`}</span>
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
              <p className="py-8 text-center text-sm text-zinc-500">{filters.show_dismissed ? "No dismissed positions." : "No recurring positions for these filters."}</p>
            ) : (
              <PositionList
                items={cards}
                overlaySuspended={creating !== null}
                accent={(item) => `${item.topClassification ? ACCENT[item.topClassification] : ""} ${item.dismissed ? "opacity-60" : ""}`}
                headerRight={(item) => (
                  <button
                    type="button"
                    aria-label={item.dismissed ? "Restore" : "Dismiss"}
                    title={item.dismissed ? "Restore" : "Dismiss"}
                    disabled={busy === item.fen}
                    className="shrink-0 text-sm text-zinc-400 hover:text-zinc-900 disabled:opacity-50 dark:hover:text-zinc-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      void toggleDismissed(item.fen, !!item.dismissed);
                    }}
                  >
                    {item.dismissed ? "↩" : "✕"}
                  </button>
                )}
                overlayActions={(item) => (
                  <>
                    <a href={lichessAnalyzeUrl(item.moves, item.ply, item.color)} target="_blank" rel="noreferrer" className={action}>
                      Analyze on Lichess
                    </a>
                    <button type="button" className={action} onClick={() => setCreating({ source: "blunder", fen: item.fen, color: item.color === "white" ? "w" : "b", movePlayed: item.movePlayed, bestMove: item.bestMove, bestLine: item.bestLine, lineNames: item.lineNames, moves: item.moves, ply: item.ply })}>
                      Create puzzle
                    </button>
                    <button type="button" className={action} disabled={busy === item.fen} onClick={() => void toggleDismissed(item.fen, !!item.dismissed)}>
                      {item.dismissed ? "Restore" : "Dismiss"}
                    </button>
                  </>
                )}
              />
            )}
          </div>
        )}
      </div>

      {creating && <CreatePuzzleModal key={creating.fen} source={creating} onClose={() => setCreating(null)} onCreated={(made) => setToast(made.visible ? "Puzzle created" : "Puzzle created, but hidden for now: this board is dismissed, or your repertoire already covers the position")} />}
      {toast && (
        <div role="status" className="fixed bottom-6 left-1/2 z-[60] max-w-md -translate-x-1/2 rounded-lg border border-zinc-200 bg-white px-4 py-2 text-sm shadow-2xl dark:border-zinc-800 dark:bg-zinc-900">
          ✓ {toast}
        </div>
      )}
    </div>
  );
}

export default function Blunders() {
  const [filters, setFilters] = useState<BlunderFilters | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Record<string, unknown>>("/settings")
      .then((s) => setFilters(defaultFilters(s)))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight">Blunders</h1>
      <p className="mb-4 mt-1 text-sm text-zinc-500">Recurring positions where I go wrong, worst first. A board marked NEW has not been shown here before.</p>
      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
      {filters && <List filters={filters} setFilters={setFilters} />}
    </div>
  );
}
