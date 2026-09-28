import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Review from "./Review";
import { ApiError } from "../api";
import type { OpeningFamily, OpeningSubgroup, ReviewGameRow, ReviewPage, ReviewPool } from "../review";

// The network calls are mocked; the labels and helpers are the real ones.
const getReviewPage = vi.fn();
const getPoolEvents = vi.fn();
const touchPoolShown = vi.fn();
vi.mock("../review", async () => {
  const actual = await vi.importActual<typeof import("../review")>("../review");
  return {
    ...actual,
    getReviewPage: (...args: unknown[]) => getReviewPage(...args),
    getPoolEvents: (...args: unknown[]) => getPoolEvents(...args),
    touchPoolShown: (...args: unknown[]) => touchPoolShown(...args),
  };
});

const FAM = "v1:fam:aaaaaaaaaaaaaaaa";
const SUB = `${FAM}:var:bbbbbbbbbbbbbbbb`;

const row = (over: Partial<ReviewGameRow> = {}): ReviewGameRow => ({
  chess_game_id: 101,
  anchor_ply: 14,
  cost: 42.5,
  phase: "opening",
  piece_label: "knight",
  book_relation: null,
  evidence: {},
  base_route: "lapse_defense",
  displayed_route: "opening",
  pool_key: "canon:Scandinavian::Main Line",
  board_key: 555,
  played_at: "2026-07-01T12:00:00Z",
  opponent_username: "magnus_wannabe",
  result: "loss",
  url: "https://example.test/101",
  time_class: "rapid",
  canonical_family: "Scandinavian",
  canonical_variation: "Main Line",
  reviewed: false,
  ...over,
});

const subgroup = (over: Partial<OpeningSubgroup> = {}): OpeningSubgroup => ({
  subgroup_id: SUB,
  label: "Main Line",
  kind: "variation",
  severity: 60.1,
  raw_severity: 80.2,
  event_count: 7,
  confidence: "low",
  book_relation_verdict: "deviation_before",
  total_games: 6,
  to_review_games: 4,
  representative_game: row({ book_relation: "deviation_before", best_move: "Nf3", best_line: "Nf3 e6 Bd3 Nc6", expected_move: "c4", deviated_at_ply: 10 }),
  ...over,
});

const family = (over: Partial<OpeningFamily> = {}): OpeningFamily => ({
  family_id: FAM,
  family_key: "Scandinavian",
  label: "Scandinavian",
  severity: 63.2,
  raw_severity: 84.3,
  event_count: 9,
  confidence: "high",
  total_games: 8,
  to_review_games: 5,
  subgroups: [subgroup()],
  representative_game: row(),
  ...over,
});

const pool = (over: Partial<ReviewPool> = {}): ReviewPool => ({
  pool_id: "v1:route:lapse_defense:knight",
  pool_key: null,
  label: "knight",
  severity: 30,
  raw_severity: 40,
  event_count: 4,
  confidence: "low",
  book_relation_verdict: null,
  total_games: 3,
  to_review_games: 2,
  // A verdict on the underlying event that must not surface on a route pool.
  representative_game: row({ displayed_route: "lapse_defense", pool_key: null, canonical_family: null, canonical_variation: null, book_relation: "opponent_left" }),
  ...over,
});

const page = (over: Partial<ReviewPage> = {}): ReviewPage => ({
  categories: {
    opening: { families: [family()], total_games: 8, to_review_games: 5 },
    oversights: {
      defense: { pools: [pool(), pool({ pool_id: "v1:route:lapse_defense:forced-loss", label: "forced-loss", total_games: 2, to_review_games: 1 })], total_games: 4, to_review_games: 3 },
      offense: { pools: [pool({ pool_id: "v1:route:lapse_offense:fork", label: "fork", total_games: 3, to_review_games: 2 })], total_games: 3, to_review_games: 2 },
      total_games: 6,
      to_review_games: 4,
    },
    endgame: { pools: [pool({ pool_id: "v1:route:endgame_technique", label: "Endgame technique", total_games: 2, to_review_games: 1 })], total_games: 2, to_review_games: 1 },
    faded: { pools: [pool({ pool_id: "v1:route:faded", label: "Faded advantage", total_games: 2, to_review_games: 0 })], total_games: 2, to_review_games: 0 },
    lost_wins: {
      games: [row({ chess_game_id: 301, opponent_username: "convert_me", base_route: "faded", url: "https://example.test/301" }), row({ chess_game_id: 302, opponent_username: "already_seen", base_route: "faded", reviewed: true })],
      total_games: 2,
      to_review_games: 1,
    },
  },
  page: { total_games: 12, to_review_games: 7 },
  filter: {
    time_class: "focus",
    opening: "__all__",
    openings: [
      { key: "__all__", label: "All openings", to_review_games: 7 },
      { key: "Scandinavian", label: "Scandinavian", to_review_games: 5 },
    ],
    group_by: "variation",
  },
  ...over,
});

