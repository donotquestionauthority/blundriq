import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import Layout from "./Layout";
import { _resetUnsavedAttemptForTests, holdUnsavedAttempt } from "../utils/unsavedAttempt";

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout onLoggedOut={() => {}} />}>
          <Route path="/" element={<p>home page</p>} />
          <Route path="/practice" element={<p>practice page</p>} />
          <Route path="/repertoire" element={<p>repertoire page</p>} />
          <Route path="/repertoire/conflicts" element={<p>conflicts page</p>} />
          <Route path="/games" element={<p>games page</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

afterEach(() => _resetUnsavedAttemptForTests());

describe("Layout navigation", () => {
  it("shows the primary links inline and the rest only once More is opened", () => {
    renderAt("/");
    for (const label of ["Home", "Practice", "Blunders", "Deviations"]) expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Repertoire" })).not.toBeInTheDocument();
    const more = screen.getByRole("button", { name: "More ▾" });
    expect(more).toHaveAttribute("aria-haspopup", "menu");
    expect(more).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(more);
    const menu = screen.getByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((el) => el.textContent)).toEqual(["Repertoire", "Games", "Preferences"]);
    expect(more).toHaveAttribute("aria-expanded", "true");
  });

  it("highlights More when the current page is under it, and closes on Escape, a menu link and a click outside", () => {
    renderAt("/repertoire/conflicts");
    const more = screen.getByRole("button", { name: "More ▾" });
    expect(more.className).toContain("text-zinc-900");
    expect(screen.getByRole("link", { name: "Home" }).className).not.toContain("text-zinc-900");
    fireEvent.click(more);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(more);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(more);
    fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "Games" }));
    expect(screen.getByText("games page")).toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(more.className).toContain("text-zinc-900"); // Games is under More too
  });

  it("moves between menu items with the arrow keys", () => {
    renderAt("/");
    fireEvent.click(screen.getByRole("button", { name: "More ▾" }));
    const items = within(screen.getByRole("menu")).getAllByRole("menuitem");
    fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowDown" });
    expect(items[0]).toHaveFocus();
    fireEvent.keyDown(items[0], { key: "ArrowUp" });
    expect(items[2]).toHaveFocus();
  });

  it("the hamburger panel lists all seven pages and Log out, and closes on a link", () => {
    renderAt("/");
    const burger = screen.getByRole("button", { name: "Menu" });
    expect(burger).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById("nav-panel")).toBeNull();
    fireEvent.click(burger);
    const panel = document.getElementById("nav-panel")!;
    expect(within(panel).getAllByRole("link").map((el) => el.textContent)).toEqual(["Home", "Practice", "Blunders", "Deviations", "Repertoire", "Games", "Preferences"]);
    expect(within(panel).getByRole("button", { name: "Log out" })).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("link", { name: "Practice" }));
    expect(screen.getByText("practice page")).toBeInTheDocument();
    expect(document.getElementById("nav-panel")).toBeNull();
  });

  it("held state disables every link in the bar, the menu and the panel, and the menus still open", () => {
    renderAt("/practice");
    act(() => holdUnsavedAttempt({ puzzle_id: 1, attempt_id: "a", session_id: "s", solved: true, moves_played: "e4", presentation_ply: null }));
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText("Home").tagName).toBe("SPAN");
    expect(screen.getByText("Home")).toHaveAttribute("title", "Save your attempt on the Practice page first");
    fireEvent.click(screen.getByRole("button", { name: "More ▾" }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("Repertoire").tagName).toBe("SPAN");
    expect(within(menu).getByText("Repertoire")).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(within(document.getElementById("nav-panel")!).getByText("Games").tagName).toBe("SPAN");
    for (const b of screen.getAllByRole("button", { name: "Log out" })) expect(b).toBeDisabled();
  });
});
