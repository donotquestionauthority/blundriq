import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { useApi } from "../hooks/useApi";
import { daysAgo } from "../blunders";
import { OPENING_ALL, belowExpectation, getPositionPage, givenAway, isStaleOpeningError, lineText, moveLabel, pct, positionPath, positionSearch, readPositionPage, readReviewSettings, reviewSettingsSearch, whyThisGame } from "../review";
import type { CostlyGame, MistakeDetail, PositionGame } from "../review";
import { MistakeChip, PositionBoard, StatusChip, TrendBars, VisitStrip } from "../components/ReviewBits";
import { returnTarget } from "../utils/returnTo";
import type { From } from "../utils/returnTo";

/**
 * One position's page at `/review/positions/:colour/:key`, under the Review page's two settings
 * (the same query string, plus `page` for the games past the first fifty). The board as Rob reached it, the line most of his games took to it;
 * when he is to move there, his moves from it (how often, what each gave away, the engine's move)
 * and the games where his move cost him; then the results below rating expectation: numbers and trend, what happens next (each move a link to that position's page, except a return to the start), and his games
 * through it whose moves are still stored: playable-then-not-won first, each with one sentence on
 * why it is worth opening. "Review" opens the game at its turning point, "From here" at the board;
 * both carry this page's whole state as the way back, so a game's Close returns here and this page's
 * Back still returns to where it was opened from. A focused opening that no longer qualifies drops
 * to All openings, as on the Review page.
 */

const btn = "rounded border border-zinc-300 px-3 py-1 text-sm text-zinc-600 hover:border-zinc-500 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400";
const resultWord = (r: string | null) => (r === "win" ? "Won" : r === "draw" ? "Drew" : r === "loss" ? "Lost" : "—");

function CostlyRow({ g, from }: { g: CostlyGame; from: From }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5 text-xs" data-testid="costly-game">
      {g.has_moves ? (
        <Link to={`/review/${g.chess_game_id}?ply=${g.ply}`} state={{ from }} className="whitespace-nowrap underline">
          Review →
        </Link>
      ) : (
        <span className="whitespace-nowrap text-zinc-400" title="Only this game's opening is still stored">
          Opening only
        </span>
      )}
      <span className="font-mono">{moveLabel(g.ply, g.san)}</span>
      <span className="tabular-nums text-zinc-500">−{Math.round(g.loss)}</span>
      <span>
        {g.opponent_username ?? "—"}
        {g.opponent_rating ? ` (${g.opponent_rating})` : ""}
      </span>
      <span className="text-zinc-500">{daysAgo(g.played_at) ?? "—"}</span>
      <span>{resultWord(g.result)}</span>
    </li>
  );
}

/** Rob's moves from the board: the numbers the opening-mistakes card has, every move he played
 *  there with the engine's, and the games where his move was costly, newest first. */
function YourMoves({ m, ply, from }: { m: MistakeDetail; ply: number; from: From }) {
  const unchecked = m.decisions - m.evaluated;
  return (
    <section data-testid="your-moves">
      <h2 className="mb-1 text-base font-semibold">Your moves from here</h2>
      <p className="text-sm tabular-nums">
        Costly in {m.costly_games} of {m.games} games · {givenAway(m.per_month)}
        {unchecked > 0 ? ` · ${m.evaluated} of ${m.decisions} visits checked` : ""}
      </p>
      <div className="my-2 flex flex-wrap items-center gap-2">
        {m.ranked && <MistakeChip status={m.status} />}
        <VisitStrip strip={m.strip} />
      </div>
      <p className="mb-2 text-xs text-zinc-500">
        {m.terminal ? "The game is over here." : m.best_move ? `The engine plays ${moveLabel(ply, m.best_move)} here.` : "The engine has not checked this position yet."} Losses are in expected-score points (a whole game is 100); a move is costly when it gives away more than the floor in your Preferences.
      </p>
      <table className="text-left text-xs">
        <thead className="text-zinc-500">
          <tr>
            <th className="py-1 pr-3 font-normal">Move</th>
            <th className="py-1 pr-3 text-right font-normal">Times</th>
            <th className="py-1 pr-3 text-right font-normal">Avg lost</th>
            <th className="py-1 pr-3 text-right font-normal">Costly</th>
            <th className="py-1 font-normal">Last</th>
          </tr>
        </thead>
        <tbody>
          {m.moves.map((mv) => (
            <tr key={mv.san} className="border-t border-zinc-200 dark:border-zinc-800">
              <td className="py-1 pr-3 font-mono">{moveLabel(ply, mv.san)}</td>
              <td className="py-1 pr-3 text-right tabular-nums">{mv.n}</td>
              <td className="py-1 pr-3 text-right tabular-nums">{mv.mates ? "mate" : mv.mean_loss == null ? "—" : mv.mean_loss.toFixed(1)}</td>
              <td className="py-1 pr-3 text-right tabular-nums">{mv.costly}</td>
              <td className="py-1 text-zinc-500">{daysAgo(mv.last_played) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {m.costly_rows.length > 0 && (
        <>
          <h3 className="mb-1 mt-3 text-sm font-medium">Games where it cost you</h3>
          <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {m.costly_rows.map((g) => (
              <CostlyRow key={`${g.chess_game_id}:${g.ply}`} g={g} from={from} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

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
      {data && (node || data.mistake) && (
        <div className={`space-y-5 ${isStale ? "opacity-50" : ""}`}>
          {(() => {
            const head = node ?? data.mistake;
            if (!head) return null;
            return (
              <div className="flex flex-col gap-4 sm:flex-row">
                <div className="aspect-square w-full max-w-[320px] shrink-0">{head.fen ? <PositionBoard fen={head.fen} colour={head.colour} lastMove={head.last_move} /> : null}</div>
                <div className="min-w-0 space-y-2">
                  <h1 className="break-words font-mono text-base font-semibold">
                    <span className="mr-2 font-sans text-sm font-normal text-zinc-500">{head.colour === "white" ? "White" : "Black"}</span>
                    {head.line_san.length ? lineText(head.line_san) : "Starting position"}
                  </h1>
                </div>
              </div>
            );
          })()}

          {data.mistake && <YourMoves m={data.mistake} ply={data.mistake.line_san.length} from={from} />}

          {node && (
            <section data-testid="results">
              <h2 className="mb-1 text-base font-semibold">Results below rating expectation</h2>
              <p className="mb-2 text-xs text-zinc-500">How your games through this position scored against the rating expectation. This does not say where they went wrong.</p>
              <p className="text-sm tabular-nums">
                {node.n} games · {pct(node.score)} (expected {pct(node.expected)}) · now {pct(node.current_score)} (expected {pct(node.current_expected)})
              </p>
              <p className="text-sm tabular-nums text-zinc-600 dark:text-zinc-400">{node.leak_per_month > 0 ? belowExpectation(node.leak_per_month) : "Not below expectation now"}</p>
              <div className="my-2">
                <StatusChip status={node.status} />
              </div>
              <TrendBars trend={node.trend} height={36} />
            </section>
          )}

          {node && (
            <>
              <section>
                <h2 className="mb-1 text-base font-semibold">What happens next</h2>
                <p className="mb-2 text-xs text-zinc-500">{node.rob_to_move ? "How your games scored after each of your moves." : "Their replies from here."}</p>
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
            </>
          )}
        </div>
      )}
    </div>
  );
}
