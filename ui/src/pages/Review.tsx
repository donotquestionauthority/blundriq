import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useApi } from "../hooks/useApi";
import { GROUP_BY_LABELS, GROUP_BY_MODES, OPENING_ALL, REVIEW_DEFAULTS, REVIEW_TIME_CLASS_LABELS, bookRelationLabel, countLabel, getPoolEvents, getReviewPage, isStaleOpeningError, pieceLabelDisplay, pieceOrTheme, prettyToken, readOpenSnapshot, readReviewSettings, reviewSettingsKey, reviewSettingsSearch, touchPoolShown } from "../review";
import type { GroupByMode, OpeningFamily, OpeningSubgroup, PoolCategory, ReviewGameRow, ReviewOpenSnapshot, ReviewPool, ReviewSettings, ReviewTimeClass, ReviewedScope } from "../review";
import { ReviewedScopeToggle } from "../components/ReviewedScopeToggle";
import { daysAgo } from "../blunders";

/**
 * The Review worklist: category → node → game, over GET /review. Everything is derived server-side
 * per request, so a time-class, opening or group-by change is a fresh page; the To review / All
 * scope is the client's (both counts come with every node), and changing it only collapses open
 * drill-downs so they reload under the new scope. Opening problems opens expanded because it hosts
 * the Focus-opening and Group-by controls; every other category opens collapsed. Opening a node
 * loads its first page of games lazily and stamps the node shown (best effort) so the
 * representative rotates. A focused opening that no longer has review games (422 from the server)
 * resets to All openings with a one-line notice; any other error stays an error. A game row's
 * "Review →" opens the game's review at its anchor ply, carrying this page's location so the
 * review's Close returns here; the Lost wins list shows fifty games at a time.
 *
 * Every request belongs to a view. A drill request belongs to the drill view (a generation bumped
 * by each scope or server-param change, the stale-opening recovery and unmount); the page request
 * belongs to the page view (bumped by the same things except a scope change, which is client-only
 * and leaves the page request current). A response from an earlier view is dropped, success or
 * failure, so a slow request can neither overwrite a fresh drill-down nor restore page 2 alone
 * after an A → B → A round trip of the same filters — while a page request that outlives a scope
 * change still recovers from a stale opening.
 *
 * The four settings (time class, scope, focus opening, group-by) are the URL's query string, the
 * defaults left out; what is expanded is this history entry's state, keyed by the settings it was
 * taken under. A game link carries the snapshot with its way back, so Close and Back both return to
 * the same view, drill-downs reloaded; the top bar's link, with no state, is the default view
 * collapsed. Nothing is stored anywhere else.
 */

const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const SEVERITY_TITLE = "Severity — points lost, weighted toward recent games and discounted when only seen a few times. Higher = fix first.";

function ConfidenceBadge({ confidence }: { confidence: "high" | "low" }) {
  return confidence === "high" ? <span className="whitespace-nowrap rounded-full border border-emerald-600/40 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-700 dark:text-emerald-400">High</span> : <span className="whitespace-nowrap rounded-full border border-zinc-300 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-700">Low</span>;
}

/** Count, severity and badge; wraps under the label on a phone instead of pushing past the screen. */
function Meta({ count, severity, confidence }: { count: string; severity?: number; confidence?: "high" | "low" }) {
  return (
    <span className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-x-3 gap-y-1">
      <span className="whitespace-nowrap text-xs tabular-nums text-zinc-500">{count}</span>
      {severity !== undefined && (
        <span className="whitespace-nowrap text-xs tabular-nums text-zinc-600 dark:text-zinc-400" title={SEVERITY_TITLE}>
          Severity {severity.toFixed(1)}
        </span>
      )}
      {confidence && <ConfidenceBadge confidence={confidence} />}
    </span>
  );
}

function Chevron({ open }: { open: boolean }) {
  return <span className={`inline-block w-3 text-xs text-zinc-400 transition-transform ${open ? "rotate-90" : ""}`}>▸</span>;
}

