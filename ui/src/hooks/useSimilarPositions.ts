/**
 * The one fetcher for `GET /repertoire/similar`: the Similar-positions panel on a card and the
 * solver's modal share it, so the request identity, the cache and the abort rules live in one
 * place. The identity is (fen, queriedMove), not the FEN alone — `is_queried_move` is computed
 * against the move, and two askers can share a board while questioning different moves.
 *
 * Nothing is requested while `enabled` is false. Enabling asks once per identity and remembers
 * the answer for the hook's lifetime — an answer once known is returned whether or not the hook is
 * still enabled, so a collapsed panel keeps its count; `idle` means not asked. Disabling, unmounting
 * or a change of identity aborts what is in flight, and a failure is forgotten when the hook is
 * enabled again, so re-enabling searches afresh. A response is keyed by the request it answers and
 * is only ever shown for that identity: a slow answer for an old board can never land under a new
 * one. `retry` asks again after a failure.
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
    // A failure recorded for this very request is not the answer to asking again.
    setResult((r) => (r?.key === attemptKey ? null : r));
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
    // attemptKey carries the identity and the attempt; fen and queriedMove are read through it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, attemptKey, cache]);

  const retry = () => setAttempt((n) => n + 1);
  const cached = cache.get(requestKey);
  if (cached) return { status: "loaded", data: cached, retry };
  if (!enabled) return { status: "idle", data: null, retry };
  if (result?.key === attemptKey) return { status: result.status, data: result.data, retry };
  return { status: "loading", data: null, retry };
}
