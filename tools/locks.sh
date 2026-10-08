#!/usr/bin/env bash
# The three lock files and the one command that writes each.
#   bash tools/locks.sh          re-resolve after a pyproject.toml / build.in change, keeping every other pin
#   bash tools/locks.sh upgrade  move every pin to the newest release (a deliberate upgrade)
#   bash tools/locks.sh check    fail if any lock is not what its command writes (CI)
# Needs uv at the version CI uses (below): another version may write the same pins differently.
# Installs never use uv; they read the locks with pip (tools/install.sh).
# `check` needs the package index: a pinned release that is yanked or gains a file also reads as
# out of date, and the fix is the same command.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
UV_VERSION=0.11.32
mode="${1:-compile}"
case "$mode" in compile | upgrade | check) ;; *) echo "usage: bash tools/locks.sh [upgrade|check]"; exit 2 ;; esac
have="$(uv --version | cut -d' ' -f2)"
[ "$have" = "$UV_VERSION" ] || { echo "uv $have found; the locks are written with uv $UV_VERSION (pip install uv==$UV_VERSION)"; exit 2; }

compile() { # <source> <lock> [extra args]
  local src="$1" out="$2"
  shift 2
  uv pip compile "$src" --python-version 3.14 --universal --generate-hashes --quiet \
    --custom-compile-command "bash tools/locks.sh" "$@" -o "$out"
}

status=0
# The dev lock is resolved with the runtime lock as constraints, so what CI tests (dev) and what
# deploys (runtime) are the same versions of every package they share.
for spec in "pyproject.toml requirements.lock" "pyproject.toml requirements-dev.lock --extra dev -c requirements.lock" "build.in build.lock"; do
  read -r src lock extra <<<"$spec" || true
  # shellcheck disable=SC2086
  case "$mode" in
    compile) compile "$src" "$lock" $extra ;;
    upgrade) compile "$src" "$lock" $extra --upgrade ;;
    check)
      tmp="$(mktemp)"
      cp "$lock" "$tmp"
      compile "$src" "$tmp" $extra
      if cmp -s "$lock" "$tmp"; then echo "$lock: current"; else echo "$lock: out of date (run bash tools/locks.sh)"; status=1; fi
      rm -f "$tmp"
      ;;
  esac
done
exit "$status"
