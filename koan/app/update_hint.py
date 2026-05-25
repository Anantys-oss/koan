"""
Koan -- Upstream update hint (tag-based).

Surfaces a Telegram notification when a new release tag appears upstream,
at most once every 48 hours.  Triggered at startup (run_num == 0)
and during idle sleep (alongside feature tips).

Uses the same tag-based mechanism as auto_update: compares latest upstream
tag against a cached value. The notification message informs the user
a new release is available and suggests /update.

Runtime state: ``instance/.update-hint.json``
  ``{"last_notified_at": "2026-05-18T12:00:00+00:00"}``
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.auto_update import check_for_updates, check_for_new_release_tag, _write_last_notified_tag
from app.notify import send_telegram
from app.run_log import log
from app.utils import atomic_write

# Cooldown: one notification every 48 hours.
_HINT_INTERVAL_SECONDS = 48 * 60 * 60

_STATE_FILE = ".update-hint.json"


def _read_last_notified(state_path: Path) -> Optional[datetime]:
    """Read the last notification timestamp from the state file."""
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        ts = data.get("last_notified_at")
        if ts:
            return datetime.fromisoformat(ts)
    except (json.JSONDecodeError, ValueError, OSError):
        pass
    return None


def _write_last_notified(state_path: Path) -> None:
    """Persist the current UTC timestamp as last notification time."""
    data = json.dumps({"last_notified_at": datetime.now(timezone.utc).isoformat()})
    atomic_write(state_path, data + "\n")


def _is_within_cooldown(state_path: Path) -> bool:
    """Return True if the last notification was sent less than 48 h ago."""
    last = _read_last_notified(state_path)
    if last is None:
        return False
    now = datetime.now(timezone.utc)
    # Ensure last is timezone-aware for comparison
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() < _HINT_INTERVAL_SECONDS


def _format_tag_update_message(tag: str) -> str:
    """Build the Telegram notification message for a new release tag."""
    return (
        f"⬆️ New Koan release available: **{tag}**\n\n"
        f"Run /update to apply."
    )


def maybe_send_update_hint(instance_dir: str, koan_root: str) -> bool:
    """Check for new release tags and notify if one is available (throttled to 48 h).

    Uses the same tag-based mechanism as auto_update: fetches tags via
    check_for_updates(), then compares the latest tag against a cached value.

    Called at startup and during idle sleep.  Returns True if a notification
    was sent, False otherwise.

    Args:
        instance_dir: Path to the instance directory.
        koan_root: Path to KOAN_ROOT (the Koan repo itself).

    Returns:
        True if a hint was sent, False otherwise.
    """
    instance = Path(instance_dir)
    state_path = instance / _STATE_FILE

    # 1. Cooldown gate
    if _is_within_cooldown(state_path):
        return False

    # 2. Fetch upstream refs + tags (reuses auto_update's lightweight fetch)
    try:
        fetch_result = check_for_updates(koan_root)
    except Exception as e:
        log("update-hint", f"check_for_updates failed: {e}")
        return False

    if fetch_result is None:
        return False

    # 3. Check for new release tag (same mechanism as auto_update)
    new_tag = check_for_new_release_tag(koan_root, instance_dir)
    if not new_tag:
        return False

    # 4. Build and send message
    message = _format_tag_update_message(new_tag)
    try:
        send_telegram(message)
    except Exception as e:
        log("update-hint", f"Failed to send update hint: {e}")
        return False

    # 5. Record tag + update cooldown state
    _write_last_notified_tag(instance_dir, new_tag)
    _write_last_notified(state_path)
    log("update-hint", f"Notified user about new release tag: {new_tag}")
    return True
