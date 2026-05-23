"""
Koan -- AI exploration runner.

Gathers project context and runs Claude to suggest creative improvements.
Extracted from the /ai skill handler so it can run as a queued mission
via run.py instead of inlining the full prompt into missions.md.

CLI:
    python3 -m app.ai_runner --project-path <path> --project-name <name> \
        --instance-dir <dir>
"""

import hashlib
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.project_explorer import (
    gather_git_activity,
    gather_project_structure,
    get_missions_context,
)
from app.prompts import load_skill_prompt


# ---------------------------------------------------------------------------
# Impact ordering for priority-based queueing
# ---------------------------------------------------------------------------

_IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}

# Cap on how many GitHub issues a single /ai run can create.
ISSUES_CAP = 5


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class AIFinding:
    """A single idea from the AI exploration."""

    __slots__ = ("title", "impact", "effort", "category", "location", "description")

    def __init__(
        self,
        title: str = "",
        impact: str = "medium",
        effort: str = "medium",
        category: str = "",
        location: str = "",
        description: str = "",
    ):
        self.title = title
        self.impact = impact
        self.effort = effort
        self.category = category
        self.location = location
        self.description = description

    def is_valid(self) -> bool:
        """Check if the finding has the minimum required fields."""
        return bool(self.title and self.description)


# ---------------------------------------------------------------------------
# Finding parser
# ---------------------------------------------------------------------------

_IDEA_FIELD_RE = re.compile(
    r"^(TITLE|IMPACT|EFFORT|CATEGORY|LOCATION|DESCRIPTION):\s*(.+)",
    re.MULTILINE,
)


def parse_findings(raw_output: str) -> List[AIFinding]:
    """Parse ---IDEA--- blocks from Claude's output.

    Modeled on audit_runner.parse_findings but with AI-exploration-specific
    fields (impact, effort, category, location, description).
    """
    blocks = re.split(r"---IDEA---", raw_output)

    findings: List[AIFinding] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        finding = AIFinding()
        for match in _IDEA_FIELD_RE.finditer(block):
            field = match.group(1).lower()
            value = match.group(2).strip()

            # For multiline fields, capture until the next field
            end_pos = match.end()
            next_field = _IDEA_FIELD_RE.search(block[end_pos:])
            if next_field:
                full_value = block[match.start(2):end_pos + next_field.start()].strip()
            else:
                full_value = block[match.start(2):].strip()

            # Use the full multiline value for description
            if field == "description":
                value = full_value

            setattr(finding, field, value)

        if finding.is_valid():
            findings.append(finding)

    return findings


def prioritize_findings(findings: List[AIFinding]) -> List[AIFinding]:
    """Sort findings by impact level (high first).

    Ties preserve original order from the exploration output.
    """
    return sorted(
        findings,
        key=lambda f: _IMPACT_ORDER.get(f.impact, 99),
    )


