/** What a position card shows. Pages map their own rows onto this; fields a page does not
 *  have are simply absent, and the card renders what it is given. */
import type { BlunderClass } from "../../blunders";

export interface PositionCardGame {
  game_url: string | null;
  played_at: string | null;
  result?: string | null;
  classification?: BlunderClass;
  cp_loss?: number | null;
  move_played?: string | null;
  best_move?: string | null;
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
  bestMove?: string | null;
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
}

export type BoardSize = "S" | "M" | "L";

/** First line name plus how many more: a transposition hub can match dozens. */
export function lineNamesSummary(names: string[] | null | undefined): string | null {
  if (!names?.length) return null;
  return names.length === 1 ? names[0] : `${names[0]} +${names.length - 1} more`;
}
