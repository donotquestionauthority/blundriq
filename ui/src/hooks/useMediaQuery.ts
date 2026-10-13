import { useSyncExternalStore } from "react";

const supported = () => typeof window !== "undefined" && typeof window.matchMedia === "function";

/** Whether `query` matches, tracking the viewport. Where `matchMedia` is missing (a test
 *  runtime) the answer is `fallback`, constant. */
export function useMediaQuery(query: string, fallback: boolean): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (!supported()) return () => {};
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => (supported() ? window.matchMedia(query).matches : fallback),
    () => fallback,
  );
}
