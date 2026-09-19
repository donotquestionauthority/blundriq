#!/usr/bin/env bash
# Installs the repo's git hooks into .git/hooks. Run once after cloning.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
for h in pre-commit pre-push; do
  cp "tools/hooks/$h" ".git/hooks/$h" && chmod +x ".git/hooks/$h"
done
echo "hooks installed: pre-commit (staged scan), pre-push (all outgoing commits)"
