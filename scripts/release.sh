#!/usr/bin/env bash
# Release orchestrator: tag + stable branch fast-forward + GitHub release
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

err() { echo "✗ $*" >&2; exit 1; }
ok()  { echo "✓ $*"; }
ask() { local p="$1"; local def="${2:-}"; local r; read -r -p "$p" r; echo "${r:-$def}"; }

# --- Preflight ---------------------------------------------------------------
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || err "must be on main"
[ -z "$(git status --porcelain)" ] || err "working tree not clean"
command -v gh >/dev/null || err "gh CLI required"
gh auth status >/dev/null 2>&1 || err "gh not authenticated"

ok "fetching origin"
git fetch origin --tags --quiet
LOCAL=$(git rev-parse main)
REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] || err "main not in sync with origin/main (pull/push first)"

# --- Tests -------------------------------------------------------------------
echo "→ running full test suite (must be 100% pass)"
make test-strict || err "tests failed — release aborted"

# --- Version ----------------------------------------------------------------
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0")
# Default bump: v0.NN -> v0.(NN+1), or vX.Y.Z -> vX.Y.(Z+1)
if [[ "$LAST_TAG" =~ ^v([0-9]+)\.([0-9]+)$ ]]; then
    DEFAULT="v${BASH_REMATCH[1]}.$((BASH_REMATCH[2]+1))"
elif [[ "$LAST_TAG" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    DEFAULT="v${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((BASH_REMATCH[3]+1))"
else
    DEFAULT="v0.1"
fi

echo ""
echo "Last tag: $LAST_TAG"
VERSION=$(ask "New version [$DEFAULT]: " "$DEFAULT")
[[ "$VERSION" =~ ^v[0-9]+(\.[0-9]+){1,2}$ ]] || err "invalid version: $VERSION (expected vX.Y or vX.Y.Z)"
git rev-parse "$VERSION" >/dev/null 2>&1 && err "tag $VERSION already exists"

# --- Changelog ---------------------------------------------------------------
RANGE="${LAST_TAG}..HEAD"
RAW_LOG=$(git log "$RANGE" --pretty=format:"- %s (%h)")
[ -n "$RAW_LOG" ] || err "no commits since $LAST_TAG"

CHANGELOG_FILE=$(mktemp -t koan-changelog.XXXXXX)
trap 'rm -f "$CHANGELOG_FILE"' EXIT

echo ""
echo "→ generating changelog with Claude (fallback: raw git log)"
if command -v claude >/dev/null 2>&1; then
    PROMPT="Generate a concise release changelog in markdown for Kōan $VERSION from the commits below. Group by category (Features, Fixes, Refactors, Docs, Chore). Skip trivial chores. Keep it readable. Output markdown only, no preamble.

Commits:
$RAW_LOG"
    if printf '%s' "$PROMPT" | claude --print --model claude-haiku-4-5-20251001 > "$CHANGELOG_FILE" 2>/dev/null && [ -s "$CHANGELOG_FILE" ]; then
        ok "changelog generated via Claude"
    else
        echo "## Changes since $LAST_TAG" > "$CHANGELOG_FILE"
        echo "" >> "$CHANGELOG_FILE"
        echo "$RAW_LOG" >> "$CHANGELOG_FILE"
        ok "changelog: raw git log (Claude fallback)"
    fi
else
    echo "## Changes since $LAST_TAG" > "$CHANGELOG_FILE"
    echo "" >> "$CHANGELOG_FILE"
    echo "$RAW_LOG" >> "$CHANGELOG_FILE"
fi

echo ""
echo "───── CHANGELOG ─────"
cat "$CHANGELOG_FILE"
echo "─────────────────────"
echo ""
CONFIRM=$(ask "Edit changelog before release? [y/N]: " "N")
if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
    "${EDITOR:-vi}" "$CHANGELOG_FILE"
fi

GO=$(ask "Release $VERSION now? [y/N]: " "N")
[[ "$GO" =~ ^[Yy]$ ]] || err "aborted by user"

# --- Tag + push --------------------------------------------------------------
ok "creating tag $VERSION"
git tag -a "$VERSION" -F "$CHANGELOG_FILE"
git push origin "$VERSION"

# --- stable branch -----------------------------------------------------------
if git ls-remote --exit-code --heads origin stable >/dev/null 2>&1; then
    ok "fast-forwarding stable → $VERSION"
    git fetch origin stable:stable 2>/dev/null || git branch -f stable origin/stable
    git branch -f stable "$VERSION"
    git push origin stable
else
    ok "creating stable branch at $VERSION"
    git branch -f stable "$VERSION"
    git push -u origin stable
fi

# --- GitHub release ----------------------------------------------------------
ok "publishing GitHub release"
gh release create "$VERSION" \
    --title "Kōan $VERSION" \
    --notes-file "$CHANGELOG_FILE" \
    --latest

ok "🎉 released $VERSION"
echo ""
gh release view "$VERSION" --web 2>/dev/null || true
