/**
 * Engine-evaluation display helpers. White-POV centipawns, mate as ±(MATE_SCORE − distance),
 * the convention `useStockfish` publishes and `EvalBar` draws. Only the engine surfaces read these.
 */
import { Chess } from "chess.js";
import { legalMove } from "../utils/chess";
import type { EngineEval } from "./useStockfish";

export const MATE_SCORE = 10000;
/** |eval| at or above this is a forced mate. */
export const MATE_ABS = 9000;
/** The longest best line shown. */
export const MAX_PV_PLIES = 12;

/** `+1.3`, `-0.4`, `+M3`, `-M2`, `–` for none. */
export function fmtCp(cp: number | null): string {
  if (cp == null) return "–";
  if (Math.abs(cp) >= MATE_ABS) {
    const dist = Math.max(1, MATE_SCORE - Math.abs(cp));
    return `${cp > 0 ? "+" : "-"}M${dist}`;
  }
  const sign = cp > 0 ? "+" : cp < 0 ? "-" : "";
  return `${sign}${(Math.abs(cp) / 100).toFixed(1)}`;
}

/**
 * A UCI principal variation as SAN, replayed from `fen` through `legalMove` (so the null move is
 * refused like any token the board cannot play), stopping at the first token that does not play
 * and after `cap` plies. Empty when the FEN itself does not parse.
 */
export function uciPvToSan(fen: string, pv: string[], cap = MAX_PV_PLIES): string {
  try {
    const c = new Chess(fen);
    const out: string[] = [];
    for (let i = 0; i < pv.length && i < cap; i++) {
      const u = pv[i];
      if (u.length < 4) break;
      let mv;
      try {
        mv = legalMove(c, { from: u.slice(0, 2), to: u.slice(2, 4), promotion: u.length > 4 ? u[4] : undefined });
      } catch {
        break;
      }
      if (!mv) break;
      out.push(mv.san);
    }
    return out.join(" ");
  } catch {
    return "";
  }
}

/** What the server is sent about one board: the engine's readout, bounded and nothing more. */
export interface EngineSnapshot {
  depth: number;
  eval_cp: number;
  best_move: string;
  pv: string[];
}

/**
 * The readout of a finished search as a request payload, or null while it cannot be one
 * (still thinking, no score, no best move, no line). The line is trimmed to `MAX_PV_PLIES`
 * (the hook keeps the whole PV the engine printed); the final `bestmove` and the head of the
 * last scored line go as they are, even when they differ — the server says so in the prompt
 * rather than either being rewritten to match the other.
 */
export function engineSnapshot(ev: EngineEval | null): EngineSnapshot | null {
  if (!ev || ev.thinking || ev.evalCp == null || !ev.bestMoveUci || ev.pvUci.length === 0) return null;
  return { depth: ev.depth, eval_cp: ev.evalCp, best_move: ev.bestMoveUci, pv: ev.pvUci.slice(0, MAX_PV_PLIES) };
}
