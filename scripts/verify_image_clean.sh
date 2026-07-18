#!/usr/bin/env bash
# Fail if koan's own repo-dev tooling shipped inside a built image.
#
# Regression guard for issue #2383 (packaging), complementing #2379's
# runtime-cwd guard: even a future cwd=KOAN_ROOT code path then has nothing
# to auto-load. Scoped to $ROOT (KOAN_ROOT=/app) so the CLI's legitimate
# /home/koan/.claude runtime-state dir is NOT flagged.
#
# Usage: verify_image_clean.sh <image-ref> [root]
set -euo pipefail

IMAGE="${1:?usage: verify_image_clean.sh <image-ref> [root]}"
ROOT="${2:-/app}"

# Throwaway container with an overridden entrypoint — never launches koan.
offenders="$(docker run --rm --entrypoint sh "$IMAGE" -c \
  "find '$ROOT' \\( -name CLAUDE.md -o -name AGENTS.md -o -name .claude -o -name KOAN.md \\) -print 2>/dev/null" \
  || true)"

if [ -n "$offenders" ]; then
  echo "::error::Repo dev tooling leaked into the image under $ROOT (#2383):"
  printf '%s\n' "$offenders"
  exit 1
fi

echo "✅ image clean: no CLAUDE.md / AGENTS.md / .claude / KOAN.md under $ROOT"
