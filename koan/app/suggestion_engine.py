"""
Kōan -- Automation suggestion engine.

Surfaces the recurring/schedule system to users who haven't set it up by
generating context-aware automation suggestions with copy-pasteable commands.
Triggered when the agent is idle (no pending missions, no focus mode) and
enough time has elapsed since the last suggestion for the project.

Uses a lightweight model (Haiku) to synthesize project learnings, existing
recurring tasks, and cross-project patterns into personalized proposals.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_MIN_INTERVAL_HOURS = 24
_DEFAULT_MAX_PER_DAY = 2
_MAX_LEARNINGS_LINES = 60
_MAX_CROSS_PROJECT_ENTRIES = 20


def _load_suggestion_config() -> dict:
    """Load the ``suggestions:`` section from config.yaml."""
    from app.utils import load_config
    cfg = load_config()
    return cfg.get("suggestions", {})


def _is_enabled() -> bool:
    """Check if suggestions feature is enabled (default: True)."""
    return _load_suggestion_config().get("enabled", True)


# ---------------------------------------------------------------------------
# Tracker: per-project cooldown in instance/.suggestion-tracker.json
# ---------------------------------------------------------------------------

def _tracker_path(instance: str) -> Path:
    return Path(instance) / ".suggestion-tracker.json"


def _read_tracker(instance: str) -> dict:
    """Read tracker state (shared lock)."""
    from app.locked_file import locked_json_read
    return locked_json_read(_tracker_path(instance), default={}) or {}


def _seconds_since_last(tracker: dict, project: str) -> Optional[float]:
    """Seconds since the last suggestion for *project*, or None if never."""
    entry = tracker.get(project)
    if not entry:
        return None
    last_str = entry.get("last_suggested_at")
    if not last_str:
        return None
    try:
        last = datetime.fromisoformat(last_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - last).total_seconds()
    except (ValueError, TypeError):
        return None


def _suggestions_today(tracker: dict, project: str) -> int:
    """Count suggestions sent today for *project*."""
    entry = tracker.get(project, {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if entry.get("last_date") != today:
        return 0
    return entry.get("count_today", 0)


def _record_suggestion(instance: str, project: str):
    """Record that a suggestion was sent for *project*."""
    from app.locked_file import locked_json_modify

    def _update(data: dict):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = data.get(project, {})
        if entry.get("last_date") != today:
            entry = {"count_today": 0, "last_date": today}
        entry["count_today"] = entry.get("count_today", 0) + 1
        entry["last_suggested_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        data[project] = entry

    locked_json_modify(_tracker_path(instance), _update, indent=2)


# ---------------------------------------------------------------------------
# Eligibility check
# ---------------------------------------------------------------------------

def is_eligible(
    instance: str,
    project: str,
    autonomous_mode: str,
    focus_active: bool = False,
) -> bool:
    """Determine whether a suggestion should be generated now.

    Conditions:
    - Feature enabled in config
    - Mode is ``implement`` or ``deep`` (enough budget to be useful)
    - No focus mode active
    - Minimum interval elapsed since last suggestion for this project
    - Daily cap not exceeded
    """
    if not _is_enabled():
        return False

    if autonomous_mode not in ("implement", "deep"):
        return False

    if focus_active:
        return False

    cfg = _load_suggestion_config()
    min_hours = cfg.get("min_interval_hours", _DEFAULT_MIN_INTERVAL_HOURS)
    max_per_day = cfg.get("max_per_day", _DEFAULT_MAX_PER_DAY)

    tracker = _read_tracker(instance)

    # Check daily cap
    if _suggestions_today(tracker, project) >= max_per_day:
        return False

    # Check cooldown
    secs = _seconds_since_last(tracker, project)
    if secs is not None and secs < min_hours * 3600:
        return False

    return True


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def _load_project_learnings(instance: str, project: str) -> str:
    """Load project learnings, truncated to keep prompt small."""
    learnings_path = (
        Path(instance) / "memory" / "projects" / project / "learnings.md"
    )
    if not learnings_path.exists():
        return "(no learnings recorded yet)"
    try:
        lines = learnings_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_LEARNINGS_LINES:
            lines = lines[-_MAX_LEARNINGS_LINES:]
        return "\n".join(lines)
    except OSError:
        return "(could not read learnings)"


def _load_recurring_for_project(
    instance: str, project: Optional[str]
) -> List[Dict]:
    """Load recurring entries filtered to a specific project."""
    from app.recurring import load_recurring

    recurring_path = Path(instance) / "recurring.json"
    all_entries = load_recurring(recurring_path)
    if project is None:
        return [e for e in all_entries if e.get("enabled", True)]
    return [
        e for e in all_entries
        if e.get("enabled", True) and e.get("project") == project
    ]


def _format_recurring_entries(entries: List[Dict]) -> str:
    """Format recurring entries for the prompt."""
    if not entries:
        return "(none configured)"
    lines = []
    for e in entries:
        freq = e.get("frequency", "?")
        text = e.get("text", "?")
        proj = e.get("project")
        proj_tag = f" [project:{proj}]" if proj else ""
        at_str = f" at {e['at']}" if e.get("at") else ""
        lines.append(f"- /{freq}{at_str}{proj_tag} {text}")
    return "\n".join(lines)


def _load_cross_project_recurring(
    instance: str, exclude_project: str
) -> str:
    """Load recurring entries from OTHER projects for inspiration."""
    from app.recurring import load_recurring

    recurring_path = Path(instance) / "recurring.json"
    all_entries = load_recurring(recurring_path)
    others = [
        e for e in all_entries
        if e.get("enabled", True) and e.get("project") != exclude_project
    ]
    if not others:
        return "(no recurring tasks from other projects)"
    # Limit to avoid bloating prompt
    if len(others) > _MAX_CROSS_PROJECT_ENTRIES:
        others = others[:_MAX_CROSS_PROJECT_ENTRIES]
    return _format_recurring_entries(others)


def _assemble_prompt(
    instance: str,
    project_name: str,
    project_path: str,
) -> str:
    """Build the full prompt for the Haiku suggestion call."""
    from app.prompts import load_prompt

    template = load_prompt("suggest-automations")

    learnings = _load_project_learnings(instance, project_name)
    existing = _load_recurring_for_project(instance, project_name)
    existing_text = _format_recurring_entries(existing)
    cross_project = _load_cross_project_recurring(instance, project_name)

    prompt = template
    prompt = prompt.replace("{{ project_name }}", project_name)
    prompt = prompt.replace("{{ project_path }}", project_path)
    prompt = prompt.replace("{{ existing_recurring }}", existing_text)
    prompt = prompt.replace("{{ cross_project_recurring }}", cross_project)
    prompt = prompt.replace("{{ project_learnings }}", learnings)

    return prompt


# ---------------------------------------------------------------------------
# Suggestion generation (Haiku call)
# ---------------------------------------------------------------------------

def _parse_suggestions(raw: str) -> List[Dict]:
    """Extract JSON array from model output, tolerant of markdown fences."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first and last fence lines
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try to find JSON array in the text
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []

    try:
        data = json.loads(text[start:end + 1])
        if not isinstance(data, list):
            return []
        # Validate each entry has required fields
        valid = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if "command" in item and "rationale" in item:
                valid.append(item)
        return valid
    except (json.JSONDecodeError, TypeError):
        return []


