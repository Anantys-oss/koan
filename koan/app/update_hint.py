"""
Koan -- Upstream update hint.

Surfaces a Telegram notification when the local Koan install is behind
upstream, at most once every 48 hours.  Triggered at startup (run_num == 0)
and during idle sleep (alongside feature tips).

Runtime state: ``instance/.update-hint.json``
  ``{"last_notified_at": "2026-05-18T12:00:00+00:00"}``
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.auto_update import check_for_updates
from app.notify import send_telegram
from app.run_log import log
from app.update_manager import _find_upstream_remote
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


def _get_missing_commits(koan_root: Path, remote: str) -> Optional[list]:
    """Return list of commit subject lines we are behind upstream.

    Uses ``git log HEAD..{remote}/main --oneline`` to get compact summaries.
    Returns None on error, empty list if up-to-date.
    """
    try:
        result = subprocess.run(
            ["git", "log", f"HEAD..{remote}/main", "--oneline", "--no-decorate"],
            capture_output=True,
            text=True,
            cwd=str(koan_root),
            timeout=15,
        )
        if result.returncode != 0:
            return None
        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        return lines
    except (subprocess.TimeoutExpired, OSError):
        return None


def _format_update_message(commits: list) -> str:
    """Build the Telegram notification message.

    Unicode prefix title + fenced code block of commit subjects.
    """
    count = len(commits)
    title = f"\u2b06\ufe0f Koan update available \u2014 {count} new commit{'s' if count != 1 else ''}"
    # Cap the displayed commits to avoid overly long messages
    display = commits[:20]
    code_block = "\n".join(display)
    if len(commits) > 20:
        code_block += f"\n... and {len(commits) - 20} more"
    return f"{title}\n\n```\n{code_block}\n```\n\nRun /update to apply."


def maybe_send_update_hint(instance_dir: str, koan_root: str) -> bool:
    """Check for upstream updates and notify if behind (throttled to 48 h).

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

    # 2. Check upstream for new commits (reuses auto_update's lightweight fetch)
    try:
        count = check_for_updates(koan_root)
    except Exception as e:
        log("update-hint", f"check_for_updates failed: {e}")
        return False

    if not count:
        return False

    # 3. Get commit subjects for the message body
    try:
        remote = _find_upstream_remote(Path(koan_root))
    except Exception as e:
        log("update-hint", f"Failed to find upstream remote: {e}")
        remote = None

    if remote is None:
        return False

    commits = _get_missing_commits(Path(koan_root), remote)
    if not commits:
        return False

    # 4. Build and send message
    message = _format_update_message(commits)
    try:
        send_telegram(message)
    except Exception as e:
        log("update-hint", f"Failed to send update hint: {e}")
        return False

    # 5. Update cooldown state
    _write_last_notified(state_path)
    log("update-hint", f"Notified user about {len(commits)} upstream commit(s)")
    return True
