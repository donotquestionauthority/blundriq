/** The line report's row model: one flat shape for the line, chapter and book views, with the
 *  sort and the filters the page applies to it. Pure functions, kept out of the page so the page
 *  file only exports components. */
import type { PracticeNumbers, ReportBook, ReportLine, ReportTally } from "../repertoire";

export type View = "lines" | "chapters" | "books";
export type SortKey = "colour" | "book" | "chapter" | "line" | "games" | "score" | "faithful" | "depth" | "loads" | "first_right" | "first_wrong" | "last_played";
export type Dir = "asc" | "desc";
export const VIEWS: View[] = ["lines", "chapters", "books"];
export const SORT_KEYS: SortKey[] = ["colour", "book", "chapter", "line", "games", "score", "faithful", "depth", "loads", "first_right", "first_wrong", "last_played"];

/** One row of any view: a line, a chapter or a book, with the ids of the lines it stands for. */
export interface Row extends ReportTally {
  key: string;
  kind: View;
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
        lines.push({ ...l, key: `line:${l.id}`, kind: "lines", id: l.id, colour: l.colour, book: l.book, book_id: l.book_id, chapter: l.chapter, chapter_id: l.chapter_id, line: l, lineIds: [l.id], count: 1, practice: l.practice });
      }
      chapters.push({ ...ch, key: `chapter:${ch.id}`, kind: "chapters", id: ch.id, colour: ch.colour, book: ch.book, book_id: ch.book_id, chapter: ch.title, chapter_id: ch.id, line: null, lineIds: ids, count: ch.lines.length, practice: ch.practice });
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
    case "first_wrong":
      return r.practice.first_wrong;
    case "last_played":
      return r.last_played;
  }
}

export function sortRows(rows: Row[], key: SortKey, dir: Dir): Row[] {
  const sign = dir === "asc" ? 1 : -1;
  const tie = (r: Row) => [r.book, r.chapter ?? "", r.line?.title ?? "", r.id] as const;
  return [...rows].sort((a, b) => {
    const va = sortValue(a, key);
    const vb = sortValue(b, key);
    if (va === null && vb !== null) return 1;
    if (vb === null && va !== null) return -1;
    if (va !== null && vb !== null && va !== vb) return (va < vb ? -1 : 1) * sign;
    const ta = tie(a);
    const tb = tie(b);
    for (let i = 0; i < ta.length; i++) {
      if (ta[i] !== tb[i]) return ta[i] < tb[i] ? -1 : 1;
    }
    return 0;
  });
}

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