def run_exploration(
    project_path: str,
    project_name: str,
    instance_dir: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    focus_context: str = "",
    create_issues: bool = False,
) -> Tuple[bool, str]:
    """Execute an AI exploration of a project.

    Gathers git activity, project structure, and missions context, then
    runs Claude to suggest creative improvements.

    Args:
        focus_context: Optional free-text guidance to steer the exploration
            (e.g. "explore the notification pipeline").
        create_issues: When True, create GitHub issues for high-impact
            findings (capped at :data:`ISSUES_CAP`).

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    focus_hint = f" (focus: {focus_context})" if focus_context else ""
    notify_fn(f"Exploring {project_name}{focus_hint}...")

    # Gather context
    git_activity = gather_git_activity(project_path)
    project_structure = gather_project_structure(project_path)
    missions_context = get_missions_context(Path(instance_dir))

    # Build focus block (mirrors audit's EXTRA_CONTEXT pattern)
    focus_block = ""
    if focus_context:
        focus_block = (
            f"## Exploration Focus\n\n"
            f"The human has asked you to focus on:\n"
            f"> {focus_context}\n\n"
            f"Prioritize ideas related to this guidance, but don't "
            f"ignore other significant opportunities you discover."
        )

    # Build prompt from skill template
    if skill_dir is None:
        skill_dir = (
            Path(__file__).resolve().parent.parent / "skills" / "core" / "ai"
        )

    prompt = load_skill_prompt(
        skill_dir,
        "ai-explore",
        PROJECT_NAME=project_name,
        GIT_ACTIVITY=git_activity,
        PROJECT_STRUCTURE=project_structure,
        MISSIONS_CONTEXT=missions_context,
        FOCUS_CONTEXT=focus_block,
    )

    # Run Claude
    try:
        from app.cli_provider import run_command_streaming
        from app.config import get_skill_max_turns, get_skill_timeout
        result = run_command_streaming(
            prompt, project_path,
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            max_turns=get_skill_max_turns(),
            timeout=get_skill_timeout(),
        )
    except Exception as e:
        return False, f"Exploration failed: {str(e)[:300]}"

    if not result:
        return False, "Claude returned an empty exploration result."

    # Extract structured findings or fall back to MISSION: lines
    findings = parse_findings(result)
    if findings:
        findings = prioritize_findings(findings)
        missions = _findings_to_missions(findings, project_name)
    else:
        missions = _extract_missions_legacy(result, project_name)

    if missions:
        missions_path = Path(instance_dir) / "missions.md"
        _queue_missions(missions_path, missions, findings if findings else None)

    # Create GitHub issues for high-impact findings (opt-in)
    issues_created = 0
    if create_issues and findings:
        issues_created = _create_issues_for_findings(
            findings, project_path, project_name, notify_fn,
        )

    # Send result to Telegram (truncated, without structured blocks)
    cleaned = _clean_response(result)
    report = _strip_structured_output(cleaned)
    parts = []
    if missions:
        parts.append(f"{len(missions)} mission(s) queued")
    if issues_created:
        parts.append(f"{issues_created} GitHub issue(s) created")
    suffix = f"\n\n({', '.join(parts)})" if parts else ""
    notify_fn(f"AI exploration of {project_name}:\n\n{report}{suffix}")

    issue_suffix = f", {issues_created} issues created" if issues_created else ""
    return True, f"Exploration of {project_name} completed ({len(missions)} missions queued{issue_suffix})."


# ---------------------------------------------------------------------------
# GitHub issue creation
# ---------------------------------------------------------------------------

_IMPACT_LABELS = {
    "high": "\U0001f534",     # red circle
    "medium": "\U0001f7e1",   # yellow circle
    "low": "\U0001f7e2",      # green circle
}

_EFFORT_LABELS = {
    "quick_win": "\u26a1 Quick win",
    "small": "\u26a1 Quick fix",
    "medium": "\U0001f6e0\ufe0f Moderate effort",
    "significant": "\U0001f3d7\ufe0f Significant work",
    "large": "\U0001f3d7\ufe0f Significant work",
}

# Marker embedded in issue body for dedup across runs.
_FINGERPRINT_MARKER_RE = re.compile(r"<!-- koan-ai-id: ([a-f0-9]+) -->")


def _compute_ai_fingerprint(finding: AIFinding) -> str:
    """Stable 16-char fingerprint for dedup across AI exploration runs."""
    location = " ".join((finding.location or "").lower().split())
    category = " ".join((finding.category or "").lower().split())
    title = " ".join((finding.title or "").lower().split())
    digest = hashlib.sha256(f"{title}:{location}:{category}".encode("utf-8")).hexdigest()
    return digest[:16]


def _build_ai_issue_body(finding: AIFinding) -> str:
    """Build a GitHub issue body from an AI exploration finding."""
    impact_icon = _IMPACT_LABELS.get(finding.impact, "\u2753")
    effort_label = _EFFORT_LABELS.get(finding.effort, finding.effort)
    fingerprint = _compute_ai_fingerprint(finding)

    lines = [
        "## Description",
        "",
        finding.description,
        "",
        "## Details",
        "",
        "| | |",
        "|---|---|",
        f"| **Impact** | {impact_icon} {finding.impact.capitalize()} |",
        f"| **Category** | {finding.category or 'general'} |",
        f"| **Location** | `{finding.location or 'N/A'}` |",
        f"| **Effort** | {effort_label} |",
        "",
        "---",
        "\U0001f916 Created by K\u014dan from AI exploration",
        f"<!-- koan-ai-id: {fingerprint} -->",
    ]
    return "\n".join(lines)


def _build_ai_fingerprint_index(
    existing_issues: List[Dict],
) -> Dict[str, str]:
    """Map koan-ai-id fingerprints to issue URLs for dedup."""
    index: Dict[str, str] = {}
    for issue in existing_issues:
        body = issue.get("body") or ""
        url = issue.get("url") or ""
        if not body or not url:
            continue
        match = _FINGERPRINT_MARKER_RE.search(body)
        if not match:
            continue
        index.setdefault(match.group(1), url)
    return index


def _create_issues_for_findings(
    findings: List[AIFinding],
    project_path: str,
    project_name: str,
    notify_fn=None,
) -> int:
    """Create GitHub issues for high-impact AI findings.

    Only processes findings with ``impact == "high"`` or ``"medium"``,
    capped at :data:`ISSUES_CAP`. Deduplicates against existing open
    issues using embedded fingerprints.

    Returns the number of issues created.
    """
    from app.github import issue_create, resolve_target_repo

    target_repo = resolve_target_repo(project_path, project_name=project_name)

    # Fetch existing AI-exploration issues for dedup
    existing_index = _build_ai_fingerprint_index(
        _list_open_ai_issues(repo=target_repo, cwd=project_path)
    )

    created = 0
    for finding in findings:
        if created >= ISSUES_CAP:
            break

        # Only create issues for high/medium impact findings
        if finding.impact not in ("high", "medium"):
            continue

        # Dedup check
        fp = _compute_ai_fingerprint(finding)
        if fp in existing_index:
            if notify_fn:
                notify_fn(
                    f"  \u21a9\ufe0f Already tracked: {finding.title} — "
                    f"{existing_index[fp]}"
                )
            continue

        if notify_fn:
            notify_fn(f"  \U0001f4dd Creating issue: {finding.title}")

        try:
            url = issue_create(
                title=finding.title,
                body=_build_ai_issue_body(finding),
                repo=target_repo,
                cwd=project_path,
            )
        except Exception as e:
            print(
                f"[ai_runner] Failed to create issue '{finding.title}': {e}",
                file=sys.stderr,
            )
            continue

        url = url.strip() if url else ""
        if url:
            created += 1
            if notify_fn:
                notify_fn(f"  \U0001f517 {url}")

    if created and notify_fn:
        cap_note = f" (cap: {ISSUES_CAP})" if created >= ISSUES_CAP else ""
        notify_fn(
            f"  \u2705 Created {created} GitHub issue(s) "
            f"from AI exploration{cap_note}"
        )

    return created


def _list_open_ai_issues(
    repo: Optional[str] = None, cwd: Optional[str] = None,
) -> List[Dict]:
    """Fetch open issues that contain the AI exploration marker.

    Reuses the same ``gh issue list`` pattern as audit_runner but
    searches for the ``koan-ai-id`` marker instead.
    """
    from app.github import run_gh

    args = [
        "issue", "list",
        "--state", "open",
        "--search", "koan-ai-id in:body",
        "--json", "title,url,body",
        "--limit", "100",
    ]
    if repo:
        args.extend(["--repo", repo])
    try:
        import json
        raw = run_gh(*args, cwd=cwd)
        return json.loads(raw) if raw else []
    except Exception as e:
        print(f"[ai_runner] Failed to list open AI issues: {e}", file=sys.stderr)
        return []


def _findings_to_missions(
    findings: List[AIFinding], project_name: str,
) -> list:
    """Convert structured AIFindings into missions.md entries."""
    missions = []
    for f in findings:
        desc = f.title
        if f.location:
            desc = f"{desc} ({f.location})"
        missions.append(f"- [project:{project_name}] {desc}")
    return missions


def _extract_missions_legacy(text: str, project_name: str) -> list:
    """Extract MISSION: lines from Claude output (legacy fallback).

    Used when Claude doesn't output ---IDEA--- blocks.
    """
    tag_re = re.compile(r"^\[project:[^\]]+\]\s*", re.IGNORECASE)

    missions = []
    for line in text.splitlines():
        match = re.match(r"^MISSION:\s*(.+)$", line.strip())
        if match:
            desc = match.group(1).strip()
            # Strip leading bullet if Claude added one
            desc = re.sub(r"^-\s+", "", desc)
            # Strip duplicate project tag if Claude added one despite prompt
            desc = tag_re.sub("", desc)
            desc = desc.strip()
            if desc:
                missions.append(f"- [project:{project_name}] {desc}")
    return missions


# Keep old name as alias for backward-compatible imports in tests
_extract_missions = _extract_missions_legacy


def _queue_missions(
    missions_path: Path,
    missions: list,
    findings: Optional[List[AIFinding]] = None,
):
    """Insert extracted missions into the Pending section of missions.md.

    When *findings* are provided, high-impact findings get ``urgent=True``
    so they appear near the top of the pending queue.
    """
    from app.utils import insert_pending_mission

    for i, entry in enumerate(missions):
        urgent = False
        if findings and i < len(findings):
            urgent = findings[i].impact == "high"
        insert_pending_mission(missions_path, entry, urgent=urgent)


def _strip_structured_output(text: str) -> str:
    """Remove ---IDEA--- blocks and MISSION: lines from Telegram output."""
    # Remove entire ---IDEA--- blocks (everything from marker to next marker or end)
    text = re.sub(
        r"---IDEA---.*?(?=---IDEA---|$)",
        "",
        text,
        flags=re.DOTALL,
    )
    # Also strip legacy MISSION: lines
    lines = text.splitlines()
    filtered = [ln for ln in lines if not ln.strip().startswith("MISSION:")]
    return "\n".join(filtered).rstrip()


# Keep old name for backward compatibility
_strip_mission_lines = _strip_structured_output


def _clean_response(text: str) -> str:
    """Clean Claude CLI output for Telegram delivery."""
    from app.text_utils import clean_cli_response

    return clean_cli_response(text)


# ---------------------------------------------------------------------------
# CLI entry point -- python3 -m app.ai_runner
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for ai_runner.

    Returns exit code (0 = success, 1 = failure).
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Run AI exploration on a project and report findings."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--project-name", required=True,
        help="Human-readable project name",
    )
    parser.add_argument(
        "--instance-dir", required=True,
        help="Path to the instance directory",
    )
    parser.add_argument(
        "--focus-context", default="",
        help="Optional free-text guidance to steer the exploration",
    )
    parser.add_argument(
        "--issues", action="store_true", default=False,
        help="Create GitHub issues for high-impact findings",
    )
    cli_args = parser.parse_args(argv)

    skill_dir = (
        Path(__file__).resolve().parent.parent / "skills" / "core" / "ai"
    )

    success, summary = run_exploration(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        skill_dir=skill_dir,
        focus_context=cli_args.focus_context,
        create_issues=cli_args.issues,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
