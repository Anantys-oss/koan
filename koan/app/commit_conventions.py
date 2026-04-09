"""Kōan — Project commit convention detection and parsing.

Detects commit message conventions from a target project's CLAUDE.md or
recent commit history, and provides helpers for parsing convention-aware
commit subjects from Claude output.
"""

import re
import subprocess
from pathlib import Path
from typing import Optional


# Section headings that likely contain commit convention guidance.
_COMMIT_HEADING_KEYWORDS = re.compile(
    r"commit|convention|message.format|git.style|changelog|trailer",
    re.IGNORECASE,
)

# Matches the COMMIT_SUBJECT marker in Claude output.
_COMMIT_SUBJECT_RE = re.compile(r"^COMMIT_SUBJECT:\s*(.+)$", re.MULTILINE)

# Conventional commit pattern (reused from pr_quality.py).
_CONVENTIONAL_RE = re.compile(
    r"^[a-f0-9]+\s+(?:feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(?:\([^)]+\))?!?:\s"
)

# Ticket/case reference patterns.
_TICKET_RE = re.compile(
    r"^[a-f0-9]+\s+.*?(?:Case\s+)?([A-Z][A-Z0-9_]+-\d+)", re.IGNORECASE
)

# Matches file references in markdown: backtick-quoted paths or bare paths
# ending in common extensions.  Used to resolve instruction file references
# found inside commit-related CLAUDE.md sections.
_FILE_REF_RE = re.compile(
    r"`([^`]+\.(?:md|txt|instructions\.md))`"
)

_MAX_GUIDANCE_CHARS = 4000
_MAX_REFERENCED_FILE_CHARS = 3000
_MAX_SUBJECT_CHARS = 150


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_project_commit_guidance(project_path: str, base_ref: str = "HEAD") -> str:
    """Return commit convention guidance for the project.

    Checks two sources in priority order:
      1. CLAUDE.md sections related to commit conventions (explicit rules).
      2. Heuristic analysis of recent commit history (implicit patterns).

    Returns a formatted guidance string for inclusion in prompts,
    or empty string if no conventions detected.
    """
    # Primary: explicit guidance from CLAUDE.md
    guidance = _extract_commit_sections_from_claude_md(project_path)
    if guidance:
        return (
            "## Project Commit Conventions\n\n"
            "This project has specific commit message rules. "
            "You MUST follow them:\n\n"
            f"{guidance}"
        )

    # Fallback: infer from commit history
    inferred = _infer_commit_style_from_history(project_path, base_ref)
    if inferred:
        return (
            "## Project Commit Conventions (inferred from history)\n\n"
            "No explicit rules were found, but this project follows a "
            "consistent commit message pattern. Match it:\n\n"
            f"{inferred}"
        )

    return ""


def parse_commit_subject(claude_output: str) -> Optional[str]:
    """Extract a COMMIT_SUBJECT line from Claude's output.

    Looks for a line matching ``COMMIT_SUBJECT: <subject text>``.
    If multiple matches exist, the last one wins (Claude may revise).

    Returns the subject text, or None if not found or invalid.
    """
    matches = _COMMIT_SUBJECT_RE.findall(claude_output)
    if not matches:
        return None

    subject = matches[-1].strip()

    # Validate: non-empty, single line, reasonable length
    if not subject or "\n" in subject or len(subject) > _MAX_SUBJECT_CHARS:
        return None

    return subject


