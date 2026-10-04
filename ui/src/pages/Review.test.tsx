import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router";
import { returnTarget } from "../utils/returnTo";
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

function ReviewStub() {
  const location = useLocation();
  return <pre data-testid="review-state">{JSON.stringify(location.state)}</pre>;
}

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={["/review"]}>
      <Routes>
        <Route path="/review" element={<Review />} />
        <Route path="/review/:gameId" element={<ReviewStub />} />
      </Routes>
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
    expect(screen.getByText(/\+2 more in this game/)).toBeInTheDocument();
    expect(screen.getByText("Book move: c4")).toBeInTheDocument();
    expect(screen.getByText("Best move: Nf3")).toBeInTheDocument();
    expect(screen.getAllByText("You left book first — drill the line").length).toBeGreaterThan(0);
    // Opening rows carry the Best column.
    expect(screen.getByRole("columnheader", { name: "Best" })).toBeInTheDocument();
  });

  it("a game row opens the game's review at its anchor ply, carrying where to come back to", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Lost wins"));
    const link = (await screen.findAllByRole("link", { name: "Review →" }))[0];
    expect(link).toHaveAttribute("href", "/review/301?ply=14");
    fireEvent.click(link);
    const state = JSON.parse((await screen.findByTestId("review-state")).textContent ?? "{}");
    expect(state).toEqual({ from: { pathname: "/review", search: "", open: { cats: ["opening", "lost_wins"], nodes: [], key: "focus|to_review|__all__|variation" } } });
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

  it("a 422 for a focus the user has since left never undoes the newer choice", async () => {
    let rejectScandi: (e: unknown) => void = () => undefined;
    getReviewPage.mockImplementation((_t: string, opening: string) => {
      if (opening === "Scandinavian") return new Promise((_, reject) => (rejectScandi = reject));
      return Promise.resolve(page({ filter: { ...page().filter, openings: [...page().filter.openings, { key: "Italian", label: "Italian", to_review_games: 2 }] } }));
    });
    renderPage();
    await screen.findByText("Opening problems");
    fireEvent.change(combo("Focus opening"), { target: { value: "Scandinavian" } });
    await waitFor(() => expect(lastPageCall()).toEqual(["focus", "Scandinavian", "variation"]));
    fireEvent.change(combo("Focus opening"), { target: { value: "Italian" } });
    await waitFor(() => expect(lastPageCall()).toEqual(["focus", "Italian", "variation"]));
    await screen.findByText(/focused: Italian/);
    rejectScandi(new ApiError(422, "unknown opening key: 'Scandinavian'"));
    await new Promise((r) => setTimeout(r, 20));
    expect(combo("Focus opening").value).toBe("Italian");
    expect(screen.queryByText(/no longer has review games/)).toBeNull();
    expect(getReviewPage.mock.calls.map((c) => c[1]).filter((o) => o === "__all__")).toHaveLength(1);
  });

  it("a single-pool category left open across a scope change reloads under the new scope", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("heading", { name: "Endgame technique" }));
    await screen.findByText("magnus_wannabe");
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    // Closed, not stuck on "Loading…"; opening it again fetches under the new scope.
    expect(screen.queryByText("magnus_wannabe")).toBeNull();
    expect(screen.queryByText("Loading…")).toBeNull();
    fireEvent.click(screen.getByRole("heading", { name: "Endgame technique" }));
    await waitFor(() => expect(getPoolEvents).toHaveBeenLastCalledWith("v1:route:endgame_technique", "focus", "__all__", "all", 1));
    await screen.findByText("magnus_wannabe");
  });

  it("a scope round trip refetches a drill-down instead of serving rows from before", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Scandinavian"));
    fireEvent.click(await screen.findByText("Main Line"));
    await screen.findByText("magnus_wannabe");
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    fireEvent.click(screen.getByRole("tab", { name: "To review" }));
    fireEvent.click(screen.getByText("Scandinavian"));
    fireEvent.click(await screen.findByText("Main Line"));
    await screen.findByText("magnus_wannabe");
    expect(getPoolEvents).toHaveBeenCalledTimes(2);
  });

  const deferred = () => {
    let resolve: (v: unknown) => void = () => undefined;
    const promise = new Promise((r) => (resolve = r));
    return { promise, resolve };
  };
  const events = (names: string[], page = 1, total = names.length) => ({ events: names.map((n, i) => row({ chess_game_id: 500 + i + page * 10, opponent_username: n, url: `https://example.test/${n}` })), total, page, page_size: 1, total_pages: 2 });
  const ENDGAME = "v1:route:endgame_technique";

  it("a drill response from before a scope round trip never overwrites the fresh rows", async () => {
    const held = deferred();
    getPoolEvents.mockImplementationOnce(() => held.promise);
    renderPage();
    fireEvent.click(await screen.findByRole("heading", { name: "Endgame technique" }));
    await waitFor(() => expect(getPoolEvents).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    fireEvent.click(screen.getByRole("tab", { name: "To review" }));
    getPoolEvents.mockResolvedValueOnce(events(["fresh_one"]));
    fireEvent.click(screen.getByRole("heading", { name: "Endgame technique" }));
    await screen.findByText("fresh_one");
    held.resolve(events(["obsolete_one"]));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText("fresh_one")).toBeInTheDocument();
    expect(screen.queryByText("obsolete_one")).toBeNull();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("a Load more from before a scope round trip never restores page 2 alone", async () => {
    getPoolEvents.mockResolvedValueOnce(events(["first_a"], 1, 2));
    renderPage();
    fireEvent.click(await screen.findByRole("heading", { name: "Endgame technique" }));
    await screen.findByText("first_a");
    const held = deferred();
    getPoolEvents.mockImplementationOnce(() => held.promise);
    fireEvent.click(screen.getByRole("button", { name: "Load more (1 of 2)" }));
    await waitFor(() => expect(getPoolEvents).toHaveBeenLastCalledWith(ENDGAME, "focus", "__all__", "to_review", 2));
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    fireEvent.click(screen.getByRole("tab", { name: "To review" }));
    held.resolve(events(["second_a"], 2, 2));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("second_a")).toBeNull();
    // Reopening fetches page 1 again; nothing from the old view survives.
    getPoolEvents.mockResolvedValueOnce(events(["first_b"], 1, 2));
    fireEvent.click(screen.getByRole("heading", { name: "Endgame technique" }));
    await waitFor(() => expect(getPoolEvents).toHaveBeenLastCalledWith(ENDGAME, "focus", "__all__", "to_review", 1));
    await screen.findByText("first_b");
    expect(screen.queryByText("second_a")).toBeNull();
    expect(screen.getByRole("button", { name: "Load more (1 of 2)" })).toBeInTheDocument();
  });

  it("a drill error from before a scope round trip is dropped, not shown on the reopened node", async () => {
    const held = deferred();
    getPoolEvents.mockImplementationOnce(() => held.promise);
    renderPage();
    fireEvent.click(await screen.findByRole("heading", { name: "Endgame technique" }));
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    fireEvent.click(screen.getByRole("tab", { name: "To review" }));
    getPoolEvents.mockResolvedValueOnce(events(["fresh_one"]));
    fireEvent.click(screen.getByRole("heading", { name: "Endgame technique" }));
    await screen.findByText("fresh_one");
    held.resolve(Promise.reject(new ApiError(500, "old boom")));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("old boom")).toBeNull();
    expect(screen.getByText("fresh_one")).toBeInTheDocument();
  });

  it("a scope change while the page request is pending leaves its stale-opening recovery intact", async () => {
    const held = deferred();
    getReviewPage.mockImplementation((_t: string, opening: string) => (opening === "Scandinavian" ? held.promise : Promise.resolve(page())));
    renderPage();
    await screen.findByText("Opening problems");
    fireEvent.change(combo("Focus opening"), { target: { value: "Scandinavian" } });
    await waitFor(() => expect(lastPageCall()).toEqual(["focus", "Scandinavian", "variation"]));
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    held.resolve(Promise.reject(new ApiError(422, "unknown opening key: 'Scandinavian'")));
    await screen.findByText(/no longer has review games/);
    await waitFor(() => expect(combo("Focus opening").value).toBe("__all__"));
    expect(screen.queryByRole("alert")).toBeNull();
    // The chosen scope survived the recovery.
    expect(screen.getByRole("tab", { name: "All" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "Faded advantage" })).toBeInTheDocument();
  });

  it("the Focus opening list shows each family's to-review count", async () => {
    renderPage();
    await screen.findByText("Opening problems");
    const options = within(combo("Focus opening")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["All openings (7)", "Scandinavian (5)"]);
  });

  it("Lost wins shows fifty games at a time, and a scope change starts again from the first fifty", async () => {
    const games = Array.from({ length: 120 }, (_, i) => row({ chess_game_id: 1000 + i, opponent_username: `lw${i}`, base_route: "faded", reviewed: i % 2 === 1 }));
    const p = page();
    p.categories.lost_wins = { games, total_games: 120, to_review_games: 60 };
    getReviewPage.mockResolvedValue(p);
    renderPage();
    fireEvent.click(await screen.findByText("Lost wins"));
    expect(screen.getAllByRole("link", { name: "Review →" })).toHaveLength(50);
    expect(screen.getByText("lw0")).toBeInTheDocument();
    expect(screen.queryByText("lw100")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 10 more (10 remaining)" }));
    expect(screen.getAllByRole("link", { name: "Review →" })).toHaveLength(60);
    expect(screen.getByText("lw118")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show .* more/ })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    expect(screen.getAllByRole("link", { name: "Review →" })).toHaveLength(50);
    expect(screen.getByRole("button", { name: "Show 50 more (70 remaining)" })).toBeInTheDocument();
  });

  it("theme and piece tokens read as words: camelCase and snake_case alike", async () => {
    const { prettyToken, pieceOrTheme } = await vi.importActual<typeof import("../review")>("../review");
    expect(prettyToken("hangingPiece")).toBe("Hanging piece");
    expect(prettyToken("discoveredAttack")).toBe("Discovered attack");
    expect(prettyToken("mateIn2")).toBe("Mate in 2");
    expect(prettyToken("forced_loss")).toBe("Forced loss");
    expect(prettyToken("fork")).toBe("Fork");
    expect(pieceOrTheme(row({ piece_label: null, evidence: { theme: "backRankMate" } }))).toBe("Back rank mate");
  });
});

