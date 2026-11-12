/** Review latest game: the site, the Home toggle's stored choice, and the board rule the page uses. */
import { Chess } from "chess.js";
import type { LatestSource } from "./home";

export const SITE_LABEL: Record<LatestSource, string> = { chesscom: "Chess.com", lichess: "Lichess" };
/** After this long the loading line says the server may be waking up. */
export const SLOW_FETCH_MS = 5_000;
/** Where the toggle's last choice is kept; read and written inside try/catch, never trusted. */
export const LATEST_SOURCE_KEY = "blundriq.latestReviewSource";

export function sourceOf(raw: string | null): LatestSource | null {
  return raw === "chesscom" || raw === "lichess" ? raw : null;
}

/** The site on show: the stored choice when it is listed, otherwise — nothing stored, storage
 *  unavailable or throwing, a stored site no longer listed — the first listed site. */
export function chosenSource(sources: LatestSource[], stored: string | null): LatestSource | null {
  if (sources.length === 0) return null;
  return sources.find((s) => s === stored) ?? sources[0];
}

export function readStoredSource(): string | null {
  try {
    return window.localStorage.getItem(LATEST_SOURCE_KEY);
  } catch {
    return null;
  }
}

export function storeSource(s: LatestSource): void {
  try {
    window.localStorage.setItem(LATEST_SOURCE_KEY, s);
  } catch {
    /* the choice holds for this visit */
  }
}

/** Checkmate or stalemate on the board: nothing to search. */
export function boardIsOver(fen: string): boolean {
  try {
    const c = new Chess(fen);
    return c.isCheckmate() || c.isStalemate();
  } catch {
    return false;
  }
}
