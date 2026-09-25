import { useEffect, useId, useState } from "react";
import { api, ApiError } from "../api";
import { LAST_N_OPTIONS, MIN_OCCURRENCE_OPTIONS } from "../blunders";
import { CreatePuzzleModal } from "../components/CreatePuzzleModal";
import type { CreatePuzzleSource } from "../components/CreatePuzzleModal";
import { PositionList } from "../components/PositionCard/PositionList";
import { ArrowBoard } from "../components/RepertoireBits";
import { useApi } from "../hooks/useApi";
import { COLOR_LABELS, TIER_ACCENT, createProfile, decisionNodeToCard, defaultFilters, deleteProfile, dismissBoard, fenActiveColor, getDecisionNodes, getDismissed, getLineGames, getPositions, getProfiles, getReport, restoreBoard, scoutToCard } from "../scout";
import type { FamilyRow, LineRow, OpponentProfile, ScoutColor, ScoutFilters, ScoutReport } from "../scout";
import { lichessAnalyzeUrl } from "../utils/chess";

/**
 * One opponent at a time: how active they are, what they play and where it goes well or badly
 * for them, the positions where they choose between replies on lines I play against them, and
 * the positions we both reach — my blunders first, then my repertoire, then everything else.
 * Adding an opponent verifies the handles and queues the import for the next hourly run;
 * removing one deletes the profile. A dismissal here is the same as on Blunders and sticks;
 * the Dismissed-boards panel at the foot is where any dismissed board comes back from.
 */

const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const input = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const action = "rounded border border-zinc-300 px-3 py-1.5 text-sm hover:border-zinc-500 disabled:opacity-50 dark:border-zinc-700";
const primary = "rounded border border-zinc-900 bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:opacity-50 dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900";
const box = "rounded-lg border border-zinc-200 p-4 dark:border-zinc-800";
const heading = "mb-2 text-xs font-medium uppercase tracking-widest text-zinc-500";

const ADD_ERRORS: Record<string, string> = {
  name_taken: "You already have an opponent with that name.",
  chesscom_username_not_found: "Chess.com has no account with that name.",
  lichess_username_not_found: "Lichess has no account with that name.",
  platform_unreachable: "Could not reach the platform to check the name — try again in a moment.",
};

function AddForm({ onAdded }: { onAdded: (id: number) => void }) {
  const [name, setName] = useState("");
  const [chesscom, setChesscom] = useState("");
  const [lichess, setLichess] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const can = name.trim().length > 0 && (chesscom.trim().length > 0 || lichess.trim().length > 0) && !busy;
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-label="Add opponent"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          const r = await createProfile({ name: name.trim(), chesscom_username: chesscom.trim() || undefined, lichess_username: lichess.trim() || undefined });
          setName("");
          setChesscom("");
          setLichess("");
          onAdded(r.profile_id);
        } catch (err) {
          const detail = err instanceof ApiError ? err.message : String(err);
          setError(ADD_ERRORS[detail] ?? detail);
        } finally {
          setBusy(false);
        }
      }}
    >
      <label className="text-xs text-zinc-500">
        Name
        <input aria-label="Name" className={`${input} mt-1 block`} value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
      </label>
      <label className="text-xs text-zinc-500">
        Chess.com
        <input aria-label="Chess.com handle" className={`${input} mt-1 block`} value={chesscom} onChange={(e) => setChesscom(e.target.value)} maxLength={50} />
      </label>
      <label className="text-xs text-zinc-500">
        Lichess
        <input aria-label="Lichess handle" className={`${input} mt-1 block`} value={lichess} onChange={(e) => setLichess(e.target.value)} maxLength={50} />
      </label>
      <button type="submit" className={primary} disabled={!can}>
        {busy ? "Checking…" : "Add"}
      </button>
      {error && (
        <p role="alert" className="w-full text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </form>
  );
}

