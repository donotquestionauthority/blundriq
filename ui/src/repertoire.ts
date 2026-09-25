/** Types and calls for the repertoire read side: books, sections, toggles, notes, the walk-through,
 *  and the two compare surfaces. */
import { api } from "./api";

export interface RepertoireBook {
  book_id: number;
  title: string;
  color: "white" | "black";
  active: boolean;
  source_url: string | null;
  source_author: string | null;
  source_title: string | null;
  total_lines: number;
  active_lines: number;
}

export interface RepertoireLine {
  id: number;
  name: string;
  moves: string[];
  active: boolean;
  is_alternative: boolean;
}

export interface RepertoireSection {
  chapter_id: number;
  title: string;
  active: boolean;
  root_fen: string | null;
  lines: RepertoireLine[];
}

export type NoteSource = "course" | "manual";

/** The unattached note at a position (the editor on any card). */
export interface Annotation {
  text: string;
  source: NoteSource;
  author: string | null;
  book_title: string | null;
  updated_at: string | null;
}

/** A note projected onto one ply of a line; `from_chapter` names another chapter it came from. */
export interface LineNote {
  text: string;
  source: NoteSource;
  author: string | null;
  book_title: string | null;
  from_chapter: string | null;
}

export interface LineReaderPosition {
  ply: number;
  fen: string;
  move: string | null;
  annotation: LineNote | null;
}

export interface LineReaderLine {
  line_id: number;
  line_name: string;
  color: "white" | "black";
  book_title: string;
  chapter_title: string;
  positions: LineReaderPosition[];
}

/** One occurrence of a board inside a repertoire line, with the games' record there. */
export interface RepLine {
  book: string;
  chapter: string;
  line_name: string;
  line_id: number;
  is_alternative: boolean;
  expected_move: string | null;
  occurrence_fen: string | null;
  line_moves: string[];
  line_ply: number;
  followed: number;
  last_followed: string | null;
  deviated_by_me: number;
  last_deviated: string | null;
  me_dev_expected: string | null;
  me_dev_played: string | null;
  me_dev_ply: number | null;
  deviated_by_opp: number;
  opp_dev_expected: string | null;
  opp_dev_played: string | null;
  opp_dev_ply: number | null;
}

// --- Similar positions and Compare ----------------------------------------------------------
// Both responses are hierarchical and server-final: one entry per board carrying all of its
// groups, capped by the server in boards. The UI groups nothing, reduces nothing and derives no
// chess of its own beyond turning a server SAN into arrow squares.

export interface ArrivingMove {
  san: string;
  from: string;
  to: string;
  promotion: "q" | "r" | "b" | "n" | null;
  is_castling: boolean;
  is_en_passant: boolean;
}

export interface PrepGroup {
  prep_move: string | null; // canonical SAN; null unless prep_status is 'move'
  prep_status: "move" | "end_of_line" | "unreadable";
  prep_raw_token: string | null; // the stored token, when 'unreadable'
  is_queried_move: boolean;
  arriving: ArrivingMove;
  book_title: string | null;
  chapter_title: string | null;
  line_name: string | null;
  line_id: number;
  line_ply: number;
  is_alternative: boolean;
  carried_by_line_count: number;
}

export interface SimilarNeighbour {
  fen: string;
  distance: number;
  same_material: boolean;
  castling_delta: string[];
  is_castle_shape: boolean;
  diff_squares: { square: string; from: string | null; to: string | null }[];
  board_prep_divergent: boolean;
  groups: PrepGroup[];
}

export interface SimilarPositionsResponse {
  query: { fen: string; move: string | null; max_distance: number; max_positions: number };
  truncated: boolean;
  positions_omitted: number;
  neighbours: SimilarNeighbour[];
}

export interface MoveSquares {
  from: string;
  to: string;
}

export interface BranchSources {
  repertoire: {
    reply_san: string | null; // null when end_of_line or divergent
    reply_squares: MoveSquares | null;
    end_of_line: boolean;
    line_count: number;
    board_prep_divergent: boolean;
    groups: PrepGroup[];
  } | null;
  /** Null whenever `repertoire` is set: the repertoire wins on a board. */
  blunders: {
    games: number;
    worst: { move_played_san: string; move_played_squares: MoveSquares; best_move_san: string | null; best_move_squares: MoveSquares | null; centipawn_loss: number | null };
  } | null;
  /** Phase 6; always null until then, rendered whenever present. */
  scout: { total_games: number; profiles: { name: string; games: number }[]; best_move_san: string | null; best_move_squares: MoveSquares | null } | null;
}

export interface CompareBranch {
  child_fen: string;
  opponent_move: ArrivingMove;
  sources: BranchSources;
}

export interface BranchCompareResponse {
  query: { fen: string; pre_fen: string; book_color: "white" | "black"; max_boards: number };
  /** The branch the puzzle came from: always present, never in `branches`, never capped. */
  current: CompareBranch;
  truncated: boolean;
  boards_omitted: number;
  branches: CompareBranch[];
}

export const getSimilarPositions = (fen: string, move: string | null, signal?: AbortSignal) => api.get<SimilarPositionsResponse>(`/repertoire/similar?fen=${encodeURIComponent(fen)}${move ? `&move=${encodeURIComponent(move)}` : ""}`, signal);
export const getBranchCompare = (fen: string, preFen: string, signal?: AbortSignal) => api.get<BranchCompareResponse>(`/repertoire/branch-compare?fen=${encodeURIComponent(fen)}&pre_fen=${encodeURIComponent(preFen)}`, signal);

export const getBooks = () => api.get<{ books: RepertoireBook[] }>("/repertoire");
export const getSections = (bookId: number) => api.get<{ sections: RepertoireSection[] }>(`/repertoire/${bookId}/sections`);
export const setActive = (kind: "books" | "chapters" | "lines", id: number, active: boolean) => api.patch<{ detail: string }>(`/repertoire/${kind}/${id}`, { active });

export const getAnnotation = (fen: string) => api.get<Annotation | null>(`/repertoire/annotation?fen=${encodeURIComponent(fen)}`);
export const putAnnotation = (fen: string, text: string, lineId: number | null) => api.put<Annotation | { detail: string; line_id: number }>("/repertoire/annotation", { fen, text, line_id: lineId });
export const deleteAnnotation = (fen: string, lineId: number | null) => api.del<{ detail: string }>(`/repertoire/annotation?fen=${encodeURIComponent(fen)}${lineId != null ? `&line_id=${lineId}` : ""}`);
export const getLineAnnotated = (lineId: number) => api.get<LineReaderLine>(`/repertoire/lines/${lineId}/annotated`);

/** "Imported: <title> by <author>", or the url, or nothing. */
export function provenanceLabel(b: RepertoireBook): string | null {
  if (b.source_title && b.source_author) return `${b.source_title} by ${b.source_author}`;
  if (b.source_title) return b.source_title;
  if (b.source_author) return `by ${b.source_author}`;
  return b.source_url;
}
