import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import RepertoireReport, { PAGE } from "./RepertoireReport";
import { filterRows, flatten, sortRows } from "./reportRows";
import type { GapCard, GapsResponse, PracticeNumbers, ReportBook, ReportLine, ReportTally } from "../repertoire";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string } }) => <div data-testid="board" data-id={options.id} data-position={options.position} />,
}));
vi.mock("../components/AddLineModal", () => ({
  AddLineModal: ({ card, prefillPlies, maxPlies, onClose, onAdded }: { card: GapCard; prefillPlies: number; maxPlies: number; onClose: () => void; onAdded: (r: { id: number }) => void }) => (
    <div role="dialog" aria-label="Add to repertoire" data-fen={card.fen} data-prefill={prefillPlies} data-max={maxPlies}>
      <button type="button" onClick={() => onAdded({ id: 42 })}>
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
          line({ id: 2, title: "Evans", shared: true, slip: null, left_by_me: 0, faithful: 3, games: 1, score: 1, origin: "manual", last_played: "2026-10-11T10:00:00+00:00" }),
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
        lines: [line({ id: 3, title: "Line C", games: 9, score: 0.4, faithful: 4, book_id: 2, chapter_id: 2, slip: null, left_by_me: 5 })],
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
/** The one read: the lines and both colours' gaps. */
const reportBody = (b: ReportBook[] = books(), white: Partial<GapsResponse> = {}, black: Partial<GapsResponse> = {}, drillMax = 50) => ({ lines: { books: b, drill_max_lines: drillMax }, gaps: { white: gaps("white", white), black: gaps("black", black) } });

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
    expect(cells(1).slice(6, 11)).toEqual(["67%", "67%", "3 of 3", "5", "3 ✓ · 2 ✗"]);
    expect(cells(1)).toHaveLength(12);
    expect(within(rows[1]).getByText(/You left at your move 3 in 1 of 1/)).toBeInTheDocument();
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
    expect(calls.filter((c) => c.path === "/repertoire/report").map((c) => c.query.get("time_class"))).toEqual(["focus"]);
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
    expect(within(rows[1]).getAllByRole("cell").slice(7, 9).map((c) => c.textContent)).toEqual(["5", "3 ✓ · 2 ✗"]);
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
    expect(screen.getByRole("button", { name: "Drill 2 lines" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "" } });
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(3);
    expect(screen.getByLabelText("Select Line C")).not.toBeChecked();
    expect(screen.getByLabelText("Select all shown")).not.toBeChecked();
    // Sort by score ascending puts Main before Evans; the drill follows the table.
    fireEvent.click(screen.getByRole("button", { name: /Score/ }));
    fireEvent.click(screen.getByRole("button", { name: /Score/ }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=score&dir=asc");
    fireEvent.click(screen.getByRole("button", { name: "Drill 2 lines" }));
    expect(screen.getByTestId("location").textContent).toBe("/practice?drill=1,2");
    expect(screen.getByText("practice page")).toBeInTheDocument();
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

  it("a container row and Select all shown reach only the lines the filters leave", async () => {
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody() }),
    });
    renderPage("/repertoire/report?view=chapters&origin=manual");
    await screen.findByTestId("report-table");
    // Origin applies to lines: the Giuoco chapter row stands for Evans alone.
    fireEvent.click(screen.getByLabelText("Select Giuoco"));
    expect(screen.getByRole("button", { name: "Drill 1 line" })).toBeEnabled();
    fireEvent.click(screen.getByRole("tab", { name: "By line" }));
    expect(screen.getAllByTestId("report-row-lines")).toHaveLength(1);
    expect(screen.getByLabelText("Select Evans")).toBeChecked();
    fireEvent.change(screen.getByLabelText("Origin"), { target: { value: "" } });
    expect(screen.getByLabelText("Select Main")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Drill 1 line" })).toBeEnabled();
    // Hiding a checked line takes it out of the drill; showing it again brings it back.
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "black" } });
    expect(screen.getByRole("button", { name: "Drill 0 lines" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "" } });
    expect(screen.getByRole("button", { name: "Drill 1 line" })).toBeEnabled();
  });

  it("a drill over the cap is refused on the page", async () => {
    const many: ReportBook[] = [{ ...books()[0], chapters: [{ ...books()[0].chapters[0], lines: Array.from({ length: 51 }, (_, i) => line({ id: 100 + i, title: `L${i}` })) }] }];
    stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody(many) }),
    });
    renderPage();
    await screen.findByTestId("report-table");
    fireEvent.click(screen.getByLabelText("Select all shown"));
    expect(screen.getByRole("button", { name: "Drill 51 lines" })).toBeDisabled();
    expect(screen.getByText("50 at most")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Select L0"));
    expect(screen.getByRole("button", { name: "Drill 50 lines" })).toBeEnabled();
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
    expect(within(screen.getByLabelText("Sort")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Colour", "Book", "Chapter", "Line", "Games", "Score", "Faithful", "Depth", "Loads", "First try", "Last played"]);
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "line" } });
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=line&dir=asc");
    expect(firstCells(4).map((c) => /^(Evans|Main|Line C)/.exec(c ?? "")?.[1])).toEqual(["Evans", "Line C", "Main"]);
    fireEvent.click(screen.getByRole("button", { name: /Ascending; sort descending/ }));
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=line&dir=desc");
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "first_right" } });
    expect(screen.getByTestId("location").textContent).toBe("/repertoire/report?sort=first_right&dir=desc");
    expect(screen.getByRole("columnheader", { name: /First try/ })).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(screen.getByRole("tab", { name: "By book" }));
    expect(within(screen.getByLabelText("Sort")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Colour", "Book", "Games", "Score", "Faithful", "Loads", "First try", "Last played"]);
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
    expect(screen.getByRole("button", { name: "Drill 205 lines" })).toBeEnabled();
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
    expect(within(rows[1]).getByText(/You left at your move 3 in 1 of 1/)).toBeInTheDocument();
    expect(rows[1].textContent).toContain("3 (2–0–1) games");
    expect(rows[1].textContent).toContain("67% score");
    expect(rows[1].textContent).toContain("3 of 3 depth");
    expect(rows[1].textContent).toContain("5 loads");
    expect(rows[1].textContent).toContain("3 ✓ · 2 ✗ first try");
    expect(rows[1].textContent).toContain("2026-10-10 last");
    expect(within(rows[2]).getByText("custom")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Select Main"));
    expect(screen.getByRole("button", { name: "Drill 1 line" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "score" } });
    expect(screen.getAllByTestId("report-row-lines")[0].textContent).toContain("Evans");
    fireEvent.click(screen.getByRole("tab", { name: "By chapter" }));
    const chapters = screen.getAllByTestId("report-row-chapters");
    expect(chapters.map((c) => c.textContent?.slice(0, 30))).toEqual(["Giuoco(2 lines)White · Italian", "Qa5(1 lines)Black · Scandi9 (2"]); // still by score
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

  it("opens the modal with the card and the server's constants; a save marks the card and re-reads the lines", async () => {
    const calls = stubFetch({
      "/repertoire/report": () => ({ status: 200, body: reportBody([], { gaps: [card()] }) }),
    });
    renderPage();
    const cards = await screen.findAllByTestId("gap-card");
    fireEvent.click(within(cards[0]).getByRole("button", { name: "Add to repertoire" }));
    const dialog = screen.getByRole("dialog", { name: "Add to repertoire" });
    expect(dialog).toHaveAttribute("data-fen", GAP);
    expect(dialog).toHaveAttribute("data-prefill", "10");
    expect(dialog).toHaveAttribute("data-max", "60");
    const before = calls.filter((c) => c.path === "/repertoire/report").length;
    fireEvent.click(screen.getByText("fake-save"));
    expect(calls.filter((c) => c.path === "/repertoire/report")).toHaveLength(before); // the card is marked at once; the read waits for the close
    fireEvent.click(screen.getByText("fake-close"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(within(screen.getAllByTestId("gap-card")[0]).getByText(/Added to your repertoire/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path === "/repertoire/report").length).toBe(before + 1));
  });
});
