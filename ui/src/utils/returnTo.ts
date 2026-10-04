/** Where a game's review (or a Review position's page) returns to: the page it was opened from
 *  (`state.from`), else the Review page. The whole state goes back, so the opener can restore
 *  itself (the Games page reads its own snapshot from it); the Review page's expansion, carried in
 *  `from.open`, becomes the new entry's own `open`. An opener with its own way back (a position's
 *  page, opened from Review) sends its whole state as `from.state`, and gets exactly that back. */
export type From = { pathname: string; search?: string; open?: unknown; state?: unknown };

export function returnTarget(state: unknown): { to: string; state: unknown } {
  const from = (state as { from?: From } | null)?.from;
  if (!from) return { to: "/review", state: null };
  const to = `${from.pathname}${from.search ?? ""}`;
  if (from.state !== undefined) return { to, state: from.state };
  return { to, state: from.open ? { ...(state as object), open: from.open } : state };
}
