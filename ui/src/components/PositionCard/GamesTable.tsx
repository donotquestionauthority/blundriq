import { daysAgo } from "../../blunders";
import { ClassBadge } from "./CardInner";
import type { PositionCardGame } from "./types";

/** The games a position occurred in, newest first. Columns with nothing in them are left out. */
export function GamesTable({ games, bestLabel, title = "Games" }: { games: PositionCardGame[]; bestLabel: string; title?: string }) {
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
                  {g.game_url ? (
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