const emptyPage = (): ReviewPage =>
  page({
    categories: {
      opening: { families: [], total_games: 0, to_review_games: 0 },
      oversights: { defense: { pools: [], total_games: 0, to_review_games: 0 }, offense: { pools: [], total_games: 0, to_review_games: 0 }, total_games: 0, to_review_games: 0 },
      endgame: { pools: [], total_games: 0, to_review_games: 0 },
      faded: { pools: [], total_games: 0, to_review_games: 0 },
      lost_wins: { games: [], total_games: 0, to_review_games: 0 },
    },
    page: { total_games: 0, to_review_games: 0 },
  });

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={["/review"]}>
      <Review />
    </MemoryRouter>,
  );

const lastPageCall = () => getReviewPage.mock.calls.at(-1);
const combo = (name: string) => screen.getByRole("combobox", { name }) as HTMLSelectElement;

beforeEach(() => {
  getReviewPage.mockReset().mockResolvedValue(page());
  getPoolEvents.mockReset().mockResolvedValue({
    events: [row({ best_move: "Nf3", best_line: "Nf3 e6 Bd3 Nc6", expected_move: "c4", deviated_at_ply: 10, extra_in_game: 2 }), row({ chess_game_id: 102, anchor_ply: 22, opponent_username: "hikaru_fan", best_move: "Qh5", extra_in_game: 0, url: "https://example.test/102" })],
    total: 2,
    page: 1,
    page_size: 50,
    total_pages: 1,
  });
  touchPoolShown.mockReset().mockResolvedValue(undefined);
});
afterEach(cleanup);

