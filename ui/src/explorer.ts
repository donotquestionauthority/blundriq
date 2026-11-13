/**
 * The Opening Explorer's two reads (GET /explorer/position, GET /explorer/lines) and their
 * shapes. The URL and the entry helper are `utils/explorer.ts`.
 */
import { api } from "./api";
import type { Colour, ExplorerTimeClass, ExplorerUrl } from "./utils/explorer";

export const EXPLORER_LINES_PAGE = 50;

export interface ExplorerNumbers {
  n: number;
  w: number;
  d: number;
  l: number;
  score: number | null;
  n_rated: number;
  vs_expectation: number | null;
}

export interface ExplorerMove extends ExplorerNumbers {
  san: string;
  in_repertoire: boolean;
}

export interface RepertoireMove {
  san: string;
  book: string;
  chapter: string;
  line_name: string | null;
  line_id: number;
  line_ply: number;
  lines: number;
}

export type ExplorerStatus = "match" | "agree" | "end_of_line" | "conflict" | "unreadable" | "none" | "replies";

export interface ExplorerPosition {
  colour: Colour;
  fen: string;
  ply: number;
  player_to_move: boolean;
  repertoire: { status: ExplorerStatus; moves: RepertoireMove[] };
  node: (ExplorerNumbers & { continued: number }) | null;
  moves: ExplorerMove[];
  arrival: { off_book: boolean; book_moves: string[] } | null;
  lines_through: number;
  months: number;
  time_class: ExplorerTimeClass;
  beyond_prefix: boolean;
}

export interface ExplorerLine {
  line_id: number;
  line_name: string | null;
  book: string;
  chapter: string;
  line_ply: number;
}

export interface ExplorerLines {
  total: number;
  rows: ExplorerLine[];
  offset: number;
  page_size: number;
}

export const getExplorerPosition = (u: ExplorerUrl) => {
  const q = new URLSearchParams({ colour: u.colour, moves: u.moves.join(","), time_class: u.timeClass });
  return api.get<ExplorerPosition>(`/explorer/position?${q.toString()}`);
};

export const getExplorerLines = (colour: Colour, moves: string[], offset: number, signal?: AbortSignal) => {
  const q = new URLSearchParams({ colour, moves: moves.join(","), offset: String(offset) });
  return api.get<ExplorerLines>(`/explorer/lines?${q.toString()}`, signal);
};

/** The moves the repertoire prescribes to the player on this answer: none unless it reads a
 *  match, an agreement or a conflict. */
export function prescribedMoves(p: ExplorerPosition): RepertoireMove[] {
  const s = p.repertoire.status;
  return s === "match" || s === "agree" || s === "conflict" ? p.repertoire.moves : [];
}
