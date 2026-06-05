"""
Koan -- Deep exploration runner.

Runs a thorough autonomous exploration of a project codebase with full
tool access (Read, Glob, Grep, Bash). Unlike ai_runner (which suggests
quick wins), deep_runner does an in-depth analysis: reads code, runs
tests, checks architecture, and generates detailed follow-up missions.

CLI:
    python3 -m skills.core.deep.deep_runner \
        --project-path <path> --project-name <name> --instance-dir <dir> \
        [--focus-context "error handling"]
"""

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from app.prompts import load_skill_prompt


def _build_project_context(
    project_path: str,
    instance_dir: str,
    project_name: str,
) -> dict:
    """Gather project context for the deep exploration prompt."""
    from app.project_explorer import (
        gather_git_activity,
        gather_project_structure,
        get_missions_context,
    )

    git_activity = gather_git_activity(project_path)
    project_structure = gather_project_structure(project_path)
    missions_context = get_missions_context(Path(instance_dir))

    project_memory = ""
    try:
        from app.skill_memory import build_memory_block_for_skill
        project_memory = build_memory_block_for_skill(
            project_path, f"Deep exploration of {project_name}",
        )
    except Exception as e:
        print(f"[deep_runner] memory injection failed: {e}", file=sys.stderr)

    project_health = ""
    try:
        from app.ai_runner import _build_project_health_block
        project_health = _build_project_health_block(instance_dir, project_name)
    except Exception as e:
        print(f"[deep_runner] health block failed: {e}", file=sys.stderr)

    return {
        "git_activity": git_activity,
        "project_structure": project_structure,
        "missions_context": missions_context,
        "project_memory": project_memory,
        "project_health": project_health,
    }


def build_deep_prompt(
    project_name: str,
    context: dict,
    focus_context: str = "",
    skill_dir: Optional[Path] = None,
) -> str:
    """Build the deep exploration prompt."""
    focus_block = ""
    if focus_context:
        focus_block = (
            f"## Exploration Focus\n\n"
            f"The human has asked you to focus on:\n"
            f"> {focus_context}\n\n"
            f"Prioritize analysis related to this guidance, but don't "
            f"ignore other significant issues you discover."
        )

    if skill_dir is None:
        skill_dir = Path(__file__).resolve().parent

    return load_skill_prompt(
        skill_dir,
        "deep-explore",
        PROJECT_NAME=project_name,
        GIT_ACTIVITY=context["git_activity"],
        PROJECT_STRUCTURE=context["project_structure"],
        MISSIONS_CONTEXT=context["missions_context"],
        FOCUS_CONTEXT=focus_block,
        PROJECT_MEMORY=context["project_memory"],
        PROJECT_HEALTH=context["project_health"],
    )


def _run_claude_deep(prompt: str, project_path: str) -> str:
    """Run Claude CLI with full tool access for deep exploration."""
    from app.cli_provider import run_command_streaming
    from app.config import get_skill_max_turns, get_skill_timeout

    return run_command_streaming(
        prompt, project_path,
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        model_key="mission",
        max_turns=get_skill_max_turns(),
        timeout=get_skill_timeout(),
    )


_MISSION_FIELD_RE = re.compile(
    r"^(TITLE|PRIORITY|CATEGORY|SCOPE|RATIONALE):\s*(.+)",
    re.MULTILINE,
)


class DeepFinding:
    """A mission extracted from deep exploration output."""

    __slots__ = ("title", "priority", "category", "scope", "rationale")

    def __init__(self, **kwargs):
        self.title = kwargs.get("title", "")
        self.priority = kwargs.get("priority", "medium")
        self.category = kwargs.get("category", "")
        self.scope = kwargs.get("scope", "")
        self.rationale = kwargs.get("rationale", "")

    def is_valid(self) -> bool:
        return bool(self.title and self.rationale)


def parse_findings(raw_output: str) -> List[DeepFinding]:
    """Parse ---MISSION--- blocks from Claude's output."""
    blocks = re.split(r"---MISSION---", raw_output)

    findings: List[DeepFinding] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        finding = DeepFinding()
        for match in _MISSION_FIELD_RE.finditer(block):
            field = match.group(1).lower()
            value = match.group(2).strip()

            end_pos = match.end()
            next_field = _MISSION_FIELD_RE.search(block[end_pos:])
            if next_field:
                full_value = block[match.start(2):end_pos + next_field.start()].strip()
            else:
                full_value = block[match.start(2):].strip()

            if field == "rationale":
                value = full_value

            setattr(finding, field, value)

        if finding.is_valid():
            findings.append(finding)

    return findings