// --- The settings in the URL, the expansion in the entry ---------------------------------------------

/** A game review stand-in with the review's two ways back: the browser's Back, and Close (which
 *  goes through the same return target the review uses). A top-bar link starts a fresh visit. */
function GameStub() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div>
      <button type="button" onClick={() => navigate(-1)}>
        browser back
      </button>
      <button
        type="button"
        onClick={() => {
          const back = returnTarget(location.state);
          navigate(back.to, back.state != null ? { state: back.state } : undefined);
        }}
      >
        close
      </button>
      <Link to="/review">top bar Review</Link>
    </div>
  );
}

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname + location.search}</output>;
}

/** The browser's Back and the top bar's link, beside the worklist (the page stays mounted). */
function Chrome() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(-1)}>
        page back
      </button>
      <Link to="/review">page top bar</Link>
    </>
  );
}

type Entry = string | { pathname: string; search?: string; state?: unknown };
const renderApp = (entry: Entry | Entry[] = "/review") => {
  const entries = Array.isArray(entry) ? entry : [entry];
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
      <Routes>
        <Route
          path="/review"
          element={
            <>
              <Review />
              <Where />
              <Chrome />
            </>
          }
        />
        <Route path="/review/:gameId" element={<GameStub />} />
        <Route path="/elsewhere" element={<p>elsewhere</p>} />
      </Routes>
    </MemoryRouter>,
  );
};
const where = () => screen.getByTestId("where").textContent;

