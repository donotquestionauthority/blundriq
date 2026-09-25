/**
 * What could the opponent have played? One half-move back from a puzzle position, every option
 * the server knows, full screen. The pinned board is the sole renderer of the server's `current`;
 * the grid owns `branches` (the alternatives) exactly, so nothing is drawn twice and the cap note
 * counts alternatives only.
 *
 * Arrows per branch, all from wire squares: blue for the opponent's move always; orange for the
 * repertoire's reply (the repertoire is the only source of orange); for a blunder-only branch the
 * move played in vermilion and the engine's best in green; a scout best move in green when it is
 * the only source. Self-fetching (there is no earlier fetch to reuse), aborted on close or a change
 * of pair, errors surfaced with Retry. Escape is consumed in the capture phase; hosts also gate
 * their own listeners on the open state.
 */
import { useEffect, useId, useState } from "react";
import { Chessboard } from "react-chessboard";
import { getBranchCompare } from "../repertoire";
import type { BranchCompareResponse, CompareBranch } from "../repertoire";
import { branchArrows, groupKey } from "../compare";
import { SQUARES } from "../utils/board";
import { GroupRow } from "./PositionCard/SimilarPositionsPanel";

type Status = "loading" | "loaded" | "error";

function BranchCaption({ b }: { b: CompareBranch }) {
  const { repertoire: rep, blunders: blu, scout: sco } = b.sources;
  return (
    <div className="space-y-1 text-xs">
      <p>
        <span className="font-mono font-medium">{b.opponent_move.san}</span>
        {rep && (
          <span className="ml-2 text-zinc-600 dark:text-zinc-400">
            In your repertoire
            {rep.line_count > 1 && <> · ×{rep.line_count} lines</>}
          </span>
        )}
        {rep?.board_prep_divergent && <span className="ml-2 rounded bg-orange-100 px-1 text-[10px] text-orange-800 dark:bg-orange-900/40 dark:text-orange-300">conflicting prep</span>}
        {rep?.end_of_line && !rep.board_prep_divergent && rep.reply_san === null && <span className="ml-2 italic text-zinc-500">line ends here</span>}
      </p>
      {blu && (
        <p className="text-zinc-600 dark:text-zinc-400">
          Not in your repertoire — you've blundered here{blu.games > 1 ? ` in ${blu.games} games` : ""}: played <span className="font-mono text-red-600 dark:text-red-400">{blu.worst.move_played_san}</span>
          {blu.worst.best_move_san && (
            <>
              , best was <span className="font-mono text-emerald-600 dark:text-emerald-400">{blu.worst.best_move_san}</span>
            </>
          )}
          {blu.worst.centipawn_loss != null && <> (−{blu.worst.centipawn_loss}cp)</>}
        </p>
      )}
      {sco && (
        <p className="text-zinc-600 dark:text-zinc-400">
          {!rep && !blu && <>Not in your repertoire — </>}
          {sco.profiles.map((p) => `${p.name} plays this ×${p.games}`).join(" · ")}
          {sco.best_move_san && (
            <>
              {" "}
              · best: <span className="font-mono">{sco.best_move_san}</span>
            </>
          )}
        </p>
      )}
      {rep && (
        <div className="space-y-1">
          {rep.groups.map((g) => (
            <GroupRow key={groupKey(g)} g={g} />
          ))}
        </div>
      )}
    </div>
  );
}

function BranchBoard({ b, orientation, testid }: { b: CompareBranch; orientation: "white" | "black"; testid: string }) {
  const boardId = "branch" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  return (
    <div className="aspect-square w-full max-w-[16rem]" data-testid={testid}>
      <Chessboard options={{ id: boardId, position: b.child_fen, allowDragging: false, boardStyle: { borderRadius: "6px" }, ...SQUARES, boardOrientation: orientation, arrows: branchArrows(b) }} />
    </div>
  );
}

