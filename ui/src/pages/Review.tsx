import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useApi } from "../hooks/useApi";
import { OPENING_ALL, REVIEW_DEFAULTS, REVIEW_TIME_CLASS_LABELS, getHabitGames, getReviewPage, isStaleOpeningError, lineText, pointsAMonth, positionPath, readOpenSnapshot, readReviewSettings, reviewSettingsKey, reviewSettingsSearch } from "../review";
import type { HabitGame, LostWin, ReviewHabit, ReviewOpenSnapshot, ReviewPosition, ReviewSettings, ReviewTimeClass } from "../review";
import { PositionCard } from "../components/ReviewBits";
import { daysAgo } from "../blunders";
import type { From } from "../utils/returnTo";

/**
 * The Review page over GET /review: the positions costing Rob points now ("Where you're losing
 * points"), the ones that look fixed ("Fixed?"), his mistake habits, and lost wins — everything
 * derived server-side per request under two settings, the time class and the opening.
 *
 * The settings are the URL's query string, the defaults left out; what is expanded (the Fixed?,
 * Mistake habits and Lost wins sections, and each open habit) is this history entry's state, keyed
 * by the settings it was taken under. Every link out (a position, a game) carries the way back
 * with that snapshot, so Close and Back both return to the same view; the top bar's link, with no
 * state, is the default view. Nothing is stored anywhere else.
 *
 * A focused opening that no longer qualifies (422 `unknown opening key`) resets to All openings
 * with a one-line notice; any other error stays an error. Every request belongs to a view: a
 * response from an earlier view (a settings change, the recovery, unmount) is dropped.
 */

const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const SECTION = { fixed: "fixed", habits: "habits", lost: "lost" } as const;
const DEFAULT_SECTIONS: readonly string[] = [SECTION.habits];
const LOST_WINS_STEP = 50;

function Chevron({ open }: { open: boolean }) {
  return <span className={`inline-block w-3 text-xs text-zinc-400 transition-transform ${open ? "rotate-90" : ""}`}>▸</span>;
}

function Section({ title, count, open, onToggle, children }: { title: string; count: string; open?: boolean; onToggle?: () => void; children: ReactNode }) {
  const collapsible = onToggle !== undefined;
  return (
    <section className="rounded-lg border border-zinc-200 dark:border-zinc-800">
      {collapsible ? (
        <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-center gap-3 px-3 py-3 text-left sm:px-4">
          <Chevron open={!!open} />
          <h2 className="min-w-0 grow text-base font-semibold sm:text-lg">{title}</h2>
          <span className="whitespace-nowrap text-xs tabular-nums text-zinc-500">{count}</span>
        </button>
      ) : (
        <div className="flex items-center gap-3 px-3 py-3 sm:px-4">
          <h2 className="min-w-0 grow text-base font-semibold sm:text-lg">{title}</h2>
          <span className="whitespace-nowrap text-xs tabular-nums text-zinc-500">{count}</span>
        </div>
      )}
      {(!collapsible || open) && <div className="space-y-2 px-3 pb-4 sm:px-4">{children}</div>}
    </section>
  );
}

const resultWord = (r: string | null) => (r === "win" ? "Won" : r === "draw" ? "Drew" : r === "loss" ? "Lost" : "—");

function GameLink({ id, ply, from, children = "Review →" }: { id: number; ply: number; from: From; children?: ReactNode }) {
  return (
    <Link to={`/review/${id}?ply=${ply}`} state={{ from }} className="whitespace-nowrap underline">
      {children}
    </Link>
  );
}

