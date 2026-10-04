import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { useApi } from "../hooks/useApi";
import { daysAgo } from "../blunders";
import { OPENING_ALL, engineLabel, getPositionPage, isStaleOpeningError, lineText, pct, pointsAMonth, positionPath, positionSearch, readPositionPage, readReviewSettings, reviewSettingsSearch, whyThisGame } from "../review";
import type { PositionGame } from "../review";
import { PositionBoard, StatusChip, TrendBars } from "../components/ReviewBits";
import { returnTarget } from "../utils/returnTo";
import type { From } from "../utils/returnTo";

/**
 * One position's page at `/review/positions/:colour/:key`, under the Review page's two settings
 * (the same query string, plus `page` for the games past the first fifty). The board as Rob reached it, the line most of his games took to it, its
 * numbers and trend, what happens next (each move a link to that position's page, except a return to the start), and his games
 * through it whose moves are still stored: playable-then-not-won first, each with one sentence on
 * why it is worth opening. "Review" opens the game at its turning point, "From here" at the board;
 * both carry this page's whole state as the way back, so a game's Close returns here and this page's
 * Back still returns to where it was opened from. A focused opening that no longer qualifies drops
 * to All openings, as on the Review page.
 */

const btn = "rounded border border-zinc-300 px-3 py-1 text-sm text-zinc-600 hover:border-zinc-500 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400";
const resultWord = (r: string | null) => (r === "win" ? "Won" : r === "draw" ? "Drew" : r === "loss" ? "Lost" : "—");

