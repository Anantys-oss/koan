"""
Kōan — Feature tip system.

Proactively surfaces one undiscovered skill to the user via Telegram
each time the agent enters idle sleep, increasing feature adoption.

Tracks which skills have been advertised in instance/seen_tips.txt
(one command name per line). When all core bridge-visible skills have
been seen, resets and cycles.

Throttled: at most once every 6 hours.
"""

import random
import time
from pathlib import Path
from typing import Optional

from app.utils import atomic_write

# Throttle: one tip every 6 hours (in seconds).
_TIP_INTERVAL = 6 * 60 * 60

# Module-level timestamp of last tip sent.
_last_tip_time: float = 0.0

# When True, no new tips are sent (one tip already delivered this idle period).
_idle_tip_sent: bool = False


def _load_seen(seen_path: Path) -> set:
    """Load set of already-advertised skill command names."""
    if not seen_path.exists():
        return set()
    text = seen_path.read_text(encoding="utf-8").strip()
    if not text:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}


def _save_seen(seen_path: Path, seen: set) -> None:
    """Persist the seen set to disk."""
    content = "\n".join(sorted(seen)) + "\n" if seen else ""
    atomic_write(seen_path, content)


def _get_eligible_skills(registry) -> list:
    """Return core bridge-visible skills suitable for tips.

    Filters to scope == "core" and audience in ("bridge", "hybrid")
    so we only surface stable, user-facing skills.
    """
    skills = []
    for skill in registry.list_all():
        if skill.scope != "core":
            continue
        if skill.audience not in ("bridge", "hybrid"):
            continue
        if not skill.commands:
            continue
        skills.append(skill)
    return skills


def _format_tip(skill) -> str:
    """Build a plain-text tip message for a skill.

    Keeps it short, conversational, and Telegram-safe (no markdown).
    """
    cmd = skill.commands[0]
    cmd_name = cmd.name
    description = skill.description or cmd.description or skill.name

    lines = [
        "💡 Did you know?",
        "",
        f"/{cmd_name} — {description}",
    ]

    if cmd.usage:
        lines.append(f"Example: {cmd.usage}")

    return "\n".join(lines)


def pick_tip(instance_dir: str) -> Optional[str]:
    """Pick an unseen skill tip and return the formatted message.

    Returns None if no tip is available (no skills found).
    Side effect: marks the skill as seen in seen_tips.txt.

    Args:
        instance_dir: Path to the instance directory.

    Returns:
        Formatted tip message, or None.
    """
    from app.skills import build_registry

    instance = Path(instance_dir)
    seen_path = instance / "seen_tips.txt"

    registry = build_registry()
    eligible = _get_eligible_skills(registry)
    if not eligible:
        return None

    seen = _load_seen(seen_path)

    # Build map of primary command name -> skill for eligible skills
    skill_map = {s.commands[0].name: s for s in eligible}
    unseen = [name for name in skill_map if name not in seen]

    # All seen — reset cycle
    if not unseen:
        seen = set()
        unseen = list(skill_map.keys())

    chosen_name = random.choice(unseen)
    chosen_skill = skill_map[chosen_name]

    # Mark as seen
    seen.add(chosen_name)
    _save_seen(seen_path, seen)

    return _format_tip(chosen_skill)


def maybe_send_feature_tip(instance_dir: str) -> bool:
    """Send a feature tip if the throttle window has elapsed.

    Called from interruptible_sleep(). No-op if called too frequently
    or if a tip was already sent during the current idle period.

    Args:
        instance_dir: Path to the instance directory.

    Returns:
        True if a tip was sent, False otherwise.
    """
    global _last_tip_time, _idle_tip_sent

    if _idle_tip_sent:
        return False

    now = time.monotonic()
    if _last_tip_time > 0 and (now - _last_tip_time) < _TIP_INTERVAL:
        return False

    tip = pick_tip(instance_dir)
    if tip is None:
        return False

    # Send via outbox (bridge-retried delivery)
    from app.utils import append_to_outbox

    outbox_path = Path(instance_dir) / "outbox.md"
    append_to_outbox(outbox_path, tip)

    _last_tip_time = now
    _idle_tip_sent = True
    return True


def mark_active() -> None:
    """Reset the per-idle-period tip guard. Call when productive work resumes."""
    global _idle_tip_sent
    _idle_tip_sent = False


def reset_tip_throttle() -> None:
    """Reset the throttle timer. Useful for testing."""
    global _last_tip_time, _idle_tip_sent
    _last_tip_time = 0.0
    _idle_tip_sent = False