def strip_commit_subject_line(text: str) -> str:
    """Remove COMMIT_SUBJECT: lines from text.

    Used to clean the change summary before using it as a commit body,
    so the marker line doesn't appear in the final commit message.
    """
    return _COMMIT_SUBJECT_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _extract_commit_sections_from_claude_md(project_path: str) -> str:
    """Read CLAUDE.md and extract commit-related sections.

    Searches for markdown headings whose text contains keywords like
    'commit', 'convention', 'message format', 'git style', 'changelog',
    or 'trailer'.  Returns the matching sections' text, truncated to
    _MAX_GUIDANCE_CHARS.

    When a matched section references an instruction file (e.g.,
    ``.github/instructions/commit-messages.instructions.md``), the
    referenced file is read and appended so the full conventions are
    available in the prompt.
    """
    project = Path(project_path)
    claude_md = project / "CLAUDE.md"
    try:
        content = claude_md.read_text(errors="replace")
    except (FileNotFoundError, OSError):
        return ""

    if not content.strip():
        return ""

    # Split into sections by heading lines (## or ###)
    sections: list[str] = []
    current_heading = ""
    current_body: list[str] = []

    for line in content.splitlines():
        if re.match(r"^#{1,4}\s+", line):
            # Flush previous section if its heading matched
            if current_heading and _COMMIT_HEADING_KEYWORDS.search(current_heading):
                section_text = current_heading + "\n" + "\n".join(current_body)
                sections.append(section_text.strip())
            current_heading = line
            current_body = []
        else:
            current_body.append(line)

    # Flush last section
    if current_heading and _COMMIT_HEADING_KEYWORDS.search(current_heading):
        section_text = current_heading + "\n" + "\n".join(current_body)
        sections.append(section_text.strip())

    if not sections:
        return ""

    # Resolve file references found inside the extracted sections.
    # When CLAUDE.md says "follow `.github/instructions/commit.md`",
    # we read that file and include it so Claude has the full spec.
    combined = "\n\n".join(sections)
    referenced_content = _resolve_file_references(combined, project)
    if referenced_content:
        combined = combined + "\n\n" + referenced_content

    if len(combined) > _MAX_GUIDANCE_CHARS:
        combined = combined[:_MAX_GUIDANCE_CHARS] + "\n\n(truncated)"
    return combined


def _resolve_file_references(text: str, project: Path) -> str:
    """Read files referenced in the text and return their contents.

    Looks for backtick-quoted file paths (e.g.,
    ``.github/instructions/commit-messages.instructions.md``) that exist
    relative to *project*.  Returns the concatenated contents of all
    resolved files, capped at _MAX_REFERENCED_FILE_CHARS.
    """
    refs = _FILE_REF_RE.findall(text)
    if not refs:
        return ""

    parts: list[str] = []
    total_len = 0

    for ref_path in refs:
        full_path = project / ref_path
        try:
            ref_content = full_path.read_text(errors="replace").strip()
        except (FileNotFoundError, OSError):
            continue

        if not ref_content:
            continue

        # Cap individual file contribution
        remaining = _MAX_REFERENCED_FILE_CHARS - total_len
        if remaining <= 0:
            break

        if len(ref_content) > remaining:
            ref_content = ref_content[:remaining] + "\n\n(truncated)"

        parts.append(
            f"### Referenced: {ref_path}\n\n{ref_content}"
        )
        total_len += len(ref_content)

    return "\n\n".join(parts)


def _infer_commit_style_from_history(project_path: str, base_ref: str) -> str:
    """Analyze recent commits to describe the commit style.

    Scans the last 20 commits on *base_ref* and detects dominant patterns:
      - Conventional commits (feat:, fix:, etc.)
      - Ticket/case references (JIRA-123, PROJECT-456)

    Returns a human-readable description with examples,
    or empty string if no clear pattern found (>50% threshold).
    """
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-20", base_ref],
            capture_output=True, text=True, cwd=project_path,
            timeout=10,
        )
        if result.returncode != 0:
            return ""
    except (subprocess.TimeoutExpired, OSError):
        return ""

    lines = result.stdout.strip().splitlines()
    if not lines:
        return ""

    # Check conventional commits
    conv_matches = [l for l in lines if _CONVENTIONAL_RE.match(l)]
    if len(conv_matches) > len(lines) * 0.5:
        # Extract just the message part (strip hash)
        examples = []
        for line in conv_matches[:5]:
            msg = line.split(" ", 1)[1] if " " in line else line
            examples.append(f"  - {msg}")
        return (
            "This project uses **conventional commits** format.\n"
            "Examples from recent history:\n"
            + "\n".join(examples)
        )

    # Check ticket/case references
    ticket_matches = [l for l in lines if _TICKET_RE.match(l)]
    if len(ticket_matches) > len(lines) * 0.5:
        examples = []
        for line in ticket_matches[:5]:
            msg = line.split(" ", 1)[1] if " " in line else line
            examples.append(f"  - {msg}")
        return (
            "This project references ticket/case IDs in commit messages.\n"
            "Examples from recent history:\n"
            + "\n".join(examples)
        )

    return ""