// --- The game table (one row per game) ------------------------------------------------------------

const LOST_WINS_STEP = 50;

/** The Lost wins games, fifty at a time: the rows are all in the page response, so "Show more"
 *  reveals the next fifty without a request. A scope change starts again from the first fifty. */
function LostWinsList({ rows, scope }: { rows: ReviewGameRow[]; scope: ReviewedScope }) {
  const [shown, setShown] = useState(LOST_WINS_STEP);
  const [shownScope, setShownScope] = useState(scope);
  if (shownScope !== scope) {
    setShownScope(scope);
    setShown(LOST_WINS_STEP);
  }
  const remaining = rows.length - shown;
  return (
    <>
      <GameTable rows={rows.slice(0, shown)} showBestMove={false} scope={scope} />
      {remaining > 0 && (
        <button type="button" onClick={() => setShown((n) => n + LOST_WINS_STEP)} className="mt-2 text-xs underline">
          Show {Math.min(remaining, LOST_WINS_STEP)} more ({remaining} remaining)
        </button>
      )}
    </>
  );
}

/** The one place the link into a game's review is built: the anchor ply, and where to come back to. */
function GameTable({ rows, showBestMove, scope }: { rows: ReviewGameRow[]; showBestMove: boolean; scope: ReviewedScope }) {
  const location = useLocation();
  // The way back carries this entry's expansion as well, so an explicit Close finds it too.
  const from = { pathname: location.pathname, search: location.search, open: (location.state as { open?: unknown } | null)?.open };
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-zinc-500">
          <tr>
            <th className="py-1 font-normal">Game</th>
            <th className="py-1 font-normal">Opponent</th>
            <th className="py-1 font-normal">Date</th>
            <th className="py-1 font-normal">Phase</th>
            <th className="py-1 pr-4 text-right font-normal">Cost</th>
            <th className="py-1 font-normal">Piece / theme</th>
            {showBestMove && <th className="py-1 font-normal">Best</th>}
            {scope === "all" && <th className="py-1 font-normal">Status</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={`${e.chess_game_id}:${e.anchor_ply}`} className="border-t border-zinc-200 dark:border-zinc-800">
              <td className="py-1 pr-2 whitespace-nowrap">
                <Link to={`/review/${e.chess_game_id}?ply=${e.anchor_ply}`} state={{ from }} className="underline">
                  Review →
                </Link>
              </td>
              <td className="py-1 pr-2">
                {e.opponent_username || "—"}
                {e.extra_in_game ? <span className="ml-1.5 text-[10px] text-zinc-500">+{e.extra_in_game} more in this game</span> : null}
              </td>
              <td className="py-1 pr-2 whitespace-nowrap text-zinc-500">{daysAgo(e.played_at) ?? "—"}</td>
              <td className="py-1 pr-2 whitespace-nowrap text-zinc-500">{e.phase ? prettyToken(e.phase) : "—"}</td>
              <td className="py-1 pr-4 text-right font-mono font-semibold text-orange-600 dark:text-orange-400">{e.cost.toFixed(1)}</td>
              <td className="py-1 pr-2 whitespace-nowrap text-zinc-500">{pieceOrTheme(e)}</td>
              {showBestMove && <td className="py-1 pr-2 font-mono text-emerald-600 dark:text-emerald-400">{e.best_move ?? "—"}</td>}
              {scope === "all" && <td className="py-1 whitespace-nowrap text-[10px]">{e.reviewed ? <span className="font-medium text-emerald-600 dark:text-emerald-400">✓ reviewed</span> : <span className="text-zinc-400">—</span>}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- Lazy drill-down state ---------------------------------------------------------------------------

interface NodeEventsState {
  loading: boolean;
  error: string | null;
  rows: ReviewGameRow[];
  total: number;
  page: number;
}

/** The correct move (and the book move when the player left book first) for an opening node's representative. */
function OpeningMovePanel({ rep }: { rep: ReviewGameRow }) {
  const showBook = rep.book_relation === "deviation_before" && rep.expected_move;
  if (!rep.best_move && !showBook) return null;
  return (
    <div className="mb-3 space-y-0.5 text-xs">
      {showBook && <p className="text-zinc-600 dark:text-zinc-400">Book move: {rep.expected_move}</p>}
      {rep.best_move && (
        <>
          <p className="text-zinc-600 dark:text-zinc-400">Best move: {rep.best_move}</p>
          {rep.best_line && <p className="text-[11px] text-zinc-500">{rep.best_line}</p>}
        </>
      )}
    </div>
  );
}

function DrillDown({ state, onLoadMore, showBestMove, scope }: { state: NodeEventsState | undefined; onLoadMore: () => void; showBestMove: boolean; scope: ReviewedScope }) {
  if (!state || (state.loading && state.rows.length === 0)) return <p className="py-2 text-sm text-zinc-500">Loading…</p>;
  if (state.error)
    return (
      <p role="alert" className="py-2 text-sm text-red-600 dark:text-red-400">
        {state.error}
      </p>
    );
  if (state.rows.length === 0) return <p className="py-2 text-sm text-zinc-500">No games under this filter.</p>;
  return (
    <>
      <GameTable rows={state.rows} showBestMove={showBestMove} scope={scope} />
      {state.rows.length < state.total && (
        <div className="mt-3 flex justify-center">
          <button type="button" onClick={onLoadMore} disabled={state.loading} className="rounded border border-zinc-300 px-3 py-1 text-sm text-zinc-600 hover:border-zinc-500 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-400">
            {state.loading ? "Loading…" : `Load more (${state.rows.length} of ${state.total})`}
          </button>
        </div>
      )}
    </>
  );
}

// --- Rows -----------------------------------------------------------------------------------------------

/** An expandable leaf: a route pool or an opening subgroup. */
function NodeRow({ label, sublabel, count, severity, confidence, isOpening, representative, open, onToggle, state, onLoadMore, scope }: { label: string; sublabel: string | null; count: string; severity: number; confidence: "high" | "low"; isOpening: boolean; representative: ReviewGameRow; open: boolean; onToggle: () => void; state: NodeEventsState | undefined; onLoadMore: () => void; scope: ReviewedScope }) {
  return (
    <div className="rounded border border-zinc-200 dark:border-zinc-800">
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left hover:bg-zinc-50 dark:hover:bg-zinc-900">
        <Chevron open={open} />
        <span className="min-w-0 grow basis-48">
          <span className="block truncate text-sm font-medium">{label}</span>
          {sublabel && <span className="block truncate text-xs text-zinc-500">{sublabel}</span>}
        </span>
        <Meta count={count} severity={severity} confidence={confidence} />
      </button>
      {open && (
        <div className="border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
          {isOpening && <OpeningMovePanel rep={representative} />}
          <DrillDown state={state} onLoadMore={onLoadMore} showBestMove={isOpening} scope={scope} />
        </div>
      )}
    </div>
  );
}

/** An opening family: expands to its subgroups, never straight to games. */
function FamilyRow({ family, open, onToggle, scope, renderSubgroup }: { family: OpeningFamily; open: boolean; onToggle: () => void; scope: ReviewedScope; renderSubgroup: (s: OpeningSubgroup) => ReactNode }) {
  const subgroups = scope === "to_review" ? family.subgroups.filter((s) => s.to_review_games > 0) : family.subgroups;
  return (
    <div className="rounded border border-zinc-200 dark:border-zinc-800">
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left hover:bg-zinc-50 dark:hover:bg-zinc-900">
        <Chevron open={open} />
        <span className="min-w-0 grow basis-48 truncate text-sm font-medium">{family.label}</span>
        <Meta count={countLabel(scope, family.total_games, family.to_review_games)} severity={family.severity} confidence={family.confidence} />
      </button>
      {open && <div className="space-y-2 border-t border-zinc-200 px-2 py-2 dark:border-zinc-800">{subgroups.length === 0 ? <p className="py-1 text-sm text-zinc-500">Nothing to review in this opening.</p> : subgroups.map(renderSubgroup)}</div>}
    </div>
  );
}

/** A category. A single-pool category (Endgame, Faded) carries its pool's severity and badge on the header. */
function CategorySection({ title, count, open, onToggle, children, severity, confidence }: { title: string; count: string; open: boolean; onToggle: () => void; children: ReactNode; severity?: number; confidence?: "high" | "low" }) {
  return (
    <section className="rounded-lg border border-zinc-200 dark:border-zinc-800">
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-3 text-left sm:px-4">
        <Chevron open={open} />
        <h2 className="min-w-0 grow basis-48 text-base font-semibold sm:text-lg">{title}</h2>
        <Meta count={count} severity={severity} confidence={confidence} />
      </button>
      {open && <div className="space-y-2 px-3 pb-4 sm:px-4">{children}</div>}
    </section>
  );
}

// --- The page -----------------------------------------------------------------------------------------

const CAT = { opening: "opening", oversights: "oversights", endgame: "endgame", faded: "faded", lostWins: "lost_wins" } as const;

const DEFAULT_CATS: readonly string[] = [CAT.opening];

export default function Review() {
  const location = useLocation();
  const navigate = useNavigate();
  // The settings live in the URL and the expansion in this history entry's state: both are read
  // here on every render, and every change writes the entry (replace, never push — a filter or a
  // toggle is not a Back step). Back, a game's Close and a reload therefore all find them again,
  // and a top-bar visit (a new entry with no state) starts collapsed.
  const settings = readReviewSettings(new URLSearchParams(location.search));
  const { timeClass, opening, groupBy, scope } = settings;
  const snapshot = readOpenSnapshot(location.state, settings);
  const openCats = new Set(snapshot ? snapshot.cats : DEFAULT_CATS);
  const openNodes = new Set(snapshot ? snapshot.nodes : []);
  const [notice, setNotice] = useState<string | null>(null);
  const [eventsByNode, setEventsByNode] = useState<Record<string, NodeEventsState>>({});
  // The drill-downs asked for in this view, synchronously: state lags a render behind a navigation.
  const requested = useRef(new Set<string>());
  const clearEvents = () => {
    requested.current = new Set();
    setEventsByNode({});
  };
  // The latest settings, for a recovery that lands after the user changed something else.
  const settingsRef = useRef(settings);
  settingsRef.current = settings;

  // Two view generations, bumped in event handlers (never during render) whenever what is on
  // screen changes so that an outstanding request no longer describes it. A request captures the
  // generation it was made for and is ignored unless it is still the current one. `pageGen` is the
  // page request's; `viewGen` the drill-downs'. A scope change bumps only the drills: the page
  // request does not carry the scope, so it stays current and may still recover.
  const pageGen = useRef(0);
  const viewGen = useRef(0);
  const nextDrillView = () => {
    viewGen.current += 1;
  };
  const nextView = () => {
    pageGen.current += 1;
    nextDrillView();
  };
  useEffect(() => () => nextView(), []);

  /** The one writer of this entry: its URL from the settings, its state the expansion under them. */
  const writeEntry = useCallback(
    (s: ReviewSettings, cats: Iterable<string>, nodes: Iterable<string>) => {
      const open: ReviewOpenSnapshot = { cats: [...cats], nodes: [...nodes], key: reviewSettingsKey(s) };
      navigate({ pathname: location.pathname, search: reviewSettingsSearch(s) }, { replace: true, state: { open } });
    },
    [navigate, location.pathname],
  );

  // A settings change this page did not make (the top bar, Back to another worklist entry) is a
  // fresh view as well: nothing outstanding may land in it.
  // As in the handlers, a scope change leaves the page request current (it does not carry the scope).
  const settingsKey = reviewSettingsKey(settings);
  const seenSettings = useRef(settings);
  // Set while the stale-opening recovery writes its own change, so its notice outlives it.
  const recovering = useRef(false);
  useEffect(() => {
    const seen = seenSettings.current;
    seenSettings.current = settings;
    if (seen.timeClass !== timeClass || seen.opening !== opening || seen.groupBy !== groupBy) nextView();
    else if (seen.scope !== scope) nextDrillView();
    else return;
    clearEvents();
    if (!recovering.current) setNotice(null);
    recovering.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settingsKey]);

  // A focused opening that no longer has review games: back to All openings, everything but the
  // Opening section collapsed (its ids are opening-scoped), one line saying why.
  const recoverToAllOpenings = useCallback(() => {
    nextView();
    clearEvents();
    recovering.current = true;
    writeEntry({ ...settingsRef.current, opening: OPENING_ALL }, DEFAULT_CATS, []);
    setNotice("That opening no longer has review games — showing all openings.");
  }, [writeEntry]);

  const fetchPage = useCallback(async () => {
    const gen = pageGen.current;
    try {
      return await getReviewPage(timeClass, opening, groupBy);
    } catch (err) {
      // A 422 for a focus the user has since left must not undo their newer choice.
      if (isStaleOpeningError(err) && opening !== OPENING_ALL && gen === pageGen.current) {
        recoverToAllOpenings();
        return await getReviewPage(timeClass, OPENING_ALL, groupBy);
      }
      throw err;
    }
  }, [timeClass, opening, groupBy, recoverToAllOpenings]);
  const { data, isLoading, error, isStale } = useApi(fetchPage, [timeClass, opening, groupBy]);

  // A drill-down is cached under every server-visible axis plus the scope, for the life of that view.
  const cacheKey = (nodeId: string) => `${timeClass}::${opening}::${scope}::${nodeId}`;

  function loadEvents(nodeId: string, page: number) {
    const key = cacheKey(nodeId);
    const gen = viewGen.current;
    requested.current.add(key);
    setEventsByNode((prev) => ({ ...prev, [key]: { loading: true, error: null, rows: prev[key]?.rows ?? [], total: prev[key]?.total ?? 0, page: prev[key]?.page ?? 0 } }));
    getPoolEvents(nodeId, timeClass, opening, scope, page)
      .then((res) => {
        if (gen !== viewGen.current) return;
        setEventsByNode((prev) => {
          const cur = prev[key];
          const rows = page === 1 ? res.events : [...(cur?.rows ?? []), ...res.events];
          return { ...prev, [key]: { loading: false, error: null, rows, total: res.total, page: res.page } };
        });
      })
      .catch((err: unknown) => {
        if (gen !== viewGen.current) return;
        // The opening went stale between the page fetch and this drill: recover at the page level.
        if (isStaleOpeningError(err) && opening !== OPENING_ALL) {
          recoverToAllOpenings();
          return;
        }
        const message = err instanceof Error ? err.message : "Failed to load games";
        setEventsByNode((prev) => ({ ...prev, [key]: { loading: false, error: message, rows: prev[key]?.rows ?? [], total: prev[key]?.total ?? 0, page: prev[key]?.page ?? 0 } }));
      });
  }

  // An expansion restored from this entry (mount, Back, a game's Close) has no rows yet: fetch them
  // once the page is here. A restored node is not stamped shown: it is the same look, resumed. A
  // node opened by a click loads in its handler.
  useEffect(() => {
    if (!data || isStale) return;
    // Only the leaves on screen: an open subgroup inside a closed family or section waits for it.
    const leaves = new Set<string>();
    if (openCats.has(CAT.opening)) for (const f of data.categories.opening.families) if (openNodes.has(f.family_id)) for (const sg of f.subgroups) leaves.add(sg.subgroup_id);
    if (openCats.has(CAT.oversights)) for (const pool of [...data.categories.oversights.defense.pools, ...data.categories.oversights.offense.pools]) leaves.add(pool.pool_id);
    const wanted = [...openNodes].filter((id) => leaves.has(id));
    for (const [catKey, category] of [
      [CAT.endgame, data.categories.endgame],
      [CAT.faded, data.categories.faded],
    ] as const) {
      const pools = category.pools.filter((p) => scope === "all" || p.to_review_games > 0);
      if (openCats.has(catKey) && pools.length === 1) wanted.push(pools[0].pool_id);
    }
    for (const id of wanted) if (!requested.current.has(cacheKey(id))) loadEvents(id, 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, isStale, location.key]);

  const loadMore = (nodeId: string) => loadEvents(nodeId, (eventsByNode[cacheKey(nodeId)]?.page ?? 1) + 1);
  const stamp = (nodeId: string) => touchPoolShown(nodeId, timeClass, opening).catch(() => undefined);

  function setNodeOpen(nodeId: string, willOpen: boolean) {
    const next = new Set(openNodes);
    if (willOpen) next.add(nodeId);
    else next.delete(nodeId);
    writeEntry(settings, openCats, next);
  }

  /** A leaf: opening it loads its first page and stamps it shown. */
  function toggleNode(nodeId: string) {
    const willOpen = !openNodes.has(nodeId);
    setNodeOpen(nodeId, willOpen);
    if (willOpen) {
      if (!requested.current.has(cacheKey(nodeId))) loadEvents(nodeId, 1);
      void stamp(nodeId);
    }
  }

  /** A family: opening it reveals its subgroups and stamps the family shown. */
  function toggleFamily(familyId: string) {
    const willOpen = !openNodes.has(familyId);
    setNodeOpen(familyId, willOpen);
    if (willOpen) void stamp(familyId);
  }

  function toggleCat(catKey: string) {
    const next = new Set(openCats);
    if (next.has(catKey)) next.delete(catKey);
    else next.add(catKey);
    writeEntry(settings, next, openNodes);
  }

  /** A single-pool category expands straight to its games: the header behaves as the leaf would. */
  function toggleSinglePoolCat(catKey: string, poolId: string) {
    const willOpen = !openCats.has(catKey);
    toggleCat(catKey);
    if (willOpen) {
      if (!requested.current.has(cacheKey(poolId))) loadEvents(poolId, 1);
      void stamp(poolId);
    }
  }

  // A server-param change is a fresh page: ids and memberships are filter-dependent, so everything
  // collapses — except Opening problems, which hosts the controls that were just used.
  function changeSettings(next: Partial<ReviewSettings>) {
    setNotice(null);
    nextView();
    clearEvents();
    writeEntry({ ...settings, ...next }, DEFAULT_CATS, []);
  }
  const changeTimeClass = (next: ReviewTimeClass) => changeSettings({ timeClass: next });
  const changeOpening = (next: string) => changeSettings({ opening: next });
  const changeGroupBy = (next: GroupByMode) => changeSettings({ groupBy: next });
  // Scope is client-side: sections stay open, drill-downs collapse and forget their rows so they
  // reload under the new scope. A single-pool category is its own drill-down, so it closes too.
  const changeScope = (next: ReviewedScope) => {
    nextDrillView();
    clearEvents();
    writeEntry(
      { ...settings, scope: next },
      [...openCats].filter((c) => c !== CAT.endgame && c !== CAT.faded),
      [],
    );
  };
  const isDefault = reviewSettingsKey(settings) === reviewSettingsKey(REVIEW_DEFAULTS);
  const resetFilters = () => changeSettings(REVIEW_DEFAULTS);

  const visible = (toReview: number) => scope === "all" || toReview > 0;
  const catVisible = (total: number, toReview: number) => (scope === "all" ? total > 0 : toReview > 0);

  const renderSubgroup = (s: OpeningSubgroup) => <NodeRow key={s.subgroup_id} label={s.label} sublabel={s.book_relation_verdict ? bookRelationLabel(s.book_relation_verdict) : null} count={countLabel(scope, s.total_games, s.to_review_games)} severity={s.severity} confidence={s.confidence} isOpening representative={s.representative_game} open={openNodes.has(s.subgroup_id)} onToggle={() => toggleNode(s.subgroup_id)} state={eventsByNode[cacheKey(s.subgroup_id)]} onLoadMore={() => loadMore(s.subgroup_id)} scope={scope} />;

  const renderPool = (pool: ReviewPool) => <NodeRow key={pool.pool_id} label={pieceLabelDisplay(pool.label)} sublabel={pool.book_relation_verdict ? bookRelationLabel(pool.book_relation_verdict) : null} count={countLabel(scope, pool.total_games, pool.to_review_games)} severity={pool.severity} confidence={pool.confidence} isOpening={pool.pool_key != null} representative={pool.representative_game} open={openNodes.has(pool.pool_id)} onToggle={() => toggleNode(pool.pool_id)} state={eventsByNode[cacheKey(pool.pool_id)]} onLoadMore={() => loadMore(pool.pool_id)} scope={scope} />;

  /** Endgame technique / Faded advantage: one pool, so the category drills straight to its games. */
  function renderSinglePoolCategory(catKey: string, title: string, category: PoolCategory) {
    const pools = category.pools.filter((p) => visible(p.to_review_games));
    const single = pools.length === 1 ? pools[0] : null;
    return (
      <CategorySection title={title} count={countLabel(scope, category.total_games, category.to_review_games)} open={openCats.has(catKey)} onToggle={() => (single ? toggleSinglePoolCat(catKey, single.pool_id) : toggleCat(catKey))} severity={single ? single.severity : undefined} confidence={single ? single.confidence : undefined}>
        {single ? <DrillDown state={eventsByNode[cacheKey(single.pool_id)]} onLoadMore={() => loadMore(single.pool_id)} showBestMove={false} scope={scope} /> : <div className="space-y-2">{pools.map(renderPool)}</div>}
      </CategorySection>
    );
  }

  const focusedLabel = data && opening !== OPENING_ALL ? (data.filter.openings.find((o) => o.key === opening)?.label ?? opening) : null;

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight">Review</h1>
      <p className="mb-4 mt-1 text-sm text-zinc-500">My recurring leaks, bucketed — biggest fixable problems first.</p>

      <div className="flex flex-wrap items-end gap-3 border-b border-zinc-200 pb-3 dark:border-zinc-800">
        <label className="text-xs text-zinc-500">
          Time class
          <select aria-label="Time class" className={`${select} mt-1 block`} value={timeClass} onChange={(e) => changeTimeClass(e.target.value as ReviewTimeClass)}>
            {(Object.keys(REVIEW_TIME_CLASS_LABELS) as ReviewTimeClass[]).map((t) => (
              <option key={t} value={t}>
                {REVIEW_TIME_CLASS_LABELS[t]}
              </option>
            ))}
          </select>
        </label>
        <ReviewedScopeToggle value={scope} onChange={changeScope} />
        {!isDefault && (
          <button type="button" onClick={resetFilters} className="pb-1 text-xs text-zinc-500 underline hover:text-zinc-900 dark:hover:text-zinc-100">
            Reset filters
          </button>
        )}
      </div>

      <p className="mt-3 text-xs text-zinc-500">
        Sorted worst-first. <span className="font-medium text-zinc-700 dark:text-zinc-300">Severity</span> = points lost, weighted toward recent games and discounted when only seen a few times — higher means fix it first.
      </p>

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
        {data &&
          (data.page.total_games === 0 ? (
            <p className="py-8 text-center text-sm text-zinc-500">No review events yet — they appear after the next hourly run.</p>
          ) : (
            <div className={`space-y-3 ${isStale ? "opacity-50" : ""}`}>
              {/* Opening problems hosts the focus and group-by controls, so it shows whenever any opening can be focused. */}
              {data.filter.openings.length > 1 && (
                <CategorySection title={focusedLabel ? `Opening problems — focused: ${focusedLabel}` : "Opening problems"} count={countLabel(scope, data.categories.opening.total_games, data.categories.opening.to_review_games)} open={openCats.has(CAT.opening)} onToggle={() => toggleCat(CAT.opening)}>
                  <div className="mb-3 flex flex-wrap items-end gap-3">
                    <label className="text-xs text-zinc-500">
                      Focus opening
                      <select aria-label="Focus opening" className={`${select} mt-1 block max-w-[70vw]`} value={opening} onChange={(e) => changeOpening(e.target.value)}>
                        {data.filter.openings.map((o) => (
                          <option key={o.key} value={o.key}>
                            {o.label} ({o.to_review_games})
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="text-xs text-zinc-500">
                      Group openings
                      <select aria-label="Group openings" className={`${select} mt-1 block`} value={groupBy} onChange={(e) => changeGroupBy(e.target.value as GroupByMode)}>
                        {GROUP_BY_MODES.map((m) => (
                          <option key={m} value={m}>
                            {GROUP_BY_LABELS[m]}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  {focusedLabel && <p className="mb-2 text-xs text-zinc-500">Showing all lines in this opening, including thin ones that don't clear the usual bar.</p>}
                  {(() => {
                    const families = data.categories.opening.families.filter((f) => visible(f.to_review_games));
                    if (families.length === 0) return <p className="py-1 text-sm text-zinc-500">{opening === OPENING_ALL ? "No opening problems clear the bar across all openings. Focus one opening above to see its weakest lines." : "Nothing to review in this opening."}</p>;
                    return families.map((family) => <FamilyRow key={family.family_id} family={family} open={openNodes.has(family.family_id)} onToggle={() => toggleFamily(family.family_id)} scope={scope} renderSubgroup={renderSubgroup} />);
                  })()}
                </CategorySection>
              )}

              {catVisible(data.categories.oversights.total_games, data.categories.oversights.to_review_games) && (
                <CategorySection title="Tactical oversights" count={countLabel(scope, data.categories.oversights.total_games, data.categories.oversights.to_review_games)} open={openCats.has(CAT.oversights)} onToggle={() => toggleCat(CAT.oversights)}>
                  {data.categories.oversights.defense.pools.some((p) => visible(p.to_review_games)) && (
                    <div>
                      <h3 className="mb-1.5 text-xs uppercase tracking-wide text-zinc-500">Gave away material</h3>
                      <div className="space-y-2">{data.categories.oversights.defense.pools.filter((p) => visible(p.to_review_games)).map(renderPool)}</div>
                    </div>
                  )}
                  {data.categories.oversights.offense.pools.some((p) => visible(p.to_review_games)) && (
                    <div className="mt-3">
                      <h3 className="mb-1.5 text-xs uppercase tracking-wide text-zinc-500">Missed material &amp; mates</h3>
                      <div className="space-y-2">{data.categories.oversights.offense.pools.filter((p) => visible(p.to_review_games)).map(renderPool)}</div>
                    </div>
                  )}
                </CategorySection>
              )}

              {catVisible(data.categories.endgame.total_games, data.categories.endgame.to_review_games) && renderSinglePoolCategory(CAT.endgame, "Endgame technique", data.categories.endgame)}
              {catVisible(data.categories.faded.total_games, data.categories.faded.to_review_games) && renderSinglePoolCategory(CAT.faded, "Faded advantage", data.categories.faded)}

              {catVisible(data.categories.lost_wins.total_games, data.categories.lost_wins.to_review_games) && (
                <CategorySection title="Lost wins" count={countLabel(scope, data.categories.lost_wins.total_games, data.categories.lost_wins.to_review_games)} open={openCats.has(CAT.lostWins)} onToggle={() => toggleCat(CAT.lostWins)}>
                  <p className="mb-2 text-xs text-zinc-500">Games I was winning and didn't convert — from the review corpus.</p>
                  <LostWinsList rows={scope === "to_review" ? data.categories.lost_wins.games.filter((g) => !g.reviewed) : data.categories.lost_wins.games} scope={scope} />
                </CategorySection>
              )}
            </div>
          ))}
      </div>
    </div>
  );
}
