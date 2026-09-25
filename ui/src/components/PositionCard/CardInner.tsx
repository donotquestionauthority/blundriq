import { useId } from "react";
import { Chessboard } from "react-chessboard";
import { daysAgo } from "../../blunders";
import { SQUARES } from "../../utils/board";
import { buildArrows, decisionNodeArrows } from "../../utils/chess";
import { lineNamesSummary, recommended } from "./types";
import type { BoardSize, PositionCardData } from "./types";

const BOARD_MAX: Record<BoardSize, string> = { S: "max-w-[160px]", M: "max-w-[200px]", L: "max-w-[240px]" };

const CLASS_TONE: Record<string, string> = {
  miss: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  blunder: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  mistake: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  inaccuracy: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
};

export function ClassBadge({ cls }: { cls: string }) {
  return <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${CLASS_TONE[cls] ?? ""}`}>{cls}</span>;
}

/** "after your Nf3, they play: Nf6 ×4 · Nc6 ×2 · +1 more" — a decision node's replies. */
export function RepliesLine({ d }: { d: PositionCardData }) {
  const replies = d.oppReplies ?? [];
  return (
    <p className="text-xs" data-testid="replies-line">
      <span className="text-zinc-500">{d.leadIn ? `after your ${d.leadIn}, they play:` : "they play:"}</span>{" "}
      <span className="font-mono">{replies.map((r) => `${r.move} ×${r.cnt}`).join(" · ")}</span>
      {d.repliesMore ? <span className="text-zinc-500"> · +{d.repliesMore} more</span> : null}
    </p>
  );
}

/** The face of a card: a static board with the three arrows, and the position's numbers. */
export function CardInner({ d, headerRight, boardSize = "M", onBoardClick }: { d: PositionCardData; headerRight?: React.ReactNode; boardSize?: BoardSize; onBoardClick?: () => void }) {
  // react-chessboard resolves a tap by looking its squares up by element id, so every board
  // on a page needs its own id or taps on later boards land on the first one.
  const boardId = "pc" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const node = d.oppReplies != null;
  const played = node ? null : (d.movePlayed ?? d.mostCommonPlayed ?? null);
  const best = node ? null : recommended(d).move;
  const arrows = node ? decisionNodeArrows({ fen: d.fen, replies: d.oppReplies, leadIn: d.leadIn, leadPreFen: d.leadPreFen }) : buildArrows({ fen: d.fen, moves: d.moves, ply: d.ply, movePlayed: played, bestMove: best });
  return (
    <div className="flex gap-3 p-3">
      <div className={`aspect-square w-7/12 shrink-0 ${BOARD_MAX[boardSize]}`}>
        <Chessboard
          options={{
            id: boardId,
            position: d.fen,
            allowDragging: false,
            // A tap on the board does not reach the card's own click handler.
            onSquareClick: onBoardClick ? () => onBoardClick() : undefined,
            boardStyle: { borderRadius: "4px", cursor: onBoardClick ? "pointer" : undefined },
            ...SQUARES,
            boardOrientation: d.color,
            arrows,
          }}
        />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-start justify-between gap-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-lg font-bold">{d.times}×</span>
            {d.score !== undefined && <span className="font-mono text-xs text-zinc-500">score {d.score}</span>}
            {d.topClassification && <ClassBadge cls={d.topClassification} />}
            {d.isNew && <span className="inline-block rounded bg-sky-100 px-1.5 py-0.5 text-xs font-semibold text-sky-800 dark:bg-sky-900/40 dark:text-sky-300">NEW</span>}
          </div>
          {headerRight}
        </div>
        {d.book ? (
          <div className="text-xs leading-tight">
            <p className="font-medium">📖 {d.book}</p>
            {d.chapter && <p className="ml-4 mt-0.5 text-zinc-500">{d.chapter}</p>}
            {d.lineNames?.length ? <p className="ml-4 mt-0.5 truncate italic text-zinc-500">{lineNamesSummary(d.lineNames)}</p> : null}
          </div>
        ) : (
          d.context && <p className="line-clamp-2 text-xs text-zinc-500">{d.context}</p>
        )}
        {node && <RepliesLine d={d} />}
        {(played || best) && (
          <div className="flex flex-wrap items-center gap-1.5 font-mono text-xs">
            {played && <span className="text-red-600 dark:text-red-400">{played}</span>}
            {played && best && <span className="text-zinc-400">→</span>}
            {best && <span className="text-emerald-600 dark:text-emerald-400">{best}</span>}
          </div>
        )}
        {d.lastSeen && <p className="text-xs text-zinc-500">last {daysAgo(d.lastSeen)}</p>}
        <p className="mt-auto text-xs text-zinc-500">as {d.color}</p>
      </div>
    </div>
  );
}