const ENDGAME_POOL = "v1:route:endgame_technique";

describe("Review keeps its settings in the URL", () => {
  it("a setting change writes the URL, defaults left out, and replaces rather than adding a Back step", async () => {
    renderApp();
    await screen.findByText("Opening problems");
    expect(where()).toBe("/review");
    fireEvent.change(combo("Time class"), { target: { value: "all" } });
    await waitFor(() => expect(where()).toBe("/review?tc=all"));
    fireEvent.change(combo("Group openings"), { target: { value: "position" } });
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    await waitFor(() => expect(where()).toBe("/review?tc=all&scope=all&group=position"));
    fireEvent.change(combo("Time class"), { target: { value: "focus" } });
    await waitFor(() => expect(where()).toBe("/review?scope=all&group=position"));
  });

  it("no setting change or toggle adds a Back step", async () => {
    renderApp(["/elsewhere", "/review"]);
    await screen.findByText("Opening problems");
    fireEvent.change(combo("Time class"), { target: { value: "all" } });
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    fireEvent.click(await screen.findByText("Scandinavian"));
    fireEvent.click(await screen.findByText("Main Line"));
    await screen.findByText("magnus_wannabe");
    fireEvent.click(screen.getByRole("button", { name: "page back" }));
    expect(await screen.findByText("elsewhere")).toBeInTheDocument();
  });

  it("an unknown time class, even an object property's name, is the default", async () => {
    renderApp("/review?tc=constructor");
    await screen.findByText("Opening problems");
    expect(lastPageCall()).toEqual(["focus", "__all__", "variation"]);
  });

  it("Back between two worklist entries while the page stays mounted restores each one", async () => {
    renderApp();
    await screen.findByText("Opening problems");
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    fireEvent.click(await screen.findByText("Scandinavian"));
    fireEvent.click(await screen.findByText("Main Line"));
    await screen.findByText("magnus_wannabe");
    expect(getPoolEvents).toHaveBeenCalledTimes(1);
    // The top bar: a new entry, the default view, collapsed, on the same mounted page.
    fireEvent.click(screen.getByRole("link", { name: "page top bar" }));
    await waitFor(() => expect(where()).toBe("/review"));
    expect(screen.getByRole("tab", { name: "To review" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("Main Line")).toBeNull();
    // Back: the earlier entry's settings and expansion, its rows fetched again for this view.
    fireEvent.click(screen.getByRole("button", { name: "page back" }));
    await waitFor(() => expect(where()).toBe("/review?scope=all"));
    expect(await screen.findByText("magnus_wannabe")).toBeInTheDocument();
    expect(getPoolEvents).toHaveBeenCalledTimes(2);
    expect(getPoolEvents).toHaveBeenLastCalledWith(SUB, "focus", "__all__", "all", 1);
  });

  it("a notice from a recovery does not outlive a settings change made elsewhere", async () => {
    getReviewPage.mockImplementation((_t: string, opening: string) => (opening === "Scandinavian" ? Promise.reject(new ApiError(422, "unknown opening key: 'Scandinavian'")) : Promise.resolve(page())));
    renderApp("/review?opening=Scandinavian&tc=all");
    await screen.findByText(/no longer has review games/);
    fireEvent.click(screen.getByRole("link", { name: "page top bar" }));
    await waitFor(() => expect(where()).toBe("/review"));
    await waitFor(() => expect(screen.queryByText(/no longer has review games/)).toBeNull());
  });

  it("an open single-pool section comes back open with its games after a game", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("heading", { name: "Endgame technique" }));
    await waitFor(() => expect(getPoolEvents).toHaveBeenCalledWith(ENDGAME_POOL, "focus", "__all__", "to_review", 1));
    fireEvent.click((await screen.findAllByRole("link", { name: "Review →" }))[0]);
    fireEvent.click(await screen.findByRole("button", { name: "close" }));
    await screen.findByText("Opening problems");
    expect(await screen.findByText("magnus_wannabe")).toBeInTheDocument();
    expect(getPoolEvents.mock.calls.filter((c) => c[0] === ENDGAME_POOL)).toHaveLength(2);
  });

  it("an open subgroup inside a closed family waits for the family before its rows are fetched", async () => {
    const open = { cats: ["opening"], nodes: [SUB], key: "focus|to_review|__all__|variation" };
    renderApp({ pathname: "/review", search: "", state: { open } });
    await screen.findByText("Scandinavian");
    expect(getPoolEvents).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Scandinavian"));
    expect(await screen.findByText("magnus_wannabe")).toBeInTheDocument();
    expect(getPoolEvents).toHaveBeenCalledTimes(1);
  });

  it("opens at the URL's settings, and reads a value it does not know as the default", async () => {
    renderApp("/review?tc=all&scope=all&opening=Scandinavian&group=repertoire");
    await screen.findByText(/^Opening problems/);
    expect(lastPageCall()).toEqual(["all", "Scandinavian", "repertoire"]);
    expect(combo("Time class").value).toBe("all");
    expect(screen.getByRole("tab", { name: "All" })).toHaveAttribute("aria-selected", "true");
    cleanup();
    getReviewPage.mockClear();
    renderApp("/review?tc=bullet&scope=maybe&group=nonsense");
    await screen.findByText("Opening problems");
    expect(lastPageCall()).toEqual(["focus", "__all__", "variation"]);
    expect(screen.getByRole("tab", { name: "To review" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("button", { name: "Reset filters" })).toBeNull();
  });

  it("Reset filters returns every setting to its default and collapses", async () => {
    renderApp("/review?tc=all&scope=all&group=position");
    fireEvent.click(await screen.findByText("Lost wins"));
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    await waitFor(() => expect(where()).toBe("/review"));
    expect(lastPageCall()).toEqual(["focus", "__all__", "variation"]);
    expect(screen.queryByRole("button", { name: "Reset filters" })).toBeNull();
    expect(screen.queryAllByRole("link", { name: "Review →" })).toHaveLength(0); // Lost wins collapsed
  });

  it("a stale focused opening is dropped from the URL, the other settings kept", async () => {
    getReviewPage.mockImplementation((_t: string, opening: string) => (opening === "Scandinavian" ? Promise.reject(new ApiError(422, "unknown opening key: 'Scandinavian'")) : Promise.resolve(page())));
    renderApp("/review?tc=all&opening=Scandinavian");
    await screen.findByText(/no longer has review games/);
    await waitFor(() => expect(where()).toBe("/review?tc=all"));
  });

  for (const way of ["browser back", "close"] as const) {
    it(`${way} from a game returns to the same settings, the same branch open and its rows reloaded`, async () => {
      renderApp();
      await screen.findByText("Opening problems");
      fireEvent.click(screen.getByRole("tab", { name: "All" }));
      fireEvent.change(combo("Group openings"), { target: { value: "repertoire" } });
      await waitFor(() => expect(where()).toBe("/review?scope=all&group=repertoire"));
      fireEvent.click(await screen.findByText("Scandinavian"));
      fireEvent.click(await screen.findByText("Main Line"));
      await screen.findByText("magnus_wannabe");
      expect(getPoolEvents).toHaveBeenCalledTimes(1);
      const stamped = touchPoolShown.mock.calls.length;

      fireEvent.click(screen.getAllByRole("link", { name: "Review →" })[0]);
      fireEvent.click(await screen.findByRole("button", { name: way }));

      await screen.findByText("Opening problems");
      expect(where()).toBe("/review?scope=all&group=repertoire");
      expect(screen.getByRole("tab", { name: "All" })).toHaveAttribute("aria-selected", "true");
      expect(combo("Group openings").value).toBe("repertoire");
      expect(screen.getByText("Main Line")).toBeInTheDocument(); // the family is open
      expect(await screen.findByText("magnus_wannabe")).toBeInTheDocument(); // the subgroup is open, its rows back
      expect(getPoolEvents).toHaveBeenCalledTimes(2);
      expect(getPoolEvents).toHaveBeenLastCalledWith(SUB, "focus", "__all__", "all", 1);
      expect(touchPoolShown.mock.calls.length).toBe(stamped); // resuming a look is not a new look

      // The top bar after the trip: the default view, collapsed.
      fireEvent.click(screen.getAllByRole("link", { name: "Review →" })[0]);
      fireEvent.click(await screen.findByRole("link", { name: "top bar Review" }));
      await screen.findByText("Opening problems");
      expect(where()).toBe("/review");
      expect(screen.getByRole("tab", { name: "To review" })).toHaveAttribute("aria-selected", "true");
      expect(screen.queryByText("Main Line")).toBeNull();
      expect(screen.queryByText("magnus_wannabe")).toBeNull();
    });
  }

  it("a snapshot taken under other settings is ignored", async () => {
    const open = { cats: ["opening"], nodes: [FAM, SUB], key: "all|all|__all__|variation" };
    renderApp({ pathname: "/review", search: "", state: { open } });
    await screen.findByText("Opening problems");
    expect(screen.queryByText("Main Line")).toBeNull();
    expect(getPoolEvents).not.toHaveBeenCalled();
  });
});

