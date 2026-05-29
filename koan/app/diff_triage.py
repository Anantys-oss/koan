"""Content-aware diff triage for PR reviews.

Classifies each file in a unified diff as NEEDS_REVIEW or trivial,
allowing the review pipeline to skip files that add no review value.
Complements the static ignore patterns (config-based globs/regexes)
with heuristic content analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Triage result
# ---------------------------------------------------------------------------


@dataclass
class TriagedFile:
    """A file that was triaged out of the review."""
    path: str
    reason: str


# ---------------------------------------------------------------------------
# Lockfile / generated file patterns
# ---------------------------------------------------------------------------

_LOCKFILE_NAMES = frozenset({
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Gemfile.lock",
    "Pipfile.lock",
    "poetry.lock",
    "composer.lock",
    "Cargo.lock",
    "go.sum",
    "flake.lock",
    "pdm.lock",
    "uv.lock",
    "bun.lockb",
    "packages.lock.json",
})

_GENERATED_PATTERNS = (
    re.compile(r"\.min\.(js|css)$"),
    re.compile(r"\.map$"),
    re.compile(r"\.snap$"),
    re.compile(r"dist/"),
    re.compile(r"vendor/"),
    re.compile(r"__generated__/"),
    re.compile(r"\.pb\.go$"),
    re.compile(r"_pb2\.py$"),
)


# ---------------------------------------------------------------------------
# Change analysis helpers
# ---------------------------------------------------------------------------

_HUNK_HEADER_RE = re.compile(r"^@@\s")
_ADDED_LINE_RE = re.compile(r"^\+(?!\+\+)")
_REMOVED_LINE_RE = re.compile(r"^-(?!--)")


def _extract_changed_lines(hunks_text: str) -> Tuple[List[str], List[str]]:
    """Extract added and removed lines from hunk text (excluding headers)."""
    added: List[str] = []
    removed: List[str] = []
    for line in hunks_text.splitlines():
        if _ADDED_LINE_RE.match(line):
            added.append(line[1:])
        elif _REMOVED_LINE_RE.match(line):
            removed.append(line[1:])
    return added, removed


def _is_whitespace_only(added: List[str], removed: List[str]) -> bool:
    """Check if changes are purely whitespace (blank lines, indentation)."""
    if not added and not removed:
        return True
    stripped_added = [line.strip() for line in added]
    stripped_removed = [line.strip() for line in removed]
    if sorted(stripped_added) == sorted(stripped_removed):
        return True
    all_blank_added = all(line.strip() == "" for line in added)
    all_blank_removed = all(line.strip() == "" for line in removed)
    if all_blank_added and all_blank_removed:
        return True
    return False


def _is_rename_only(block: str) -> bool:
    """Detect file renames with no content changes."""
    if "rename from " in block and "rename to " in block:
        has_hunks = bool(re.search(r"^@@\s", block, re.MULTILINE))
        if not has_hunks:
            return True
    return False


# ---------------------------------------------------------------------------
# Main triage function
# ---------------------------------------------------------------------------


def triage_diff_files(
    diff: str,
    config: dict,
) -> Tuple[str, List[TriagedFile]]:
    """Classify files in a unified diff and filter out trivial changes.

    Args:
        diff: Unified diff string (GitHub format).
        config: Triage configuration dict with keys:
            - enabled (bool): Master switch. If False, returns diff unchanged.
            - skip_lockfiles (bool): Skip lock/dependency files.
            - skip_generated (bool): Skip generated/minified files.
            - skip_whitespace_only (bool): Skip whitespace-only changes.
            - skip_renames (bool): Skip file renames with no content change.

    Returns:
        (filtered_diff, triaged_files) tuple. filtered_diff has trivial file
        blocks removed. triaged_files lists what was skipped and why.
    """
    if not diff or not config.get("enabled", False):
        return diff, []

    skip_lockfiles = config.get("skip_lockfiles", True)
    skip_generated = config.get("skip_generated", True)
    skip_whitespace = config.get("skip_whitespace_only", True)
    skip_renames = config.get("skip_renames", True)

    raw_blocks = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    if len(raw_blocks) <= 1:
        return diff, []

    kept_blocks: List[str] = []
    triaged: List[TriagedFile] = []

    for block in raw_blocks:
        if not block.strip():
            continue

        if not block.startswith("diff --git "):
            kept_blocks.append(block)
            continue

        first_line = block.split("\n", 1)[0]
        m = re.search(r" b/(.+)$", first_line)
        path = m.group(1).strip() if m else ""

        reason = _classify_file(
            path, block, skip_lockfiles, skip_generated,
            skip_whitespace, skip_renames,
        )

        if reason:
            triaged.append(TriagedFile(path=path, reason=reason))
        else:
            kept_blocks.append(block)

    if not triaged:
        return diff, []

    return "\n".join(kept_blocks), triaged


def _classify_file(
    path: str,
    block: str,
    skip_lockfiles: bool,
    skip_generated: bool,
    skip_whitespace: bool,
    skip_renames: bool,
) -> str:
    """Return a skip reason if the file is trivial, or empty string to keep."""
    import os

    basename = os.path.basename(path)

    if skip_lockfiles and basename in _LOCKFILE_NAMES:
        return "lockfile"

    if skip_generated:
        for pat in _GENERATED_PATTERNS:
            if pat.search(path):
                return "generated"

    if skip_renames and _is_rename_only(block):
        return "rename-only"

    if skip_whitespace:
        hunk_sections = re.split(r"(?=^@@)", block, flags=re.MULTILINE)
        hunks_text = "".join(hunk_sections[1:]) if len(hunk_sections) > 1 else ""
        if hunks_text:
            added, removed = _extract_changed_lines(hunks_text)
            if _is_whitespace_only(added, removed):
                return "whitespace-only"

    return ""
