"""Track koan's own HEAD commit across agent startups.

On each startup, records koan's current HEAD SHA. On subsequent startups,
detects changes and reports new commits via Telegram so the human sees
what koan code changes landed while the agent was off.

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


def _get_head(repo_path: str) -> str:
    rc, stdout, _ = run_git("rev-parse", "HEAD", cwd=repo_path, timeout=5)
    return stdout.strip() if rc == 0 else ""


def _get_log(repo_path: str, since_sha: str, limit: int = MAX_LOG_LINES) -> Tuple[List[str], int]:
    """Get oneline log from since_sha..HEAD.

    Returns (lines, total_count). lines is capped at limit; total_count
    is the real number of commits so the message can say "and N more".
    """
    rc, stdout, _ = run_git(
        "log", "--oneline", f"{since_sha}..HEAD",
        cwd=repo_path, timeout=15,
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
    """Record koan's HEAD; report changes since last startup.

    Args:
        koan_root: Path to the koan repository root.
        instance_dir: Path to instance/ directory.

    Returns:
        Telegram message string if HEAD changed, None otherwise.
    """
    old_state = _load_state(instance_dir)
    old_head = old_state.get("head", "")

    head = _get_head(koan_root)
    if not head:
        log("git", "[commit-tracker] Could not read koan HEAD")
        return None

    _save_state(instance_dir, {"head": head})

    if not old_head:
        log("git", f"[commit-tracker] First run, recording HEAD {head[:10]}")
        return None

    if old_head == head:
        return None

    lines, total = _get_log(koan_root, old_head)
    if lines:
        log("git", f"[commit-tracker] {total} new koan commit(s) since last startup")
        header = f"\U0001f4cb {total} new koan commit(s) since last startup:"
        body = "\n".join(lines)
        if total > MAX_LOG_LINES:
            body += f"\n… and {total - MAX_LOG_LINES} more"
        return f"{header}\n{body}"

    log("git", "[commit-tracker] HEAD changed but log empty (force-push or rebase?)")
    return f"\U0001f4cb koan HEAD changed: {old_head[:10]} → {head[:10]} (no linear log — force-push?)"