export function BranchCompareView({ fen, preFen, orientation, onClose }: { fen: string; preFen: string; orientation: "white" | "black"; onClose: () => void }) {
  const [retry, setRetry] = useState(0);
  // The answer is keyed by the request it answers; a pair (or retry) it does not match is loading.
  const requestKey = `${fen}|${preFen}|${retry}`;
  const [result, setResult] = useState<{ key: string; status: Exclude<Status, "loading">; data: BranchCompareResponse | null } | null>(null);
  const status: Status = result?.key === requestKey ? result.status : "loading";
  const data = result?.key === requestKey ? result.data : null;

  useEffect(() => {
    const controller = new AbortController();
    getBranchCompare(fen, preFen, controller.signal)
      .then((resp) => {
        if (!controller.signal.aborted) setResult({ key: requestKey, status: "loaded", data: resp });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResult({ key: requestKey, status: "error", data: null });
      });
    return () => controller.abort();
  }, [fen, preFen, requestKey]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const current = status === "loaded" ? (data?.current ?? null) : null;
  const branches = status === "loaded" ? (data?.branches ?? []) : [];
  const box = "rounded border border-zinc-200 dark:border-zinc-800";

  return (
    <div role="dialog" aria-label="Compare similar positions" className="fixed inset-0 z-[60] overflow-y-auto overscroll-contain bg-zinc-50 dark:bg-zinc-950" data-testid="branch-compare-view">
      <div className="mx-auto w-full max-w-6xl p-3 lg:flex lg:items-start lg:gap-6 lg:p-6">
        <div className="sticky top-0 z-10 -mx-3 border-b border-zinc-200 bg-zinc-50 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950 lg:top-6 lg:mx-0 lg:w-72 lg:shrink-0 lg:self-start lg:rounded lg:border lg:p-3">
          <div className="flex items-center justify-between pb-2">
            <button type="button" onClick={onClose} className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
              ← Back
            </button>
            {status === "loaded" && (
              <span className="text-xs text-zinc-500">
                {branches.length} alternative{branches.length === 1 ? "" : "s"}
              </span>
            )}
          </div>
          {current ? (
            <div className="flex items-start gap-3 lg:block">
              <div className="w-44 shrink-0 lg:w-full">
                <BranchBoard b={current} orientation={orientation} testid="branch-current-board" />
              </div>
              <div className="lg:mt-2">
                <p className="pb-1 text-xs text-zinc-500">
                  What happened: your opponent played <span className="font-mono text-zinc-900 dark:text-zinc-100">{current.opponent_move.san}</span>.
                </p>
                <BranchCaption b={current} />
              </div>
            </div>
          ) : (
            <p className="text-xs text-zinc-500">{status === "loading" ? "Computing…" : "Couldn't load."}</p>
          )}
        </div>

        <div className="mt-3 flex-1 lg:mt-0">
          {status === "loading" && (
            <div className="flex items-center gap-2 p-4 text-sm text-zinc-500">
              <span className="h-4 w-4 animate-spin rounded-full border border-zinc-400 border-t-transparent" />
              Computing what else your opponent could have played…
            </div>
          )}
          {status === "error" && (
            <div className={`flex items-center justify-between p-4 text-sm ${box}`}>
              <span role="alert" className="text-red-600 dark:text-red-400">
                Couldn't load the comparison.
              </span>
              <button type="button" onClick={() => setRetry((t) => t + 1)} className="rounded border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700">
                Retry
              </button>
            </div>
          )}
          {status === "loaded" && branches.length === 0 && <p className="p-4 text-xs text-zinc-500">No alternatives from the previous position: no other repertoire branch and no blunder history there.</p>}
          {status === "loaded" && branches.length > 0 && (
            <>
              <div className="grid grid-cols-2 gap-3 xl:grid-cols-3">
                {branches.map((b) => (
                  <div key={b.child_fen} className={`space-y-1.5 p-2 ${box}`}>
                    <BranchBoard b={b} orientation={orientation} testid="branch-board" />
                    <BranchCaption b={b} />
                  </div>
                ))}
              </div>
              {data?.truncated && (
                <p className="mt-2 text-xs text-zinc-500">
                  {data.boards_omitted} more alternative{data.boards_omitted === 1 ? "" : "s"} beyond the cap (Preferences › Compare).
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