_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def prioritize_findings(findings: List[DeepFinding]) -> List[DeepFinding]:
    """Sort findings by priority (high first)."""
    return sorted(
        findings,
        key=lambda f: _PRIORITY_ORDER.get(f.priority, 99),
    )


def _findings_to_missions(
    findings: List[DeepFinding], project_name: str,
) -> list:
    """Convert findings into missions.md entries."""
    missions = []
    for f in findings:
        desc = f.title
        if f.scope:
            desc = f"{desc} ({f.scope})"
        missions.append(f"- [project:{project_name}] {desc}")
    return missions


def _extract_missions_legacy(text: str, project_name: str) -> list:
    """Extract MISSION: lines from Claude output (legacy fallback)."""
    tag_re = re.compile(r"^\[project:[^\]]+\]\s*", re.IGNORECASE)

    missions = []
    for line in text.splitlines():
        match = re.match(r"^MISSION:\s*(.+)$", line.strip())
        if match:
            desc = match.group(1).strip()
            desc = re.sub(r"^-\s+", "", desc)
            desc = tag_re.sub("", desc)
            desc = desc.strip()
            if desc:
                missions.append(f"- [project:{project_name}] {desc}")
    return missions


def _queue_missions(
    missions_path: Path,
    missions: list,
    findings: Optional[List[DeepFinding]] = None,
):
    """Insert extracted missions into Pending section of missions.md."""
    from app.utils import insert_pending_mission

    for i, entry in enumerate(missions):
        urgent = False
        if findings and i < len(findings):
            urgent = findings[i].priority == "high"
        insert_pending_mission(missions_path, entry, urgent=urgent)


def run_deep_exploration(
    project_path: str,
    project_name: str,
    instance_dir: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    focus_context: str = "",
) -> Tuple[bool, str]:
    """Execute a deep exploration of a project.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    focus_hint = f" (focus: {focus_context})" if focus_context else ""
    notify_fn(f"🧠 Deep exploration starting for {project_name}{focus_hint}...")

    context = _build_project_context(project_path, instance_dir, project_name)

    prompt = build_deep_prompt(
        project_name, context,
        focus_context=focus_context,
        skill_dir=skill_dir,
    )

    try:
        raw_output = _run_claude_deep(prompt, project_path)
    except (RuntimeError, OSError, Exception) as e:
        return False, f"Deep exploration failed: {str(e)[:300]}"

    if not raw_output:
        return False, f"Deep exploration produced no output for {project_name}."

    findings = parse_findings(raw_output)
    if findings:
        findings = prioritize_findings(findings)
        missions = _findings_to_missions(findings, project_name)
    else:
        missions = _extract_missions_legacy(raw_output, project_name)

    if missions:
        missions_path = Path(instance_dir) / "missions.md"
        _queue_missions(missions_path, missions, findings if findings else None)

    from app.text_utils import clean_cli_response
    cleaned = clean_cli_response(raw_output)
    cleaned = re.sub(
        r"---MISSION---.*?(?=---MISSION---|$)", "", cleaned, flags=re.DOTALL,
    )
    lines = cleaned.splitlines()
    report = "\n".join(ln for ln in lines if not ln.strip().startswith("MISSION:")).rstrip()

    suffix = f"\n\n({len(missions)} mission(s) queued)" if missions else ""
    notify_fn(f"🧠 Deep exploration of {project_name}:\n\n{report}{suffix}")

    return True, f"Deep exploration of {project_name} completed ({len(missions)} missions queued)."


def main(argv=None):
    """CLI entry point for deep_runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run a deep autonomous exploration on a project."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--project-name", required=True,
        help="Project name for labeling",
    )
    parser.add_argument(
        "--instance-dir", required=True,
        help="Path to instance directory",
    )
    parser.add_argument(
        "--focus-context", default="",
        help="Optional free-text guidance to steer the exploration",
    )
    cli_args = parser.parse_args(argv)

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_deep_exploration(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        skill_dir=skill_dir,
        focus_context=cli_args.focus_context,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