describe("Review worklist", () => {
  it("opens with Opening problems expanded (hosting Focus opening and Group openings), the other categories collapsed, and the severity legend", async () => {
    renderPage();
    await screen.findByText("Opening problems");
    for (const t of ["Tactical oversights", "Endgame technique", "Lost wins"]) expect(screen.getByText(t)).toBeInTheDocument();
    expect(screen.getByText("Scandinavian")).toBeInTheDocument(); // the family row is visible: the section is open
    expect(combo("Focus opening")).toBeInTheDocument();
    expect(combo("Group openings")).toBeInTheDocument();
    expect(screen.getAllByRole("combobox")).toHaveLength(3); // time class, focus, group by
    expect(screen.queryByText("Forced material loss")).toBeNull(); // Tactical oversights is collapsed
    expect(screen.getByText(/higher means fix it first/)).toBeInTheDocument();
    expect(getReviewPage).toHaveBeenCalledWith("focus", "__all__", "variation");
  });

  it("drills an opening: the family stamps, the subgroup stamps and loads game rows with '+N more', the best and book moves", async () => {
    renderPage();
    const fam = await screen.findByText("Scandinavian");
    expect(getPoolEvents).not.toHaveBeenCalled();
    expect(fam.closest("button")!.textContent).toMatch(/High/);

    fireEvent.click(fam);
    await waitFor(() => expect(touchPoolShown).toHaveBeenCalledWith(FAM, "focus", "__all__"));
    const sub = await screen.findByText("Main Line");
    expect(sub.closest("button")!.textContent).toMatch(/Low/);

    fireEvent.click(sub);
    await waitFor(() => expect(getPoolEvents).toHaveBeenCalledWith(SUB, "focus", "__all__", "to_review", 1));
    expect(touchPoolShown).toHaveBeenCalledWith(SUB, "focus", "__all__");
    await screen.findByText("magnus_wannabe");
    expect(screen.getByText(/\+2 more in this line/)).toBeInTheDocument();
    expect(screen.getByText("Book move: c4")).toBeInTheDocument();
    expect(screen.getByText("Best move: Nf3")).toBeInTheDocument();
    expect(screen.getAllByText("You left book first — drill the line").length).toBeGreaterThan(0);
    // Opening rows carry the Best column.
    expect(screen.getByRole("columnheader", { name: "Best" })).toBeInTheDocument();
  });

  it("a game row opens the game on its platform in a new tab (8c replaces it with the in-app review)", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Lost wins"));
    const link = (await screen.findAllByRole("link", { name: "Open game ↗" }))[0];
    expect(link).toHaveAttribute("href", "https://example.test/301");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("relabels forced-loss and never shows a book-relation label on a route pool", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Tactical oversights"));
    expect(await screen.findByText("Forced material loss")).toBeInTheDocument();
    expect(screen.queryByText("Forced-loss")).toBeNull();
    expect(screen.queryByText("Opponent left your prep — extend prep here")).toBeNull();
    expect(screen.getByText("Gave away material")).toBeInTheDocument();
    expect(screen.getByText("Missed material & mates")).toBeInTheDocument();
    expect(screen.getByText("Fork")).toBeInTheDocument();
  });

  it("scope: To review hides reviewed lost wins and a category with nothing to review, adds no Status column; All reveals them without a refetch", async () => {
    renderPage();
    await screen.findByText("Lost wins");
    expect(screen.queryByRole("heading", { name: "Faded advantage" })).toBeNull();

    fireEvent.click(screen.getByText("Lost wins"));
    expect(await screen.findByText("convert_me")).toBeInTheDocument();
    expect(screen.queryByText("already_seen")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Status" })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    expect(await screen.findByText("already_seen")).toBeInTheDocument();
    expect(screen.getByText("✓ reviewed")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Status" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Faded advantage" })).toBeInTheDocument();
    expect(screen.getAllByText("2 games · 1 to review").length).toBeGreaterThan(0); // Endgame and Lost wins both
    expect(getReviewPage).toHaveBeenCalledTimes(1);
  });

  it("changing scope collapses open drill-downs so they reload under the new scope", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Scandinavian"));
    fireEvent.click(await screen.findByText("Main Line"));
    await screen.findByText("magnus_wannabe");
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    expect(screen.queryByText("magnus_wannabe")).toBeNull();
    fireEvent.click(screen.getByText("Scandinavian"));
    fireEvent.click(await screen.findByText("Main Line"));
    await waitFor(() => expect(getPoolEvents).toHaveBeenLastCalledWith(SUB, "focus", "__all__", "all", 1));
  });

  it("refetches with the new server params on a Focus-opening, Group-by or Time-class change, and Opening problems stays expanded", async () => {
    renderPage();
    await screen.findByText("Opening problems");

    fireEvent.change(combo("Focus opening"), { target: { value: "Scandinavian" } });
    await waitFor(() => expect(lastPageCall()).toEqual(["focus", "Scandinavian", "variation"]));
    await screen.findByText(/Opening problems — focused: Scandinavian/);
    expect(screen.getByText(/including thin ones/)).toBeInTheDocument();

    fireEvent.change(combo("Group openings"), { target: { value: "repertoire" } });
    await waitFor(() => expect(lastPageCall()).toEqual(["focus", "Scandinavian", "repertoire"]));

    fireEvent.change(combo("Time class"), { target: { value: "all" } });
    await waitFor(() => expect(lastPageCall()).toEqual(["all", "Scandinavian", "repertoire"]));
    await waitFor(() => expect(combo("Focus opening")).toBeInTheDocument());
    expect(screen.getByText("Scandinavian")).toBeInTheDocument();
    expect(screen.getAllByRole("combobox")).toHaveLength(3);
  });

  it("a single-pool category (Endgame technique) drills straight to its games: one header click loads page 1 and stamps the pool", async () => {
    renderPage();
    await screen.findByText("Endgame technique");
    const header = screen.getByRole("heading", { name: "Endgame technique" }).closest("button")!;
    expect(header.textContent).toMatch(/Severity 30\.0/);
    expect(header.textContent).toMatch(/Low/);
    expect(getPoolEvents).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("heading", { name: "Endgame technique" }));
    await waitFor(() => expect(getPoolEvents).toHaveBeenCalledWith("v1:route:endgame_technique", "focus", "__all__", "to_review", 1));
    await waitFor(() => expect(touchPoolShown).toHaveBeenCalledWith("v1:route:endgame_technique", "focus", "__all__"));
    expect(await screen.findByText("magnus_wannabe")).toBeInTheDocument();
    // No Best column on a route pool's rows.
    expect(screen.queryByRole("columnheader", { name: "Best" })).toBeNull();
  });

  it("loads more pages of a drill-down", async () => {
    getPoolEvents.mockImplementation((_id: string, _t: string, _o: string, _s: string, p: number) =>
      Promise.resolve({
        events: [row({ chess_game_id: 100 + p, opponent_username: `opp${p}`, url: `https://example.test/${100 + p}` })],
        total: 2,
        page: p,
        page_size: 1,
        total_pages: 2,
      }),
    );
    renderPage();
    fireEvent.click(await screen.findByText("Scandinavian"));
    fireEvent.click(await screen.findByText("Main Line"));
    await screen.findByText("opp1");
    fireEvent.click(screen.getByRole("button", { name: "Load more (1 of 2)" }));
    await screen.findByText("opp2");
    expect(screen.getByText("opp1")).toBeInTheDocument();
    expect(getPoolEvents).toHaveBeenLastCalledWith(SUB, "focus", "__all__", "to_review", 2);
    expect(screen.queryByRole("button", { name: /Load more/ })).toBeNull();
  });

  it("renders the empty state when the page has no games", async () => {
    getReviewPage.mockResolvedValue(emptyPage());
    renderPage();
    expect(await screen.findByText(/No review events yet/)).toBeInTheDocument();
    expect(screen.queryByText("Opening problems")).toBeNull();
  });

  it("recovers a stale-opening 422 by resetting to All openings with a one-line notice", async () => {
    getReviewPage.mockImplementation((_t: string, opening: string) => (opening === "Scandinavian" ? Promise.reject(new ApiError(422, "unknown opening key: 'Scandinavian'")) : Promise.resolve(page())));
    renderPage();
    await screen.findByText("Opening problems");
    fireEvent.change(combo("Focus opening"), { target: { value: "Scandinavian" } });

    await screen.findByText(/no longer has review games/);
    await waitFor(() => expect(combo("Focus opening").value).toBe("__all__"));
    expect(screen.getByText("Opening problems")).toBeInTheDocument();
    const openings = getReviewPage.mock.calls.map((c) => c[1]);
    expect(openings).toContain("Scandinavian");
    expect(openings.filter((o) => o === "__all__").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByRole("alert")).toBeNull();

    fireEvent.click(screen.getByText("Dismiss"));
    expect(screen.queryByText(/no longer has review games/)).toBeNull();
  });

  it("does not launder any other error into a stale-opening reset", async () => {
    getReviewPage.mockImplementation((_t: string, opening: string) => (opening === "Scandinavian" ? Promise.reject(new ApiError(500, "boom")) : Promise.resolve(page())));
    renderPage();
    await screen.findByText("Opening problems");
    fireEvent.change(combo("Focus opening"), { target: { value: "Scandinavian" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.queryByText(/no longer has review games/)).toBeNull();
    expect(getReviewPage.mock.calls.map((c) => c[1]).filter((o) => o === "__all__")).toHaveLength(1);
  });

  it("a drill-down that 422s on a stale opening recovers at the page level; any other drill error stays on the node", async () => {
    getReviewPage.mockResolvedValue(page({ filter: { time_class: "focus", opening: "Scandinavian", openings: page().filter.openings, group_by: "variation" } }));
    getPoolEvents.mockRejectedValueOnce(new ApiError(500, "node boom"));
    renderPage();
    fireEvent.click(await screen.findByText("Scandinavian"));
    fireEvent.click(await screen.findByText("Main Line"));
    expect(await screen.findByRole("alert")).toHaveTextContent("node boom");

    // Focus the opening, then have its drill-down go stale.
    fireEvent.change(combo("Focus opening"), { target: { value: "Scandinavian" } });
    await waitFor(() => expect(lastPageCall()).toEqual(["focus", "Scandinavian", "variation"]));
    getPoolEvents.mockRejectedValueOnce(new ApiError(422, "unknown opening key: 'Scandinavian'"));
    fireEvent.click(await screen.findByText("Scandinavian"));
    fireEvent.click(await screen.findByText("Main Line"));
    await screen.findByText(/no longer has review games/);
    await waitFor(() => expect(combo("Focus opening").value).toBe("__all__"));
  });

  it("the Focus opening list shows each family's to-review count", async () => {
    renderPage();
    await screen.findByText("Opening problems");
    const options = within(combo("Focus opening")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["All openings (7)", "Scandinavian (5)"]);
  });
});
