"""Provider-aware comment formatting for tracker updates."""

from __future__ import annotations

import re
from typing import Dict, List, Optional


_BULLET_RE = re.compile(r"^\s*[-*+]\s+")
_ORDERED_RE = re.compile(r"^\s*\d+\.\s+")
# GitHub collapsible code blocks (<details>/<summary>) render as literal text on
# Jira, so flatten them: drop the <details> wrappers and turn the summary label
# into a plain "Label:" line above the (always-visible) code.
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.IGNORECASE)
_DETAILS_TAG_RE = re.compile(r"</?details\s*>", re.IGNORECASE)
# GitHub-flavored alert blocks (`> [!WARNING]` etc.) render as literal
# unrendered text on Jira. Detect the EXACT canonical opener (5 kinds only,
# nothing trailing) so ordinary human blockquotes are never mangled.
_ALERT_OPEN_RE = re.compile(
    r"^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$",
    re.IGNORECASE,
)
_BLOCKQUOTE_PREFIX_RE = re.compile(r"^>\s?")


def _flatten_github_alerts(text: str) -> str:
    """Fold GitHub ``> [!TYPE]`` alert blocks into plain ``TYPE: ...`` text.

    The opener must be exactly ``> [!TYPE]`` (one of the 5 canonical kinds);
    the block body is the contiguous run of ``>``-prefixed lines below it,
    stopping at the next alert opener so back-to-back blocks stay separate.
    The first body line is labelled ``TYPE: <text>`` and the rest follow as
    plain lines. Alert syntax inside a fenced code block is left untouched.
    This is the single definition of the plain-text degradation for a GitHub
    alert on a markdown-less tracker; keep it in sync with the native-alert
    helper once #2301's ``build_alert()`` lands.
    """
    if not text:
        return ""

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: List[str] = []
    i, n = 0, len(lines)
    in_fence = False
    while i < n:
        if lines[i].strip().startswith("```"):
            in_fence = not in_fence
            out.append(lines[i])
            i += 1
            continue
        match = None if in_fence else _ALERT_OPEN_RE.match(lines[i].strip())
        if not match:
            out.append(lines[i])
            i += 1
            continue
        kind = match.group(1).upper()
        body: List[str] = []
        i += 1
        while (
            i < n
            and lines[i].lstrip().startswith(">")
            and not _ALERT_OPEN_RE.match(lines[i].strip())
        ):
            body.append(_BLOCKQUOTE_PREFIX_RE.sub("", lines[i].lstrip()).rstrip())
            i += 1
        content = [line for line in body if line.strip()]
        if content:
            out.append(f"{kind}: {content[0]}")
            out.extend(content[1:])
        else:
            out.append(f"{kind}:")
    return "\n".join(out)


def flatten_github_markdown_for_jira(text: str) -> str:
    """Keep standard Markdown while flattening GitHub-only extensions.

    The result is intentionally still Markdown: Jira's ADF renderer can turn
    headings, lists, code fences, and marks into native nodes.  Only GitHub's
    ``details`` and alert extensions are rewritten because Jira has no
    equivalent representation for them.
    """
    if not text:
        return ""

    flattened = _flatten_github_alerts(text)
    lines: List[str] = []
    in_fence = False
    for raw_line in flattened.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.strip().startswith("```"):
            in_fence = not in_fence
            lines.append(raw_line.rstrip())
            continue
        if in_fence:
            # `/plan` posts code examples verbatim; rewriting details/summary
            # inside a fence corrupts the very content it is meant to convey.
            # Scoped to ``` fences on purpose: markdown_to_adf also treats a
            # 4-space-indented block as code, but guarding that too would leave
            # a genuine <details> wrapper nested under a list item unflattened.
            lines.append(raw_line.rstrip())
            continue
        line = _SUMMARY_RE.sub(lambda m: f"**{m.group(1).strip()}**", raw_line)
        line = _DETAILS_TAG_RE.sub("", line)
        lines.append(line.rstrip())
    return "\n".join(lines)


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


def _jira_pr_link_label(pr_url: str, pr_title: str) -> str:
    pr_number = pr_url.rstrip("/").rsplit("/", 1)[-1]
    prefix = f"PR #{pr_number}" if pr_number.isdigit() else "Pull request"
    safe_title = " ".join(
        (pr_title or "").replace("[", "(").replace("]", ")").split()
    )
    return f"{prefix} — {safe_title}" if safe_title else prefix


