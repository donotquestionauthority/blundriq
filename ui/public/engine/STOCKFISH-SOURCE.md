# Stockfish (in-browser engine) — source, version, and modification status

BlundrIQ ships the Stockfish chess engine to the browser as WebAssembly for the
**Explore** view. This conveys the engine binary to the user,
which is GPLv3 conveyance; the files in this directory exist to satisfy that.

## What is shipped

| File | What it is |
|---|---|
| `stockfish-18-lite-single.js` | Engine worker glue (UCI over `postMessage`). |
| `stockfish-18-lite-single.wasm` | Stockfish 18 Lite, single-threaded, WebAssembly (~7 MB). |
| `Copying.txt` | Full GNU GPL v3 license text (upstream, unmodified). |
| `AUTHORS` | Stockfish authors / contributors (upstream). |
| `NNUE-NOTICE.txt` | NNUE network attribution + license. |
| `stockfish-source.tar.gz` | **Corresponding Source** for the exact binary above. |

## Exact version (pinned)

- Prebuilt binary: **npm `stockfish@18.0.7`** (author Nathan Rugg; sponsored by
  Chess.com) — the same in-browser build Chess.com uses. SHA-256 of the
  conveyed WASM: `a8fbc05ec6920b56d7485826dcb02c5ffd2826bcbf751cf973046f237a9096f1`.
- Flavor: **lite, single-threaded** (`node build.js --single-threaded --lite -f`).
- Engine source: **official-stockfish/Stockfish** tag **`sf_18`** (upstream
  commit `cb3d4ee`).
- WASM wrapper: **nmrugg/stockfish.js** (Emscripten **3.1.7**).
- NNUE network embedded by this Lite build: **`nn-9067e33176e8.nnue`**. The Lite
  flavor pins its network in the wrapper's `src/lite_nets.h`
  (`EvalFileDefaultNameBig = "nn-9067e33176e8.nnue"`, `EvalFileDefaultNameSmall = ""`),
  **not** in `src/evaluate.h`. The `evaluate.h` names
  (Big `nn-c288c895ea92.nnue`, Small `nn-37f18f62d772.nnue`) belong to the
  **standard** (non-Lite) build and are not embedded by the conveyed binary.

## UNMODIFIED (N6)

The engine binary is the upstream prebuilt artifact, **byte-unmodified** by
BlundrIQ. BlundrIQ does not patch, recompile, or alter the engine. **If the
engine is ever modified, those modifications must be released under GPLv3 with
their corresponding source** — this is a hard gate on any future change to these
files.

## Corresponding Source (N2, conservative)

`stockfish-source.tar.gz` is self-hosted here (not a bare upstream link) and
contains the nmrugg/stockfish.js build repo (including `src/lite_nets.h`) + the
official-stockfish/Stockfish `sf_18` source + `BUILD-NOTES.md` (toolchain, the
Lite network mechanism, reproduce steps). It is linked from the Explore
view's footer and remains reachable for as long as the engine is distributed.

The exact NNUE evaluation network embedded by the Lite build,
`nn-9067e33176e8.nnue` (SHA-256
`9067e33176e8c5edb7aa8db6a3aedd012f84a1f39872e86357c6c2d0993f314d`), is
**physically included** in the archive under `networks/`
(`stockfish-corresponding-source/networks/nn-9067e33176e8.nnue`) — not merely
referenced by name — so the corresponding source is complete and self-contained,
with no network fetch required to reproduce the binary. The network is part of
Stockfish and is covered by the same GNU GPL v3 as the engine. The standard-build
networks `nn-c288c895ea92.nnue` and `nn-37f18f62d772.nnue` (pinned in
`src/evaluate.h`) are not embedded by the single-threaded Lite build and are not
included.

## Provenance note

`Copying.txt` is byte-identical between the npm binary distribution, the nmrugg
source repo, and the copy shipped here (SHA-256
`0b383d5a63da644f628d99c33976ea6487ed89aaa59f0b3257992deac1171e6b`). The upstream
`official-stockfish/Stockfish` `sf_18` tree carries its own `Copying.txt` (SHA-256
`3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`) — the same GNU
GPL v3, differing only in file formatting. `AUTHORS` is sourced from the upstream
nmrugg/stockfish.js + official-stockfish/Stockfish repositories (it is not in the
npm tarball, which ships `bin/` + `Copying.txt` + `README.md` + `index.js` +
`scripts/` + `package.json`).
