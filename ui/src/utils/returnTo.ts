/** Where a game's review returns to: the page it was opened from (`state.from`), else the
 *  worklist. The whole state goes back, so the opener can restore itself (the Games page reads
 *  its own snapshot from it); the worklist's expansion, carried in `from.open`, becomes the new
 *  entry's own `open`. */
export type From = { pathname: string; search?: string; open?: unknown };

export function returnTarget(state: unknown): { to: string; state: unknown } {
  const from = (state as { from?: From } | null)?.from;
  if (!from) return { to: "/review", state: null };
  return { to: `${from.pathname}${from.search ?? ""}`, state: from.open ? { ...(state as object), open: from.open } : state };
}
