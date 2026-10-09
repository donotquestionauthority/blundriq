# Stockfish (in-browser engine) — source, version, and modification status

BlundrIQ ships the Stockfish chess engine to the browser as WebAssembly for the
**Explore** view. This conveys the engine binary to the user,
which is GPLv3 conveyance; the files in this directory exist to satisfy that.

## What is shipped

| File | What it is |
|---|---|
| `stockfish-19-lite-single.js` | Engine worker glue (UCI over `postMessage`). |
| `stockfish-19-lite-single.wasm` | Stockfish 19 Lite, single-threaded, WebAssembly (~1.8 MB). |
| `Copying.txt` | Full GNU GPL v3 license text (upstream, unmodified). |
| `AUTHORS` | Stockfish authors / contributors (upstream, `sf_19`). |
| `NNUE-NOTICE.txt` | NNUE network attribution + license. |
| `stockfish-source.tar.gz` | **Corresponding Source** for the exact binary above. |

## Exact version (pinned)

- Prebuilt binary: **npm `stockfish@19.0.0`** (author Nathan Rugg; sponsored by
  Chess.com) — the same in-browser build Chess.com uses. SHA-256 of the
  conveyed WASM: `57ac2d72312aba346760e3f173f687a8c211208e97a87268436f7f0e10bb5387`;
  of the glue: `d3344124ab067fb0b90ee77873bb8e9fbf5fc01bc525fe714b0f942581e889e6`.
- Flavor: **lite, single-threaded** (`node build.js --single-threaded --lite -f`).
- WASM wrapper: **nmrugg/stockfish.js** tag `v19.0.0` (commit `9cb3e50`),
  Emscripten **3.1.7**. Its `src/` is the Stockfish 19 engine source with the
  wrapper's changes merged in, the Lite network's feature set included.
- Engine source: **official-stockfish/Stockfish** tag **`sf_19`** (upstream
  commit `edb0d9d`).
- Lite network lineage: **sscg13/Stockfish** branch `sf19-1mb` (commit
  `f5e5c63`), credited by the wrapper for the Stockfish 19 lite nets.
- NNUE network embedded by this Lite build: **`nn-61e7af4bb97d.nnue`**. The
  wrapper's `src/evaluate.h` names it as `EvalFileLiteName`, aliased to the
  default under `USE_LITE_NET` for Lite builds. The standard build's network,
  `nn-1a298aa575a0.nnue`, is **not** embedded by the conveyed binary.

## UNMODIFIED

The engine binary is the upstream prebuilt artifact, **byte-unmodified** by
BlundrIQ. BlundrIQ does not patch, recompile, or alter the engine. **If the
engine is ever modified, those modifications must be released under GPLv3 with
their corresponding source** — this is a hard gate on any future change to these
files.

## Corresponding Source

`stockfish-source.tar.gz` is self-hosted here (not a bare upstream link) and
contains the nmrugg/stockfish.js build repo at `v19.0.0` (its `src/` tree,
`build.js`, the Emscripten glue) + the official-stockfish/Stockfish `sf_19`
source + `BUILD-NOTES.md` (toolchain, the Lite network mechanism, reproduce
steps). It is linked from the Explore view's footer and remains reachable for
as long as the engine is distributed.

The exact NNUE evaluation network embedded by the Lite build,
`nn-61e7af4bb97d.nnue` (1,166,381 bytes, SHA-256
`61e7af4bb97d51eeeb25d322916f86513b5cd3a827ce189c98c6e31946f99e5b`), is
**physically included** in the archive under `networks/`
(`stockfish-corresponding-source/networks/nn-61e7af4bb97d.nnue`) — not merely
referenced by name — so the corresponding source is complete and self-contained,
with no network fetch required to reproduce the binary. The network is part of
Stockfish and is covered by the same GNU GPL v3 as the engine. The standard-build
network `nn-1a298aa575a0.nnue` is not embedded by the single-threaded Lite build
and is not included.

## Provenance note

`Copying.txt` is byte-identical between the npm binary distribution, the nmrugg
source repo, and the copy shipped here (SHA-256
`0b383d5a63da644f628d99c33976ea6487ed89aaa59f0b3257992deac1171e6b`). The upstream
`official-stockfish/Stockfish` `sf_19` tree carries its own `Copying.txt` (SHA-256
`3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`) — the same GNU
GPL v3, differing only in file formatting. `AUTHORS` is the upstream
`official-stockfish/Stockfish` `sf_19` file (it is not in the npm tarball, which
ships `bin/` + `Copying.txt` + `README.md` + `index.js` + `scripts/` +
`package.json`).
