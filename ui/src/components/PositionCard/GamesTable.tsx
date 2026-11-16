import type { MouseEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { daysAgo } from "../../blunders";
import type { From } from "../../utils/returnTo";
import { ClassBadge } from "./CardInner";
import type { PositionCardGame } from "./types";

/** The platform a game's own page is on, for the external link's label. */
function siteName(url: string): string {
  try {
    const host = new URL(url).hostname;
    return host.endsWith("lichess.org") ? "Lichess" : host.endsWith("chess.com") ? "Chess.com" : host;
  } catch {
    return "the site";
  }
}

/**
 * The date of the player's own game, opening its review at the card's move. A plain click first
 * writes the page's snapshot (`returnState`) into the entry it leaves, then opens the review with
 * `from.back`, so Back, Close and Escape all return to that entry, which restores the page and
 * spends the snapshot. A modified click (a new tab) is the browser's.
 */
function ReviewDate({ target, label, returnState }: { target: { gameId: number; ply: number }; label: string; returnState?: () => unknown }) {
  const location = useLocation();
  const navigate = useNavigate();
  const to = `/review/${target.gameId}?ply=${target.ply}`;
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const here = { pathname: location.pathname, search: location.search };
    const card = returnState?.();
    if (card !== undefined) navigate(here, { replace: true, state: { ...((location.state as object | null) ?? {}), card } });
    const from: From = { ...here, back: true };
    navigate(to, { state: { from } });
  };
  return (
    <Link to={to} onClick={onClick} className="underline" data-testid="game-review-link">
      {label}
    </Link>
  );
}

/** The games a position occurred in, newest first. Columns with nothing in them are left out.
 *  The player's own game (`review`) opens BlundrIQ's review at the move, with the game's own page
 *  one tap away (↗); any other game's date links to its page on the platform. */
export function GamesTable({ games, bestLabel, title = "Games", returnState }: { games: PositionCardGame[]; bestLabel: string; title?: string; returnState?: () => unknown }) {
  const hasMoves = games.some((g) => g.move_played || g.best_move);
  const hasCp = games.some((g) => g.cp_loss);
  const hasOpening = games.some((g) => g.opening || g.as);
  const hasResult = games.some((g) => g.result || g.classification);
  return (
    <div>
      <p className="mb-2 text-xs uppercase tracking-wide text-zinc-500">
        {title} ({games.length})
      </p>
      <table className="w-full text-left text-xs">
        <thead className="text-zinc-500">
          <tr>
            <th className="py-1 font-normal">Date</th>
            {hasResult && <th className="py-1 font-normal">Result</th>}
            {hasOpening && <th className="py-1 font-normal">Opening</th>}
            {hasMoves && <th className="py-1 font-normal">Played</th>}
            {hasMoves && <th className="py-1 font-normal">{bestLabel}</th>}
            {hasCp && <th className="py-1 text-right font-normal">CP</th>}
          </tr>
        </thead>
        <tbody>
          {games.map((g, i) => {
            const when = daysAgo(g.played_at) ?? "—";
            return (
              <tr key={`${g.game_url ?? ""}-${i}`} className="border-t border-zinc-200 dark:border-zinc-800">
                <td className="py-1">
                  {g.review ? (
                    <span className="whitespace-nowrap">
                      <ReviewDate target={g.review} label={when} returnState={returnState} />
                      {g.game_url && (
                        <a href={g.game_url} target="_blank" rel="noreferrer" aria-label={`Open on ${siteName(g.game_url)}`} title={`Open on ${siteName(g.game_url)}`} className="ml-1.5 text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
                          ↗
                        </a>
                      )}
                    </span>
                  ) : g.game_url ? (
                    <a href={g.game_url} target="_blank" rel="noreferrer" className="underline">
                      {when}
                    </a>
                  ) : (
                    when
                  )}
                </td>
                {hasResult && (
                  <td className="py-1">
                    <span className="mr-1.5 text-zinc-500">{g.result ?? ""}</span>
                    {g.classification && <ClassBadge cls={g.classification} />}
                  </td>
                )}
                {hasOpening && (
                  <td className="py-1 text-zinc-600 dark:text-zinc-400">
                    {g.opening || "—"}
                    {g.as ? <span className="text-zinc-500"> · as {g.as}</span> : null}
                  </td>
                )}
                {hasMoves && <td className="py-1 font-mono text-red-600 dark:text-red-400">{g.move_played ?? "—"}</td>}
                {hasMoves && <td className="py-1 font-mono text-emerald-600 dark:text-emerald-400">{g.best_move ?? "—"}</td>}
                {hasCp && <td className="py-1 text-right font-mono text-zinc-500">{g.cp_loss ? `−${g.cp_loss}` : ""}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
