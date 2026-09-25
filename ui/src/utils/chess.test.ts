import { describe, expect, it } from "vitest";
import { Chess } from "chess.js";
import { lastMoveSquares, mapKey, normalizeSan, sanResolvesToMove, sanToSquares } from "./chess";

describe("normalizeSan", () => {
  // Pinned against the Python mirror; the two must agree.
  it.each([
    ["0-0", "O-O"],
    ["0-0-0", "O-O-O"],
    ["Nf3+", "Nf3"],
    ["Qxf7#", "Qxf7"],
    ["e8=Q+!!", "e8=Q"],
    ["Bb5?!", "Bb5"],
    ["O-O+", "O-O"],
    ["a4", "a4"],
  ])("%s → %s", (input, expected) => {
    expect(normalizeSan(input)).toBe(expected);
  });
});

describe("sanResolvesToMove", () => {
  const start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  it("compares the resolved move, tolerating over-disambiguation and suffixes", () => {
    expect(sanResolvesToMove(start, "Ngf3!", { from: "g1", to: "f3" })).toBe(true);
    expect(sanResolvesToMove(start, "Nf3", { from: "b1", to: "c3" })).toBe(false);
    expect(sanResolvesToMove(start, "not a move", { from: "e2", to: "e4" })).toBe(false);
  });
});

describe("mapKey", () => {
  it("blanks the en-passant square unless a legal capture exists", () => {
    // After 1.e4 no black pawn can capture on e3, so the key carries "-" whatever the FEN says.
    const g = new Chess("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1");
    expect(mapKey(g)).toBe("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -");
    // 1.e4 a6 2.e5 d5: exd6 is legal, so d6 stays.
    g.move("a6");
    g.move("e5");
    g.move("d5");
    expect(mapKey(g)).toBe("rnbqkbnr/1pp1pppp/p7/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6");
    // The move-count fields never take part in the key.
    expect(mapKey(new Chess("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 7 40"))).toBe("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - -");
  });
});

describe("null moves in the arrow helpers", () => {
  const FEN = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4";
  it("`--` draws no arrow and breaks a replay: chess.js plays it, the server does not", () => {
    expect(sanToSquares(FEN, "--")).toBeNull();
    expect(sanToSquares(FEN, "c3")).toEqual(["c2", "c3"]);
    expect(lastMoveSquares(["e4", "--", "Nf3"], 3)).toBeNull();
    expect(lastMoveSquares(["e4", "e5", "--"], 3)).toBeNull();
    expect(lastMoveSquares(["e4", "e5", "Nf3"], 3)).toEqual(["g1", "f3"]);
  });
});
