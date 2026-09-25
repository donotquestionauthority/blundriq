/**
 * The one fetcher for `GET /repertoire/similar`: the Similar-positions panel on a card and the
 * solver's modal share it, so the request identity, the cache and the abort rules live in one
 * place. The identity is (fen, queriedMove), not the FEN alone — `is_queried_move` is computed
 * against the move, and two askers can share a board while questioning different moves.
 *
 * Nothing is requested while `enabled` is false. Enabling asks once per identity and remembers
 * the answer for the hook's lifetime; disabling, unmounting or a change of identity aborts what is
 * in flight. A response is keyed by the request it answers and is only ever shown for that
 * identity: a slow answer for an old board can never land under a new one. `retry` asks again
 * after a failure.
 */
import { useEffect, useState } from "react";
import { getSimilarPositions } from "../repertoire";
import type { SimilarPositionsResponse } from "../repertoire";

export type SimilarStatus = "idle" | "loading" | "loaded" | "error";

export interface SimilarPositions {
  status: SimilarStatus;
  data: SimilarPositionsResponse | null;
  retry: () => void;
}

/** '|' occurs in neither a FEN nor a SAN, so the key is injective. */
export function similarRequestKey(fen: string, queriedMove: string | null | undefined): string {
  return fen + "|" + (queriedMove ?? "");
}

export function useSimilarPositions(fen: string, queriedMove: string | null | undefined, enabled: boolean): SimilarPositions {
  const requestKey = similarRequestKey(fen, queriedMove);
  // One map for the hook's lifetime; mutated only under a setResult that re-renders, read in render.
  const [cache] = useState(() => new Map<string, SimilarPositionsResponse>());
  const [attempt, setAttempt] = useState(0);
  // The answer is keyed by the request (identity + attempt) it answers; anything else is loading.
  const attemptKey = `${requestKey}|${attempt}`;
  const [result, setResult] = useState<{ key: string; status: "loaded" | "error"; data: SimilarPositionsResponse | null } | null>(null);

  useEffect(() => {
    if (!enabled || cache.has(requestKey)) return;
    const controller = new AbortController();
    getSimilarPositions(fen, queriedMove ?? null, controller.signal)
      .then((resp) => {
        cache.set(requestKey, resp); // under the key the request was made with
        if (!controller.signal.aborted) setResult({ key: attemptKey, status: "loaded", data: resp });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResult({ key: attemptKey, status: "error", data: null });
      });
    return () => controller.abort();
  }, [enabled, fen, queriedMove, requestKey, attemptKey, cache]);

  const retry = () => setAttempt((n) => n + 1);
  if (!enabled) return { status: "idle", data: null, retry };
  const cached = cache.get(requestKey);
  if (cached) return { status: "loaded", data: cached, retry };
  if (result?.key === attemptKey) return { status: result.status, data: result.data, retry };
  return { status: "loading", data: null, retry };
}
