"""
Koan -- Spec-drift audit runner.

Performs a read-only spec-drift analysis comparing documentation against
code and saves the report to the project's learnings directory.
Optionally queues top findings as fix missions.

Pipeline:
1. Build a spec-drift audit prompt with project context
2. Run Claude Code CLI (read-only tools) to analyze the codebase
3. Parse Claude's structured report
4. Save report to learnings
5. Queue suggested missions

CLI:
    python3 -m skills.core.spec_audit.spec_audit_runner \
        --project-path <path> --project-name <name> --instance-dir <dir>
"""

import re
from pathlib import Path
from typing import Optional, Tuple

from app.prompts import load_prompt_or_skill


def build_spec_audit_prompt(
    project_name: str,
    skill_dir: Optional[Path] = None,
) -> str:
    """Build a prompt for Claude to scan for spec drift."""
    return load_prompt_or_skill(
        skill_dir, "spec_audit",
        PROJECT_NAME=project_name,
    )


def _run_claude_scan(prompt: str, project_path: str) -> str:
    """Run Claude CLI with read-only tools and return the output text."""
    from app.cli_provider import run_command_streaming
    from app.config import get_analysis_max_turns, get_skill_timeout

    return run_command_streaming(
        prompt, project_path,
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=get_analysis_max_turns(),
        timeout=get_skill_timeout(),
    )


def _extract_report_body(raw_output: str) -> str:
    """Extract structured report from Claude's raw output."""
    match = re.search(r'(Spec-Drift Report\b.*)', raw_output, re.DOTALL)
    if match:
        return match.group(1).strip()

    match = re.search(r'(## Summary\b.*)', raw_output, re.DOTALL)
    if match:
        return match.group(1).strip()

    return raw_output.strip()


def _extract_drift_score(report: str) -> Optional[int]:
    """Extract the drift score from the report.

    Returns the score as an integer (1-10) or None if not found.
    """
    match = re.search(r'\*\*Drift Score\*\*:\s*(\d+)/10', report)
    if match:
        score = int(match.group(1))
        if 1 <= score <= 10:
            return score
    return None


def _extract_missions(report: str) -> list:
    """Extract suggested missions from the report."""
    missions = []
    match = re.search(
        r'## Suggested Missions\s*\n(.*?)(?:\n##|\n---|\Z)',
        report, re.DOTALL,
    )
    if not match:
        return missions

    section = match.group(1)
    for line in section.strip().splitlines():
        m = re.match(r'\d+\.\s+(.+?)(?:\s*[—\-]+\s*addresses.*)?$', line.strip())
        if m:
            title = m.group(1).strip()
            if title:
                missions.append(title)

    return missions[:5]


def _save_report(
    instance_dir: Path,
    project_name: str,
    report: str,
    drift_score: Optional[int],
) -> Path:
    """Save the spec-drift report to the project's learnings directory."""
    from datetime import datetime as _dt

    learnings_dir = instance_dir / "memory" / "projects" / project_name
    learnings_dir.mkdir(parents=True, exist_ok=True)

    report_path = learnings_dir / "spec_drift.md"

    timestamp = _dt.now().strftime("%Y-%m-%d %H:%M")
    header = f"<!-- Last scan: {timestamp} -->\n"
    if drift_score is not None:
        header += f"<!-- Drift score: {drift_score}/10 -->\n"
    header += "\n"

    report_path.write_text(header + report)
    return report_path


def _queue_missions(
    instance_dir: Path,
    project_name: str,
    missions: list,
    max_missions: int = 3,
) -> int:
    """Queue top suggested missions to missions.md."""
    from app.utils import insert_pending_mission

    queued = 0

    for title in missions[:max_missions]:
        insert_pending_mission(title, project_name)
        queued += 1

    return queued


def run_spec_audit(
    project_path: str,
    project_name: str,
    instance_dir: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    queue_missions: bool = True,
) -> Tuple[bool, str]:
    """Execute a spec-drift audit on a project.

    Args:
        project_path: Local path to the project.
        project_name: Project name for labeling.
        instance_dir: Path to instance directory.
        notify_fn: Optional callback for progress notifications.
        skill_dir: Optional path to the spec_audit skill directory for prompts.
        queue_missions: Whether to queue suggested missions (default True).

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    instance_path = Path(instance_dir)

    # Step 1: Build prompt
    notify_fn(f"\U0001f4d0 Scanning spec drift for {project_name}...")
    prompt = build_spec_audit_prompt(project_name, skill_dir=skill_dir)

    # Step 2: Run Claude scan (read-only)
    try:
        raw_output = _run_claude_scan(prompt, project_path)
    except RuntimeError as e:
        return False, f"Spec-drift audit failed: {e}"

    if not raw_output:
        return False, f"Spec-drift audit produced no output for {project_name}."

    # Step 3: Extract structured report
    report = _extract_report_body(raw_output)
    drift_score = _extract_drift_score(report)

    # Step 4: Save report
    report_path = _save_report(instance_path, project_name, report, drift_score)

    # Step 5: Queue missions (unless disabled)
    missions = _extract_missions(report)
    queued = 0
    if queue_missions and missions:
        queued = _queue_missions(instance_path, project_name, missions)

    # Build summary
    score_text = f" (score: {drift_score}/10)" if drift_score is not None else ""
    queue_text = f", {queued} missions queued" if queued else ""
    summary = (
        f"Spec-drift report saved to {report_path.name}{score_text}{queue_text}"
    )
    notify_fn(f"\u2705 {summary}")

    return True, summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for spec_audit_runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Audit docs/code alignment for spec drift."
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
        "--no-queue", action="store_true",
        help="Don't queue suggested missions",
    )
    cli_args = parser.parse_args(argv)

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_spec_audit(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        skill_dir=skill_dir,
        queue_missions=not cli_args.no_queue,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