function GameRow({ g, from }: { g: PositionGame; from: From }) {
  const reviewPly = g.turning_ply ?? g.ply;
  return (
    <li className="space-y-0.5 py-2 text-xs" data-testid="position-game">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <Link to={`/review/${g.chess_game_id}?ply=${reviewPly}`} state={{ from }} className="whitespace-nowrap underline">
          Review →
        </Link>
        <Link to={`/review/${g.chess_game_id}?ply=${g.ply}`} state={{ from }} className="whitespace-nowrap text-zinc-500 underline">
          From here
        </Link>
        <span>
          {g.opponent_username ?? "—"}
          {g.opponent_rating ? ` (${g.opponent_rating})` : ""}
        </span>
        <span className="text-zinc-500">{daysAgo(g.played_at) ?? "—"}</span>
        <span>{resultWord(g.result)}</span>
        {g.reviewed && <span className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">✓ reviewed</span>}
      </div>
      <p className="text-zinc-600 dark:text-zinc-400">{whyThisGame(g)}</p>
    </li>
  );
}

export default function ReviewPosition() {
  const { colour = "", key = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const settings = readReviewSettings(new URLSearchParams(location.search));
  const { timeClass, opening } = settings;
  // The page of games is the URL's too, so a game's Close, browser Back and a reload all come
  // back to it. A settings change or another position's link starts again from page 1, since
  // neither carries it.
  const page = readPositionPage(new URLSearchParams(location.search));
  const setPage = (next: number) => navigate({ pathname: location.pathname, search: positionSearch(settings, next) }, { replace: true, state: location.state });

  // A response belongs to the view it was asked for: one that lands after Back, a link or a
  // settings change must not act on whatever is showing now.
  const viewKey = `${colour}:${key}:${timeClass}:${opening}:${page}`;
  const currentView = useRef(viewKey);
  currentView.current = viewKey;
  useEffect(
    () => () => {
      currentView.current = "";
    },
    [],
  );
  // The recovery's notice belongs to this position; another position's page starts without it.
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeFor, setNoticeFor] = useState(`${colour}:${key}`);
  if (noticeFor !== `${colour}:${key}`) {
    setNoticeFor(`${colour}:${key}`);
    setNotice(null);
  }

  const fetchPage = useCallback(async () => {
    const asked = viewKey;
    try {
      return await getPositionPage(colour, key, timeClass, opening, page);
    } catch (err) {
      if (isStaleOpeningError(err) && opening !== OPENING_ALL && asked === currentView.current) {
        setNotice("That opening no longer has enough games — showing all openings.");
        navigate({ pathname: location.pathname, search: reviewSettingsSearch({ ...settings, opening: OPENING_ALL }) }, { replace: true, state: location.state });
      }
      throw err;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colour, key, timeClass, opening, page]);
  const { data, isLoading, error, isStale } = useApi(fetchPage, [colour, key, timeClass, opening, page]);

  const back = () => {
    const t = returnTarget(location.state);
    navigate(t.to, t.state != null ? { state: t.state } : undefined);
  };
  // A game opened from here comes back to exactly this entry, its own way back included.
  const from: From = { pathname: location.pathname, search: location.search, state: location.state ?? null };
  const node = data?.node;

  return (
    <div>
      <button type="button" onClick={back} className="mb-3 text-sm text-zinc-500 underline hover:text-zinc-900 dark:hover:text-zinc-100">
        ← Back
      </button>
      {notice && (
        <p role="status" className="mb-3 rounded border border-zinc-200 px-3 py-2 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error.includes("no counted game") ? "None of your games under this filter reach this position." : error}
        </p>
      )}
      {isLoading && !data && <p className="text-sm text-zinc-500">Loading…</p>}
      {data && node && (
        <div className={`space-y-5 ${isStale ? "opacity-50" : ""}`}>
          <div className="flex flex-col gap-4 sm:flex-row">
            <div className="aspect-square w-full max-w-[320px] shrink-0">{node.fen ? <PositionBoard fen={node.fen} colour={node.colour} lastMove={node.last_move} /> : null}</div>
            <div className="min-w-0 space-y-2">
              <h1 className="break-words font-mono text-base font-semibold">
                <span className="mr-2 font-sans text-sm font-normal text-zinc-500">{node.colour === "white" ? "White" : "Black"}</span>
                {lineText(node.line_san)}
              </h1>
              <p className="text-sm tabular-nums">
                {node.n} games · {pct(node.score)} (expected {pct(node.expected)}) · now {pct(node.current_score)} (expected {pct(node.current_expected)})
              </p>
              <p className="text-sm tabular-nums text-zinc-600 dark:text-zinc-400">{node.leak_per_month > 0 ? pointsAMonth(node.leak_per_month) : "Not costing points now"}</p>
              <div className="flex flex-wrap items-center gap-2">
                <StatusChip status={node.status} />
                <span className="text-xs text-zinc-500">{engineLabel(node.es_at_node)}</span>
              </div>
              <TrendBars trend={node.trend} height={36} />
            </div>
          </div>

          <section>
            <h2 className="mb-1 text-base font-semibold">What happens next</h2>
            <p className="mb-2 text-xs text-zinc-500">{node.rob_to_move ? "Your moves from here." : "Their replies from here."}</p>
            {data.children.length === 0 ? (
              <p className="text-sm text-zinc-500">No move from here is recorded in your games' opening moves.</p>
            ) : (
              <table className="text-left text-xs">
                <thead className="text-zinc-500">
                  <tr>
                    <th className="py-1 pr-4 font-normal">Move</th>
                    <th className="py-1 pr-4 text-right font-normal">Games</th>
                    <th className="py-1 pr-4 text-right font-normal">You scored</th>
                    <th className="py-1 text-right font-normal">Expected</th>
                  </tr>
                </thead>
                <tbody>
                  {data.children.map((c) => (
                    <tr key={c.key} className="border-t border-zinc-200 dark:border-zinc-800">
                      <td className="py-1 pr-4 font-mono">
                        {c.linkable ? (
                          <Link to={positionPath(node.colour, c.key, settings)} state={location.state} className="underline">
                            {c.san}
                          </Link>
                        ) : (
                          <span title="Back to the starting position, which is not a position of its own">{c.san}</span>
                        )}
                      </td>
                      <td className="py-1 pr-4 text-right tabular-nums">{c.n}</td>
                      <td className="py-1 pr-4 text-right tabular-nums">{pct(c.score)}</td>
                      <td className="py-1 text-right tabular-nums text-zinc-500">{pct(c.expected)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section>
            <h2 className="mb-1 text-base font-semibold">Your games from here</h2>
            {data.games.total === 0 ? (
              <p className="text-sm text-zinc-500">None of these games still has its moves stored.</p>
            ) : (
              <>
                <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
                  {data.games.rows.map((g) => (
                    <GameRow key={g.chess_game_id} g={g} from={from} />
                  ))}
                </ul>
                {data.games.total_pages > 1 && (
                  <div className="mt-2 flex items-center gap-3 text-xs text-zinc-500">
                    <button type="button" className={btn} disabled={page <= 1} onClick={() => setPage(page - 1)}>
                      Previous
                    </button>
                    <span>
                      Page {data.games.page} of {data.games.total_pages}
                    </span>
                    <button type="button" className={btn} disabled={page >= data.games.total_pages} onClick={() => setPage(page + 1)}>
                      Next
                    </button>
                  </div>
                )}
              </>
            )}
            {data.older_games > 0 && <p className="mt-2 text-xs text-zinc-500">Plus {data.older_games} older games in the numbers above.</p>}
          </section>
        </div>
      )}
    </div>
  );
}
