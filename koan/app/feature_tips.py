"""
Kōan — Feature tip system.

Proactively surfaces one undiscovered skill to the user via Telegram
each time the agent enters idle sleep, increasing feature adoption.

Smart selection: tracks which skills the user has actually used (90 days)
and which hints were recently shown (7 days). Prioritizes key development
skills the user hasn't tried, and avoids repeating hints.

Throttled: at most once every 6 hours.
"""

import random
import time
from pathlib import Path
from typing import Optional

from app.utils import atomic_write

_TIP_INTERVAL = 6 * 60 * 60

_last_tip_time: float = 0.0

# When True, no new tips are sent (one tip already delivered this idle period).
_idle_tip_sent: bool = False

_KEY_DEV_SKILLS = frozenset({
    "fix", "plan", "review", "implement", "rebase", "squash",
    "pr", "check", "ci_check", "dead_code", "refactor",
    "security_audit", "tech_debt", "explain",
})


def _get_eligible_skills(registry) -> list:
    """Return core bridge-visible skills suitable for tips."""
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
    """Build a plain-text tip message for a skill."""
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


def _score_skill(
    skill_name: str,
    used_skills: set,
    recently_hinted: set,
) -> int:
    """Score a skill for tip priority. Higher = more likely to be shown.

    Returns -1 to exclude the skill entirely.
    """
    if skill_name in recently_hinted:
        return -1

    score = 0

    if skill_name not in used_skills:
        score += 10

    if skill_name in _KEY_DEV_SKILLS:
        if skill_name not in used_skills:
            score += 5
        else:
            score += 2

    return score


def pick_tip(instance_dir: str) -> Optional[str]:
    """Pick a skill tip using usage-aware scoring.

    Priority: unused key dev skills > unused other skills > used key skills > rest.
    Excludes skills hinted within the last 7 days.
    Falls back to random selection from the top-scoring group.

    Returns None if no tip is available.
    Side effect: records the hint in hint history.
    """
    from app.skill_usage import get_recently_hinted, get_used_skills, record_hint_shown
    from app.skills import build_registry

    instance = Path(instance_dir)

    registry = build_registry()
    eligible = _get_eligible_skills(registry)
    if not eligible:
        return None

    used_skills = get_used_skills(str(instance))
    recently_hinted = get_recently_hinted(str(instance))

    skill_map = {s.commands[0].name: s for s in eligible}

    scored = []
    for name in skill_map:
        s = _score_skill(name, used_skills, recently_hinted)
        if s >= 0:
            scored.append((name, s))

    if not scored:
        return None

    max_score = max(s for _, s in scored)
    top_tier = [name for name, s in scored if s == max_score]

    chosen_name = random.choice(top_tier)
    chosen_skill = skill_map[chosen_name]

    record_hint_shown(str(instance), chosen_name)

    return _format_tip(chosen_skill)


def maybe_send_feature_tip(instance_dir: str) -> bool:
    """Send a feature tip if the throttle window has elapsed.

    Called from interruptible_sleep(). No-op if called too frequently
    or if a tip was already sent during the current idle period.
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
