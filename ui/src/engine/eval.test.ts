import { describe, expect, it } from "vitest";
import { fmtCp, uciPvToSan } from "./eval";

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

describe("fmtCp", () => {
  it.each([
    [null, "–"],
    [0, "0.0"],
    [130, "+1.3"],
    [-40, "-0.4"],
    [9997, "+M3"],
    [-9998, "-M2"],
    [10000, "+M1"],
  ])("%s → %s", (cp, label) => {
    expect(fmtCp(cp)).toBe(label);
  });
});

describe("uciPvToSan", () => {
  it("replays the line as SAN", () => {
    expect(uciPvToSan(START, ["e2e4", "e7e5", "g1f3"])).toBe("e4 e5 Nf3");
  });
  it("stops at the first token that does not play", () => {
    expect(uciPvToSan(START, ["e2e4", "e2e4", "g1f3"])).toBe("e4");
    expect(uciPvToSan(START, ["e2e4", "0000", "g1f3"])).toBe("e4");
    expect(uciPvToSan(START, ["e2e4", "--", "g1f3"])).toBe("e4");
  });
  it("caps the plies and tolerates a bad FEN", () => {
    expect(uciPvToSan(START, ["e2e4", "e7e5", "g1f3", "b8c6"], 2)).toBe("e4 e5");
    expect(uciPvToSan("nonsense", ["e2e4"])).toBe("");
  });
  it("carries a promotion", () => {
    expect(uciPvToSan("8/P6k/8/8/8/8/8/K7 w - - 0 1", ["a7a8q"])).toBe("a8=Q");
  });
});
