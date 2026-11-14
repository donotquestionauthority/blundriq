/// <reference types="node" />
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Chess } from "chess.js";
import { describe, expect, it } from "vitest";
import { EXPLORER_MAX_PLIES, EXPLORER_PREFIX_PLIES, explorerEntry, explorerSearch, legalPrefix, readExplorerUrl } from "./explorer";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const SHUFFLE = ["Nf3", "Nf6", "Ng1", "Ng8"];
const sixty = Array.from({ length: 15 }, () => SHUFFLE).flat();

const fenAfter = (moves: string[]) => {
  const g = new Chess();
  for (const m of moves) g.move(m);
  return g.fen();
};
const read = (search: string) => readExplorerUrl(new URLSearchParams(search));

describe("the explorer's bounds", () => {
  it("are core/constants.py's (the routes refuse past EXPLORER_MAX_PLIES; the statistics end at OPENING_PREFIX_PLIES)", () => {
    const constants = readFileSync(join(REPO, "core", "constants.py"), "utf8");
    expect(constants).toMatch(new RegExp(`^EXPLORER_MAX_PLIES = ${EXPLORER_MAX_PLIES}$`, "m"));
    expect(constants).toMatch(new RegExp(`^OPENING_PREFIX_PLIES = ${EXPLORER_PREFIX_PLIES}$`, "m"));
  });
});

describe("readExplorerUrl", () => {
  it("keeps a legal path and the values it names", () => {
    expect(read("?colour=black&moves=e4,c6,d4&time_class=all")).toEqual({ colour: "black", moves: ["e4", "c6", "d4"], timeClass: "all", rewrite: false });
  });

  it("drops the first illegal or unparsable token and everything after it", () => {
    expect(read("?colour=white&moves=e4,e5,Ke3,Nf3&time_class=focus")).toMatchObject({ moves: ["e4", "e5"], rewrite: true });
    expect(read("?colour=white&moves=e4,--,e5&time_class=focus")).toMatchObject({ moves: ["e4"], rewrite: true });
    expect(read("?colour=white&moves=e4,Zz9&time_class=focus")).toMatchObject({ moves: ["e4"], rewrite: true });
    expect(read("?colour=white&moves=e2e4,e5&time_class=focus")).toMatchObject({ moves: ["e4", "e5"], rewrite: true });
  });

  it("cuts a path past the bound to its first sixty plies", () => {
    const got = read(`?colour=white&moves=${[...sixty, "Nf3", "Nf6"].join(",")}&time_class=focus`);
    expect(got.moves).toEqual(sixty);
    expect(got.rewrite).toBe(true);
    expect(read(`?colour=white&moves=${sixty.join(",")}&time_class=focus`).rewrite).toBe(false);
  });

  it("means white and focus for a missing or unknown colour and time class", () => {
    expect(read("")).toEqual({ colour: "white", moves: [], timeClass: "focus", rewrite: true });
    expect(read("?colour=red&time_class=blitz")).toMatchObject({ colour: "white", timeClass: "focus", rewrite: true });
  });

  it("keeps the commas, escapes + and #, and reads a typed + back", () => {
    expect(explorerSearch({ colour: "white", moves: ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"], timeClass: "focus" })).toBe("?colour=white&moves=e4,e5,Qh5,Nc6,Bc4,Nf6,Qxf7%23&time_class=focus");
    expect(read("?colour=white&moves=e4,f5,Qh5+&time_class=focus")).toMatchObject({ moves: ["e4", "f5", "Qh5+"], rewrite: false });
    expect(read("?colour=white&moves=e4,f5,Qh5%2B&time_class=focus")).toMatchObject({ moves: ["e4", "f5", "Qh5+"], rewrite: false });
  });

  it("writes back what it reads", () => {
    const u = { colour: "black" as const, moves: ["d4", "Nf6"], timeClass: "all" as const };
    expect(read(explorerSearch(u))).toEqual({ ...u, rewrite: false });
    expect(legalPrefix(["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#", "Ke7"])).toHaveLength(7);
  });
});

describe("explorerEntry", () => {
  it("opens exactly the board on show up to the bound", () => {
    const moves = [...sixty, "Nf3", "Nf6"];
    const at60 = explorerEntry(moves, 60, fenAfter(sixty), "black");
    expect(at60).toEqual({ to: `/explorer${explorerSearch({ colour: "black", moves: sixty, timeClass: "focus" })}` });
    expect(explorerEntry(["e4", "e5", "Nf3"], 2, fenAfter(["e4", "e5"]), "white")).toEqual({ to: "/explorer?colour=white&moves=e4,e5&time_class=focus" });
    expect(explorerEntry(["e4"], 0, new Chess().fen(), "white")).toEqual({ to: "/explorer?colour=white&time_class=focus" });
  });

  it("is disabled with its reason past the bound", () => {
    const moves = [...sixty, "Nf3", "Nf6"];
    expect(explorerEntry(moves, 61, fenAfter(moves.slice(0, 61)), "white")).toEqual({ unavailable: "The Opening Explorer covers the first 30 moves." });
  });

  it("is absent when the path does not reach the board", () => {
    expect(explorerEntry(["e4", "e5"], 2, fenAfter(["d4", "d5"]), "white")).toEqual({ unavailable: null });
    expect(explorerEntry(["e4", "--"], 2, fenAfter(["e4"]), "white")).toEqual({ unavailable: null });
    expect(explorerEntry(null, 2, fenAfter(["e4", "e5"]), "white")).toEqual({ unavailable: null });
    expect(explorerEntry(["e4", "e5"], 3, fenAfter(["e4", "e5"]), "white")).toEqual({ unavailable: null });
    expect(explorerEntry(["e4", "e5"], 2, "not a fen", "white")).toEqual({ unavailable: null });
  });
});
