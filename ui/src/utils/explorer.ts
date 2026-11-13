/**
 * The Opening Explorer's client side: the URL that carries the board (colour, the path from the
 * start in SAN, the time class), the two reads, and the one helper every entry point uses.
 *
 * A URL's path is replayed with `legalMove` (chess.js plays `--`); the first token that is not a
 * legal move, and everything after it, is dropped, and so is anything past EXPLORER_MAX_PLIES.
 * The page rewrites the URL to what was kept. The reads are `../explorer.ts`.
 */
import { Chess } from "chess.js";
import { legalMove, mapKey } from "./chess";

/** The longest path, in plies: core/constants.py EXPLORER_MAX_PLIES (a test pins the two). */
export const EXPLORER_MAX_PLIES = 60;
/** The statistics end where the games' stored openings do: core/constants.py OPENING_PREFIX_PLIES. */
export const EXPLORER_PREFIX_PLIES = 30;

export type Colour = "white" | "black";
export type ExplorerTimeClass = "focus" | "all";

export interface ExplorerUrl {
  colour: Colour;
  moves: string[];
  timeClass: ExplorerTimeClass;
}

export interface ReadUrl extends ExplorerUrl {
  /** The URL said something else (a dropped tail, an unknown value): rewrite it. */
  rewrite: boolean;
}

/** The URL's query: the path's commas kept as they are, so it reads (and edits) as
 *  `moves=e4,c6,d4`; a check's `+` and a mate's `#` are escaped. */
export function explorerSearch(u: ExplorerUrl): string {
  const moves = u.moves.length ? `&moves=${u.moves.map(encodeURIComponent).join(",")}` : "";
  return `?colour=${u.colour}${moves}&time_class=${u.timeClass}`;
}

/** The longest legal prefix of `tokens` from the start, at most EXPLORER_MAX_PLIES, as SAN. */
export function legalPrefix(tokens: string[]): string[] {
  const g = new Chess();
  const kept: string[] = [];
  for (const t of tokens.slice(0, EXPLORER_MAX_PLIES)) {
    let mv;
    try {
      mv = legalMove(g, t);
    } catch {
      mv = null;
    }
    if (!mv) break;
    kept.push(mv.san);
  }
  return kept;
}

export function readExplorerUrl(params: URLSearchParams): ReadUrl {
  const rawColour = params.get("colour");
  const rawClass = params.get("time_class");
  const colour: Colour = rawColour === "black" ? "black" : "white";
  const timeClass: ExplorerTimeClass = rawClass === "all" ? "all" : "focus";
  // A `+` typed into the URL arrives as a space, which no move has.
  const raw = params.get("moves")?.replaceAll(" ", "+") ?? null;
  const tokens = raw ? raw.split(",") : [];
  const moves = legalPrefix(tokens);
  const rewrite = rawColour !== colour || rawClass !== timeClass || moves.join(",") !== (raw ?? "") || (raw === "" && params.has("moves"));
  return { colour, moves, timeClass, rewrite };
}

export type Entry = { to: string } | { unavailable: string | null };

/**
 * Where an entry point opens the explorer: exactly the board on show, or nothing. `moves`
 * from the start, the first `ply` of them reach `fen` (compared by `mapKey`), as `colour`.
 * Past the explorer's bound the entry is disabled with its reason; a path that does not reach
 * the board (a decision node's card, a game from another start) has no entry at all
 * (`unavailable: null`).
 */
export function explorerEntry(moves: string[] | null | undefined, ply: number | null | undefined, fen: string, colour: Colour): Entry {
  if (!moves || ply == null || ply < 0 || ply > moves.length) return { unavailable: null };
  if (ply > EXPLORER_MAX_PLIES) return { unavailable: "The Opening Explorer covers the first 30 moves." };
  const path = moves.slice(0, ply);
  const kept = legalPrefix(path);
  if (kept.length !== path.length) return { unavailable: null };
  const g = new Chess();
  for (const m of kept) legalMove(g, m);
  let target: string;
  try {
    target = mapKey(new Chess(fen));
  } catch {
    return { unavailable: null };
  }
  if (mapKey(g) !== target) return { unavailable: null };
  return { to: `/explorer${explorerSearch({ colour, moves: kept, timeClass: "focus" })}` };
}

/** The request key: an answer is applied only while its key is the URL's. */
export const requestKey = (u: ExplorerUrl) => `${u.colour}|${u.timeClass}|${u.moves.join(",")}`;
