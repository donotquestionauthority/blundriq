import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { ALL_COLUMNS, DAY_OPTIONS, LAST_N_OPTIONS, buildQuery, pgnOf, sortGames } from "../games";
import type { Filters, FilterValues, Game, SortKey, Summary } from "../games";

/**
 * The game log: filters, a summary line, a sortable table and an expandable row
 * with ratings, issue breakdown, repertoire detail and the PGN. Which columns
 * show and the default day window come from the settings row (games_columns,
 * games_default_window_days), so they are changed on Preferences, not here.
 * A Review button is added once the Review page exists.
 */

function Badge({ children, tone }: { children: React.ReactNode; tone: "green" | "red" | "gray" | "blue" | "yellow" | "purple" }) {
  const tones = {
    green: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    red: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
    gray: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
    blue: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
    yellow: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    purple: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
  };
  return <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>;
}

function ResultBadge({ result }: { result: Game["result"] }) {
  return <Badge tone={result === "win" ? "green" : result === "loss" ? "red" : "gray"}>{result}</Badge>;
}

function Deviation({ g }: { g: Game }) {
  if (!g.book_title) return <span className="text-zinc-400">—</span>;
  if (g.deviation_by === "none") return <span className="text-emerald-600 dark:text-emerald-400">followed line</span>;
  if (g.deviation_by === "me") return <span className="text-amber-600 dark:text-amber-400">I deviated · ply {g.deviated_at_ply}</span>;
  if (g.deviation_by === "opponent") return <span className="text-sky-600 dark:text-sky-400">opp deviated · ply {g.deviated_at_ply}</span>;
  return <span className="text-zinc-400">—</span>;
}

function Issues({ g }: { g: Game }) {
  if (!g.analyzed) return <span className="text-zinc-400">pending</span>;
  if (g.issue_count === 0) return <span className="text-emerald-600 dark:text-emerald-400">clean</span>;
  return (
    <span className="space-x-1 font-mono text-xs">
      {g.miss_count > 0 && <span className="text-rose-600 dark:text-rose-400">{g.miss_count}M</span>}
      {g.blunder_count > 0 && <span className="text-red-600 dark:text-red-400">{g.blunder_count}B</span>}
      {g.mistake_count > 0 && <span className="text-orange-600 dark:text-orange-400">{g.mistake_count}x</span>}
      {g.inaccuracy_count > 0 && <span className="text-amber-600 dark:text-amber-400">{g.inaccuracy_count}i</span>}
    </span>
  );
}

function Detail({ g }: { g: Game }) {
  const [copied, setCopied] = useState(false);
  const pgn = pgnOf(g.moves);
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap gap-4 text-xs text-zinc-500">
        {g.player_rating != null && <span>my rating {g.player_rating}</span>}
        {g.opponent_rating != null && <span>opp rating {g.opponent_rating}</span>}
        {g.time_control && <span>time {g.time_control}</span>}
        {g.opening_eco && <span>ECO {g.opening_eco}</span>}
        {g.termination && <span>{g.termination}</span>}
      </div>
      {g.book_title && (
        <div className="rounded border border-zinc-200 px-3 py-2 dark:border-zinc-800">
          <div className="text-xs uppercase tracking-wide text-zinc-500">Repertoire</div>
          <div>
            {g.book_title}
            {g.chapter_title && <span className="text-zinc-500"> › {g.chapter_title}</span>}
          </div>
          {g.line_name && <div className="text-xs italic text-zinc-500">{g.line_name}</div>}
          {g.deviation_by === "me" && g.expected_move && (
            <div className="mt-1 text-xs text-amber-600 dark:text-amber-400">
              expected <span className="font-mono">{g.expected_move}</span>, played <span className="font-mono">{g.played_move}</span> at ply {g.deviated_at_ply}
            </div>
          )}
          {g.deviation_by === "opponent" && (
            <div className="mt-1 text-xs text-sky-600 dark:text-sky-400">
              opponent played <span className="font-mono">{g.played_move}</span> at ply {g.deviated_at_ply}
            </div>
          )}
        </div>
      )}
      {pgn ? (
        <div className="rounded border border-zinc-200 px-3 py-2 dark:border-zinc-800">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase tracking-wide text-zinc-500">PGN</span>
            <button
              type="button"
              className="text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
              onClick={async () => {
                await navigator.clipboard.writeText(pgn);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? "copied" : "copy"}
            </button>
          </div>
          <p className="line-clamp-3 break-all font-mono text-xs text-zinc-600 dark:text-zinc-400">{pgn}</p>
        </div>
      ) : (
        <p className="text-xs text-zinc-500">Moves are no longer stored for this game (outside the analysis window).</p>
      )}
      {g.url && (
        <a href={g.url} target="_blank" rel="noreferrer" className="text-xs text-zinc-500 underline hover:text-zinc-900 dark:hover:text-zinc-100">
          view on {g.source === "lichess" ? "Lichess" : "Chess.com"}
        </a>
      )}
    </div>
  );
}

