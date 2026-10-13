/** Types and calls for the repertoire read side: books, sections, toggles, notes, the walk-through,
 *  the two compare surfaces, and the Conflicts page. */
import { api, ApiError } from "./api";

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
  /** 'course' from the import; 'manual' added by hand from a gap card. */
  origin: "course" | "manual";
  /** How many times its puzzle was loaded in the solver (the delete confirm quotes it). */
  loads: number;
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
  /** Reserved: no producer yet, so always null; rendered whenever present. */
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

// --- Conflicts ------------------------------------------------------------------------------

/** One line's say at a position: who it is and whether it is in play. */
export interface ConflictLine {
  line_id: number;
  line_name: string;
  chapter_id: number;
  chapter_title: string;
  book_id: number;
  book_title: string;
  line_active: boolean;
  chapter_active: boolean;
  book_active: boolean;
  effective: boolean;
}

/** A position where two or more lines, in any state, prescribe different moves. `contested`
 *  when two of them are in play (the cards and puzzles then show no repertoire move there). */
export interface ConflictPosition {
  fen: string;
  color: "white" | "black";
  contested: boolean;
  active_moves: number;
  moves: { move: string; lines: (ConflictLine & { move: string })[] }[];
}

/** Identical lines (same colour, same start, same moves) in different chapters. The group's
 *  identity is (color, root, moves): the same moves from two starting positions are two groups. */
export interface DuplicateGroup {
  color: "white" | "black";
  root: string;
  moves: string[];
  lines: ConflictLine[];
}

export interface ConflictsResponse {
  positions: ConflictPosition[];
  duplicates: DuplicateGroup[];
  contested: number;
}

/** Why a line could not come on: the first position that blocked it, its move there, and the
 *  lines holding the position against it (their moves included). */
export interface Refusal {
  line_id: number;
  line_name: string;
  chapter_title: string;
  fen: string;
  move: string;
  reason: "anchor" | "dirty" | "cohort";
  rivals: { line_id: number; line_name: string; chapter_id: number; chapter_title: string; book_id: number; book_title: string; move: string }[];
}

export interface ToggleResponse {
  detail: string;
  rematch: { candidates: number; matched: number; no_match: number; lines: number };
  /** Lines a chapter or book could not bring on with it; empty for a line and for switching off. */
  held_back: Refusal[];
}

export const getBooks = () => api.get<{ books: RepertoireBook[] }>("/repertoire");
export const getSections = (bookId: number) => api.get<{ sections: RepertoireSection[] }>(`/repertoire/${bookId}/sections`);
export const setActive = (kind: "books" | "chapters" | "lines", id: number, active: boolean) => api.patch<ToggleResponse>(`/repertoire/${kind}/${id}`, { active });
export const getConflicts = () => api.get<ConflictsResponse>("/repertoire/conflicts");

/** The refusal carried by a 409 on a line toggle, or null for any other error. */
export function refusalOf(err: unknown): Refusal | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { detail?: unknown; refusal?: Refusal } | undefined;
  return body?.detail === "activation_conflict" && body.refusal ? body.refusal : null;
}

export const conflictsPath = (opts: { fen?: string; filter?: "all" } = {}) => {
  const q = new URLSearchParams();
  if (opts.fen) q.set("fen", opts.fen);
  if (opts.filter) q.set("filter", opts.filter);
  const s = q.toString();
  return `/repertoire/conflicts${s ? `?${s}` : ""}`;
};

export const getAnnotation = (fen: string) => api.get<Annotation | null>(`/repertoire/annotation?fen=${encodeURIComponent(fen)}`);
export const putAnnotation = (fen: string, text: string, lineId: number | null) => api.put<Annotation | { detail: string; line_id: number }>("/repertoire/annotation", { fen, text, line_id: lineId });
export const deleteAnnotation = (fen: string, lineId: number | null) => api.del<{ detail: string }>(`/repertoire/annotation?fen=${encodeURIComponent(fen)}${lineId != null ? `&line_id=${lineId}` : ""}`);
/** `GET /repertoire/coverage`: the repertoire's view of one card's board. `line_id` names a line
 *  to walk through (the selected one, or any of the board's lines when they disagree) and
 *  `line_ply` the board's index in it; `played_is_book` is null without a move to judge. */
export interface RepertoireCoverage {
  status: "match" | "agree" | "end_of_line" | "conflict" | "unreadable" | "none";
  transposed: boolean | null;
  book_move: string | null;
  played_is_book: boolean | null;
  book: string | null;
  chapter: string | null;
  line_name: string | null;
  line_id: number | null;
  line_ply: number | null;
  more_lines: number;
}
export const getCoverage = (fen: string, move: string | null, signal?: AbortSignal) => api.get<RepertoireCoverage>(`/repertoire/coverage?fen=${encodeURIComponent(fen)}${move ? `&move=${encodeURIComponent(move)}` : ""}`, signal);
export const getLineAnnotated = (lineId: number) => api.get<LineReaderLine>(`/repertoire/lines/${lineId}/annotated`);

export interface LineExplanation {
  explanation: string;
  cached: boolean;
  model: string;
  prompt_label: string;
}
/** Why the move arriving at `ply` matters, given the whole line and its notes; `question` may be empty. */
export const explainLineMove = (lineId: number, ply: number, question: string) => api.post<LineExplanation>(`/repertoire/lines/${lineId}/explain`, { ply, question });