def _dedup_against_existing(
    suggestions: List[Dict],
    instance: str,
    project: str,
) -> List[Dict]:
    """Filter suggestions that are too similar to existing recurring tasks.

    Uses keyword overlap: if >50% of the significant words in a suggestion
    match an existing task, it's considered a duplicate.
    """
    existing = _load_recurring_for_project(instance, project)
    if not existing:
        return suggestions

    # Build keyword sets from existing tasks
    stop_words = {
        "the", "a", "an", "and", "or", "for", "to", "in", "of", "on",
        "is", "it", "this", "that", "with", "from", "at", "by", "as",
    }
    existing_words = set()
    for e in existing:
        words = set(e.get("text", "").lower().split()) - stop_words
        existing_words.update(words)

    filtered = []
    for s in suggestions:
        cmd = s.get("command", "")
        # Extract mission text (after the frequency command and project tag)
        parts = cmd.split("]", 1)
        mission_text = parts[-1] if len(parts) > 1 else cmd
        # Remove leading slash command
        for prefix in ("/daily", "/weekly", "/every"):
            if mission_text.strip().startswith(prefix):
                mission_text = mission_text.strip()[len(prefix):].strip()
                break

        words = set(mission_text.lower().split()) - stop_words
        if not words:
            continue

        overlap = len(words & existing_words) / len(words)
        if overlap < 0.5:
            filtered.append(s)

    return filtered


