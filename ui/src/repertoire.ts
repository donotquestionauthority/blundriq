/** Types and calls for the repertoire read side: books, sections, toggles, notes, the walk-through. */
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