/** "Imported: <title> by <author>", or the url, or nothing. */
export function provenanceLabel(b: RepertoireBook): string | null {
  if (b.source_title && b.source_author) return `${b.source_title} by ${b.source_author}`;
  if (b.source_title) return b.source_title;
  if (b.source_author) return `by ${b.source_author}`;
  return b.source_url;
}

// --- the line report (GET /repertoire/report/lines, /gaps) --------------------------------

/** One scope's games: a line, a chapter or a book. `slip` is the most common whole departure. */
export interface ReportTally {
  games: number;
  wins: number;
  draws: number;
  losses: number;
  score: number | null;
  left_by_me: number;
  left_by_opponent: number;
  ended: number;
  ran_out: number;
  faithful: number;
  /** {own moves reached: games} */
  depth_reached: Record<string, number>;
  slip: { own_move: number; expected: string; played: string; count: number } | null;
  last_played: string | null;
  /** The positions Practice serves for this scope; 0 means nothing to drill. */
  puzzles: number;
}
/** Loads of a line's puzzle, and how the first attempt of each load went (core/puzzles/stats.py). */
export interface PracticeNumbers {
  loads: number;
  first_right: number;
  first_wrong: number;
}
export interface ReportLine extends ReportTally {
  id: number;
  title: string;
  is_alternative: boolean;
  origin: "course" | "manual";
  depth_total: number;
  shared: boolean;
  book_id: number;
  book: string;
  chapter_id: number;
  chapter: string;
  colour: "white" | "black";
  practice: PracticeNumbers;
}
export interface ReportChapter extends ReportTally {
  id: number;
  title: string;
  book_id: number;
  book: string;
  colour: "white" | "black";
  lines: ReportLine[];
  practice: PracticeNumbers;
}
export interface ReportBook extends ReportTally {
  id: number;
  title: string;
  color: "white" | "black";
  colour: "white" | "black";
  chapters: ReportChapter[];
  practice: PracticeNumbers;
}

export interface GapCard {
  fen: string;
  colour: "white" | "black";
  /** The move that reached the board (the opponent's, or White's first move for a bookless opening). */
  arrived_by: string | null;
  games: number;
  wins: number;
  draws: number;
  losses: number;
  score: number | null;
  family: string | null;
  /** The book the games were in and where the opponent left it; null for a board no book reaches. */
  context: { book_id: number; book: string; chapter_id: number; chapter: string; line_id: number; line_name: string; expected: string; played: string; count: number } | null;
  continuations: { moves: string[]; games: number; score: number | null }[];
  /** The stored engine line from the board (the most recent analysed game's); null when none is analysed. */
  engine: { best_move: string | null; line: string[]; eval: number | null; mate_in: number | null } | null;
  last_played: string | null;
  /** The representative game's moves from the start to this board, re-rendered by the server;
   *  null when they do not replay there (no line can be added from the card then). */
  stem: string[] | null;
  /** Start-rooted active chapters of this colour's books, the context chapter first. */
  chapters: { id: number; title: string; book_id: number; book: string }[];
  /** The titles the catch-all option would use, whether or not the rows exist yet. */
  catch_all: { book: string; chapter: string };
}
export interface PreparedRow {
  fen: string;
  book_move: string;
  played: string;
  games: number;
  wins: number;
  draws: number;
  losses: number;
  line_id: number | null;
  line_name: string | null;
  book: string | null;
  chapter: string | null;
  puzzles: number;
  last_played: string | null;
}
export interface GapsResponse {
  colour: "white" | "black";
  no_book: boolean;
  prepared: PreparedRow[];
  gaps: GapCard[];
  /** How much of a card's engine line a new line opens with, and the most plies a line may have (core/constants.py). */
  prefill_plies: number;
  max_plies: number;
}

export interface NewLineBody {
  moves: string[];
  line_name: string;
  chapter_id?: number;
  catch_all?: { colour: "white" | "black"; family: string | null };
}
export interface NewLineResponse {
  id: number;
  chapter_id: number;
  book_id: number;
  rematch: { candidates: number; matched: number; no_match: number; lines: number };
}
/** 409 bodies of POST /repertoire/lines. */
export interface DuplicateLine {
  line_id: number;
  line_name: string;
  active: boolean;
  kind: "identical" | "prefix";
}
export const addLine = (body: NewLineBody) => api.post<NewLineResponse>("/repertoire/lines", body);
export const deleteLine = (lineId: number) => api.del<{ detail: string; loads: number }>(`/repertoire/lines/${lineId}`);
export function duplicateOf(err: unknown): DuplicateLine | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { detail?: unknown; duplicate?: unknown } | null | undefined;
  return body?.detail === "duplicate" && body.duplicate ? (body.duplicate as DuplicateLine) : null;
}

/** `drill_max_lines`: how many lines one drill takes (core/constants.py; the server refuses more). */
export const getReportLines = (timeClass: string) => api.get<{ books: ReportBook[]; drill_max_lines: number }>(`/repertoire/report/lines?time_class=${encodeURIComponent(timeClass)}`);
export const getReportGaps = (colour: "white" | "black", timeClass: string) => api.get<GapsResponse>(`/repertoire/report/gaps?colour=${colour}&time_class=${encodeURIComponent(timeClass)}`);
export const reportPath = () => "/repertoire/report";
