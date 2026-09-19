import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi, describe, it, expect } from "vitest";
import App from "./App";

describe("App", () => {
  it("shows the login page when the session check returns 401", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 401, statusText: "Unauthorized", json: async () => ({ detail: "not logged in" }) })));
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByLabelText("Password")).toBeInTheDocument();
  });
});
