/** The line report's row model: one flat shape for the line, chapter and book views, with the
 *  sort and the filters the page applies to it. Pure functions, kept out of the page so the page
 *  file only exports components. */
import type { DecisionRow, PracticeNumbers, ReportBook, ReportLine, ReportTally } from "../repertoire";

/** The three table views over the lines, and the decisions view over the boards. */
export type View = "lines" | "chapters" | "books" | "decisions";
export type TableView = Exclude<View, "decisions">;
/** `study` is the Study order preset: slips, then the latest slip, then the tie-break. */
export type SortKey = "study" | "colour" | "book" | "chapter" | "line" | "games" | "slips" | "last_slip" | "trouble" | "tested" | "score" | "faithful" | "depth" | "loads" | "first_right" | "last_played";
export type Dir = "asc" | "desc";
export const VIEWS: View[] = ["lines", "chapters", "books", "decisions"];
export const TABLE_VIEWS: TableView[] = ["lines", "chapters", "books"];
export const SORT_KEYS: SortKey[] = ["study", "colour", "book", "chapter", "line", "games", "slips", "last_slip", "trouble", "tested", "score", "faithful", "depth", "loads", "first_right", "last_played"];

/** One row of any view: a line, a chapter or a book, with the ids of the lines it stands for. */
export interface Row extends ReportTally {
  key: string;
  kind: TableView;
  id: number;
  colour: "white" | "black";
  book: string;
  book_id: number;
  chapter: string | null;
  chapter_id: number | null;
  line: ReportLine | null;
  lineIds: number[];
  count: number; // lines under a chapter, chapters under a book
  practice: PracticeNumbers;
}

export function flatten(books: ReportBook[]): { lines: Row[]; chapters: Row[]; books: Row[] } {
  const lines: Row[] = [];
  const chapters: Row[] = [];
  const out: Row[] = [];
  for (const b of books) {
    const bookLines: number[] = [];
    for (const ch of b.chapters) {
      const ids = ch.lines.map((l) => l.id);
      bookLines.push(...ids);
      for (const l of ch.lines) {
        lines.push({ ...l, key: `line:${l.id}`, kind: "lines", id: l.id, colour: b.colour, book: b.title, book_id: b.id, chapter: ch.title, chapter_id: ch.id, line: l, lineIds: [l.id], count: 1, practice: l.practice });
      }
      chapters.push({ ...ch, key: `chapter:${ch.id}`, kind: "chapters", id: ch.id, colour: b.colour, book: b.title, book_id: b.id, chapter: ch.title, chapter_id: ch.id, line: null, lineIds: ids, count: ch.lines.length, practice: ch.practice });
    }
    out.push({ ...b, key: `book:${b.id}`, kind: "books", id: b.id, colour: b.colour, book: b.title, book_id: b.id, chapter: null, chapter_id: null, line: null, lineIds: bookLines, count: b.chapters.length, practice: b.practice });
  }
  return { lines, chapters, books: out };
}

/** The depth histogram's median own move, from {own moves: games}. */
export function medianDepth(reached: Record<string, number>): number {
  const entries = Object.entries(reached)
    .map(([k, v]) => [Number(k), v] as const)
    .sort((a, b) => a[0] - b[0]);
  const total = entries.reduce((n, [, v]) => n + v, 0);
  let seen = 0;
  for (const [k, v] of entries) {
    seen += v;
    if (seen * 2 >= total) return k;
  }
  return 0;
}

const fraction = (n: number, of: number) => (of === 0 ? -1 : n / of);

/** The sort value of a row under a key; nulls last in either direction. */
function sortValue(r: Row, key: SortKey): number | string | null {
  switch (key) {
    case "colour":
      return r.colour;
    case "book":
      return r.book;
    case "chapter":
      return r.chapter ?? "";
    case "line":
      return r.line?.title ?? "";
    case "games":
      return r.games;
    case "slips":
      return r.left_by_me;
    case "last_slip":
      return r.last_slip;
    case "trouble":
      return r.line ? (r.line.trouble_spot ? r.line.trouble_spot.slips : null) : null;
    case "tested":
      return r.line ? (r.line.decisions_total === 0 ? null : fraction(r.line.tested, r.line.decisions_total)) : null;
    case "score":
      return r.score;
    case "faithful":
      return r.games === 0 ? null : fraction(r.faithful, r.games);
    case "depth":
      return r.line ? (r.games === 0 ? null : medianDepth(r.depth_reached)) : null;
    case "loads":
      return r.practice.loads;
    case "first_right":
      return r.practice.first_right;
    case "last_played":
      return r.last_played;
    case "study":
      return null; // composite: see sortRows
  }
}

