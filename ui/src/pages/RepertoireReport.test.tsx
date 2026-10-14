import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RepertoireReport, { PAGE } from "./RepertoireReport";
import { decisionKey, filterDecisions, filterRows, flatten, sortDecisions, sortRows } from "./reportRows";
import type { DecisionRow, GapCard, GapsResponse, PracticeNumbers, ReportBook, ReportLine, ReportResponse, ReportTally } from "../repertoire";
import { readQueue, resetQueueForTests } from "../utils/drillQueue";
import pageSource from "./RepertoireReport?raw";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string } }) => <div data-testid="board" data-id={options.id} data-position={options.position} />,
}));
vi.mock("../components/PositionCard/LineReaderPanel", () => ({
  LineWalkthrough: ({ lineId, initialPly }: { lineId: number; initialPly?: number }) => <div data-testid="walkthrough" data-line={lineId} data-ply={initialPly} />,
}));
vi.mock("../components/AddLineModal", () => ({
  AddLineModal: ({ card, destinations, prefillPlies, maxPlies, onClose, onAdded }: { card: GapCard; destinations: { id: number }[]; prefillPlies: number; maxPlies: number; onClose: () => void; onAdded: (r: { id: number }) => void }) => (
    <div role="dialog" aria-label="Add to repertoire" data-fen={card.fen} data-destinations={destinations.map((d) => d.id).join(",")} data-prefill={prefillPlies} data-max={maxPlies}>
      <button
        type="button"
        onClick={() => {
          // The real modal's sequence after a confirmed save: onAdded, then onClose, same turn.
          onAdded({ id: 42 });
          onClose();
        }}
      >
        fake-save
      </button>
      <button type="button" onClick={onClose}>
        fake-close
      </button>
    </div>
  ),
}));

type Reply = { status: number; body: unknown };
function stubFetch(routes: Record<string, (query: URLSearchParams) => Reply>) {
  const calls: Array<{ path: string; query: URLSearchParams }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const [rawPath, rawQuery] = url.replace(/^.*\/api/, "").split("?");
      const call = { path: rawPath, query: new URLSearchParams(rawQuery ?? "") };
      calls.push(call);
      const h = routes[rawPath];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = h(call.query);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

const practice = (over: Partial<PracticeNumbers> = {}): PracticeNumbers => ({ loads: 0, first_right: 0, first_wrong: 0, ...over });
const tally = (over: Partial<ReportTally> = {}): ReportTally => ({
  games: 3,
  wins: 2,
  draws: 0,
  losses: 1,
  score: 0.667,
  left_by_me: 1,
  left_by_opponent: 2,
  ended: 0,
  ran_out: 0,
  faithful: 2,
  depth_reached: { "2": 1, "3": 2 },
  slip: { own_move: 3, expected: "Bc4", played: "d4", count: 1 },
  last_played: "2026-10-10T10:00:00+00:00",
  last_slip: "2026-10-09T10:00:00+00:00",
  ...over,
});
const line = (over: Partial<ReportLine> = {}): ReportLine => ({
  ...tally(),
  id: 1,
  title: "Main",
  is_alternative: false,
  origin: "course",
  depth_total: 3,
  shared: false,
  book_id: 1,
  chapter_id: 1,
  practice: practice(),
  decisions: [
    [0, 1, "e4", "clean", 3, 0],
    [2, 2, "Nf3", "clean", 3, 0],
    [4, 3, "Bc4", "slipped", 3, 1],
  ],
  decisions_total: 3,
  tested: 3,
  deepest_tested: 3,
  correct_through: 2,
  trouble_spot: { own_move: 3, move: "Bc4", slips: 1, opportunities: 3 },
  recent: [
    ["ok", 3, null],
    ["fail", 2, 3],
  ],
  unknown_loads: 0,
  ...over,
});
const untested = (): Partial<ReportLine> => ({ decisions: [[0, 1, "e4", "untested", 0, 0]], decisions_total: 1, tested: 0, deepest_tested: null, correct_through: null, trouble_spot: null, recent: [], unknown_loads: 0 });
const decision = (over: Partial<DecisionRow> = {}): DecisionRow => ({
  colour: "white",
  own_move: 3,
  move: "Bc4",
  unreadable: false,
  opportunities: 8,
  slips: 6,
  most_played: "Bb5",
  last_slip: "2026-10-09T10:00:00+00:00",
  last_played: "2026-10-10T10:00:00+00:00",
  lines: [[4, [1, 2]]],
  pick: 2,
  practice: { failed: 2, of: 5 },
  ...over,
});
const books = (): ReportBook[] => [
  {
    ...tally({ games: 4 }),
    id: 1,
    title: "Italian",
    color: "white",
    colour: "white",
    practice: practice({ loads: 5, first_right: 3, first_wrong: 2 }),
    chapters: [
      {
        ...tally({ ended: 1 }),
        id: 1,
        title: "Giuoco",
        book_id: 1,
        practice: practice({ loads: 5, first_right: 3, first_wrong: 2 }),
        lines: [
          line({ practice: practice({ loads: 5, first_right: 3, first_wrong: 2 }) }),
          line({ id: 2, title: "Evans", shared: true, slip: null, left_by_me: 0, faithful: 3, games: 1, score: 1, origin: "manual", last_played: "2026-10-11T10:00:00+00:00", last_slip: null, ...untested() }),
        ],
      },
    ],
  },
  {
    ...tally({ games: 9, score: 0.4 }),
    id: 2,
    title: "Scandi",
    color: "black",
    colour: "black",
    practice: practice(),
    chapters: [
      {
        ...tally({ games: 9, score: 0.4 }),
        id: 2,
        title: "Qa5",
        book_id: 2,
        practice: practice(),
        lines: [line({ id: 3, title: "Line C", games: 9, score: 0.4, faithful: 4, book_id: 2, chapter_id: 2, slip: null, left_by_me: 5, last_slip: "2026-10-12T10:00:00+00:00" })],
      },
    ],
  },
];

const GAP = "r1bqkbnr/ppp2ppp/2np4/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4";
const card = (over: Partial<GapCard> = {}): GapCard => ({
  fen: GAP,
  colour: "white",
  arrived_by: "d6",
  games: 5,
  wins: 2,
  draws: 1,
  losses: 2,
  score: 0.5,
  family: "Italian Game",
  context: { book_id: 1, book: "Italian", chapter_id: 1, chapter: "Giuoco", line_id: 1, line_name: "Main", expected: "Bc5", played: "d6", count: 5 },
  continuations: [{ moves: ["c3", "Be7", "d4", "exd4"], games: 3, score: 0.667 }],
  engine: { best_move: "c3", line: ["c3", "Be7", "d4", "exd4", "cxd4"], eval: 40, mate_in: null },
  last_played: "2026-10-10T10:00:00+00:00",
  stem: ["e4", "e5", "Nf3", "Nc6", "Bc4", "d6"],
  catch_all: { book: "Catch-all (white)", chapter: "Italian Game" },
  ...over,
});
const gaps = (colour: "white" | "black", over: Partial<GapsResponse> = {}): GapsResponse => ({ colour, no_book: false, prepared: [], gaps: [], destinations: colour === "white" ? [{ id: 1, title: "Giuoco", book_id: 1, book: "Italian" }] : [], prefill_plies: 10, max_plies: 60, ...over });
/** The one read: the lines, both colours' gaps, the decisions and the scope the server used. */
const reportBody = (b: ReportBook[] = books(), white: Partial<GapsResponse> = {}, black: Partial<GapsResponse> = {}, drillMax = 50, extra: Partial<ReportResponse> = {}): ReportResponse => ({
  lines: { books: b, drill_max_lines: drillMax },
  gaps: { white: gaps("white", white), black: gaps("black", black) },
  decisions: { rows: [decision(), decision({ colour: "black", own_move: 1, move: "d5", opportunities: 9, slips: 5, most_played: "e5", last_slip: "2026-10-12T10:00:00+00:00", lines: [[1, [3]]], pick: 3, practice: { failed: 0, of: 0 } })], contested: [], unknown_loads: 0 },
  scope: { games: 500, limit: 1000, options: [100, 250, 500, 1000] },
  ...extra,
});

function Location() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}
const renderPage = (initial = "/repertoire/report") =>
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/repertoire/report" element={<RepertoireReport />} />
        <Route path="/practice" element={<p>practice page</p>} />
      </Routes>
      <Location />
    </MemoryRouter>,
  );
