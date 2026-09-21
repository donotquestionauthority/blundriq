import { useCallback, useEffect, useRef, useState } from "react";

interface ApiState<T> {
  data: T | null;
  isLoading: boolean;
  error: string | null;
  /** The deps that PRODUCED `data`, written in the same setState so the pair is never torn. */
  dataDeps: unknown[] | null;
}

function shallowEqualDeps(a: unknown[] | null, b: unknown[]): boolean {
  if (a === null || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (!Object.is(a[i], b[i])) return false;
  return true;
}

/**
 * Fetch on mount and whenever `deps` change; `refetch()` re-runs the fetcher in place.
 *
 * Only the newest run may write state, so a slow earlier response never overwrites a later one.
 * `isStale` is derived DURING RENDER from the deps stored alongside the data: it is true from the
 * very first render that sees new deps until the matching response lands. An effect-set flag
 * would land one render late and let the old data mount once under the new deps. Pass
 * primitives in `deps`; a freshly-constructed object compares unequal every render.
 */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []): ApiState<T> & { refetch: () => void; isStale: boolean } {
  const [state, setState] = useState<ApiState<T>>({ data: null, isLoading: true, error: null, dataDeps: null });

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const runIdRef = useRef(0);

  const run = useCallback(() => {
    const myRun = ++runIdRef.current;
    const runDeps = deps;
    setState((s) => ({ ...s, isLoading: true, error: null }));
    fetcherRef
      .current()
      .then((data) => {
        if (myRun !== runIdRef.current) return;
        setState({ data, isLoading: false, error: null, dataDeps: runDeps });
      })
      .catch((err: unknown) => {
        if (myRun !== runIdRef.current) return;
        setState({ data: null, isLoading: false, error: err instanceof Error ? err.message : String(err), dataDeps: null });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    run();
    return () => {
      runIdRef.current += 1;
    };
  }, [run]);

  const isStale = state.data != null && !shallowEqualDeps(state.dataDeps, deps);

  return { ...state, refetch: run, isStale };
}
