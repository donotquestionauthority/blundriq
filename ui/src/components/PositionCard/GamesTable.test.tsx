import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { GamesTable } from "./GamesTable";
import type { PositionCardGame } from "./types";
import { leaveReview } from "../../utils/returnTo";

const now = new Date().toISOString();
const own: PositionCardGame = { game_url: "https://lichess.org/AbCdEfGh", played_at: now, review: { gameId: 7, ply: 12 } };
const ownChesscom: PositionCardGame = { game_url: "https://www.chess.com/game/daily/42", played_at: now, review: { gameId: 8, ply: 3 } };
const theirs: PositionCardGame = { game_url: "https://www.chess.com/game/live/9", played_at: now, opening: "Italian Game", as: "black" };

function Where() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <p data-testid="where">{`${location.pathname}${location.search}|${JSON.stringify(location.state)}`}</p>
      <button type="button" onClick={() => leaveReview(location.state, navigate)}>
        Leave
      </button>
    </>
  );
}

/** The opener's entry as the review left it: what it was given back. */
function Left() {
  return <p data-testid="left">{JSON.stringify(useLocation().state)}</p>;
}

const renderTable = (games: PositionCardGame[], returnState?: () => unknown) =>
  render(
    <MemoryRouter initialEntries={["/blunders?x=1"]}>
      <Routes>
        <Route
          path="/blunders"
          element={
            <>
              <GamesTable games={games} bestLabel="Best" returnState={returnState} />
              <Left />
            </>
          }
        />
        <Route path="/review/:gameId" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );

describe("GamesTable", () => {
  it("the player's own game opens the review at the move, with the site's own page one tap away", () => {
    renderTable([own, ownChesscom]);
    const [lichess, chesscom] = screen.getAllByTestId("game-review-link");
    expect(lichess).toHaveAttribute("href", "/review/7?ply=12");
    expect(lichess).not.toHaveAttribute("target");
    expect(chesscom).toHaveAttribute("href", "/review/8?ply=3");
    expect(screen.getByRole("link", { name: "Open on Lichess" })).toHaveAttribute("href", "https://lichess.org/AbCdEfGh");
    expect(screen.getByRole("link", { name: "Open on Lichess" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Open on Chess.com" })).toHaveAttribute("href", "https://www.chess.com/game/daily/42");
  });

  it("an opponent's game, or one with no review, keeps its date linking out", () => {
    renderTable([theirs, { game_url: "https://lichess.org/zzzzzzzz", played_at: now }]);
    expect(screen.queryByTestId("game-review-link")).not.toBeInTheDocument();
    const out = screen.getAllByRole("link");
    expect(out.map((a) => a.getAttribute("href"))).toEqual(["https://www.chess.com/game/live/9", "https://lichess.org/zzzzzzzz"]);
    expect(out.every((a) => a.getAttribute("target") === "_blank")).toBe(true);
  });

  it("a click writes the page's snapshot into the entry it leaves, and the review goes back to it", () => {
    renderTable([own], () => ({ kind: "test", openKey: "k" }));
    fireEvent.click(screen.getByTestId("game-review-link"));
    expect(screen.getByTestId("where").textContent).toBe('/review/7?ply=12|{"from":{"pathname":"/blunders","search":"?x=1","back":true}}');
    fireEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(screen.getByTestId("left").textContent).toBe('{"card":{"kind":"test","openKey":"k"}}');
  });

  it("a modified click is the browser's: no navigation here, nothing written into the entry", () => {
    const returnState = vi.fn(() => ({ kind: "test", openKey: "k" }));
    renderTable([own], returnState);
    fireEvent.click(screen.getByTestId("game-review-link"), { ctrlKey: true });
    fireEvent.click(screen.getByTestId("game-review-link"), { metaKey: true });
    expect(screen.getByTestId("game-review-link")).toBeInTheDocument();
    expect(returnState).not.toHaveBeenCalled();
  });

  it("without a snapshot the review still knows where it came from", () => {
    renderTable([own]);
    fireEvent.click(screen.getByTestId("game-review-link"));
    expect(screen.getByTestId("where").textContent).toBe('/review/7?ply=12|{"from":{"pathname":"/blunders","search":"?x=1","back":true}}');
  });
});
