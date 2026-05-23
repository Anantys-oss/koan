"""
Koan -- AI exploration runner.

Gathers project context and runs Claude to suggest creative improvements.
Extracted from the /ai skill handler so it can run as a queued mission
via run.py instead of inlining the full prompt into missions.md.

CLI:
    python3 -m app.ai_runner --project-path <path> --project-name <name> \
        --instance-dir <dir>
"""

from pathlib import Path
from typing import Optional, Tuple

from app.project_explorer import (
    gather_git_activity,
    gather_project_structure,
    get_missions_context,
)
from app.prompts import load_skill_prompt


def run_exploration(
    project_path: str,
    project_name: str,
    instance_dir: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute an AI exploration of a project.

    Gathers git activity, project structure, and missions context, then
    runs Claude to suggest creative improvements.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    notify_fn(f"Exploring {project_name}...")

    # Gather context
    git_activity = gather_git_activity(project_path)
    project_structure = gather_project_structure(project_path)
    missions_context = get_missions_context(Path(instance_dir))

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

    # Extract MISSION: lines and queue them as pending missions
    missions = _extract_missions(result, project_name)
    queued = 0
    if missions:
        missions_path = Path(instance_dir) / "missions.md"
        queued = _queue_missions(missions_path, missions)

    # Send result to Telegram (truncated, without MISSION: lines)
    cleaned = _clean_response(result)
    report = _strip_mission_lines(cleaned)
    if queued:
        skipped = len(missions) - queued
        suffix = f"\n\n({queued} mission(s) queued"
        if skipped:
            suffix += f", {skipped} duplicate(s) skipped"
        suffix += ")"
    else:
        suffix = ""
    notify_fn(f"AI exploration of {project_name}:\n\n{report}{suffix}")

    return True, f"Exploration of {project_name} completed ({queued} missions queued)."


def _extract_missions(text: str, project_name: str) -> list:
    """Extract MISSION: lines from Claude output.

    Sanitizes each description to match the missions.md convention:
    ``- [project:<name>] <description>``

    Handles common Claude output quirks:
    - Leading ``- `` bullet prefix
    - Duplicate ``[project:name]`` tags (prompt says not to, but LLMs…)
    """
    import re

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


def _queue_missions(missions_path: Path, missions: list) -> int:
    """Insert extracted missions, skipping duplicates.

    Checks each new mission against existing Pending/In Progress entries
    using word-overlap similarity to catch both exact duplicates and
    minor rephrasing across repeated /ai runs.

    Returns:
        Number of missions actually queued (after dedup).
    """
    from app.missions import parse_sections
    from app.utils import insert_pending_mission

    # Read existing missions once for similarity checking
    existing_texts = []
    if missions_path.exists():
        content = missions_path.read_text(encoding="utf-8")
        sections = parse_sections(content)
        existing_texts = (
            sections.get("pending", []) + sections.get("in_progress", [])
        )

    queued = 0
    for entry in missions:
        # Check fuzzy similarity against existing missions
        if _has_similar_mission(entry, existing_texts):
            continue

        if insert_pending_mission(missions_path, entry):
            # Track newly inserted missions for intra-batch dedup
            existing_texts.append(entry)
            queued += 1

    return queued


def _normalize_mission_text(text: str) -> str:
    """Strip mission metadata, leaving only the core intent.

    Removes leading bullet, ``[project:X]`` tag, timestamps, and
    normalizes whitespace + case for comparison.
    """
    import re

    # Strip leading bullet and project tag
    text = re.sub(r"^\s*-\s*(\[project:[^\]]+\]\s*)?", "", text)
    # Strip timestamps like ⏳(2026-05-23T04:24)
    text = re.sub(r"⏳\([^)]+\)", "", text)
    # Strip leading slash-commands (e.g. /fix, /review)
    text = re.sub(r"^/\w+\s+", "", text)
    # Normalize whitespace and case
    return " ".join(text.lower().split())


def _mission_words(text: str) -> set:
    """Extract significant words from mission text for similarity.

    Keeps words with 4+ alphanumeric chars to filter noise words
    (the, for, use, add) that inflate Jaccard overlap.
    """
    import re

    normalized = _normalize_mission_text(text)
    return set(re.findall(r"[a-z0-9_]{4,}", normalized))


# Minimum Jaccard similarity to consider two missions as duplicates.
_SIMILARITY_THRESHOLD = 0.6
# Minimum number of shared words required — avoids false positives on
# short missions where a single shared word would exceed the threshold.
_MIN_SHARED_WORDS = 3


def _is_similar_mission(
    new_text: str, existing_text: str,
    threshold: float = _SIMILARITY_THRESHOLD,
) -> bool:
    """Check if two missions share the same core intent via word overlap."""
    new_words = _mission_words(new_text)
    existing_words = _mission_words(existing_text)
    if not new_words or not existing_words:
        return False
    shared = new_words & existing_words
    if len(shared) < _MIN_SHARED_WORDS:
        return False
    union = new_words | existing_words
    return len(shared) / len(union) >= threshold


def _has_similar_mission(entry: str, existing: list) -> bool:
    """Return True if *entry* is similar to any mission in *existing*."""
    return any(_is_similar_mission(entry, ex) for ex in existing)


def _strip_mission_lines(text: str) -> str:
    """Remove MISSION: lines from the report sent to Telegram."""
    lines = text.splitlines()
    filtered = [l for l in lines if not l.strip().startswith("MISSION:")]
    # Clean up trailing blank lines
    result = "\n".join(filtered).rstrip()
    return result


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
    cli_args = parser.parse_args(argv)

    skill_dir = (
        Path(__file__).resolve().parent.parent / "skills" / "core" / "ai"
    )

    success, summary = run_exploration(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        skill_dir=skill_dir,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
