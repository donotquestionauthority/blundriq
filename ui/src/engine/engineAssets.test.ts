/**
 * The engine files under `public/engine/` are pinned by hash. The WASM, the worker glue and the
 * licence text are the upstream release, byte-unmodified; the corresponding-source archive is the
 * one tracked file CLAUDE.md rule 2 lets carry third-party attribution addresses, and its hash is
 * what makes the exception exactly that file: a byte changed inside it — an address added, one
 * replaced, a file removed — is a different archive, which is a new review and a new pin
 * (`tests/test_engine_source.py` records what this one contains).
 */
/// <reference types="node" />
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "public", "engine");
const sha256 = (name: string) => createHash("sha256").update(readFileSync(join(DIR, name))).digest("hex");

const PINNED = {
  "stockfish-18-lite-single.wasm": "a8fbc05ec6920b56d7485826dcb02c5ffd2826bcbf751cf973046f237a9096f1",
  "stockfish-18-lite-single.js": "2278005057f381491f1c9bb3e44c9f5920b3a00bef9759e33cc6582769a1f1fe",
  "Copying.txt": "0b383d5a63da644f628d99c33976ea6487ed89aaa59f0b3257992deac1171e6b",
  "stockfish-source.tar.gz": "85eb95e2e56e75e38381fa0255d975b6b2471277d393038667c9d65e9d2cdba3",
} as const;

const SEVEN = ["stockfish-18-lite-single.js", "stockfish-18-lite-single.wasm", "Copying.txt", "AUTHORS", "NNUE-NOTICE.txt", "stockfish-source.tar.gz", "STOCKFISH-SOURCE.md"];

describe("engine assets", () => {
  it("ships the seven files", () => {
    for (const f of SEVEN) expect(existsSync(join(DIR, f)), f).toBe(true);
  });

  it.each(Object.entries(PINNED))("%s matches its pinned SHA-256", (name, hash) => {
    expect(sha256(name)).toBe(hash);
  });

  it("the two notices written here no longer name the dropped /licenses page", () => {
    for (const f of ["STOCKFISH-SOURCE.md", "NNUE-NOTICE.txt"]) {
      const text = readFileSync(join(DIR, f), "utf8");
      expect(text, f).not.toContain("/licenses");
      expect(text, f).toContain("Explore");
    }
  });
});