def generate_suggestions(
    instance: str,
    project_name: str,
    project_path: str,
) -> List[Dict]:
    """Generate automation suggestions for a project using the lightweight model.

    Returns list of suggestion dicts, or empty list on failure.
    """
    prompt = _assemble_prompt(instance, project_name, project_path)

    try:
        from app.provider import run_command

        raw = run_command(
            prompt=prompt,
            project_path=project_path,
            allowed_tools=["Read", "Glob", "Grep"],
            model_key="lightweight",
            max_turns=3,
            timeout=120,
        )
    except (RuntimeError, OSError) as e:
        print(
            f"[suggestions] Haiku call failed for {project_name}: {e}",
            file=sys.stderr,
        )
        return []

    suggestions = _parse_suggestions(raw)
    if not suggestions:
        return []

    # Post-filter: dedup against existing recurring
    return _dedup_against_existing(suggestions, instance, project_name)


# ---------------------------------------------------------------------------
# Outbox formatting
# ---------------------------------------------------------------------------

def _format_outbox_message(
    project_name: str, suggestions: List[Dict]
) -> str:
    """Format suggestions as a Telegram-friendly outbox message."""
    lines = [f"💡 [{project_name}] Automation suggestions\n"]
    lines.append(
        "I noticed you don't have many recurring tasks set up. "
        "Here are some that could help:\n"
    )

    for i, s in enumerate(suggestions, 1):
        cmd = s.get("command", "")
        rationale = s.get("rationale", "")
        confidence = s.get("confidence", "medium")
        category = s.get("category", "")

        confidence_marker = {"high": "★", "medium": "☆", "low": "○"}.get(
            confidence, "○"
        )

        lines.append(f"{confidence_marker} **{category}**")
        lines.append(f"`{cmd}`")
        if rationale:
            lines.append(f"_{rationale}_")
        lines.append("")

    lines.append(
        "Copy any command above and send it to me to activate it."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main entry point (called from iteration_manager)
# ---------------------------------------------------------------------------

def maybe_suggest_automations(
    instance: str,
    project_name: str,
    project_path: str,
    autonomous_mode: str,
    focus_active: bool = False,
) -> bool:
    """Check eligibility and generate/send suggestions if appropriate.

    Returns True if suggestions were sent, False otherwise.
    """
    if not is_eligible(instance, project_name, autonomous_mode, focus_active):
        return False

    suggestions = generate_suggestions(instance, project_name, project_path)
    if not suggestions:
        return False

    # Write to outbox
    from app.utils import append_to_outbox

    outbox_path = Path(instance) / "outbox.md"
    message = _format_outbox_message(project_name, suggestions)

    try:
        from app.notify import NotificationPriority
        append_to_outbox(outbox_path, message, priority=NotificationPriority.INFO)
    except ImportError:
        append_to_outbox(outbox_path, message)

    # Record in tracker
    _record_suggestion(instance, project_name)

    print(
        f"[suggestions] Sent {len(suggestions)} suggestions for {project_name}",
        file=sys.stderr,
    )

    return True
