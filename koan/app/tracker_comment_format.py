"""Provider-aware comment formatting for tracker updates."""

from __future__ import annotations

import re
from typing import Dict, List, Optional


_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+")
_ORDERED_RE = re.compile(r"^\s*\d+\.\s+")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
# GitHub collapsible code blocks (<details>/<summary>) render as literal text on
# Jira, so flatten them: drop the <details> wrappers and turn the summary label
# into a plain "Label:" line above the (always-visible) code.
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.IGNORECASE)
_DETAILS_TAG_RE = re.compile(r"</?details\s*>", re.IGNORECASE)


def _parse_markdown_sections(markdown: str) -> Dict[str, List[str]]:
    """Parse ``##``-style markdown sections into lowercase section keys."""
    sections: Dict[str, List[str]] = {}
    current = "__root__"
    sections[current] = []
    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _collect_section_lines(sections: Dict[str, List[str]], *keys: str) -> List[str]:
    lines: List[str] = []
    for key in keys:
        lines.extend(sections.get(key, []))
    return lines


def _lines_to_bullets(lines: List[str]) -> List[str]:
    bullets: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _BULLET_RE.match(stripped):
            bullets.append(_BULLET_RE.sub("", stripped, count=1).strip())
            continue
        if _ORDERED_RE.match(stripped):
            bullets.append(_ORDERED_RE.sub("", stripped, count=1).strip())
    return bullets


def _first_nonempty_line(lines: List[str]) -> str:
    for line in lines:
        if line.strip():
            return line.strip()
    return ""


def _strip_markdown_for_jira(text: str) -> str:
    """Make markdown text human-friendly for Jira plain ADF paragraphs."""
    if not text:
        return ""

    out: List[str] = []
    in_fence = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(f"    {line}")
            continue

        line = _SUMMARY_RE.sub(lambda m: f"{m.group(1).strip()}:", line)
        line = _DETAILS_TAG_RE.sub("", line)
        if not line.strip():
            out.append("")
            continue

        line = _HEADING_RE.sub("", line)
        line = _LINK_RE.sub(r"\1 (\2)", line)
        line = _INLINE_CODE_RE.sub(r"\1", line)
        line = line.replace("**", "").replace("__", "")
        line = _ORDERED_RE.sub("- ", line)
        line = _BULLET_RE.sub("- ", line)
        line = re.sub(r"^\s*---+\s*$", "", line)
        out.append(line)

    # Collapse excessive blank lines, preserve section spacing.
    collapsed: List[str] = []
    blank = 0
    for line in out:
        if line.strip():
            blank = 0
            collapsed.append(line)
            continue
        blank += 1
        if blank <= 1:
            collapsed.append("")
    return "\n".join(collapsed).strip()


