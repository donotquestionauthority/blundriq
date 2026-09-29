/**
 * The one fetcher for `GET /repertoire/coverage`: whether a card's board is in the repertoire,
 * and the line to read from there. One request per (fen, move); a change of either aborts what
 * is in flight. The answer is keyed by the request it answers and is only ever shown for that
 * identity, so a slow answer for the previous card cannot land under this one. A failed request
 * is `unknown`: the badge renders nothing rather than claim the board is off book.
 */
import { useEffect, useState } from "react";
import { getCoverage } from "../repertoire";
import type { RepertoireCoverage } from "../repertoire";

export type CoverageState = { status: "loading" | "unknown"; data: null } | { status: "known"; data: RepertoireCoverage };

/** '|' occurs in neither a FEN nor a SAN, so the key is injective. */
export function coverageRequestKey(fen: string, move: string | null): string {
  return fen + "|" + (move ?? "");
}

export function useCoverage(fen: string, move: string | null): CoverageState {
  const requestKey = coverageRequestKey(fen, move);
  const [result, setResult] = useState<{ key: string; data: RepertoireCoverage | null } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getCoverage(fen, move, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setResult({ key: requestKey, data });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResult({ key: requestKey, data: null });
      });
    return () => controller.abort();
  }, [fen, move, requestKey]);
  if (!result || result.key !== requestKey) return { status: "loading", data: null };
  return result.data ? { status: "known", data: result.data } : { status: "unknown", data: null };
}
