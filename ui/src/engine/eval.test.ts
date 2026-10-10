import { describe, expect, it } from "vitest";
import { MAX_PV_PLIES, engineSnapshot, fmtCp, uciPvToSan } from "./eval";
import type { EngineEval } from "./useStockfish";

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

describe("engineSnapshot", () => {
  const ev = (over: Partial<EngineEval> = {}): EngineEval => ({ fen: START, evalCp: 35, bestMoveUci: "e2e4", pvUci: ["e2e4", "e7e5"], depth: 16, thinking: false, ...over });
  it("is the bounded payload of a finished search", () => {
    expect(engineSnapshot(ev())).toEqual({ depth: 16, eval_cp: 35, best_move: "e2e4", pv: ["e2e4", "e7e5"] });
  });
  it("trims the line the engine printed to the cap; the hook does not", () => {
    const pv = Array.from({ length: 15 }, (_, i) => `m${i}`);
    expect(engineSnapshot(ev({ pvUci: pv }))?.pv).toEqual(pv.slice(0, MAX_PV_PLIES));
  });
  it("keeps a final best move beside a line that starts with another move; neither is rewritten", () => {
    const snap = engineSnapshot(ev({ bestMoveUci: "d2d4", pvUci: ["e2e4", "e7e5"] }));
    expect(snap).toEqual({ depth: 16, eval_cp: 35, best_move: "d2d4", pv: ["e2e4", "e7e5"] });
  });
  it("is null while the readout cannot be sent", () => {
    expect(engineSnapshot(null)).toBeNull();
    expect(engineSnapshot(ev({ thinking: true }))).toBeNull();
    expect(engineSnapshot(ev({ bestMoveUci: null }))).toBeNull();
    expect(engineSnapshot(ev({ evalCp: null }))).toBeNull();
    expect(engineSnapshot(ev({ pvUci: [] }))).toBeNull();
  });
});
