"""
Koan -- AI exploration runner.

Gathers project context and runs Claude to suggest creative improvements.
Extracted from the /ai skill handler so it can run as a queued mission
via run.py instead of inlining the full prompt into missions.md.

CLI:
    python3 -m app.ai_runner --project-path <path> --project-name <name> \
        --instance-dir <dir>
"""

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

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


def _build_project_health_block(
    instance_dir: str,
    project_name: str,
) -> str:
    """Build a project health summary from mission metrics.

    Combines success rates and recent failure context so the AI explorer
    can avoid suggesting fixes for already-known pain points and focus
    on areas with persistent failures.

    Returns empty string when no meaningful data is available.
    """
    parts: list[str] = []

    # Success rate
    try:
        from app.mission_metrics import get_project_success_rates
        rates = get_project_success_rates(instance_dir, [project_name])
        rate = rates.get(project_name)
        if rate is not None:
            pct = int(rate * 100)
            parts.append(f"- **Success rate** (30-day): {pct}%")
    except Exception as e:
        print(f"[ai_runner] success rate lookup failed: {e}", file=sys.stderr)

    # Recent failure context
    try:
        from app.mission_summary import get_failure_context
        failure_ctx = get_failure_context(instance_dir, project_name, max_chars=500)
        if failure_ctx:
            parts.append(f"- **Recent failure patterns**:\n```\n{failure_ctx}\n```")
    except Exception as e:
        print(f"[ai_runner] failure context lookup failed: {e}", file=sys.stderr)

    if not parts:
        return ""

    return "## Project Health\n\n" + "\n".join(parts) + "\n"


def run_exploration(
    project_path: str,
    project_name: str,
    instance_dir: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    focus_context: str = "",
) -> Tuple[bool, str]:
    """Execute an AI exploration of a project.

    Gathers git activity, project structure, and missions context, then
    runs Claude to suggest creative improvements.

    Args:
        focus_context: Optional free-text guidance to steer the exploration
            (e.g. "explore the notification pipeline").

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

    # Build memory and health blocks
    project_memory = ""
    try:
        from app.skill_memory import build_memory_block_for_skill
        project_memory = build_memory_block_for_skill(
            project_path, f"AI exploration of {project_name}",
        )
    except Exception as e:
        print(f"[ai_runner] memory injection failed: {e}", file=sys.stderr)

    project_health = _build_project_health_block(instance_dir, project_name)

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
        PROJECT_MEMORY=project_memory,
        PROJECT_HEALTH=project_health,
    )

    # Run Claude
    try:
        from app.cli_provider import run_command_streaming
        from app.config import get_skill_max_turns, get_skill_timeout
        result = run_command_streaming(
            prompt, project_path,
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            model_key="mission",
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
        _queue_missions(instance_dir, missions, findings if findings else None)

    # Send result to Telegram (truncated, without structured blocks)
    cleaned = _clean_response(result)
    report = _strip_structured_output(cleaned)
    suffix = f"\n\n({len(missions)} mission(s) queued)" if missions else ""
    notify_fn(f"AI exploration of {project_name}:\n\n{report}{suffix}")

    return True, f"Exploration of {project_name} completed ({len(missions)} missions queued)."


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
    instance_dir,
    missions: list,
    findings: Optional[List[AIFinding]] = None,
):
    """Insert extracted missions into the pending queue.

    When *findings* are provided, high-impact findings get ``urgent=True``
    so they appear near the top of the pending queue.
    """
    from app.utils import insert_pending_mission, parse_project

    for i, entry in enumerate(missions):
        urgent = False
        if findings and i < len(findings):
            urgent = findings[i].impact == "high"
        project, text = parse_project(entry)
        text = text.removeprefix("- ")
        insert_pending_mission(text, project, urgent=urgent)


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
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
