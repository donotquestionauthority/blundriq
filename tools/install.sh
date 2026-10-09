#!/usr/bin/env bash
# Installs the project into the environment of the given Python, from the locks only.
#   bash tools/install.sh <python> runtime   the app and the pipeline (Render, the hourly job)
#   bash tools/install.sh <python> dev       plus the [dev] tools, the project editable (local work, CI)
# Three steps, in this order: the installer and the build backend (build.lock), then the
# dependencies, then the project itself. Build isolation is off for the last two, so nothing
# a build needs is fetched outside the locks: the sdist in the lock and the project are built
# with the setuptools step one installed. Every file pip downloads is checked against its hash.
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: bash tools/install.sh <python> runtime|dev"; exit 2; }
# The interpreter as the caller named it: a bare name stays a PATH lookup, a path is made absolute
# before the cd below.
case "$1" in */*) py="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")" ;; *) py="$1" ;; esac
cd "$(dirname "$0")/.."
# A virtualenv made by uv has no pip; give it the bundled one before the hashed pip replaces it.
"$py" -m pip --version >/dev/null 2>&1 || "$py" -m ensurepip --upgrade >/dev/null
case "$2" in
  runtime) lock=requirements.lock; target=(.) ;;
  dev) lock=requirements-dev.lock; target=(-e .) ;;
  *) echo "usage: bash tools/install.sh <python> runtime|dev"; exit 2 ;;
esac
"$py" -m pip install --quiet --require-hashes -r build.lock
"$py" -m pip install --quiet --require-hashes --no-build-isolation -r "$lock"
"$py" -m pip install --quiet --no-deps --no-build-isolation "${target[@]}"
