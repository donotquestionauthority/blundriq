import { describe, expect, it } from "vitest";
import { Chess } from "chess.js";
import { lastMoveSquares, legalMove, mapKey, moveArrows, normalizeSan, parsesAsFen, sanResolvesToMove, sanToSquares, spineLastMove, reviewArrowsFromPlyAnalysis, reviewArrowsForPly, buildLearnRevealArrows, appendBookArrow } from "./chess";
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

  it("an ordinary review ply draws only the best move in the position shown, for either side's move", () => {
    const pa = fenSequence.map((_, i) => ({ best_move: i === 2 ? "Bb5" : i === 3 ? "Bc5" : null }));
    expect(reviewArrowsFromPlyAnalysis({ fenSequence, ply: 2, plyAnalysis: pa })).toEqual([{ startSquare: "f1", endSquare: "b5", color: ARROWS.engine }]);
    // Ply 3 follows 2...Nc6, which was not the engine's Bb5-for-White, and 3 follows a move that was
    // not best: still one arrow, the best move here, never the move missed on the ply before.
    expect(reviewArrowsFromPlyAnalysis({ fenSequence, ply: 3, plyAnalysis: pa })).toEqual([{ startSquare: "f8", endSquare: "c5", color: ARROWS.engine }]);
    expect(reviewArrowsFromPlyAnalysis({ fenSequence, ply: 1, plyAnalysis: pa })).toEqual([]);
  });

  it("from blunder rows alone, the same single arrow", () => {
    expect(reviewArrowsForPly({ fenSequence, ply: 3, blunderAtPly: null })).toEqual([]);
    expect(reviewArrowsForPly({ fenSequence, ply: 2, blunderAtPly: { best_move: "Bb5" } }).map((a) => a.color)).toEqual([ARROWS.engine]);
  });

  it("the Learn reveal draws one arrow per from/to pair and legends every meaning, in row order", () => {
    const r = buildLearnRevealArrows({ fen: fenSequence[2], prevFen: fenSequence[1], committedMove: "Bc4", engineMove: "Bb5", bookMove: "Bc4", gameMove: "Nf3", gameMoveFlagged: true, opponentMove: "e5" });
    expect(r.arrows).toHaveLength(4);
    expect(r.rows.map((x) => [x.label, x.move, x.color])).toEqual([
      // The answer was the prep move: it is drawn in the prep colour, labelled as the answer first.
      ["You played · Your prep plays", "Bc4", ARROWS.book],
      ["Stockfish plays", "Bb5", ARROWS.engine],
      ["You played in the game", "Nf3", ARROWS.played],
      ["Opponent's last move", "e5", ARROWS.opponent],
    ]);
    expect(r.arrows.map((a) => a.color)).toEqual([ARROWS.book, ARROWS.engine, ARROWS.played, ARROWS.opponent]);
    // A slot that is not legal here, or absent, draws nothing; the promotion piece rides in the SAN.
    expect(buildLearnRevealArrows({ fen: fenSequence[2], committedMove: "O-O", engineMove: "--", gameMoveFlagged: true }).rows).toEqual([]);
    expect(r.rows.every((x) => x.drawn)).toBe(true);
  });

  it("an answer takes the colour of what it matched, and is ungraded blue only when it matches nothing", () => {
    const base = { fen: fenSequence[2], prevFen: fenSequence[1], engineMove: "Bb5", bookMove: "Bc4", gameMove: "Nf3", gameMoveFlagged: true, opponentMove: "e5" };
    const answer = (committedMove: string) => buildLearnRevealArrows({ ...base, committedMove }).rows.find((x) => x.slots.includes("committed"))!;
    expect([answer("Bb5").label, answer("Bb5").color]).toEqual(["You played · Stockfish plays", ARROWS.engine]);
    expect([answer("Bc4").label, answer("Bc4").color]).toEqual(["You played · Your prep plays", ARROWS.book]);
    expect([answer("Nf3").label, answer("Nf3").color]).toEqual(["You played · You played in the game", ARROWS.played]);
    expect([answer("d4").label, answer("d4").color]).toEqual(["You played", ARROWS.committed]);
    // The prep's colour wins over the game move's when the game move was the prep.
    const prepGame = buildLearnRevealArrows({ ...base, bookMove: "Nf3", committedMove: "Nf3" });
    expect(prepGame.rows[0].color).toBe(ARROWS.book);
    expect(prepGame.rows[0].label).toBe("You played · Your prep plays · You played in the game");
    // The engine's colour wins over the prep's when the answer is both.
    const both = buildLearnRevealArrows({ ...base, bookMove: "Bb5", committedMove: "Bb5" });
    expect(both.rows[0].color).toBe(ARROWS.engine);
    expect(both.arrows.find((a) => a.endSquare === "b5")!.color).toBe(ARROWS.engine);
  });

  it("the game move is red at a flagged ply and faded red at one that lost less than an inaccuracy", () => {
    const base = { fen: fenSequence[2], prevFen: fenSequence[1], engineMove: "Bb5", gameMove: "Nf3", committedMove: "d4" };
    const game = (flagged: boolean) => buildLearnRevealArrows({ ...base, gameMoveFlagged: flagged }).rows.find((x) => x.slots.includes("played"))!.color;
    expect(game(true)).toBe(ARROWS.played);
    expect(game(false)).toBe(`${ARROWS.played}66`);
    // An answer that repeats an unflagged game move takes that faded colour too.
    const repeat = buildLearnRevealArrows({ ...base, committedMove: "Nf3", gameMoveFlagged: false });
    expect(repeat.arrows.map((a) => a.color)).toContain(`${ARROWS.played}66`);
  });

  it("the reveal merges labels only for the same move: another promotion piece is its own row, undrawn", () => {
    const fen = "8/1P6/8/k7/8/8/8/7K w - - 0 1";
    const r = buildLearnRevealArrows({ fen, committedMove: "b8=N", engineMove: "b8=Q", gameMove: "b8=Q", gameMoveFlagged: true });
    expect(r.arrows).toHaveLength(1);
    expect(r.arrows[0].color).toBe(ARROWS.committed);
    expect(r.rows.map((x) => [x.label, x.move, x.drawn])).toEqual([
      ["You played", "b8=N", true],
      ["Stockfish plays · You played in the game", "b8=Q", false],
    ]);
    // The same piece spelled two ways is one move, one row.
    const same = buildLearnRevealArrows({ fen, committedMove: "b8Q", engineMove: "b8=Q", gameMoveFlagged: true });
    expect(same.rows).toHaveLength(1);
    expect(same.rows[0].label).toBe("You played · Stockfish plays");
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

describe("moveArrows", () => {
  const start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  it("draws several prep moves in the one book colour, a step quieter each, and a move out of play faded", () => {
    const arrows = moveArrows(start, [
      { move: "e4", inPlay: true },
      { move: "d4", inPlay: true },
      { move: "c4", inPlay: true },
      { move: "Nf3", inPlay: true },
      { move: "g3", inPlay: false },
    ]);
    expect(arrows.map((a) => a.color)).toEqual([`${ARROWS.book}ff`, `${ARROWS.book}cc`, `${ARROWS.book}a8`, `${ARROWS.book}a8`, `${ARROWS.book}66`]);
    // A named colour is kept, and faded when out of play.
    expect(moveArrows(start, [{ move: "e4", inPlay: true, color: ARROWS.played }, { move: "d4", inPlay: false, color: ARROWS.played }]).map((a) => a.color)).toEqual([ARROWS.played, `${ARROWS.played}66`]);
  });
});