def build_pr_comment_success(
    provider: str,
    pr_url: str,
    pr_title: str,
    pr_body: str,
    skill_name: str = "",
    base_branch: Optional[str] = None,
) -> str:
    """Build a mission-completion comment when draft PR creation succeeds."""
    # Jira has no native alert rendering: flatten before section extraction so
    # a `> [!TYPE]` line pulled into the Why/What summary never leaks literally.
    body_for_sections = (
        _flatten_github_alerts(pr_body) if provider == "jira" else pr_body
    )
    sections = _parse_markdown_sections(body_for_sections)
    what_bullets = _lines_to_bullets(
        _collect_section_lines(sections, "summary", "changes"),
    )
    how_bullets = _lines_to_bullets(_collect_section_lines(sections, "how"))
    why_text = _first_nonempty_line(_collect_section_lines(sections, "why"))
    testing_bullets = _lines_to_bullets(_collect_section_lines(sections, "testing"))
    mission = f"/{skill_name}" if skill_name else "(unknown)"
    target_branch = (base_branch or "").strip()

    if provider == "jira":
        link_label = _jira_pr_link_label(pr_url, pr_title)
        lines: List[str] = [
            "### Kōan · draft pull request created",
            "",
            f"- **Mission**: `{mission}`",
            f"- **Pull request**: [{link_label}]({pr_url})",
        ]
        if target_branch:
            lines.append(f"- **Target branch**: `{target_branch}`")
        if what_bullets:
            lines.extend(["", "**What changed**"])
            lines.extend(f"- {item}" for item in what_bullets[:8])
        if why_text:
            lines.extend(["", "**Why**", why_text])
        if how_bullets:
            lines.extend(["", "**How it was implemented**"])
            lines.extend(f"- {item}" for item in how_bullets[:8])
        if testing_bullets:
            lines.extend(["", "**Validation**"])
            lines.extend(f"- {item}" for item in testing_bullets[:8])
        lines.extend([
            "",
            "**Next**",
            "- Review the draft PR and merge when ready.",
        ])
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
        reason_text = _flatten_github_alerts(reason_text).strip()
        lines = [
            "### Kōan · pull request creation failed",
            "",
            f"- **Mission**: `{mission}`",
            f"- **Reason**: {reason_text}",
        ]
        if branch_text:
            lines.append(f"- **Current branch**: `{branch_text}`")
        if target_branch:
            lines.append(f"- **Target branch**: `{target_branch}`")
        lines.extend(
            [
                "",
                "**Next**",
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


# Sections of a generated plan that are written for the human reading the
# tracker, in the order they are rendered. Everything else is the brief the
# `/implement` agent consumes and is folded out of sight.
_PLAN_HUMAN_SECTIONS = ("summary", "open questions", "verification criteria")
_PLAN_ASSESSMENT_SECTION = "plan assessment"
_PLAN_FOLD_LABEL = (
    "\U0001F916 Implementation brief for the agent "
    "(alternatives, file map, phases, corner cases, testing, risks)"
)
_PLAN_SUMMARY_HEADING_RE = re.compile(r"^(#{2,4})\s+summary\s*$", re.IGNORECASE | re.MULTILINE)
_PLAN_PHASE_RE = re.compile(r"^#{2,5}\s+Phase\s+\d+\b", re.IGNORECASE | re.MULTILINE)


def _split_plan_sections(body: str, level: int):
    """Split a plan body into ``(heading, section_text)`` pairs at one level.

    The text before the first heading is returned under an empty heading so a
    preamble is never dropped. Deeper headings (phase titles) stay inside
    their section.
    """
    heading_re = re.compile(rf"^#{{{level}}}(?!#)\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_re.finditer(body))
    if not matches:
        return [("", body)]
    out = [("", body[: matches[0].start()])]
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out.append((match.group(1), body[match.start():end]))
    return out


def _plan_assessment_line(section_text: str, phase_count: int) -> str:
    """Render the one-line plan assessment, with the computed phase count."""
    lines = [
        line.strip() for line in section_text.splitlines()[1:] if line.strip()
    ]
    parts = [lines[0]] if lines else []
    if phase_count:
        parts.append(f"**Phases:** {phase_count}")
    return " · ".join(parts)


def fold_plan_agent_sections(body: str) -> str:
    """Collapse the agent-facing half of a plan body into one ``<details>``.

    Keeps the assessment line, Summary, Open Questions and Verification
    Criteria visible, in that order, and folds every other section into a
    single labelled block. A pure text transform: the folded sections keep
    their markdown verbatim, so `/implement` still reads them out of the
    posted comment. Fails open — an unrecognisable or already-folded body is
    returned unchanged.
    """
    if not body or _PLAN_FOLD_LABEL in body:
        return body
    match = _PLAN_SUMMARY_HEADING_RE.search(body)
    if not match:
        return body

    level = len(match.group(1))
    sections = _split_plan_sections(body, level)
    phase_count = len(_PLAN_PHASE_RE.findall(body))

    preamble = ""
    assessment = ""
    human: dict = {}
    agent = []
    for heading, text in sections:
        key = heading.strip().lower()
        if not heading:
            preamble = text.strip()
        elif key == _PLAN_ASSESSMENT_SECTION:
            assessment = _plan_assessment_line(text, phase_count)
        elif key in _PLAN_HUMAN_SECTIONS:
            human[key] = text.strip()
        else:
            agent.append(text.strip())

    visible = [part for part in (preamble, assessment) if part]
    visible.extend(human[key] for key in _PLAN_HUMAN_SECTIONS if key in human)
    if not agent:
        return "\n\n".join(visible) + "\n" if visible else body

    folded = (
        "<details>\n"
        f"<summary>{_PLAN_FOLD_LABEL}</summary>\n\n"
        + "\n\n".join(agent)
        + "\n\n</details>"
    )
    return "\n\n".join(visible + [folded]) + "\n"


def fold_plan_for_tracker(provider: str, body: str) -> str:
    """Fold a plan body for GitHub; leave other providers untouched.

    Jira has no ``<details>`` equivalent — ``flatten_github_markdown_for_jira``
    strips the wrapper — so folding there would only add an orphan label.
    """
    if provider == "jira":
        return body
    return fold_plan_agent_sections(body)


def build_plan_comment_success(provider: str, title: str, body: str) -> str:
    """Format the `/plan` iteration comment for a target tracker."""
    if provider == "jira":
        return (
            f"## {title}\n\n"
            f"{body}\n\n"
            "---\n\n"
            "Generated by Koan."
        ).strip()

    from app.pr_footer import build_koan_footer
    return (
        f"## {title}\n\n{fold_plan_agent_sections(body)}\n\n---\n"
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
