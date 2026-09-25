/** What a position card shows. Pages map their own rows onto this; fields a page does not
 *  have are simply absent, and the card renders what it is given. */
import type { BlunderClass } from "../../blunders";
import type { RepLine } from "../../repertoire";

export interface PositionCardGame {
  game_url: string | null;
  played_at: string | null;
  result?: string | null;
  classification?: BlunderClass;
  cp_loss?: number | null;
  move_played?: string | null;
  best_move?: string | null;
  /** Scout, an opponent's game: what they played and which side they had. */
  opening?: string;
  as?: string;
}

export interface PositionCardData {
  fen: string;
  color: "white" | "black";
  times: number;
  score?: number;
  classifications?: Partial<Record<BlunderClass, number>>;
  topClassification?: BlunderClass | null;
  context?: string;
  book?: string | null;
  chapter?: string | null;
  lineNames?: string[] | null;
  movePlayed?: string | null;
  /** Deviations: the move most often played instead (drawn like a played move, and the
   *  move the similar-positions search asks about). */
  mostCommonPlayed?: string | null;
  bestMove?: string | null;
  /** Scout: the date of the analysed game the engine's move was read from. */
  bestMoveDate?: string | null;
  /** Deviations: the move the matched line expected (slot 2 of the arrow precedence).
   *  Scout: the repertoire's agreed move, which there outranks the engine's. */
  expectedMove?: string | null;
  /** The one move the repertoire agrees on at this board, or null (slot 3). */
  repExpectedMove?: string | null;
  /** Repertoire lines through this board, for the RepLines panel. */
  repLines?: RepLine[] | null;
  /** Deviations: the games' results at this pattern. */
  record?: { wins: number; losses: number; draws: number; win_pct: number } | null;
  bestLine?: string | null;
  cpLoss?: number | null;
  /** Moves of the example game and how many were played before this position. */
  moves?: string[] | null;
  ply?: number | null;
  /** With `ply`, names the analysed move an explanation is about. */
  chessGameId?: number | null;
  lastSeen?: string | null;
  dismissed?: boolean;
  /** New since the visit boundary the page was opened with (Home → Blunders). */
  isNew?: boolean;
  games: PositionCardGame[];
  /** Scout: the opponent's games through this board, shown beside the player's. */
  oppGames?: PositionCardGame[];
  /** Scout, a decision node: the opponent's replies here (the arrows), how many more were
   *  cut, the move that led here across the player's coverage and the board before it. */
  oppReplies?: Array<{ move: string; cnt: number }> | null;
  repliesMore?: number;
  leadIn?: string | null;
  leadPreFen?: string | null;
  nodeFreq?: number;
}

export type BoardSize = "S" | "M" | "L";

/**
 * The move a card recommends, and what to call it. Three authorities, in order: the engine's
 * best move, the page's own expected move (a deviation's matched line), and the repertoire's
 * agreed move — the last only when nothing was actually played, so a pure-repertoire move never
 * masquerades as an engine verdict. The label is derived beside the value so they cannot disagree.
 */
export function recommended(d: Pick<PositionCardData, "bestMove" | "expectedMove" | "movePlayed" | "repExpectedMove">): { move: string | null; label: "Best" | "Expected" } {
  if (d.bestMove) return { move: d.bestMove, label: "Best" };
  if (d.expectedMove) return { move: d.expectedMove, label: "Expected" };
  return { move: d.movePlayed ? null : (d.repExpectedMove ?? null), label: "Expected" };
}

/** First line name plus how many more: a transposition hub can match dozens. */
export function lineNamesSummary(names: string[] | null | undefined): string | null {
  if (!names?.length) return null;
  return names.length === 1 ? names[0] : `${names[0]} +${names.length - 1} more`;
}
