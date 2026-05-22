"""Diff compression for large PRs.

Parses a unified diff into per-file hunks, sorts by language priority,
and fits as many hunks as possible within a configurable token budget.
Skipped files are surfaced so the review prompt can note partial coverage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


# ---------------------------------------------------------------------------
# Language priority (higher = review first)
# ---------------------------------------------------------------------------

LANGUAGE_PRIORITY: dict[str, int] = {
    ".py": 10,
    ".ts": 10,
    ".tsx": 10,
    ".js": 10,
    ".jsx": 10,
    ".go": 10,
    ".rs": 10,
    ".java": 10,
    ".kt": 10,
    ".swift": 10,
    ".rb": 8,
    ".php": 8,
    ".c": 8,
    ".cpp": 8,
    ".h": 8,
    ".sh": 6,
    ".bash": 6,
    ".zsh": 6,
    ".sql": 6,
    ".html": 4,
    ".css": 4,
    ".scss": 4,
    ".md": 3,
    ".rst": 3,
    ".txt": 2,
    ".yaml": 2,
    ".yml": 2,
    ".toml": 2,
    ".ini": 2,
    ".cfg": 2,
    ".json": 1,
    ".xml": 1,
    ".lock": 0,
    ".sum": 0,
}


def detect_language(path: str) -> str:
    """Return the file extension (e.g. '.py') from a path, or '' if none."""
    return Path(path).suffix.lower()


def _language_priority(path: str) -> int:
    """Return priority score for a file path (higher = more important)."""
    return LANGUAGE_PRIORITY.get(detect_language(path), 5)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FileDiff:
    """Structured representation of one file's diff block."""

    path: str
    header: str  # Everything up to (but not including) the first hunk
    hunks: List[str] = field(default_factory=list)
    is_binary: bool = False

    def full_text(self) -> str:
        """Reconstruct the full file diff block."""
        return self.header + "".join(self.hunks)

    def token_estimate(self) -> int:
        return estimate_tokens(self.full_text())


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Approximate token count using character-based heuristic (chars / 3.5).

    Real tokenizers average ~3.5 chars/token for code.  Using 3.5 instead of 4
    is deliberately conservative: it slightly overestimates token counts, which
    means we may include fewer files but are less likely to blow the context
    window by underestimating.
    """
    return int(len(text) / 3.5)


# ---------------------------------------------------------------------------
# Diff parser
# ---------------------------------------------------------------------------

# Matches the start of a new file block in a unified diff.
_FILE_HEADER_RE = re.compile(r"^diff --git ", re.MULTILINE)


def parse_diff_hunks(raw_diff: str) -> List[FileDiff]:
    """Parse a unified diff string into a list of FileDiff objects.

    Each FileDiff contains:
    - path: the b/ path of the changed file
    - header: diff --git header + index/mode lines + --- +++ lines
    - hunks: individual @@ hunk blocks
    - is_binary: True when a "Binary files" line is detected
    """
    if not raw_diff.strip():
        return []

    # Split the diff at each "diff --git" boundary.  The first element before
    # the first boundary is discarded (empty or preamble).
    parts = _FILE_HEADER_RE.split(raw_diff)
    results: List[FileDiff] = []

    for part in parts:
        if not part.strip():
            continue

        # Restore the prefix that was consumed by the split.
        block = "diff --git " + part

        # Extract the file path from the first line.
        first_line = block.split("\n", 1)[0]
        # "diff --git a/foo/bar.py b/foo/bar.py" — take the b/ side
        m = re.search(r" b/(.+)$", first_line)
        path = m.group(1).strip() if m else first_line.split()[-1]

        is_binary = bool(re.search(r"^Binary files ", block, re.MULTILINE))

        # Split into header and hunks.  The header is everything before the
        # first @@ line; each hunk starts at @@ and runs to the next @@ or EOF.
        hunk_split = re.split(r"(?=^@@)", block, flags=re.MULTILINE)
        header = hunk_split[0]
        hunks = hunk_split[1:]  # may be empty for binary / mode-only files

        results.append(
            FileDiff(path=path, header=header, hunks=hunks, is_binary=is_binary)
        )

    return results


# ---------------------------------------------------------------------------
# Compressed diff result
# ---------------------------------------------------------------------------


@dataclass
class CompressedDiff:
    """Result of compressing a diff to fit within a token budget."""

    diff_text: str
    skipped_files: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Compression function
# ---------------------------------------------------------------------------


def compress_diff(raw_diff: str, token_budget: int = 80_000) -> CompressedDiff:
    """Compress a unified diff to fit within *token_budget* tokens.

    Algorithm:
    1. Parse into FileDiff objects.
    2. Sort by (language_priority desc, file_size asc).
    3. Greedily include whole files until the budget is exhausted.
    4. For each file that doesn't fit whole: deduct header tokens first, then
       greedily include hunks within the remaining hunk budget.
    5. Files that don't fit at all are recorded in skipped_files.
    6. Safety: if the output would be completely empty (single file larger than
       the budget), force-include the first hunk so the diff is never blank.

    Special cases:
    - Empty diff → CompressedDiff(diff_text="", skipped_files=[])
    - Binary files → include just the header (0 tokens counted); never skipped.
    - Single massive file → include its first hunk; note as "<path> (partial)".
    """
    if not raw_diff.strip():
        return CompressedDiff(diff_text="", skipped_files=[])

    file_diffs = parse_diff_hunks(raw_diff)
    if not file_diffs:
        return CompressedDiff(diff_text=raw_diff, skipped_files=[])

    # Sort: higher priority first; ties broken by smaller file first.
    sorted_diffs = sorted(
        file_diffs,
        key=lambda fd: (-_language_priority(fd.path), fd.token_estimate()),
    )

    included_blocks: list[str] = []
    skipped: list[str] = []
    remaining_budget = token_budget

    for fd in sorted_diffs:
        if fd.is_binary:
            # Include binary file header (informational, near-zero tokens).
            included_blocks.append(fd.header)
            continue

        if not fd.hunks:
            # Mode-only change (e.g. chmod) — no content diff, include header.
            included_blocks.append(fd.header)
            continue

        file_tokens = fd.token_estimate()

        if file_tokens <= remaining_budget:
            # Whole file fits.
            included_blocks.append(fd.full_text())
            remaining_budget -= file_tokens
        elif remaining_budget > 0:
            # Try to fit individual hunks within whatever budget remains.
            # Deduct header cost first (the header is always emitted with hunks).
            header_tokens = estimate_tokens(fd.header)
            hunk_budget = max(0, remaining_budget - header_tokens)

            partial_hunks: list[str] = []
            for hunk in fd.hunks:
                hunk_cost = estimate_tokens(hunk)
                if hunk_cost <= hunk_budget:
                    partial_hunks.append(hunk)
                    hunk_budget -= hunk_cost

            if partial_hunks:
                included_blocks.append(fd.header + "".join(partial_hunks))
                remaining_budget -= header_tokens + sum(
                    estimate_tokens(h) for h in partial_hunks
                )
                if len(partial_hunks) < len(fd.hunks):
                    skipped.append(f"{fd.path} (partial)")
            else:
                skipped.append(fd.path)
        else:
            # Budget exhausted — skip entirely.
            skipped.append(fd.path)

    # Safety: never return an empty diff when there are non-binary hunks.
    # Force-include the first hunk of the first non-binary file.
    non_binary = [fd for fd in sorted_diffs if not fd.is_binary and fd.hunks]
    if not "".join(included_blocks).strip() and non_binary:
        fd = non_binary[0]
        included_blocks = [fd.header + fd.hunks[0]]
        if len(fd.hunks) > 1:
            skipped = [f"{fd.path} (partial)"] + [
                s for s in skipped if s not in (fd.path, f"{fd.path} (partial)")
            ]

    return CompressedDiff(diff_text="".join(included_blocks), skipped_files=skipped)
