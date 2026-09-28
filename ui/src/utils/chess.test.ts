import { describe, expect, it } from "vitest";
import { Chess } from "chess.js";
import { lastMoveSquares, legalMove, mapKey, normalizeSan, parsesAsFen, sanResolvesToMove, sanToSquares, spineLastMove, reviewArrowsFromPlyAnalysis, reviewArrowsForPly, buildLearnRevealArrows, appendBookArrow } from "./chess";
import { ARROWS } from "./board";

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

describe("legalMove", () => {
  it("takes a SAN token or a move object, and refuses the null move", () => {
    const g = new Chess();
    expect(legalMove(g, { from: "e2", to: "e4" })?.san).toBe("e4");
    expect(legalMove(g, "e5")?.san).toBe("e5");
    expect(legalMove(g, { from: "g1", to: "f3", promotion: "q" })?.san).toBe("Nf3");
    expect(legalMove(g, "--")).toBeNull();
    // An illegal move throws in chess.js, as before; the caller's try/catch owns that. A square
    // moved to itself is illegal in the object form (chess.js has no null move there).
    expect(() => legalMove(g, { from: "a7", to: "a5" })).toThrow();
    expect(() => legalMove(g, { from: "a1", to: "a1" })).toThrow();
  });
});

describe("parsesAsFen", () => {
  it("is true only when chess.js can seed a board", () => {
    expect(parsesAsFen(new Chess().fen())).toBe(true);
    expect(parsesAsFen("not a fen")).toBe(false);
    expect(parsesAsFen("")).toBe(false);
  });
});

describe("the game review's arrows", () => {
  const spine = (sans: string[]) => {
    const c = new Chess();
    const fenSequence = [c.fen()];
    const moves: string[] = [];
    for (const s of sans) {
      moves.push(c.move(s).san);
      fenSequence.push(c.fen());
    }
    return { fenSequence, moves };
  };
  const { fenSequence, moves } = spine(["e4", "e5", "Nf3", "Nc6"]);

  it("spineLastMove reads the move that produced the position from the spine", () => {
    expect(spineLastMove(fenSequence, moves, 1)).toEqual(["e2", "e4"]);
    expect(spineLastMove(fenSequence, moves, 0)).toBeNull();
    expect(spineLastMove(null, moves, 1)).toBeNull();
  });

  it("from the per-position analysis: bestHint here, bestMissed one ply after a move that was not the engine's", () => {
    const pa = fenSequence.map((_, i) => ({ best_move: i === 2 ? "Bb5" : i === 3 ? "Bc5" : null }));
    const at2 = reviewArrowsFromPlyAnalysis({ fenSequence, ply: 2, plyAnalysis: pa, moves });
    expect(at2.arrows.map((a) => a.color)).toEqual([ARROWS.bestHint]);
    expect(at2.bestMissed).toBe(false);
    const at3 = reviewArrowsFromPlyAnalysis({ fenSequence, ply: 3, plyAnalysis: pa, moves });
    expect(at3.arrows.map((a) => [a.startSquare, a.endSquare, a.color])).toEqual([
      ["f8", "c5", ARROWS.bestHint],
      ["f1", "b5", ARROWS.bestMissed],
    ]);
    expect(at3.bestMissed).toBe(true);
    // The move played was the engine's: no bestMissed, whatever the spelling.
    const agreed = fenSequence.map((_, i) => ({ best_move: i === 2 ? "Nf3+" : null }));
    expect(reviewArrowsFromPlyAnalysis({ fenSequence, ply: 3, plyAnalysis: agreed, moves }).bestMissed).toBe(false);
  });

  it("from blunder rows alone, the same shape", () => {
    const r = reviewArrowsForPly({ fenSequence, ply: 3, blunderAtPly: null, blunderAtPrevPly: { best_move: "Bb5", move_played: "Nf3" } });
    expect(r.arrows.map((a) => a.color)).toEqual([ARROWS.bestMissed]);
    expect(r.bestMissed).toBe(true);
    expect(reviewArrowsForPly({ fenSequence, ply: 2, blunderAtPly: { best_move: "Bb5" } }).arrows.map((a) => a.color)).toEqual([ARROWS.bestHint]);
  });

  it("the Learn reveal draws one arrow per from/to pair and legends every meaning, in precedence order", () => {
    const r = buildLearnRevealArrows({ fen: fenSequence[2], prevFen: fenSequence[1], committedMove: "Bc4", engineMove: "Bb5", bookMove: "Bc4", gameMove: "Nf3", opponentMove: "e5" });
    expect(r.arrows).toHaveLength(4);
    expect(r.rows.map((x) => [x.label, x.move, x.color])).toEqual([
      ["You played · Your prep plays", "Bc4", ARROWS.committed],
      ["Stockfish plays", "Bb5", ARROWS.engine],
      ["You played in the game", "Nf3", ARROWS.played],
      ["Opponent's last move", "e5", ARROWS.opponent],
    ]);
    // A slot that is not legal here, or absent, draws nothing; the promotion piece rides in the SAN.
    expect(buildLearnRevealArrows({ fen: fenSequence[2], committedMove: "O-O", engineMove: "--" }).rows).toEqual([]);
  });

  it("appendBookArrow draws once per pair and reports whether it drew", () => {
    const base = [{ startSquare: "f1", endSquare: "c4", color: ARROWS.engine }];
    expect(appendBookArrow(base, fenSequence[2], "Bc4")).toEqual({ arrows: base, drew: false });
    const added = appendBookArrow(base, fenSequence[2], "Bb5");
    expect(added.drew).toBe(true);
    expect(added.arrows.map((a) => a.color)).toEqual([ARROWS.engine, ARROWS.book]);
    expect(appendBookArrow(base, fenSequence[2], null)).toEqual({ arrows: base, drew: false });
    expect(appendBookArrow(base, fenSequence[2], "O-O").drew).toBe(false);
  });
});
