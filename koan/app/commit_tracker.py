"""Track Kōan's own HEAD commit across agent startups.

On each startup, records the current HEAD SHA of the Kōan repository.
On subsequent startups, detects changes and reports new commits via
Telegram so the human sees what changed in the agent itself.

State persisted in instance/.commit-tracker.json.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.git_utils import run_git
from app.run_log import log

TRACKER_FILE = ".commit-tracker.json"
MAX_LOG_LINES = 15


def _load_state(instance_dir: str) -> Dict[str, str]:
    path = Path(instance_dir) / TRACKER_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(instance_dir: str, state: Dict[str, str]) -> None:
    from app.utils import atomic_write_json
    path = Path(instance_dir) / TRACKER_FILE
    atomic_write_json(path, state, indent=2)


def _get_head(koan_root: str) -> str:
    rc, stdout, _ = run_git("rev-parse", "HEAD", cwd=koan_root, timeout=5)
    return stdout.strip() if rc == 0 else ""


def _get_log(koan_root: str, since_sha: str, limit: int = MAX_LOG_LINES) -> Tuple[List[str], int]:
    """Get oneline log from since_sha..HEAD.

    Returns (lines, total_count). lines is capped at limit; total_count
    is the real number of commits so the message can say "and N more".
    """
    rc, stdout, _ = run_git(
        "log", "--oneline", f"{since_sha}..HEAD",
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
    old_state = _load_state(instance_dir)
    head = _get_head(koan_root)
    if not head:
        log("git", "[commit-tracker] Could not read Kōan HEAD")
        return None

    old_head = old_state.get("koan", "")
    new_state = {**old_state, "koan": head}
    _save_state(instance_dir, new_state)

    if not old_head:
        short = head[:10]
        log("git", f"[commit-tracker] First run — recording Kōan HEAD {short}")
        return None

    if old_head == head:
        log("git", "[commit-tracker] Kōan unchanged since last startup")
        return None

    lines, total = _get_log(koan_root, old_head)
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
    return f"{header}\n{body}"
