import type { NavigateFunction } from "react-router";

/** Where a game's review (or a Review position's page) returns to: the page it was opened from
 *  (`state.from`), else the Review page. The whole state goes back, so the opener can restore
 *  itself (the Games page reads its own snapshot from it); the Review page's expansion, carried in
 *  `from.open`, becomes the new entry's own `open`. An opener with its own way back (a position's
 *  page, opened from Review) sends its whole state as `from.state`, and gets exactly that back. */
export type From = { pathname: string; search?: string; open?: unknown; state?: unknown; back?: true };

export function returnTarget(state: unknown): { to: string; state: unknown } {
  const from = (state as { from?: From } | null)?.from;
  if (!from) return { to: "/review", state: null };
  const to = `${from.pathname}${from.search ?? ""}`;
  if (from.state !== undefined) return { to, state: from.state };
  return { to, state: from.open ? { ...(state as object), open: from.open } : state };
}

/** Leaves a game's review. An opener that set `from.back` is the history entry right before the
 *  review and restores itself from that entry, so the way back is the browser's Back (the entry
 *  is mounted and spends what it holds); any other opener is returned to by `returnTarget`. */
export function leaveReview(state: unknown, navigate: NavigateFunction): void {
  if ((state as { from?: From } | null)?.from?.back) {
    void navigate(-1);
    return;
  }
  const back = returnTarget(state);
  void navigate(back.to, back.state != null ? { state: back.state } : undefined);
}
