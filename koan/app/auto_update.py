"""Automatic update checker and self-commit tracker for Kōan.

Handles two related concerns:

1. **Auto-update**: periodically checks if upstream has new commits and
   triggers a pull + restart when updates are available.
2. **Commit tracking**: on each startup, records Kōan's HEAD SHA and
   reports new commits since the last startup via Telegram.

Configuration (config.yaml):
    auto_update:
        enabled: true           # default: false
        check_interval: 10      # check every N iterations (default: 10)
        notify: true            # notify on Telegram before updating (default: true)

The check is lightweight (git fetch + rev-list count) and only
triggers a full pull when new commits are actually available.

Notification is tag-based: a Telegram message is sent only when a new
release tag appears on upstream. The actual update mechanism always
pulls from upstream main regardless of tags.

State files:
    instance/.last-notified-tag   — last release tag notified about
    instance/.commit-tracker.json — last known Kōan HEAD SHA
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.git_utils import run_git as _run_git_utils
from app.run_log import log
from app.update_manager import (
    find_upstream_remote,
    _run_git,
)


# Module-level cache to avoid fetching too often
_last_check_time: Optional[float] = None
_MIN_CHECK_INTERVAL_SECONDS = 120  # never check more than once per 2 min


def _load_auto_update_config() -> dict:
    """Load auto_update config section with defaults."""
    try:
        from app.utils import load_config
        config = load_config()
    except Exception as e:
        log("update", f"Config load failed, using defaults: {e}")
        config = {}
    section = config.get("auto_update", {})
    if not isinstance(section, dict):
        section = {}
    return {
        "enabled": bool(section.get("enabled", False)),
        "check_interval": int(section.get("check_interval", 10)),
        "notify": bool(section.get("notify", True)),
    }


def is_auto_update_enabled() -> bool:
    """Check if auto-update is enabled in config."""
    return _load_auto_update_config()["enabled"]


def get_check_interval() -> int:
    """Get the iteration interval for update checks."""
    return _load_auto_update_config()["check_interval"]


def check_for_updates(koan_root: str) -> Optional[int]:
    """Check if upstream has new commits without pulling.

    Returns the number of commits ahead, or None on error.
    Caches the result to avoid hammering git fetch.
    """
    global _last_check_time
    now = time.monotonic()
    if _last_check_time is not None and now - _last_check_time < _MIN_CHECK_INTERVAL_SECONDS:
        return 0
    _last_check_time = now

    koan_path = Path(koan_root)
    remote = find_upstream_remote(koan_path)
    if remote is None:
        log("update", "No upstream remote found, skipping update check")
        return None

    # Fetch upstream (lightweight, only refs + tags)
    result = _run_git(["fetch", remote, "--tags", "--quiet"], koan_path)
    if result.returncode != 0:
        log("update", f"Fetch failed: {result.stderr.strip()}")
        return None

    # Compare local main vs remote main
    result = _run_git(
        ["rev-list", "--count", f"main..{remote}/main"],
        koan_path,
    )
    if result.returncode != 0:
        log("update", f"Rev-list failed: {result.stderr.strip()}")
        return None

    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _get_latest_tag(koan_path: Path) -> Optional[str]:
    """Get the latest tag by version sort order.

    Uses git tag --sort=-version:refname for reliable results
    across all git versions (avoids git describe quirks).
    """
    result = _run_git(
        ["tag", "--sort=-version:refname"],
        koan_path,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # First line is the latest tag
    return result.stdout.strip().splitlines()[0]


def _read_last_notified_tag(instance_dir: str) -> Optional[str]:
    """Read the last tag we notified about."""
    tag_file = Path(instance_dir) / ".last-notified-tag"
    try:
        return tag_file.read_text().strip() or None
    except FileNotFoundError:
        return None


def _write_last_notified_tag(instance_dir: str, tag: str) -> None:
    """Record the tag we just notified about."""
    tag_file = Path(instance_dir) / ".last-notified-tag"
    tag_file.write_text(tag)


def _head_includes_tag(koan_path: Path, tag: str) -> bool:
    """Check if HEAD already includes the given tag (tag is ancestor of HEAD)."""
    result = _run_git(["merge-base", "--is-ancestor", tag, "HEAD"], koan_path)
    return result.returncode == 0


def check_for_new_release_tag(koan_root: str, instance_dir: str) -> Optional[str]:
    """Check if upstream has a new release tag we haven't notified about.

    Returns the new tag name if one is found, None otherwise.
    Assumes tags have already been fetched by check_for_updates().

    Suppresses notification when HEAD already includes the tagged commit
    (e.g. we're on the tag or ahead of it with extra commits).
    """
    koan_path = Path(koan_root)
    latest_tag = _get_latest_tag(koan_path)
    if latest_tag is None:
        return None

    last_notified = _read_last_notified_tag(instance_dir)
    if latest_tag == last_notified:
        return None

    if _head_includes_tag(koan_path, latest_tag):
        _write_last_notified_tag(instance_dir, latest_tag)
        log("update", f"HEAD already includes tag {latest_tag}, skipping notification")
        return None

    return latest_tag


def perform_auto_update(koan_root: str, instance: str) -> bool:
    """Check for updates and trigger pull + restart if available.

    Notification is tag-based: a Telegram message is sent only when a new
    release tag appears on upstream. The update mechanism always pulls from
    upstream main regardless of tags.

    Returns True if an update was triggered (caller should exit).
    Returns False if no update needed or update failed.
    """
    config = _load_auto_update_config()
    if not config["enabled"]:
        return False

    commits_ahead = check_for_updates(koan_root)
    if not commits_ahead:
        # Even with no new commits, check for new tags to notify about
        # (tag may have been pushed without new commits on main)
        if config["notify"]:
            new_tag = check_for_new_release_tag(koan_root, instance)
            if new_tag:
                _notify_new_release_tag(new_tag, instance)
        return False

    log("update", f"Upstream has {commits_ahead} new commit(s). Pulling...")

    # Check for new release tag before pulling (notify is tag-based)
    new_tag = None
    if config["notify"]:
        new_tag = check_for_new_release_tag(koan_root, instance)
        if new_tag:
            try:
                _notify_new_release_tag(new_tag, instance)
            except Exception as e:
                log("error", f"Tag notification failed: {e}")

    # Pull
    from app.update_manager import pull_upstream
    result = pull_upstream(Path(koan_root))

    if not result.success:
        log("error", f"Auto-update pull failed: {result.error}")
        if config["notify"] and new_tag:
            try:
                from app.notify import format_and_send
                format_and_send(
                    f"❌ Auto-update pull failed after tag {new_tag}: {result.error}",
                    instance_dir=instance,
                )
            except Exception as e:
                log("error", f"Failed to notify pull failure: {e}")
        return False

    log("update", result.summary())

    if not result.changed:
        return False

    # Trigger restart
    from app.restart_manager import request_restart
    from app.pause_manager import remove_pause
    remove_pause(koan_root)
    request_restart(koan_root)

    return True


def _notify_new_release_tag(tag: str, instance_dir: str) -> None:
    """Send a Telegram notification about a new release tag."""
    log("update", f"New release tag detected: {tag}")
    try:
        from app.notify import format_and_send
        format_and_send(
            f"🏷️ New release available: **{tag}**\n"
            f"Pulling latest changes and restarting...",
            instance_dir=instance_dir,
        )
        # Only record after successful notification to avoid
        # missing the notification if the process restarts
        _write_last_notified_tag(instance_dir, tag)
    except Exception as e:
        log("error", f"Failed to notify new release tag: {e}")


def reset_check_cache():
    """Reset the check cache (for testing)."""
    global _last_check_time
    _last_check_time = None


# ---------------------------------------------------------------------------
# Commit tracking — record Kōan HEAD across startups, report what changed
# ---------------------------------------------------------------------------

TRACKER_FILE = ".commit-tracker.json"
MAX_LOG_LINES = 15


def _load_commit_state(instance_dir: str) -> Dict[str, str]:
    path = Path(instance_dir) / TRACKER_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_commit_state(instance_dir: str, state: Dict[str, str]) -> None:
    from app.utils import atomic_write_json
    path = Path(instance_dir) / TRACKER_FILE
    atomic_write_json(path, state, indent=2)


def _get_koan_head(koan_root: str) -> str:
    rc, stdout, _ = _run_git_utils("rev-parse", "HEAD", cwd=koan_root, timeout=5)
    return stdout.strip() if rc == 0 else ""


def _get_commit_log(koan_root: str, since_sha: str, limit: int = MAX_LOG_LINES) -> Tuple[List[str], int]:
    """Get oneline log from since_sha..HEAD.

    Returns (lines, total_count). lines is capped at limit; total_count
    is the real number of commits so the message can say "and N more".
    """
    rc, stdout, _ = _run_git_utils(
        "log", "--oneline", "--no-merges", f"{since_sha}..HEAD",
        cwd=koan_root, timeout=15,
    )
    if rc != 0 or not stdout.strip():
        return [], 0
    all_lines = stdout.strip().splitlines()
    total = len(all_lines)
    return all_lines[:limit], total


def record_and_report(
    koan_root: str,
    instance_dir: str,
) -> Optional[str]:
    """Record Kōan's HEAD; report changes since last startup.

    Args:
        koan_root: Path to the Kōan repository root.
        instance_dir: Path to instance/ directory.

    Returns:
        Telegram message string if there are changes, None otherwise.
    """
    old_state = _load_commit_state(instance_dir)
    head = _get_koan_head(koan_root)
    if not head:
        log("git", "[commit-tracker] Could not read Kōan HEAD")
        return None

    old_head = old_state.get("koan", "")
    new_state = {**old_state, "koan": head}
    _save_commit_state(instance_dir, new_state)

    if not old_head:
        short = head[:10]
        log("git", f"[commit-tracker] First run — recording Kōan HEAD {short}")
        return None

    if old_head == head:
        log("git", "[commit-tracker] Kōan unchanged since last startup")
        return None

    lines, total = _get_commit_log(koan_root, old_head)
    if not lines:
        short_old = old_head[:10]
        short_new = head[:10]
        log("git", f"[commit-tracker] Kōan HEAD changed ({short_old}→{short_new}) but no linear log")
        return f"📋 Kōan updated ({short_old}→{short_new}), non-linear history"

    log("git", f"[commit-tracker] Kōan: {total} new commit(s) since last startup")
    header = f"📋 Kōan: {total} new commit(s) since last startup:"
    body = "\n".join(lines)
    if total > MAX_LOG_LINES:
        body += f"\n… and {total - MAX_LOG_LINES} more"
    return f"{header}\n```\n{body}\n```"
