"""Track project HEAD commits across agent startups.

On each startup, records the current HEAD SHA for every managed project.
On subsequent startups, detects changes and reports new commits via
Telegram so the human sees what landed while the agent was off.

State persisted in instance/.commit-tracker.json.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

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


def _get_head(project_path: str) -> str:
    rc, stdout, _ = run_git("rev-parse", "HEAD", cwd=project_path, timeout=5)
    return stdout.strip() if rc == 0 else ""


def _get_log(project_path: str, since_sha: str, limit: int = MAX_LOG_LINES) -> Tuple[List[str], int]:
    """Get oneline log from since_sha..HEAD.

    Returns (lines, total_count). lines is capped at limit; total_count
    is the real number of commits so the message can say "and N more".
    """
    rc, stdout, _ = run_git(
        "log", "--oneline", f"{since_sha}..HEAD",
        cwd=project_path, timeout=15,
    )
    if rc != 0 or not stdout.strip():
        return [], 0
    all_lines = stdout.strip().splitlines()
    total = len(all_lines)
    return all_lines[:limit], total


def record_and_report(
    projects: list,
    instance_dir: str,
) -> List[str]:
    """Record HEAD for each project; report changes since last startup.

    Args:
        projects: List of (name, path) tuples.
        instance_dir: Path to instance/ directory.

    Returns:
        List of Telegram message strings (one per changed project,
        plus one for first-run). Empty if nothing to report.
    """
    old_state = _load_state(instance_dir)
    new_state: Dict[str, str] = {}
    messages: List[str] = []
    first_run = not old_state

    for name, path in projects:
        head = _get_head(path)
        if not head:
            log("git", f"[commit-tracker] Could not read HEAD for {name}")
            continue
        new_state[name] = head
        old_head = old_state.get(name, "")

        if first_run:
            short = head[:10]
            log("git", f"[commit-tracker] {name}: recording HEAD {short}")
        elif not old_head:
            short = head[:10]
            log("git", f"[commit-tracker] {name}: new project, recording HEAD {short}")
        elif old_head != head:
            lines, total = _get_log(path, old_head)
            if lines:
                log("git", f"[commit-tracker] {name}: {total} new commit(s) since last startup")
                header = f"📋 [{name}] {total} new commit(s) since last startup:"
                body = "\n".join(lines)
                if total > MAX_LOG_LINES:
                    body += f"\n… and {total - MAX_LOG_LINES} more"
                messages.append(f"{header}\n{body}")
            else:
                log("git", f"[commit-tracker] {name}: HEAD changed but log empty (force-push or rebase?)")
                messages.append(
                    f"📋 [{name}] HEAD changed: {old_head[:10]} → {head[:10]} (no linear log — force-push?)"
                )

    _save_state(instance_dir, new_state)

    if first_run and new_state:
        heads = ", ".join(f"{n}: {s[:10]}" for n, s in sorted(new_state.items()))
        messages.append(f"📌 Commit tracker initialized. Heads: {heads}")

    return messages