function Manage({ profiles, onChanged }: { profiles: OpponentProfile[]; onChanged: (selected?: number) => void }) {
  const [confirming, setConfirming] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className={`${box} space-y-3`} data-testid="manage">
      <AddForm onAdded={(id) => onChanged(id)} />
      {profiles.length > 0 && (
        <ul className="space-y-1 text-sm">
          {profiles.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{p.name}</span>
              <span className="text-xs text-zinc-500">
                {[p.chesscom_username && `chess.com ${p.chesscom_username}`, p.lichess_username && `lichess ${p.lichess_username}`].filter(Boolean).join(" · ")}
                {" · "}
                {p.is_initialized ? `${p.game_count} games` : "importing on the next run"}
              </span>
              {confirming === p.id ? (
                <span className="flex items-center gap-2">
                  <span className="text-xs">
                    Remove {p.name} and their {p.game_count} games from Scout?
                  </span>
                  <button
                    type="button"
                    className={action}
                    onClick={async () => {
                      try {
                        await deleteProfile(p.id);
                        setConfirming(null);
                        onChanged();
                      } catch (err) {
                        setError(err instanceof Error ? err.message : String(err));
                      }
                    }}
                  >
                    Yes, remove
                  </button>
                  <button type="button" className={action} onClick={() => setConfirming(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <button type="button" className="text-xs text-zinc-500 hover:text-red-600" onClick={() => setConfirming(p.id)}>
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}

function ResultDot({ result }: { result: string }) {
  const cls = result === "win" ? "bg-emerald-500" : result === "loss" ? "bg-red-500" : result === "draw" ? "bg-zinc-400" : "bg-zinc-200";
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${cls}`} title={result || "unknown"} />;
}

type LineSel = { family: string; variation: string | null; label: string };

function LineGamesPanel({ profileId, sel, oppLastN, onClose }: { profileId: number; sel: LineSel; oppLastN: number; onClose: () => void }) {
  const { data, error, isLoading } = useApi(() => getLineGames(profileId, sel.family, sel.variation, oppLastN), [profileId, sel.family, sel.variation, oppLastN]);
  return (
    <div className={box} data-testid="line-games">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="min-w-0 truncate text-sm font-medium" title={sel.label}>
          {sel.label}
        </h3>
        <button type="button" className="text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100" onClick={onClose}>
          Close
        </button>
      </div>
      {isLoading && !data && <p className="text-xs text-zinc-500">Loading…</p>}
      {error && (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
      {data && data.games.length === 0 && <p className="text-xs text-zinc-500">No games in this line for the window.</p>}
      {data && data.games.length > 0 && (
        <ul className="space-y-1 text-xs">
          {data.games.map((g, i) => (
            <li key={`${g.url}-${i}`} className="flex items-center gap-2">
              <ResultDot result={g.result} />
              <span className="text-zinc-500">{g.date}</span>
              <span className="min-w-0 flex-1 truncate">{g.opening || "—"}</span>
              <span className="text-zinc-500">{g.color}</span>
              {g.url && (
                <a href={g.url} target="_blank" rel="noreferrer" className="underline">
                  view ↗
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Families({ title, rows, onPick }: { title: string; rows: FamilyRow[]; onPick: (row: FamilyRow) => void }) {
  return (
    <section className={box}>
      <h2 className={heading}>{title}</h2>
      {rows.length === 0 ? (
        <p className="text-xs text-zinc-500">Nothing yet.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {rows.map((r) => (
            <li key={r.family}>
              <button type="button" className="flex w-full items-center gap-2 text-left hover:underline" onClick={() => onPick(r)}>
                <span className="min-w-0 flex-1 truncate">{r.family}</span>
                <span className="text-xs text-zinc-500">
                  {r.cnt} · {r.pct}%
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Lines({ title, rows, onPick }: { title: string; rows: LineRow[]; onPick: (row: LineRow) => void }) {
  return (
    <section className={box}>
      <h2 className={heading}>
        {title} <span className="normal-case tracking-normal">(their win %)</span>
      </h2>
      {rows.length === 0 ? (
        <p className="text-xs text-zinc-500">Nothing yet.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {rows.map((r) => (
            <li key={`${r.family}|${r.variation}`}>
              <button type="button" className="flex w-full items-center gap-2 text-left hover:underline" onClick={() => onPick(r)}>
                {r.eco && <span className="font-mono text-xs text-zinc-500">{r.eco}</span>}
                <span className="min-w-0 flex-1 truncate">{r.variation}</span>
                <span className="text-xs text-zinc-500">
                  {r.win_pct}% · {r.games}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Activity({ name, a }: { name: string; a: ScoutReport["activity"] }) {
  return (
    <section className={box} aria-label="Activity">
      <h2 className={heading}>{name} — activity</h2>
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          ["24 h", a.last_1],
          ["7 d", a.last_7],
          ["30 d", a.last_30],
          ["all", a.total],
        ].map(([label, n]) => (
          <div key={label}>
            <p className="text-2xl font-bold">{n}</p>
            <p className="text-xs text-zinc-500">{label}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function DismissedPanel({ version, onRestored }: { version: number; onRestored: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const { data, error, refetch } = useApi(getDismissed, [version]);
  const id = useId().replace(/[^a-zA-Z0-9-]/g, "");
  const boards = data?.boards ?? [];
  return (
    <section className={box} data-testid="dismissed-panel">
      <button type="button" className="flex w-full items-center justify-between text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className={`${heading} mb-0`}>Dismissed boards ({boards.length})</span>
        <span className="text-xs text-zinc-500">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <p className="text-xs text-zinc-500">Every board dismissed here or on Blunders, whether or not it still shows up anywhere. Restore brings it back on both pages.</p>
          {error && (
            <p role="alert" className="text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
          )}
          {boards.length === 0 && <p className="text-xs text-zinc-500">Nothing dismissed.</p>}
          {boards.map((b, i) => (
            <div key={b.fen} className="flex items-center gap-3">
              <ArrowBoard id={`${id}-${i}`} fen={b.fen} orientation={fenActiveColor(b.fen)} arrows={[]} size={96} />
              <div className="space-y-1 text-xs">
                <p className="text-zinc-500">dismissed {new Date(b.dismissed_at).toLocaleDateString()}</p>
                <button
                  type="button"
                  className={action}
                  disabled={busy === b.fen}
                  onClick={async () => {
                    setBusy(b.fen);
                    try {
                      await restoreBoard(b.fen);
                      refetch();
                      onRestored();
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  Restore
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/** The page for one opponent. `version` is the page-wide dismissal counter: a dismissal here or
 *  a restore from the Dismissed panel bumps it and every list refetches. */
function ScoutBody({ profile, filters, setFilters, version, bump }: { profile: OpponentProfile; filters: ScoutFilters; setFilters: (f: ScoutFilters) => void; version: number; bump: () => void }) {
  const [page, setPage] = useState(0);
  const [sel, setSel] = useState<LineSel | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [creating, setCreating] = useState<CreatePuzzleSource | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const key = JSON.stringify(filters);
  const report = useApi(() => getReport(profile.id, filters.opp_last_n), [profile.id, filters.opp_last_n, version]);
  const nodes = useApi(() => getDecisionNodes(profile.id, filters), [profile.id, key, version]);
  const positions = useApi(async () => {
    const r = await getPositions(profile.id, filters, page);
    if (page > 0 && page > r.total_pages - 1) setPage(r.total_pages - 1);
    return r;
  }, [profile.id, key, page, version]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const change = (patch: Partial<ScoutFilters>) => {
    setPage(0);
    setSel(null);
    setFilters({ ...filters, ...patch });
  };

  async function dismiss(fen: string) {
    setBusy(fen);
    setFailure(null);
    try {
      await dismissBoard(fen);
      bump();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const dismissButton = (fen: string) => (
    <button
      type="button"
      aria-label="Dismiss"
      title="Dismiss"
      disabled={busy === fen}
      className="shrink-0 text-sm text-zinc-400 hover:text-zinc-900 disabled:opacity-50 dark:hover:text-zinc-100"
      onClick={(e) => {
        e.stopPropagation();
        void dismiss(fen);
      }}
    >
      ✕
    </button>
  );

  const r = report.data;
  const pos = positions.data;
  const tc = pos?.tier_counts ?? {};
  const cards = (pos?.positions ?? []).map(scoutToCard);
  const nodeCards = (nodes.data?.nodes ?? []).map(decisionNodeToCard);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-zinc-500">
          My games
          <select aria-label="My games" className={`${select} mt-1 block`} value={filters.my_last_n} onChange={(e) => change({ my_last_n: Number(e.target.value) })}>
            {[...new Set([...LAST_N_OPTIONS, filters.my_last_n])]
              .filter((n) => n > 0)
              .sort((a, b) => a - b)
              .map((n) => (
                <option key={n} value={n}>
                  Last {n}
                </option>
              ))}
            <option value={0}>All</option>
          </select>
        </label>
        <label className="text-xs text-zinc-500">
          Their games
          <select aria-label="Their games" className={`${select} mt-1 block`} value={filters.opp_last_n} onChange={(e) => change({ opp_last_n: Number(e.target.value) })}>
            {[...new Set([...LAST_N_OPTIONS, filters.opp_last_n])]
              .filter((n) => n > 0)
              .sort((a, b) => a - b)
              .map((n) => (
                <option key={n} value={n}>
                  Last {n}
                </option>
              ))}
            <option value={0}>All</option>
          </select>
        </label>
        <label className="text-xs text-zinc-500">
          Color
          <select aria-label="Color" className={`${select} mt-1 block`} value={filters.color} onChange={(e) => change({ color: e.target.value as ScoutColor })}>
            {(Object.keys(COLOR_LABELS) as ScoutColor[]).map((c) => (
              <option key={c} value={c}>
                {COLOR_LABELS[c]}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-zinc-500">
          Opp min freq
          <select aria-label="Opp min freq" className={`${select} mt-1 block`} value={filters.min_freq} onChange={(e) => change({ min_freq: Number(e.target.value) })}>
            {[...new Set([...MIN_OCCURRENCE_OPTIONS, filters.min_freq])].sort((a, b) => a - b).map((n) => (
              <option key={n} value={n}>
                {n}+
              </option>
            ))}
          </select>
        </label>
      </div>

      {(failure || report.error) && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {failure ?? report.error}
        </p>
      )}
      {!profile.is_initialized && <p className="text-sm text-zinc-500">This opponent's games are imported on the next hourly run; the page fills in after that.</p>}

      {r && <Activity name={profile.name} a={r.activity} />}
      {r && (
        <div className="grid gap-4 md:grid-cols-3">
          <Families title="Most played" rows={r.most_played} onPick={(row) => setSel({ family: row.family, variation: null, label: row.family })} />
          <Lines title="Best lines" rows={r.best_lines} onPick={(row) => setSel({ family: row.family, variation: row.variation, label: row.variation })} />
          <Lines title="Worst lines" rows={r.worst_lines} onPick={(row) => setSel({ family: row.family, variation: row.variation, label: row.variation })} />
        </div>
      )}
      {sel && <LineGamesPanel profileId={profile.id} sel={sel} oppLastN={filters.opp_last_n} onClose={() => setSel(null)} />}

      <section className="space-y-2">
        <div>
          <h2 className={`${heading} mb-0`}>Where they choose</h2>
          <p className="mt-0.5 text-xs text-zinc-500">Positions on lines you play against them where their replies fan out. Arrow strength is how often they pick each.</p>
        </div>
        {nodes.error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {nodes.error}
          </p>
        )}
        {nodes.data && nodeCards.length === 0 && <p className="text-sm text-zinc-500">No branching decisions on your lines yet for this window.</p>}
        {nodeCards.length > 0 && (
          <div className={nodes.isStale ? "opacity-50" : ""}>
            <PositionList items={nodeCards} accent={() => "border-l-orange-500"} headerRight={(item) => dismissButton(item.fen)} overlayActions={(item) => <button type="button" className={action} disabled={busy === item.fen} onClick={() => void dismiss(item.fen)}>Dismiss</button>} />
          </div>
        )}
      </section>

      <section className="space-y-2">
        {positions.error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {positions.error}
          </p>
        )}
        {positions.isLoading && !pos && <p className="text-sm text-zinc-500">Loading…</p>}
        {pos && (
          <div className={positions.isStale ? "opacity-50" : ""}>
            <div className="mb-2 flex items-center justify-between text-sm text-zinc-500">
              <span>
                {pos.total} positions · {tc["1"] ?? 0} blunder · {tc["2"] ?? 0} repertoire · {tc["3"] ?? 0} shared
              </span>
              {pos.total_pages > 1 && (
                <span className="flex items-center gap-2">
                  <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} className="disabled:opacity-30">
                    ← Prev
                  </button>
                  <span className="text-xs">
                    {pos.page + 1} / {pos.total_pages}
                  </span>
                  <button type="button" disabled={page >= pos.total_pages - 1} onClick={() => setPage(page + 1)} className="disabled:opacity-30">
                    Next →
                  </button>
                </span>
              )}
            </div>
            {cards.length === 0 ? (
              <p className="py-8 text-center text-sm text-zinc-500">No overlapping positions found.</p>
            ) : (
              <PositionList
                items={cards}
                overlaySuspended={creating !== null}
                accent={(item) => TIER_ACCENT[pos.positions.find((p) => p.fen === item.fen)?.tier ?? 3]}
                headerRight={(item) => dismissButton(item.fen)}
                overlayActions={(item) => (
                  <>
                    <a href={lichessAnalyzeUrl(item.moves, item.ply, item.color)} target="_blank" rel="noreferrer" className={action}>
                      Analyze on Lichess
                    </a>
                    <button type="button" className={action} onClick={() => setCreating({ source: "scout", fen: item.fen, color: item.color === "white" ? "w" : "b", movePlayed: item.movePlayed, bestMove: item.expectedMove ?? item.bestMove, lineNames: item.lineNames, moves: item.moves, ply: item.ply })}>
                      Create puzzle
                    </button>
                    <button type="button" className={action} disabled={busy === item.fen} onClick={() => void dismiss(item.fen)}>
                      Dismiss
                    </button>
                  </>
                )}
              />
            )}
          </div>
        )}
      </section>

      {creating && <CreatePuzzleModal key={creating.fen} source={creating} onClose={() => setCreating(null)} onCreated={(made) => setToast(made.visible ? "Puzzle created" : "Puzzle created, but hidden for now: this board is dismissed, or your repertoire already covers the position")} />}
      {toast && (
        <div role="status" className="fixed bottom-6 left-1/2 z-[60] max-w-md -translate-x-1/2 rounded-lg border border-zinc-200 bg-white px-4 py-2 text-sm shadow-2xl dark:border-zinc-800 dark:bg-zinc-900">
          ✓ {toast}
        </div>
      )}
    </div>
  );
}

export default function Scout() {
  const [filters, setFilters] = useState<ScoutFilters | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [manage, setManage] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Dismissals are shared with Blunders and outlive every opponent, so the panel that restores
  // them is mounted whether or not a profile is selected (or exists at all).
  const [version, setVersion] = useState(0);
  const bump = () => setVersion((v) => v + 1);
  const profiles = useApi(async () => (await getProfiles()).profiles);

  useEffect(() => {
    api
      .get<Record<string, unknown>>("/settings")
      .then((s) => setFilters(defaultFilters(s)))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const list = profiles.data ?? [];
  const current = list.find((p) => p.id === selected) ?? list[0] ?? null;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Scout</h1>
        <div className="flex items-center gap-2">
          {list.length > 1 && (
            <select aria-label="Opponent" className={select} value={current?.id ?? ""} onChange={(e) => setSelected(Number(e.target.value))}>
              {list.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
          <button type="button" className={action} aria-pressed={manage} onClick={() => setManage(!manage)}>
            Manage
          </button>
        </div>
      </div>
      <p className="mb-4 mt-1 text-sm text-zinc-500">What an opponent plays, where they choose, and the positions we both reach.</p>
      {(error || profiles.error) && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error ?? profiles.error}
        </p>
      )}
      {profiles.data && (manage || list.length === 0) && (
        <div className="mb-4">
          {list.length === 0 && <p className="mb-2 text-sm text-zinc-500">No opponents yet — add one to scout.</p>}
          <Manage
            profiles={list}
            onChanged={(id) => {
              if (id !== undefined) setSelected(id);
              profiles.refetch();
            }}
          />
        </div>
      )}
      {filters && current && <ScoutBody key={current.id} profile={current} filters={filters} setFilters={setFilters} version={version} bump={bump} />}
      {profiles.data && (
        <div className="mt-4">
          <DismissedPanel version={version} onRestored={bump} />
        </div>
      )}
    </div>
  );
}