/** Nulls last in either direction; equal values fall through. */
function compare(va: number | string | null, vb: number | string | null, sign: number): number {
  if (va === null && vb !== null) return 1;
  if (vb === null && va !== null) return -1;
  if (va !== null && vb !== null && va !== vb) return (va < vb ? -1 : 1) * sign;
  return 0;
}

const tieBreak = (ta: readonly (string | number)[], tb: readonly (string | number)[]) => {
  for (let i = 0; i < ta.length; i++) {
    if (ta[i] !== tb[i]) return ta[i] < tb[i] ? -1 : 1;
  }
  return 0;
};

/** The keys a composite sort compares in turn; a plain key is itself. Study order is slips, then
 *  the latest slip, then the tie-break (book › chapter › line › id). */
const keysOf = (key: SortKey): SortKey[] => (key === "study" ? ["slips", "last_slip"] : [key]);

export function sortRows(rows: Row[], key: SortKey, dir: Dir): Row[] {
  const sign = dir === "asc" ? 1 : -1;
  const tie = (r: Row) => [r.book, r.chapter ?? "", r.line?.title ?? "", r.id] as const;
  const keys = keysOf(key);
  return [...rows].sort((a, b) => {
    for (const k of keys) {
      const c = compare(sortValue(a, k), sortValue(b, k), sign);
      if (c !== 0) return c;
    }
    return tieBreak(tie(a), tie(b));
  });
}

// --- the decisions view ---------------------------------------------------------------------

export type DecisionSortKey = "study" | "colour" | "move" | "missed" | "opportunities" | "most_played" | "last_slip" | "lines" | "practice" | "last_played";
export const DECISION_SORT_KEYS: DecisionSortKey[] = ["study", "colour", "move", "missed", "opportunities", "most_played", "last_slip", "lines", "practice", "last_played"];

function decisionValue(r: DecisionRow, key: DecisionSortKey): number | string | null {
  switch (key) {
    case "colour":
      return r.colour;
    case "move":
      return r.own_move;
    case "missed":
      return r.slips;
    case "opportunities":
      return r.opportunities;
    case "most_played":
      return r.most_played;
    case "last_slip":
      return r.last_slip;
    case "lines":
      return r.lines.reduce((n, [, ids]) => n + ids.length, 0);
    case "practice":
      return r.practice.of === 0 ? null : r.practice.failed;
    case "last_played":
      return r.last_played;
    case "study":
      return null;
  }
}

/** Study order for decisions: most slips, latest slip, then the lowest move and the colour. */
export function sortDecisions(rows: DecisionRow[], key: DecisionSortKey, dir: Dir): DecisionRow[] {
  const sign = dir === "asc" ? 1 : -1;
  const keys: DecisionSortKey[] = key === "study" ? ["missed", "last_slip"] : [key];
  return [...rows].sort((a, b) => {
    for (const k of keys) {
      const c = compare(decisionValue(a, k), decisionValue(b, k), sign);
      if (c !== 0) return c;
    }
    return tieBreak([a.own_move, a.colour, decisionKey(a)], [b.own_move, b.colour, decisionKey(b)]);
  });
}

/** A decision is shown when any line through it is among the shown lines (the filters apply
 *  through the lines). */
export const filterDecisions = (rows: DecisionRow[], shownLines: Set<number>) => rows.filter((r) => r.lines.some(([, ids]) => ids.some((id) => shownLines.has(id))));
/** A decision's identity for the page: the board, named by the first line through it and that
 *  line's ply there (a line has one board per ply), so expansions survive a re-read of the same
 *  boards and never attach to another. */
export const decisionKey = (r: DecisionRow) => `${r.colour}:${r.lines[0]?.[0] ?? 0}:${r.lines[0]?.[1][0] ?? 0}`;
/** The lines through a decision as (id, ply) pairs, in the server's order. */
export const decisionLines = (r: DecisionRow): { line_id: number; ply: number }[] => r.lines.flatMap(([ply, ids]) => ids.map((line_id) => ({ line_id, ply })));
export const decisionLineCount = (r: DecisionRow) => r.lines.reduce((n, [, ids]) => n + ids.length, 0);

export interface Filters {
  colour: "white" | "black" | null;
  book: number | null;
  chapter: number | null;
  origin: "course" | "manual" | null;
}

export function filterRows(rows: Row[], f: Filters): Row[] {
  return rows.filter((r) => {
    if (f.colour && r.colour !== f.colour) return false;
    if (f.book !== null && r.book_id !== f.book) return false;
    if (f.chapter !== null && r.kind !== "books" && r.chapter_id !== f.chapter) return false;
    if (f.origin && r.kind === "lines" && r.line?.origin !== f.origin) return false;
    return true;
  });
}