def build_pr_comment_success(
    provider: str,
    pr_url: str,
    pr_title: str,
    pr_body: str,
    skill_name: str = "",
    base_branch: Optional[str] = None,
) -> str:
    """Build a mission-completion comment when draft PR creation succeeds."""
    sections = _parse_markdown_sections(pr_body)
    what_bullets = _lines_to_bullets(
        _collect_section_lines(sections, "summary", "changes"),
    )
    how_bullets = _lines_to_bullets(_collect_section_lines(sections, "how"))
    why_text = _first_nonempty_line(_collect_section_lines(sections, "why"))
    testing_bullets = _lines_to_bullets(_collect_section_lines(sections, "testing"))
    mission = f"/{skill_name}" if skill_name else "(unknown)"
    target_branch = (base_branch or "").strip()

    if provider == "jira":
        lines: List[str] = [
            "Koan update: Draft pull request created.",
            "",
            f"Mission: {mission}",
            f"Pull request: {pr_url}",
        ]
        if pr_title:
            lines.append(f"PR title: {pr_title}")
        if target_branch:
            lines.append(f"Target branch: {target_branch}")
        if what_bullets:
            lines.extend(["", "What changed:"])
            lines.extend(f"- {item}" for item in what_bullets[:8])
        if why_text:
            lines.extend(["", f"Why: {why_text}"])
        if how_bullets:
            lines.extend(["", "How it was implemented:"])
            lines.extend(f"- {item}" for item in how_bullets[:8])
        if testing_bullets:
            lines.extend(["", "Validation:"])
            lines.extend(f"- {item}" for item in testing_bullets[:8])
        lines.extend(["", "Next:", "- Review the draft PR and merge when ready."])
        return "\n".join(lines)

    # GitHub / generic markdown-capable trackers.
    lines = [
        "## Draft PR Created",
        "",
        f"- Mission: `{mission}`",
        f"- PR: {pr_url}",
    ]
    if target_branch:
        lines.append(f"- Target branch: `{target_branch}`")
    if what_bullets:
        lines.extend(["", "### What"])
        lines.extend(f"- {item}" for item in what_bullets[:8])
    if why_text:
        lines.extend(["", "### Why", why_text])
    if how_bullets:
        lines.extend(["", "### How"])
        lines.extend(f"- {item}" for item in how_bullets[:8])
    if testing_bullets:
        lines.extend(["", "### Validation"])
        lines.extend(f"- {item}" for item in testing_bullets[:8])
    return "\n".join(lines)


def build_pr_comment_failure(
    provider: str,
    reason: str,
    branch: str = "",
    base_branch: Optional[str] = None,
    skill_name: str = "",
) -> str:
    """Build a tracker comment when draft PR creation fails."""
    mission = f"/{skill_name}" if skill_name else "(unknown)"
    target_branch = (base_branch or "").strip()
    branch_text = (branch or "").strip()
    reason_text = (reason or "Unknown error").strip()

    if provider == "jira":
        lines = [
            "Koan update: Pull request creation failed.",
            "",
            f"Mission: {mission}",
            f"Reason: {reason_text}",
        ]
        if branch_text:
            lines.append(f"Current branch: {branch_text}")
        if target_branch:
            lines.append(f"Target branch: {target_branch}")
        lines.extend(
            [
                "",
                "Next:",
                "- Check branch state and repository permissions.",
                "- Re-run the mission after fixing the blocking issue.",
            ],
        )
        return "\n".join(lines)

    lines = [
        "## PR Creation Failed",
        "",
        f"- Mission: `{mission}`",
        f"- Reason: {reason_text}",
    ]
    if branch_text:
        lines.append(f"- Current branch: `{branch_text}`")
    if target_branch:
        lines.append(f"- Target branch: `{target_branch}`")
    return "\n".join(lines)


def build_plan_comment_success(provider: str, title: str, body: str) -> str:
    """Format the `/plan` iteration comment for a target tracker."""
    if provider == "jira":
        readable_body = _strip_markdown_for_jira(body)
        return (
            "Koan plan update\n\n"
            f"Title: {title}\n\n"
            f"{readable_body}\n\n"
            "Generated by Koan."
        ).strip()

    from app.pr_footer import build_koan_footer
    return (
        f"## {title}\n\n{body}\n\n---\n"
        f"{build_koan_footer()}"
    )


def build_plan_comment_failure(provider: str, reason: str) -> str:
    """Format a `/plan` failure status comment."""
    reason_text = (reason or "Unknown error").strip()
    if provider == "jira":
        return (
            "Koan plan update failed.\n\n"
            f"Reason: {reason_text}\n\n"
            "Next:\n"
            "- Re-run /plan after resolving the issue above."
        )
    return (
        "## Plan Update Failed\n\n"
        f"- Reason: {reason_text}\n\n"
        "Re-run `/plan` after addressing the issue."
    )


def jira_readable_markdown(text: str) -> str:
    """Expose markdown-to-readable conversion for Jira issue bodies/comments."""
    return _strip_markdown_for_jira(text or "")
