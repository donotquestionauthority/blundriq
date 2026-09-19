#!/usr/bin/env bash
# Installs the repo's git hooks into .git/hooks. Run once after cloning.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
command -v gitleaks >/dev/null || { echo "install gitleaks first (brew install gitleaks); CI pins 8.24.3, any >= 8.24 works"; exit 1; }
v="$(gitleaks version 2>/dev/null | tr -d 'v')"
printf '%s\n8.24.0\n' "$v" | sort -V | head -1 | grep -qx '8.24.0' || { echo "gitleaks $v is older than 8.24; upgrade it"; exit 1; }
for h in pre-commit pre-push; do
  cp "tools/hooks/$h" ".git/hooks/$h" && chmod +x ".git/hooks/$h"
done
echo "hooks installed: pre-commit (staged scan), pre-push (all outgoing commits)"
