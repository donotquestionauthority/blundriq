/**
 * The vertical evaluation bar beside an engine board. `evalCp` is White-POV centipawns (mate
 * ±10000). White's share fills from White's side of the board — the bottom when the board is
 * oriented for White, the top when flipped — so the bar reads the same way relative to the pieces
 * either way. It has no height of its own: the row stretches it to the board beside it.
 *
 * The label is drawn in a layer of its own outside the rounded track's clipping, so a value wider
 * than the bar ("+12.3", "-M2") is not cut off. It sits at the favoured player's end, which is
 * that player's colour by construction, so the text colour can match it.
 */
import type { CSSProperties } from "react";
import { MATE_ABS, fmtCp } from "../engine/eval";

function whiteFraction(cp: number | null): number {
  if (cp == null) return 0.5;
  if (cp >= MATE_ABS) return 1;
  if (cp <= -MATE_ABS) return 0;
  return 0.5 + Math.max(-1000, Math.min(1000, cp)) / 2000;
}

export function EvalBar({ evalCp, flipped }: { evalCp: number | null; flipped: boolean }) {
  const whitePct = `${(whiteFraction(evalCp) * 100).toFixed(1)}%`;
  const whiteAhead = (evalCp ?? 0) >= 0;
  const fill: CSSProperties = { position: "absolute", left: 0, right: 0, height: whitePct, transition: "height 150ms ease", ...(flipped ? { top: 0 } : { bottom: 0 }) };
  // The favoured end: White's is the bottom unless flipped; Black's the other.
  const atTop = flipped ? whiteAhead : !whiteAhead;
  const label: CSSProperties = { position: "absolute", left: "50%", transform: "translateX(-50%)", whiteSpace: "nowrap", pointerEvents: "none", ...(atTop ? { top: 2 } : { bottom: 2 }) };
  const text = fmtCp(evalCp);
  return (
    <div className="relative w-5 shrink-0 self-stretch" role="img" aria-label={`Evaluation ${text} (White's perspective)`} title={`Evaluation: ${text} (White's perspective)`}>
      <div className="absolute inset-0 overflow-hidden rounded bg-zinc-700 dark:bg-zinc-800">
        <div className="bg-zinc-100" style={fill} data-testid="eval-fill" />
      </div>
      <span className={`font-mono text-[10px] leading-3 tabular-nums ${whiteAhead ? "text-zinc-900" : "text-zinc-100"}`} style={label}>
        {text}
      </span>
    </div>
  );
}