const firstCells = (col: number) => screen.getAllByTestId(/^report-row-/).map((r) => within(r).getAllByRole("cell")[col].textContent);

beforeEach(() => resetQueueForTests());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("the rows", () => {
  it("flatten, sort and filter: a flat line view across books, games first, nulls last, filters by colour, book, chapter and origin", () => {
    const flat = flatten(books());
    expect(flat.lines.map((r) => r.line?.title)).toEqual(["Main", "Evans", "Line C"]);
    expect(sortRows(flat.lines, "games", "desc").map((r) => r.line?.title)).toEqual(["Line C", "Main", "Evans"]);
    expect(sortRows(flat.lines, "score", "asc").map((r) => r.line?.title)).toEqual(["Line C", "Main", "Evans"]);
    expect(sortRows(flat.lines, "faithful", "desc").map((r) => r.line?.title)).toEqual(["Evans", "Main", "Line C"]);
    expect(sortRows(flat.lines, "loads", "desc").map((r) => r.line?.title)).toEqual(["Main", "Evans", "Line C"]);
    expect(sortRows(flat.lines, "book", "asc").map((r) => r.line?.title)).toEqual(["Evans", "Main", "Line C"]); // ties by chapter, then line name
    const zero = [...flat.lines, { ...flat.lines[0], key: "line:9", id: 9, games: 0, score: null, faithful: 0 }];
    expect(sortRows(zero, "score", "asc").map((r) => r.id)).toEqual([3, 1, 2, 9]);
    expect(sortRows(zero, "faithful", "desc").map((r) => r.id)).toEqual([2, 1, 3, 9]);
    expect(filterRows(flat.lines, { colour: "black", book: null, chapter: null, origin: null }).map((r) => r.id)).toEqual([3]);
    expect(filterRows(flat.lines, { colour: null, book: 1, chapter: null, origin: "manual" }).map((r) => r.id)).toEqual([2]);
    expect(filterRows(flat.chapters, { colour: null, book: null, chapter: 2, origin: null }).map((r) => r.id)).toEqual([2]);
    expect(filterRows(flat.books, { colour: null, book: 2, chapter: 2, origin: "manual" }).map((r) => r.id)).toEqual([2]);
    expect(flat.chapters[0].lineIds).toEqual([1, 2]);
    expect(flat.books[0].lineIds).toEqual([1, 2]);
    // Study order: slips, then the latest slip (nulls last), then the tie-break; the new keys sort.
    expect(sortRows(flat.lines, "study", "desc").map((r) => r.id)).toEqual([3, 1, 2]);
    const tied = flat.lines.map((r) => ({ ...r, left_by_me: 1 }));
    expect(sortRows(tied, "study", "desc").map((r) => r.id)).toEqual([3, 1, 2]); // 10-12, 10-09, null
    expect(sortRows(flat.lines, "trouble", "desc").map((r) => r.id)).toEqual([1, 3, 2]); // Evans has none: last
    expect(sortRows(flat.lines, "tested", "desc").map((r) => r.id)).toEqual([1, 3, 2]);
    expect(sortRows(flat.lines, "last_slip", "asc").map((r) => r.id)).toEqual([1, 3, 2]);
  });

  it("a repeated board keeps the line's own move numbers, so a short trace is not the whole line", async () => {
    const knights = line({ id: 7, title: "Knights", decisions: [[0, 1, "Nf3", "clean", 1, 0], [2, 2, "Ng1", "clean", 1, 0], [4, 3, "Nf3", "clean", 1, 0], [6, 4, "g3", "untested", 0, 0]], decisions_total: 4, tested: 3, deepest_tested: 3, correct_through: 3, trouble_spot: null, recent: [["ok", 1, null]], depth_total: 4 });
    stubFetch({ "/repertoire/report": () => ({ status: 200, body: reportBody([{ ...books()[0], chapters: [{ ...books()[0].chapters[0], lines: [knights] }] }]) }) });
    renderPage();
    await screen.findByTestId("report-table");
    const row = screen.getAllByTestId("report-row-lines")[0];
    expect(within(row).getAllByRole("cell")[15].textContent).toBe("✓ · newest: right up to move 1");
    expect(within(row).getAllByRole("cell")[9].textContent).toBe("3 of 4 · correct through move 3");
  });

  it("decisions: Study order is most missed then latest slip; a decision is shown when any of its lines is", () => {
    const a = decision({ move: "a", slips: 2, last_slip: "2026-10-01T00:00:00+00:00", own_move: 2 });
    const b = decision({ move: "b", slips: 2, last_slip: "2026-10-05T00:00:00+00:00", own_move: 4 });
    const c = decision({ move: "c", slips: 3, last_slip: null, own_move: 1, lines: [[0, [9]]] });
    expect(sortDecisions([a, b, c], "study", "desc").map((d) => d.move)).toEqual(["c", "b", "a"]);
    expect(sortDecisions([a, b, c], "move", "asc").map((d) => d.move)).toEqual(["c", "a", "b"]);
    expect(sortDecisions([a, b, c], "last_slip", "desc").map((d) => d.move)).toEqual(["b", "a", "c"]); // null last
    expect(filterDecisions([a, b, c], new Set([2])).map((d) => d.move)).toEqual(["a", "b"]);
    expect(filterDecisions([a, b, c], new Set([7])).map((d) => d.move)).toEqual([]);
    expect(decisionKey(a)).toBe("white:4:1"); // the board: the first line through it, at its ply
  });
});

