/**
 * The engine files under `public/engine/` are pinned by hash. The WASM, the worker glue and the
 * licence text are the upstream release, byte-unmodified; the corresponding-source archive is the
 * only tracked file that carries third-party attribution addresses (two, both upstream authors'
 * own, reviewed), and its hash is what confines that to exactly this file: a byte changed inside
 * it — an address added, one replaced, a file removed — is a different archive, which is a new
 * content review and a new pin (`tests/test_engine_source.py` records what this one contains).
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
  "stockfish-19-lite-single.wasm": "57ac2d72312aba346760e3f173f687a8c211208e97a87268436f7f0e10bb5387",
  "stockfish-19-lite-single.js": "d3344124ab067fb0b90ee77873bb8e9fbf5fc01bc525fe714b0f942581e889e6",
  "Copying.txt": "0b383d5a63da644f628d99c33976ea6487ed89aaa59f0b3257992deac1171e6b",
  "stockfish-source.tar.gz": "e6fb281ec7b014fc1be23766339d504ea16376c6528df80ebd7e0d4f28f01170",
} as const;

const SEVEN = ["stockfish-19-lite-single.js", "stockfish-19-lite-single.wasm", "Copying.txt", "AUTHORS", "NNUE-NOTICE.txt", "stockfish-source.tar.gz", "STOCKFISH-SOURCE.md"];

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

  it("the notices, the glue and the footer name the same engine version as the pinned files", () => {
    const version = "19";
    for (const f of ["STOCKFISH-SOURCE.md", "NNUE-NOTICE.txt"]) {
      const text = readFileSync(join(DIR, f), "utf8");
      expect(text, f).toContain(`Stockfish ${version}`);
      expect(text, f).toContain(`stockfish-${version}-lite-single.wasm`);
    }
    const src = join(dirname(fileURLToPath(import.meta.url)), "..");
    expect(readFileSync(join(src, "engine", "useStockfish.ts"), "utf8")).toContain(`/engine/stockfish-${version}-lite-single.js`);
    expect(readFileSync(join(src, "components", "ExploreLayer.tsx"), "utf8")).toContain(`Stockfish ${version} (GPL-3.0)`);
  });
});