const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";

export default function Games() {
  const [columns, setColumns] = useState<string[]>([...ALL_COLUMNS]);
  const [filters, setFilters] = useState<Filters | null>(null);
  const [values, setValues] = useState<FilterValues>({ books: [], chapters: [] });
  const [games, setGames] = useState<Game[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "played_at", dir: "desc" });
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.get<Record<string, unknown>>("/settings"), api.get<FilterValues>("/games/filters")])
      .then(([s, fv]) => {
        const cols = s.games_columns;
        if (Array.isArray(cols) && cols.length) setColumns(cols.map(String));
        setValues(fv);
        setFilters({
          since_days: typeof s.games_default_window_days === "number" ? s.games_default_window_days : 60,
          last_n_games: 0,
          color: "",
          result: "",
          platform: "",
          variant: "",
          book: "",
          chapter: "",
          deviation: "",
          opponent: "",
        });
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!filters) return;
    // Only the latest request may install its result: typing in the opponent box fires one
    // request per keystroke and a slow earlier response must not overwrite a newer one.
    let current = true;
    api
      .get<{ games: Game[]; summary: Summary }>(`/games?${buildQuery(filters, page)}`)
      .then((r) => {
        if (!current) return;
        setGames(r.games);
        setSummary(r.summary);
        setError(null);
      })
      .catch((e: unknown) => {
        if (current) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      current = false;
    };
  }, [filters, page]);

  const sorted = useMemo(() => sortGames(games, sort.key, sort.dir), [games, sort]);
  const chapters = useMemo(() => {
    const book = values.books.find((b) => b.title === filters?.book);
    return book ? values.chapters.filter((c) => c.book_id === book.id) : [];
  }, [values, filters?.book]);

  function set<K extends keyof Filters>(k: K, v: Filters[K]) {
    setPage(1);
    setFilters((f) => (f ? { ...f, [k]: v, ...(k === "book" ? { chapter: "" } : {}) } : f));
  }
  function toggleSort(key: SortKey) {
    setSort((s) => ({ key, dir: s.key === key && s.dir === "desc" ? "asc" : "desc" }));
  }
  const th = (label: string, key?: SortKey) => (
    <th key={label} className="px-2 py-1.5 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
      {key ? (
        <button type="button" onClick={() => toggleSort(key)} className="uppercase hover:text-zinc-900 dark:hover:text-zinc-100">
          {label}
          {sort.key === key ? (sort.dir === "desc" ? " ↓" : " ↑") : ""}
        </button>
      ) : (
        label
      )}
    </th>
  );

  const cells: Record<string, (g: Game) => React.ReactNode> = {
    Date: (g) => <span className="whitespace-nowrap font-mono text-xs">{g.played_at ? new Date(g.played_at).toLocaleDateString() : "—"}</span>,
    Opening: (g) => (
      <span className="block max-w-[14rem] truncate text-xs" title={g.opening_name ?? ""}>
        {g.opening_name ?? "—"}
      </span>
    ),
    Opponent: (g) => <span className="text-xs">{g.opponent_username ?? "—"}</span>,
    Color: (g) => <Badge tone={g.player_color === "white" ? "gray" : "blue"}>{g.player_color}</Badge>,
    Result: (g) => <ResultBadge result={g.result} />,
    Repertoire: (g) => <span className="block max-w-[12rem] truncate text-xs">{g.book_title ?? "—"}</span>,
    Section: (g) => <span className="block max-w-[12rem] truncate text-xs">{g.chapter_title ?? "—"}</span>,
    Deviation: (g) => (
      <span className="whitespace-nowrap text-xs">
        <Deviation g={g} />
      </span>
    ),
    "My Rating": (g) => <span className="font-mono text-xs">{g.player_rating ?? "—"}</span>,
    "Opp Rating": (g) => <span className="font-mono text-xs">{g.opponent_rating ?? "—"}</span>,
    Issues: (g) => <Issues g={g} />,
    Platform: (g) => <span className="text-xs text-zinc-500">{g.source === "lichess" ? "Lichess" : "Chess.com"}</span>,
    Variant: (g) => (g.variant === "chess960" ? <Badge tone="purple">960</Badge> : <span className="text-xs text-zinc-500">std</span>),
    Link: (g) =>
      g.url ? (
        <a href={g.url} target="_blank" rel="noreferrer" className="text-xs text-zinc-500 underline" onClick={(e) => e.stopPropagation()}>
          open
        </a>
      ) : null,
  };
  const sortOf: Partial<Record<string, SortKey>> = {
    Date: "played_at",
    Result: "result",
    "My Rating": "player_rating",
    "Opp Rating": "opponent_rating",
    Issues: "issue_count",
    Deviation: "deviated_at_ply",
  };
  const shown = columns.filter((c) => c in cells);

  if (!filters) return <p className="text-sm text-zinc-500">{error ?? "…"}</p>;

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Games</h1>
        {summary && (
          <p className="text-sm text-zinc-500">
            {summary.total} games · {summary.wins}W {summary.losses}L {summary.draws}D · {summary.win_pct}% wins
          </p>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <select className={select} value={filters.last_n_games} onChange={(e) => set("last_n_games", Number(e.target.value))}>
          <option value={0}>by date</option>
          {LAST_N_OPTIONS.map((n) => (
            <option key={n} value={n}>
              last {n} games
            </option>
          ))}
        </select>
        {filters.last_n_games === 0 && (
          <select className={select} value={filters.since_days ?? ""} onChange={(e) => set("since_days", e.target.value ? Number(e.target.value) : null)}>
            <option value="">all time</option>
            {DAY_OPTIONS.map((d) => (
              <option key={d} value={d}>
                last {d} days
              </option>
            ))}
          </select>
        )}
        <select className={select} value={filters.color} onChange={(e) => set("color", e.target.value)}>
          <option value="">any colour</option>
          <option value="white">white</option>
          <option value="black">black</option>
        </select>
        <select className={select} value={filters.result} onChange={(e) => set("result", e.target.value)}>
          <option value="">any result</option>
          <option value="win">win</option>
          <option value="loss">loss</option>
          <option value="draw">draw</option>
        </select>
        <select className={select} value={filters.platform} onChange={(e) => set("platform", e.target.value)}>
          <option value="">any platform</option>
          <option value="chesscom">Chess.com</option>
          <option value="lichess">Lichess</option>
        </select>
        <select className={select} value={filters.variant} onChange={(e) => set("variant", e.target.value)}>
          <option value="">any variant</option>
          <option value="standard">standard</option>
          <option value="chess960">Chess960</option>
        </select>
        <select className={select} value={filters.book} onChange={(e) => set("book", e.target.value)}>
          <option value="">any book</option>
          {values.books.map((b) => (
            <option key={b.id} value={b.title}>
              {b.title}
            </option>
          ))}
        </select>
        {chapters.length > 0 && (
          <select className={select} value={filters.chapter} onChange={(e) => set("chapter", e.target.value)}>
            <option value="">any chapter</option>
            {chapters.map((c) => (
              <option key={c.id} value={c.title}>
                {c.title}
              </option>
            ))}
          </select>
        )}
        <select className={select} value={filters.deviation} onChange={(e) => set("deviation", e.target.value)}>
          <option value="">any deviation</option>
          <option value="me">I deviated</option>
          <option value="opponent">opponent deviated</option>
          <option value="none">followed line</option>
          <option value="no_match">no match</option>
        </select>
        <input
          className={select}
          placeholder="opponent"
          value={filters.opponent}
          onChange={(e) => set("opponent", e.target.value)}
          aria-label="Opponent"
        />
        <a
          className="ml-auto self-center text-xs text-zinc-500 underline"
          href={`${import.meta.env.VITE_API_URL ?? "/api"}/games/export.csv?${buildQuery(filters, 1)}`}
        >
          export CSV
        </a>
      </div>

      {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-zinc-200 dark:border-zinc-800">{shown.map((c) => th(c, sortOf[c]))}</tr>
          </thead>
          <tbody>
            {sorted.map((g) => (
              <GameRow key={g.id} g={g} shown={shown} cells={cells} open={open === g.id} onToggle={() => setOpen(open === g.id ? null : g.id)} />
            ))}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={shown.length} className="px-2 py-6 text-center text-sm text-zinc-500">
                  No games match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {summary && summary.pages > 1 && (
        <div className="mt-3 flex items-center gap-3 text-sm text-zinc-500">
          <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="disabled:opacity-40">
            ‹ prev
          </button>
          <span>
            page {page} of {summary.pages}
          </span>
          <button type="button" disabled={page >= summary.pages} onClick={() => setPage(page + 1)} className="disabled:opacity-40">
            next ›
          </button>
        </div>
      )}
    </div>
  );
}

function GameRow({
  g,
  shown,
  cells,
  open,
  onToggle,
}: {
  g: Game;
  shown: string[];
  cells: Record<string, (g: Game) => React.ReactNode>;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer border-b border-zinc-100 hover:bg-zinc-100 dark:border-zinc-900 dark:hover:bg-zinc-900"
        aria-expanded={open}
      >
        {shown.map((c) => (
          <td key={c} className="px-2 py-1.5 align-top">
            {cells[c](g)}
          </td>
        ))}
      </tr>
      {open && (
        <tr className="border-b border-zinc-100 bg-zinc-50 dark:border-zinc-900 dark:bg-zinc-900/40">
          <td colSpan={shown.length} className="px-3 py-3">
            <Detail g={g} />
          </td>
        </tr>
      )}
    </>
  );
}