describe("the page's words", () => {
  it("calls a departure a slip and never a mistake, shows no accuracy, and says 'through' only of the continuous prefix", () => {
    const strings = [...pageSource.matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`\n]*)`|>([^<>{}\n]+)</g)].map((m) => m[1] ?? m[2] ?? m[3] ?? m[4]);
    const words = strings.filter((t) => /mistake|accuracy|accurate|blunder/i.test(t) && !t.startsWith("../")); // imports aside
    expect(words).toEqual([]);
    // Every "through" in page text is the continuous prefix ("correct through move N"), a walk-through, or
    // "through it/the board" (the lines a board sits on); the only template is the prefix's.
    const through = strings.filter((t) => /\bthrough\b/i.test(t) && !/walk through|through it|through the|through this board|runs? through|run through|correct through move/i.test(t) && t !== "through" && !t.includes("${through}") && !t.includes(".through"));
    expect(through).toEqual([]);
    expect(pageSource).toContain("`correct through move ${l.correct_through}`");
  });
});

describe("the line report page", () => {
  it("shows one flat table ordered by games with the book and chapter on each row, the practice numbers, the custom chip, and sorts on a header", async () => {
    const calls = stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    renderPage();
    const table = await screen.findByTestId("report-table");
    expect(table).toHaveAttribute("data-view", "lines");
    const rows = screen.getAllByTestId("report-row-lines");
    expect(rows).toHaveLength(3);
    const cells = (i: number) => within(rows[i]).getAllByRole("cell").map((c) => c.textContent);
    expect(cells(0).slice(1, 5)).toEqual(["Black", "Scandi", "Qa5", expect.stringContaining("Line C")]);
    expect(cells(1).slice(1, 5)).toEqual(["White", "Italian", "Giuoco", expect.stringContaining("Main")]);
    expect(cells(1)[5]).toBe("32–0–1");
    // Games, Slips and Last slip side by side; the trouble spot and the tested count with their denominators.
    expect(cells(1).slice(6, 10)).toEqual(["1", "2026-10-09", "Move 3 Bc4 · missed 1 of 3", "3 of 3 · correct through move 2"]);
    expect(cells(1).slice(10, 15)).toEqual(["67%", "67%", "3 of 3", "5", "3 ✓ · 2 ✗"]);
    expect(cells(1)[15]).toBe("✓ ✗ · newest: whole line");
    // "whole line" is judged against the line's last decision's own move number, which a line
    // that reaches a board twice (or a chapter rooted at a tabiya) keeps as its own.
    expect(within(rows[1]).getByText(/newest: whole line/)).toBeInTheDocument();
    expect(cells(1)).toHaveLength(17);
    expect(cells(2)[8]).toBe("–"); // no trouble spot
    expect(cells(2)[9]).toBe("0 of 1"); // untested: no "through"
    expect(cells(2)[15]).toBe("no practice yet");
    expect(within(rows[1]).queryByText(/You left at your move/)).toBeNull(); // the boundary-slip display gave way to the trouble spot
    // The line's decisions expand under the row, holes visible.
    fireEvent.click(within(rows[1]).getByRole("button", { name: "decisions" }));
    expect(screen.getByTestId("line-decisions").textContent).toBe("1. e4 ✓ 3 of 32. Nf3 ✓ 3 of 33. Bc4 ✗ missed 1 of 3");
    fireEvent.click(within(rows[1]).getByRole("button", { name: "hide decisions" }));
    expect(screen.queryByTestId("line-decisions")).toBeNull();
    expect(within(rows[2]).getByText("custom")).toBeInTheDocument();
    expect(within(rows[2]).getByText("shared")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Games/ })).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(screen.getByRole("button", { name: /Games/ }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?dir=asc");
    expect(firstCells(4).map((t) => /^(Evans|Main|Line C)/.exec(t ?? "")?.[1])).toEqual(["Evans", "Main", "Line C"]); // 1, 3, 9 games
    fireEvent.click(screen.getByRole("button", { name: /Score/ }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?dir=desc&sort=score");
    expect(firstCells(4).map((t) => /^(Evans|Main|Line C)/.exec(t ?? "")?.[1])).toEqual(["Evans", "Main", "Line C"]);
    expect(screen.queryByRole("link", { name: /Drill/ })).toBeNull();
    expect(calls.filter((c) => c.path === "/repertoire/report").map((c) => [c.query.get("time_class"), c.query.get("games")])).toEqual([["focus", null]]);
    fireEvent.change(screen.getByLabelText("Time class"), { target: { value: "all" } });
    await waitFor(() => expect(calls.filter((c) => c.path === "/repertoire/report").map((c) => c.query.get("time_class"))).toEqual(["focus", "all"]));
    expect(screen.getByTestId("location").textContent).toContain("time_class=all");
  });

  it("the chapter and book views, the filters, select all shown, and Drill in the table's order", async () => {
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    renderPage();
    await screen.findByTestId("report-table");
    fireEvent.click(screen.getByRole("tab", { name: "By chapter" }));
    expect(screen.getByTestId("report-table")).toHaveAttribute("data-view", "chapters");
    let rows = screen.getAllByTestId("report-row-chapters");
    expect(rows.map((r) => within(r).getAllByRole("cell")[3].textContent)).toEqual(["Qa5(1 lines)", "Giuoco(2 lines)"]);
    expect(within(rows[1]).getAllByRole("cell").slice(9, 11).map((c) => c.textContent)).toEqual(["5", "3 ✓ · 2 ✗"]);
    expect(screen.queryByLabelText("Origin")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "By book" }));
    rows = screen.getAllByTestId("report-row-books");
    expect(rows.map((r) => within(r).getAllByRole("cell")[2].textContent)).toEqual(["Scandi(1 ch.)", "Italian(1 ch.)"]);
    expect(screen.queryByLabelText("Chapter")).toBeNull();
    // Back to lines; filter by colour, then select all shown: only the shown lines are checked.
    fireEvent.click(screen.getByRole("tab", { name: "By line" }));
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "white" } });
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(2);
    expect(screen.getByText("2 lines shown")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Select all shown"));
    expect(screen.getByTestId("queued-count").textContent).toBe("2 queued");
    expect(screen.getByRole("button", { name: "Drill queue" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "" } });
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(3);
    expect(screen.getByLabelText("Select Line C")).not.toBeChecked();
    expect(screen.getByLabelText("Select all shown")).not.toBeChecked();
    // Sorting changes nothing about the drill: the queue keeps the order the lines were added in
    // (Select all shown added them in the table's order of that moment: Main, then Evans).
    fireEvent.click(screen.getByRole("button", { name: /Score/ }));
    fireEvent.click(screen.getByRole("button", { name: /Score/ }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=score&dir=asc");
    fireEvent.click(screen.getByLabelText("Select Line C"));
    expect(readQueue()).toEqual([1, 2, 3]);
    fireEvent.click(screen.getByRole("button", { name: "Drill queue" }));
    expect(screen.getByTestId("location").textContent).toBe("/practice?drill=1,2,3");
    expect(screen.getByText("practice page")).toBeInTheDocument();
    expect(readQueue()).toEqual([1, 2, 3]); // cleared by Done on the Practice page, not by the serve
  });

  it("the book filter narrows the chapter filter; the origin filter; the state round-trips through the URL", async () => {
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    renderPage("/repertoire/report?view=chapters&sort=book&dir=asc&book=1&colour=white");
    await screen.findByTestId("report-table");
    expect(screen.getByTestId("report-table")).toHaveAttribute("data-view", "chapters");
    expect(screen.getAllByTestId("report-row-chapters")).toHaveLength(1);
    expect(screen.getByRole("columnheader", { name: /Book/ })).toHaveAttribute("aria-sort", "ascending");
    expect(within(screen.getByLabelText("Chapter")).getAllByRole("option").map((o) => o.textContent)).toEqual(["All chapters", "Giuoco"]);
    fireEvent.click(screen.getByRole("tab", { name: "By line" }));
    expect(screen.getByRole("columnheader", { name: /Book/ })).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByLabelText("Sort")).toHaveValue("book");
    fireEvent.change(screen.getByLabelText("Origin"), { target: { value: "manual" } });
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(1);
    expect(screen.getByTestId("location").textContent).toContain("origin=manual");
    fireEvent.change(screen.getByLabelText("Book"), { target: { value: "" } });
    expect(screen.getByTestId("location").textContent).not.toContain("book=");
    expect(within(screen.getByLabelText("Chapter")).getAllByRole("option").map((o) => o.textContent)).toEqual(["All chapters", "Italian › Giuoco", "Scandi › Qa5"]);
  });

  it("a container row and Select all shown reach only the lines the filters leave; the queue keeps what a filter later hides", async () => {
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    renderPage("/repertoire/report?view=chapters&origin=manual");
    await screen.findByTestId("report-table");
    // Origin applies to lines: the Giuoco chapter row stands for Evans alone.
    fireEvent.click(screen.getByLabelText("Select Giuoco"));
    expect(screen.getByTestId("queued-count").textContent).toBe("1 queued");
    fireEvent.click(screen.getByRole("tab", { name: "By line" }));
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(1);
    expect(screen.getByLabelText("Select Evans")).toBeChecked();
    fireEvent.change(screen.getByLabelText("Origin"), { target: { value: "" } });
    expect(screen.getByLabelText("Select Main")).not.toBeChecked();
    // Hiding a queued line no longer takes it out of the drill (the player's ruling over the earlier
    // derived selection): the queue is what the player assembled, whatever the table shows now.
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "black" } });
    expect(screen.getByTestId("queued-count").textContent).toBe("1 queued");
    expect(screen.getByRole("button", { name: "Drill queue" })).toBeEnabled();
    fireEvent.click(screen.getByLabelText("Select Line C"));
    expect(readQueue()).toEqual([2, 3]);
    fireEvent.click(screen.getByRole("tab", { name: "By chapter" }));
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Time class"), { target: { value: "blitz" } });
    await screen.findByTestId("report-table");
    expect(readQueue()).toEqual([2, 3]);
    fireEvent.click(screen.getByText("Clear"));
    expect(readQueue()).toEqual([]);
    expect(screen.getByRole("button", { name: "Drill queue" })).toBeDisabled();
  });

  it("the queue is this tab's: it is in sessionStorage, and page state alone when storage throws", async () => {
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    const { unmount } = renderPage();
    await screen.findByTestId("report-table");
    fireEvent.click(screen.getByLabelText("Select Main"));
    expect(JSON.parse(sessionStorage.getItem("report-drill-queue") ?? "[]")).toEqual([1]);
    unmount();
    renderPage();
    await screen.findByTestId("report-table");
    expect(screen.getByLabelText("Select Main")).toBeChecked(); // a round trip within the tab keeps it
    const broken = { getItem: () => { throw new Error("no storage"); }, setItem: () => { throw new Error("no storage"); } };
    vi.stubGlobal("sessionStorage", broken);
    fireEvent.click(screen.getByLabelText("Select Evans"));
    expect(readQueue()).toEqual([1, 2]); // the module keeps it for the visit
    expect(screen.getByTestId("queued-count").textContent).toBe("2 queued");
  });

  it("the scope: the server's effective games are shown, the options come from it, a stale URL is rewritten, and every read carries it", async () => {
    const calls = stubFetch({
      "/repertoire/report": (q) => ({ status: 200, body: reportBody(books(), {}, {}, 50, { scope: { games: Math.min(Number(q.get("games") ?? 500), 1000), limit: 1000, options: [100, 250, 500, 1000] } }) }),
    });
    renderPage("/repertoire/report?games=5000");
    await screen.findByTestId("report-table");
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?games=1000"));
    expect(screen.getByLabelText("Games")).toHaveValue("1000");
    expect(within(screen.getByLabelText("Games")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Last 100", "Last 250", "Last 500", "All (1000)"]);
    expect(screen.getByText(/Your last 1000 analysable games \(the whole analysis window\); practice is not scoped\./)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Games"), { target: { value: "250" } });
    await waitFor(() => expect(screen.getByLabelText("Games")).toHaveValue("250"));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?games=250");
    // A number the server honoured but does not offer is listed as its own option.
    renderPage("/repertoire/report?games=60");
    await waitFor(() => expect(screen.getAllByLabelText("Games")[1]).toHaveValue("60"));
    expect(within(screen.getAllByLabelText("Games")[1]).getAllByRole("option").map((o) => o.textContent)).toEqual(["Last 60", "Last 100", "Last 250", "Last 500", "All (1000)"]);
    // The rewritten URL re-read once at the effective scope (a stale URL only), then the choice, then the second page.
    expect(calls.filter((c) => c.path === "/repertoire/report").map((c) => c.query.get("games"))).toEqual(["5000", "1000", "250", "60"]);
  });

  it("a drill over the cap is refused on the page", async () => {
    const many: ReportBook[] = [{ ...books()[0], chapters: [{ ...books()[0].chapters[0], lines: Array.from({ length: 51 }, (_, i) => line({ id: 100 + i, title: `L${i}` })) }] }];
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody(many) }),
    });
    renderPage();
    await screen.findByTestId("report-table");
    fireEvent.click(screen.getByLabelText("Select all shown"));
    expect(screen.getByTestId("queued-count").textContent).toBe("51 queued");
    expect(screen.getByRole("button", { name: "Drill queue" })).toBeDisabled();
    expect(screen.getByText("50 at most")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Select L0"));
    expect(screen.getByTestId("queued-count").textContent).toBe("50 queued");
    expect(screen.getByRole("button", { name: "Drill queue" })).toBeEnabled();
  });

  it("drops a late answer for the previous time class", async () => {
    let release: (() => void) | null = null;
    const slow = new Promise<void>((r) => {
      release = r;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const [, q] = url.replace(/^.*\/api/, "").split("?");
        const tc = new URLSearchParams(q).get("time_class");
        if (tc === "focus") await slow;
        const body = reportBody(tc === "focus" ? books() : []);
        return { ok: true, status: 200, statusText: "200", json: async () => body };
      }),
    );
    renderPage();
    fireEvent.change(screen.getByLabelText("Time class"), { target: { value: "all" } });
    await screen.findByText("No active line in your repertoire.");
    release!();
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId("report-table")).toBeNull();
  });


  it("the Sort control sorts like a header: text ascending, numbers descending, the arrow flips", async () => {
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    renderPage();
    await screen.findByTestId("report-table");
    expect(within(screen.getByLabelText("Sort")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Study order", "Colour", "Book", "Chapter", "Line", "Games", "Slips", "Last slip", "Trouble spot", "Tested", "Score", "Faithful", "Depth", "Loads", "First try", "Last played"]);
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "line" } });
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=line&dir=asc");
    expect(firstCells(4).map((c) => /^(Evans|Main|Line C)/.exec(c ?? "")?.[1])).toEqual(["Evans", "Line C", "Main"]);
    fireEvent.click(screen.getByRole("button", { name: /Ascending; sort descending/ }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=line&dir=desc");
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "first_right" } });
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=first_right&dir=desc");
    expect(screen.getByRole("columnheader", { name: /First try/ })).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(screen.getByRole("tab", { name: "By book" }));
    expect(within(screen.getByLabelText("Sort")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Study order", "Colour", "Book", "Games", "Slips", "Last slip", "Score", "Faithful", "Loads", "First try", "Last played"]);
  });

  it("Study order: slips, then the latest slip, then the tie-break; it sticks in the URL and is offered in every view", async () => {
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    renderPage();
    await screen.findByTestId("report-table");
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "study" } });
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=study&dir=desc");
    expect(firstCells(4).map((c) => /^(Evans|Main|Line C)/.exec(c ?? "")?.[1])).toEqual(["Line C", "Main", "Evans"]); // 5, 1, 0 slips
    expect(screen.getByRole("columnheader", { name: /Slips/ })).toHaveAttribute("aria-sort", "none"); // a preset, not a column
    fireEvent.click(screen.getByRole("tab", { name: "By chapter" }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=study&dir=desc&view=chapters");
    expect(screen.getByLabelText("Sort")).toHaveValue("study");
    // Both chapters have one slip on the same day: the tie-break (book) decides.
    expect(screen.getAllByTestId("report-row-chapters").map((r) => within(r).getAllByRole("cell")[3].textContent)).toEqual(["Giuoco(2 lines)", "Qa5(1 lines)"]);
  });

  it("the Decisions view: one row per board in Study order, the lines beneath, the pick queued with +, a walk-through, the contested notice, and the filters through the lines", async () => {
    stubFetch({
      "/repertoire/report": () => ({
        status: 200,
        body: reportBody(books(), {}, {}, 50, {
          decisions: {
            rows: [decision(), decision({ colour: "black", own_move: 1, move: "d5", opportunities: 9, slips: 5, most_played: "e5", last_slip: "2026-10-12T10:00:00+00:00", lines: [[1, [3]]], pick: 3, practice: { failed: 0, of: 0 } })],
            contested: [{ colour: "white", fen: "x w KQkq - 0 3", own_move: 3, alternatives: [{ move: "c4", line_ids: [1] }, { move: "g3", line_ids: [2] }], line_ids: [1, 2] }],
            unknown_loads: 1,
          },
        }),
      }),
    });
    renderPage();
    await screen.findByTestId("report-table");
    fireEvent.click(screen.getByRole("tab", { name: "By decision" }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?view=decisions");
    expect(screen.getByTestId("report-table")).toHaveAttribute("data-view", "decisions");
    expect(screen.getByLabelText("Sort")).toHaveValue("study");
    expect(screen.getByText("2 decisions shown")).toBeInTheDocument();
    expect(screen.getByText(/1 practice load could not be read/)).toBeInTheDocument();
    const notice = screen.getByTestId("contested-notice");
    expect(notice.textContent).toContain("2 continuations at 1 board reached by transposition — that board is not counted");
    fireEvent.click(within(notice).getByRole("button", { name: "Show" }));
    expect(notice.textContent).toContain("White, move 3: c4 in Main; g3 in Evans");
    const rows = screen.getAllByTestId("report-row-decisions");
    const cells = (i: number) => within(rows[i]).getAllByRole("cell").map((c) => c.textContent);
    expect(cells(0).slice(1)).toEqual(["White", "3. Bc4", "6 of 8 75%", "Bb5", "2026-10-09", "2 lines", "2 ✗ of 5", "2026-10-10"]);
    expect(cells(1).slice(1)).toEqual(["Black", "1. d5", "5 of 9 56%", "e5", "2026-10-12", "1 line", "not yet reached", "2026-10-10"]);
    // The + queues the decision's pick (Evans, id 2), named on the control.
    const plus = within(rows[0]).getByRole("button", { name: "Add to the drill queue: Evans" });
    expect(plus).toHaveAttribute("title", "Drills Italian › Giuoco › Evans");
    fireEvent.click(plus);
    expect(readQueue()).toEqual([2]);
    expect(screen.getByTestId("queued-count").textContent).toBe("1 queued");
    // The expansion: both lines, the pick marked, a checkbox each, a walk-through at the decision's ply + 1.
    fireEvent.click(within(rows[0]).getByRole("button", { name: "2 lines" }));
    const lines = screen.getByTestId("decision-lines");
    expect(lines.textContent).toContain("One decision · 2 lines. failed here in 2 of the last 5 first tries that reached it.");
    expect(within(lines).getAllByRole("checkbox").map((c) => (c as HTMLInputElement).checked)).toEqual([false, true]);
    expect(within(lines).getByText("drill pick")).toBeInTheDocument();
    fireEvent.click(within(lines).getByLabelText("Queue Main"));
    expect(readQueue()).toEqual([2, 1]);
    fireEvent.click(within(lines).getAllByRole("button", { name: "walk through" })[0]);
    expect(screen.getByTestId("walkthrough")).toHaveAttribute("data-line", "1");
    expect(screen.getByTestId("walkthrough")).toHaveAttribute("data-ply", "5");
    // Sorting by move ascending puts Black's move 1 first; the filters apply through the lines.
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "move" } });
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?view=decisions&sort=move&dir=asc");
    expect(screen.getAllByTestId("report-row-decisions")[0].textContent).toContain("1. d5");
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "white" } });
    expect(screen.getAllByTestId("report-row-decisions")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Origin"), { target: { value: "manual" } });
    expect(screen.getAllByTestId("report-row-decisions")).toHaveLength(1); // Evans is custom and runs through the board
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "black" } });
    expect(screen.getByText("No decision of yours was reached in these games.")).toBeInTheDocument();
    // Back to the lines: the decisions' sort key is not one of theirs, so it is dropped.
    fireEvent.click(screen.getByRole("tab", { name: "By line" }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?colour=black&origin=manual");
  });

  it("lists the first page and reveals more; a control change starts the listing over; the drill and Select all shown see every shown line", async () => {
    const many: ReportBook[] = [{ ...books()[0], chapters: [{ ...books()[0].chapters[0], lines: Array.from({ length: 205 }, (_, i) => line({ id: 100 + i, title: `L${i}`, games: 300 - i })) }] }];
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody(many, {}, {}, 500) }),
    });
    renderPage();
    await screen.findByTestId("report-table");
    expect(PAGE).toBe(200);
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(PAGE);
    expect(screen.getByText("205 lines shown, first 200 listed")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Select all shown"));
    expect(screen.getByTestId("queued-count").textContent).toBe("205 queued");
    fireEvent.click(screen.getByRole("button", { name: "Show 5 more (5 left)" }));
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(205);
    expect(screen.queryByRole("button", { name: /Show .* more/ })).toBeNull();
    expect(screen.getByLabelText("Select L204")).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: /Games/ }));
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(200);
    expect(screen.getByLabelText("Select L204")).toBeChecked(); // ascending now: the last line is listed first
    expect(firstCells(4)[0]).toContain("L204");
    // A view change starts over too; the Gaps tabs leave the listing alone.
    fireEvent.click(screen.getByRole("button", { name: "Show 5 more (5 left)" }));
    fireEvent.click(screen.getByRole("tab", { name: "Black" }));
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(205);
    fireEvent.click(screen.getByRole("tab", { name: "By chapter" }));
    fireEvent.click(screen.getByRole("tab", { name: "By line" }));
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(200);
  }, 20_000); // 200 rows rendered several times in jsdom: slow, not a race

  it("a sort the view does not offer falls back to games, and a view change drops it from the URL", async () => {
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    renderPage("/repertoire/report?view=books&sort=depth&dir=asc");
    await screen.findByTestId("report-table");
    expect(screen.getByLabelText("Sort")).toHaveValue("games");
    expect(screen.getByRole("columnheader", { name: /Games/ })).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(screen.getByRole("tab", { name: "By line" }));
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "depth" } });
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=depth&dir=desc");
    fireEvent.click(screen.getByRole("tab", { name: "By book" }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?view=books");
    expect(screen.getByLabelText("Sort")).toHaveValue("games");
  });

  it("on a phone the rows are cards with the same selection, the numbers in one strip and no table", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    renderPage();
    const cards = await screen.findByTestId("report-cards");
    expect(screen.queryByTestId("report-table")).toBeNull();
    expect(cards).toHaveAttribute("data-view", "lines");
    const rows = screen.getAllByTestId("report-row-lines");
    expect(rows).toHaveLength(3);
    expect(rows[1].textContent).toContain("White · Italian › Giuoco");
    expect(rows[1].textContent).toContain("1 slips");
    expect(rows[1].textContent).toContain("2026-10-09 last slip");
    expect(rows[1].textContent).toContain("Move 3 Bc4 · missed 1 of 3 trouble spot");
    expect(rows[1].textContent).toContain("3 of 3 · correct through move 2 tested");
    expect(rows[1].textContent).toContain("✓ ✗ · newest: whole line practice");
    expect(rows[1].textContent).toContain("3 (2–0–1) games");
    expect(rows[1].textContent).toContain("67% score");
    expect(rows[1].textContent).toContain("3 of 3 depth");
    expect(rows[1].textContent).toContain("5 loads");
    expect(rows[1].textContent).toContain("3 ✓ · 2 ✗ first try");
    expect(rows[1].textContent).toContain("2026-10-10 last");
    expect(within(rows[2]).getByText("custom")).toBeInTheDocument();
    // The + queues the line; a second tap takes it out; the decisions expand on the card.
    const plus = within(rows[1]).getByRole("button", { name: "Add to the drill queue: Main" });
    fireEvent.click(plus);
    expect(readQueue()).toEqual([1]);
    expect(within(rows[1]).getByRole("button", { name: "Remove from the drill queue: Main" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("queued-count").textContent).toBe("1 queued");
    fireEvent.click(within(rows[1]).getByRole("button", { name: "decisions" }));
    expect(within(rows[1]).getByTestId("line-decisions")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "score" } });
    expect(screen.getAllByTestId("report-row-lines")[0].textContent).toContain("Evans");
    fireEvent.click(screen.getByRole("tab", { name: "By chapter" }));
    const chapters = screen.getAllByTestId("report-row-chapters");
    expect(chapters.map((c) => c.textContent?.slice(1, 31))).toEqual(["Giuoco(2 lines)White · Italian", "Qa5(1 lines)Black · Scandi9 (2"]); // still by score; the + comes first
    expect(chapters[0].textContent).not.toContain("depth");
    fireEvent.change(screen.getByLabelText("Book"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("tab", { name: "By line" }));
    expect(screen.getAllByTestId("report-row-lines")[0].textContent).toContain("White · Giuoco");
    expect(screen.getAllByTestId("report-row-lines")[0].textContent).not.toContain("Italian");
  });

  it("shows the gaps for the colour with more of them, Add to repertoire only with a stem, Prepared but not played, and the no-book notice", async () => {
    stubFetch({
      "/repertoire/report": () => ({
        status: 200,
        body: reportBody(
          [],
          { no_book: true, destinations: [] },
          {
            prepared: [{ fen: "x", book_move: "d5", played: "c5", games: 9, wins: 4, draws: 1, losses: 4, line_id: 5, line_name: "Main", book: "Scandi", chapter: "Main", puzzles: 1, last_played: null }],
            gaps: [card({ colour: "black", fen: "a", family: "Queen's Pawn", context: null, arrived_by: "d4", engine: null, stem: ["d4"] }), card({ colour: "black", fen: "b", stem: null })],
          },
        ),
      }),
    });
    renderPage();
    const black = await screen.findByRole("tab", { name: "Black" });
    expect(black).toHaveAttribute("aria-selected", "true");
    const cards = screen.getAllByTestId("gap-card");
    expect(cards).toHaveLength(2);
    expect(screen.getByTestId("prepared-row").textContent).toContain("Your book plays d5; you played c5 in 9 games (4–1–4)");
    expect(within(cards[0]).getByText("No book reaches this board.")).toBeInTheDocument();
    expect(within(cards[0]).getByRole("button", { name: "Add to repertoire" })).toBeInTheDocument();
    expect(within(cards[1]).queryByRole("button")).toBeNull();
    expect(within(cards[1]).getByText(/No replayable prefix/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Drill/ })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "White" }));
    expect(screen.getByText("No active White line: every White game opens outside your repertoire.")).toBeInTheDocument();
    expect(screen.getByTestId("location").textContent).toContain("gaps=white");
  });

  it("opens the modal with the card and the server's constants; a save marks the card and re-reads the report in the same turn; a cancel reads nothing", async () => {
    let saved = false;
    const calls = stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody(saved ? [{ ...books()[0], chapters: [{ ...books()[0].chapters[0], lines: [line({ id: 42, title: "Added line", games: 0, origin: "manual" })] }] }] : [], { gaps: [card()] }) }),
    });
    renderPage();
    const cards = await screen.findAllByTestId("gap-card");
    fireEvent.click(within(cards[0]).getByRole("button", { name: "Add to repertoire" }));
    const dialog = screen.getByRole("dialog", { name: "Add to repertoire" });
    expect(dialog).toHaveAttribute("data-fen", GAP);
    expect(dialog).toHaveAttribute("data-destinations", "1"); // White's destinations, from the one read
    expect(dialog).toHaveAttribute("data-prefill", "10");
    expect(dialog).toHaveAttribute("data-max", "60");
    const before = calls.filter((c) => c.path === "/repertoire/report").length;
    // Cancelling re-reads nothing.
    fireEvent.click(screen.getByText("fake-close"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.filter((c) => c.path === "/repertoire/report")).toHaveLength(before);
    // A confirmed save: the card is marked, the modal is gone, and the report is read again —
    // the new line is in the table, ready to drill.
    fireEvent.click(within(screen.getAllByTestId("gap-card")[0]).getByRole("button", { name: "Add to repertoire" }));
    saved = true;
    fireEvent.click(screen.getByText("fake-save"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(within(screen.getAllByTestId("gap-card")[0]).getByText(/Added to your repertoire/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path === "/repertoire/report").length).toBe(before + 1));
    await screen.findByLabelText("Select Added line");
    fireEvent.click(screen.getByLabelText("Select Added line"));
    expect(readQueue()).toEqual([42]);
    expect(screen.getByRole("button", { name: "Drill queue" })).toBeEnabled();
  });
});