/** The Lost wins games, fifty at a time: every row is in the page response. */
function LostWinsList({ rows, from }: { rows: LostWin[]; from: From }) {
  const [shown, setShown] = useState(LOST_WINS_STEP);
  const remaining = rows.length - shown;
  return (
    <>
      <ul className="divide-y divide-zinc-200 text-xs dark:divide-zinc-800">
        {rows.slice(0, shown).map((g) => (
          <li key={g.chess_game_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5">
            <GameLink id={g.chess_game_id} ply={g.anchor_ply} from={from} />
            <span>
              {g.opponent_username ?? "—"}
              {g.opponent_rating ? ` (${g.opponent_rating})` : ""}
            </span>
            <span className="text-zinc-500">{daysAgo(g.played_at) ?? "—"}</span>
            <span className="text-zinc-600 dark:text-zinc-400">
              {g.peak_es != null ? `Winning (ES ${Math.round(g.peak_es)})` : "Winning"}
              {g.anchor_move ? `, turned at ${g.anchor_move} (−${Math.round(g.cost)})` : ""}, {resultWord(g.result).toLowerCase()}.
            </span>
            {g.reviewed && <span className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">✓ reviewed</span>}
          </li>
        ))}
      </ul>
      {remaining > 0 && (
        <button type="button" onClick={() => setShown((n) => n + LOST_WINS_STEP)} className="mt-2 text-xs underline">
          Show {Math.min(remaining, LOST_WINS_STEP)} more ({remaining} remaining)
        </button>
      )}
    </>
  );
}

interface HabitGamesState {
  loading: boolean;
  error: string | null;
  rows: HabitGame[];
  total: number;
  page: number;
}

const TREND_WORDS: Record<NonNullable<ReviewHabit["trend"]>, string> = { worse: "Getting worse", improving: "Improving", steady: "Steady" };

function HabitRow({ h, open, onToggle, state, onLoadMore, from }: { h: ReviewHabit; open: boolean; onToggle: () => void; state: HabitGamesState | undefined; onLoadMore: () => void; from: From }) {
  return (
    <div className="rounded border border-zinc-200 dark:border-zinc-800" data-testid="habit" data-habit={h.id}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        <button type="button" onClick={onToggle} aria-expanded={open} className="flex min-w-0 grow basis-48 items-center gap-2 text-left">
          <Chevron open={open} />
          <span className="text-sm font-medium">{h.label}</span>
        </button>
        <span className="whitespace-nowrap text-xs tabular-nums text-zinc-500">{h.rate_per_100.toFixed(1)} per 100 games</span>
        {h.trend && <span className="whitespace-nowrap rounded-full border border-zinc-300 px-2 py-0.5 text-[10px] uppercase tracking-wide text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">{TREND_WORDS[h.trend]}</span>}
        <span className="whitespace-nowrap text-xs tabular-nums text-zinc-600 dark:text-zinc-400">{pointsAMonth(h.points_per_month)}</span>
        {h.practice_theme && (
          <Link to={`/practice?type=motif&subtype=${encodeURIComponent(h.practice_theme)}`} className="whitespace-nowrap text-xs underline">
            Drill this
          </Link>
        )}
      </div>
      {open && (
        <div className="border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
          {!state || (state.loading && state.rows.length === 0) ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : state.error ? (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {state.error}
            </p>
          ) : state.rows.length === 0 ? (
            <p className="text-sm text-zinc-500">No games under this filter.</p>
          ) : (
            <>
              <ul className="divide-y divide-zinc-200 text-xs dark:divide-zinc-800">
                {state.rows.map((g) => (
                  <li key={g.chess_game_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5">
                    <GameLink id={g.chess_game_id} ply={g.anchor_ply} from={from} />
                    <span>{g.opponent_username ?? "—"}</span>
                    <span className="text-zinc-500">{daysAgo(g.played_at) ?? "—"}</span>
                    <span>{resultWord(g.result)}</span>
                    <span className="font-mono">{g.anchor_move ?? "—"}</span>
                    <span className="tabular-nums text-zinc-500">−{Math.round(g.cost)}</span>
                    {g.reviewed && <span className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">✓ reviewed</span>}
                  </li>
                ))}
              </ul>
              {state.rows.length < state.total && (
                <button type="button" onClick={onLoadMore} disabled={state.loading} className="mt-2 text-xs underline disabled:opacity-50">
                  {state.loading ? "Loading…" : `Load more (${state.rows.length} of ${state.total})`}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function Review() {
  const location = useLocation();
  const navigate = useNavigate();
  // The settings live in the URL and the expansion in this history entry's state: both are read
  // here on every render, and every change writes the entry (replace, never push — a filter or a
  // toggle is not a Back step).
  const settings = readReviewSettings(new URLSearchParams(location.search));
  const { timeClass, opening } = settings;
  const snapshot = readOpenSnapshot(location.state, settings);
  const openSections = new Set(snapshot ? snapshot.sections : DEFAULT_SECTIONS);
  const openHabits = new Set(snapshot ? snapshot.habits : []);
  const [notice, setNotice] = useState<string | null>(null);
  const [habitGames, setHabitGames] = useState<Record<string, HabitGamesState>>({});
  const requested = useRef(new Set<string>());
  const settingsRef = useRef(settings);
  settingsRef.current = settings;

  // A view generation, bumped whenever what is on screen changes so that an outstanding request
  // no longer describes it; a response is used only if its generation is still current.
  const viewGen = useRef(0);
  const nextView = () => {
    viewGen.current += 1;
    requested.current = new Set();
    setHabitGames({});
  };
  useEffect(
    () => () => {
      viewGen.current += 1;
    },
    [],
  );

  const ownWrite = useRef(false);
  const writeEntry = useCallback(
    (s: ReviewSettings, sections: Iterable<string>, habits: Iterable<string>) => {
      const open: ReviewOpenSnapshot = { sections: [...sections], habits: [...habits], key: reviewSettingsKey(s) };
      ownWrite.current = true;
      navigate({ pathname: location.pathname, search: reviewSettingsSearch(s) }, { replace: true, state: { open } });
    },
    [navigate, location.pathname],
  );

  // A settings change this page did not make (the top bar, Back) is a fresh view too.
  const settingsKey = reviewSettingsKey(settings);
  const seenKey = useRef(settingsKey);
  const recovering = useRef(false);
  useEffect(() => {
    if (seenKey.current === settingsKey) return;
    seenKey.current = settingsKey;
    nextView();
    if (!recovering.current) setNotice(null);
    recovering.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settingsKey]);

  // A history entry this page did not write (the top bar, Back) is a fresh visit even when its
  // settings equal the current ones: a notice from an earlier visit goes.
  const seenEntry = useRef(location.key);
  useEffect(() => {
    if (seenEntry.current === location.key) return;
    seenEntry.current = location.key;
    if (ownWrite.current) ownWrite.current = false;
    else setNotice(null);
  }, [location.key]);

  const recoverToAllOpenings = useCallback(() => {
    nextView();
    recovering.current = true;
    writeEntry({ ...settingsRef.current, opening: OPENING_ALL }, DEFAULT_SECTIONS, []);
    setNotice("That opening no longer has enough games — showing all openings.");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [writeEntry]);

  const fetchPage = useCallback(async () => {
    const gen = viewGen.current;
    try {
      return await getReviewPage(timeClass, opening);
    } catch (err) {
      // A 422 for a focus the user has since left must not undo their newer choice.
      if (isStaleOpeningError(err) && opening !== OPENING_ALL && gen === viewGen.current) {
        recoverToAllOpenings();
        return await getReviewPage(timeClass, OPENING_ALL);
      }
      throw err;
    }
  }, [timeClass, opening, recoverToAllOpenings]);
  const { data, isLoading, error, isStale } = useApi(fetchPage, [timeClass, opening]);

  function loadHabit(id: string, page: number) {
    const gen = viewGen.current;
    requested.current.add(id);
    setHabitGames((prev) => ({ ...prev, [id]: { loading: true, error: null, rows: prev[id]?.rows ?? [], total: prev[id]?.total ?? 0, page: prev[id]?.page ?? 0 } }));
    getHabitGames(id, timeClass, opening, page)
      .then((res) => {
        if (gen !== viewGen.current) return;
        setHabitGames((prev) => ({ ...prev, [id]: { loading: false, error: null, rows: page === 1 ? res.rows : [...(prev[id]?.rows ?? []), ...res.rows], total: res.total, page: res.page } }));
      })
      .catch((err: unknown) => {
        if (gen !== viewGen.current) return;
        if (isStaleOpeningError(err) && opening !== OPENING_ALL) {
          recoverToAllOpenings();
          return;
        }
        const message = err instanceof Error ? err.message : "Failed to load games";
        setHabitGames((prev) => ({ ...prev, [id]: { loading: false, error: message, rows: prev[id]?.rows ?? [], total: prev[id]?.total ?? 0, page: prev[id]?.page ?? 0 } }));
      });
  }

  // An open habit restored from this entry (mount, Back, a game's Close) has no rows yet: fetch
  // them once the page is here, and only for a habit on screen.
  useEffect(() => {
    if (!data || isStale || !openSections.has(SECTION.habits)) return;
    for (const h of data.habits) if (openHabits.has(h.id) && !requested.current.has(h.id)) loadHabit(h.id, 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, isStale, location.key]);

  function toggleSection(id: string) {
    const next = new Set(openSections);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    writeEntry(settings, next, openHabits);
  }

  function toggleHabit(id: string) {
    const next = new Set(openHabits);
    const willOpen = !next.has(id);
    if (willOpen) next.add(id);
    else next.delete(id);
    writeEntry(settings, openSections, next);
    if (willOpen && !requested.current.has(id)) loadHabit(id, 1);
  }

  function changeSettings(next: Partial<ReviewSettings>) {
    setNotice(null);
    nextView();
    writeEntry({ ...settings, ...next }, DEFAULT_SECTIONS, []);
  }
  const isDefault = settingsKey === reviewSettingsKey(REVIEW_DEFAULTS);

  // Every link out carries this entry, expansion included, as its way back.
  const from: From = { pathname: location.pathname, search: location.search, open: (location.state as { open?: unknown } | null)?.open };
  const lineOf = new Map<string, string>();
  if (data) for (const p of [...data.positions.ranked, ...data.positions.fixed]) lineOf.set(`${p.colour}:${p.key}`, lineText(p.line_san));
  const card = (p: ReviewPosition, fixed: boolean) => <PositionCard key={`${p.colour}:${p.key}`} p={p} to={positionPath(p.colour, p.key, settings)} from={from} parentLine={p.parent_key ? (lineOf.get(`${p.colour}:${p.parent_key}`) ?? null) : null} fixed={fixed} months={data?.meta.history_months ?? 12} />;

  const months = data?.meta.history_months ?? 12;
  const openingOptions = data?.filter.openings ?? [];
  const focusKnown = opening === OPENING_ALL || openingOptions.some((o) => o.key === opening);

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight">Review</h1>
      <p className="mb-4 mt-1 text-sm text-zinc-500">
        Positions and habits ranked by what they are costing you now. Your last {months} months prove a leak; your recent games decide its place.
      </p>

      <div className="flex flex-wrap items-end gap-3 border-b border-zinc-200 pb-3 dark:border-zinc-800">
        <label className="text-xs text-zinc-500">
          Time class
          <select aria-label="Time class" className={`${select} mt-1 block`} value={timeClass} onChange={(e) => changeSettings({ timeClass: e.target.value as ReviewTimeClass })}>
            {(Object.keys(REVIEW_TIME_CLASS_LABELS) as ReviewTimeClass[]).map((t) => (
              <option key={t} value={t}>
                {REVIEW_TIME_CLASS_LABELS[t]}
              </option>
            ))}
          </select>
        </label>
        <label className="min-w-0 text-xs text-zinc-500">
          Opening
          <select aria-label="Opening" className={`${select} mt-1 block max-w-[calc(100vw-2rem)]`} value={opening} onChange={(e) => changeSettings({ opening: e.target.value })}>
            <option value={OPENING_ALL}>All openings</option>
            {!focusKnown && <option value={opening}>{opening}</option>}
            {openingOptions.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label} ({o.games})
              </option>
            ))}
          </select>
        </label>
        {!isDefault && (
          <button type="button" onClick={() => changeSettings(REVIEW_DEFAULTS)} className="pb-1 text-xs text-zinc-500 underline hover:text-zinc-900 dark:hover:text-zinc-100">
            Reset filters
          </button>
        )}
      </div>

      {notice && (
        <div role="status" className="mt-3 flex items-center justify-between gap-3 rounded border border-zinc-200 px-3 py-2 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice(null)} className="whitespace-nowrap text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
            Dismiss
          </button>
        </div>
      )}

      <div className="space-y-3 pt-4">
        {error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        )}
        {isLoading && !data && <p className="text-sm text-zinc-500">Loading…</p>}
        {data && (
          <div className={`space-y-3 ${isStale ? "opacity-50" : ""}`}>
            <Section title="Where you're losing points" count={`${data.positions.ranked.length} position${data.positions.ranked.length === 1 ? "" : "s"}`}>
              {data.positions.ranked.length === 0 ? (
                <p className="py-2 text-sm text-zinc-500">
                  {data.meta.games_counted === 0 && data.meta.games_without_prefix > 0
                    ? `No position has enough games in your last ${months} months yet: ${data.meta.games_without_prefix} games still need their opening moves fetched.`
                    : "No position is costing you points right now."}
                </p>
              ) : (
                <div className="grid gap-2 lg:grid-cols-2">{data.positions.ranked.map((p) => card(p, false))}</div>
              )}
              {data.meta.games_without_prefix > 0 && data.meta.games_counted > 0 && <p className="text-[11px] text-zinc-500">{data.meta.games_without_prefix} games in this period are not counted yet: their opening moves have not been fetched.</p>}
            </Section>

            <Section title="Fixed?" count={`${data.positions.fixed.length}`} open={openSections.has(SECTION.fixed)} onToggle={() => toggleSection(SECTION.fixed)}>
              <p className="text-xs text-zinc-500">Positions that cost you points over the last {months} months and look better now, or that you have not reached lately: the last {months} months beside now.</p>
              {data.positions.fixed.length === 0 ? <p className="py-1 text-sm text-zinc-500">Nothing here yet.</p> : <div className="grid gap-2 lg:grid-cols-2">{data.positions.fixed.map((p) => card(p, true))}</div>}
            </Section>

            <Section title="Mistake habits" count={`${data.habits.length}`} open={openSections.has(SECTION.habits)} onToggle={() => toggleSection(SECTION.habits)}>
              <p className="text-xs text-zinc-500">From your last {data.meta.window_games.toLocaleString()} analysed games.</p>
              {data.habits.length === 0 ? (
                <p className="py-1 text-sm text-zinc-500">No habit has three mistakes under this filter.</p>
              ) : (
                data.habits.map((h) => <HabitRow key={h.id} h={h} open={openHabits.has(h.id)} onToggle={() => toggleHabit(h.id)} state={habitGames[h.id]} onLoadMore={() => loadHabit(h.id, (habitGames[h.id]?.page ?? 1) + 1)} from={from} />)
              )}
            </Section>

            <Section title="Lost wins" count={`${data.lost_wins.total}`} open={openSections.has(SECTION.lost)} onToggle={() => toggleSection(SECTION.lost)}>
              <p className="text-xs text-zinc-500">Games you were winning and didn't win.</p>
              {data.lost_wins.total === 0 ? <p className="py-1 text-sm text-zinc-500">None under this filter.</p> : <LostWinsList key={settingsKey} rows={data.lost_wins.games} from={from} />}
            </Section>
          </div>
        )}
      </div>
    </div>
  );
}
