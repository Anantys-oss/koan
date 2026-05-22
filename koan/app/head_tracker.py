"""Track remote HEAD changes and update local workspace accordingly.

Detects when a remote repository's default branch switches (e.g.,
master → main) and updates the local checkout to match.

State is persisted in instance/.head-tracker.json so checks are
incremental — only the network query is repeated, not the full update.
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from app.git_utils import run_git

logger = logging.getLogger(__name__)

TRACKER_FILE = ".head-tracker.json"
MIN_CHECK_INTERVAL_HOURS = 12


@dataclass
class HeadChange:
    """Records a detected HEAD change for a single project."""

    project_name: str
    remote: str
    old_branch: str
    new_branch: str
    updated: bool = False
    error: Optional[str] = None


def _load_tracker(instance_dir: str) -> Dict:
    path = Path(instance_dir) / TRACKER_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_tracker(instance_dir: str, data: Dict) -> None:
    from app.utils import atomic_write_json
    path = Path(instance_dir) / TRACKER_FILE
    atomic_write_json(path, data, indent=2)


def _get_remote_head(remote: str, project_path: str) -> Optional[str]:
    """Query the remote for its current HEAD branch.

    Uses git ls-remote --symref which is a single lightweight network call.
    Returns branch name (e.g. 'main') or None on failure.
    """
    rc, stdout, _ = run_git(
        "ls-remote", "--symref", remote, "HEAD",
        cwd=project_path, timeout=15,
    )
    if rc != 0 or not stdout:
        return None
    for line in stdout.splitlines():
        if line.startswith("ref:") and "HEAD" in line:
            ref_part = line.split()[1]
            return ref_part.rsplit("/", 1)[-1] or None
    return None


def _get_local_head_ref(remote: str, project_path: str) -> Optional[str]:
    """Read the local symbolic ref for a remote's HEAD (no network)."""
    rc, stdout, _ = run_git(
        "symbolic-ref", f"refs/remotes/{remote}/HEAD", cwd=project_path
    )
    if rc == 0 and stdout:
        return stdout.strip().rsplit("/", 1)[-1] or None
    return None


def _update_local_head(
    remote: str, new_branch: str, old_branch: Optional[str], project_path: str
) -> Optional[str]:
    """Update the local workspace to track the new default branch.

    Steps:
    1. Set the remote HEAD symbolic ref
    2. Fetch the new branch
    3. Create local branch tracking the remote if needed
    4. If currently on the old branch, switch to the new one

    Returns error string on failure, None on success.
    """
    # Update symbolic ref so future git operations see the new default
    rc, _, stderr = run_git(
        "remote", "set-head", remote, new_branch, cwd=project_path
    )
    if rc != 0:
        return f"set-head failed: {stderr}"

    # Fetch the new branch
    refspec = f"+refs/heads/{new_branch}:refs/remotes/{remote}/{new_branch}"
    rc, _, stderr = run_git("fetch", remote, refspec, cwd=project_path, timeout=30)
    if rc != 0:
        return f"fetch failed: {stderr}"

    # Ensure local branch exists
    rc, _, _ = run_git("rev-parse", "--verify", new_branch, cwd=project_path)
    if rc != 0:
        rc, _, stderr = run_git(
            "branch", "--track", new_branch, f"{remote}/{new_branch}",
            cwd=project_path,
        )
        if rc != 0:
            return f"branch create failed: {stderr}"

    # If on the old branch, switch to the new one
    rc, current, _ = run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=project_path)
    if rc == 0 and current.strip() == old_branch:
        rc, _, stderr = run_git("checkout", new_branch, cwd=project_path)
        if rc != 0:
            return f"checkout failed: {stderr}"
        rc, _, stderr = run_git(
            "merge", "--ff-only", f"{remote}/{new_branch}", cwd=project_path
        )
        if rc != 0:
            logger.debug("ff-only merge failed after branch switch: %s", stderr)

    return None


def check_project_head(
    project_name: str,
    project_path: str,
    remote: str,
    instance_dir: str,
    force: bool = False,
) -> Optional[HeadChange]:
    """Check if a project's remote HEAD has changed.

    Args:
        project_name: Name of the project.
        project_path: Path to the project's git repo.
        remote: Remote name (e.g. 'origin', 'upstream').
        instance_dir: Path to instance directory (for tracker state).
        force: Skip the throttle check and query the remote regardless.

    Returns:
        HeadChange if a change was detected, None otherwise.
    """
    tracker = _load_tracker(instance_dir)
    project_state = tracker.get(project_name, {})

    # Throttle: skip if checked recently (unless forced)
    if not force:
        last_check = project_state.get("last_check", 0)
        hours_since = (time.time() - last_check) / 3600
        if hours_since < MIN_CHECK_INTERVAL_HOURS:
            return None

    # Query remote
    remote_head = _get_remote_head(remote, project_path)
    if not remote_head:
        logger.debug("Could not determine remote HEAD for %s/%s", remote, project_name)
        return None

    # Record the check
    project_state["last_check"] = time.time()
    project_state["remote"] = remote

    # Compare against known state
    known_head = project_state.get("head_branch")
    local_head = _get_local_head_ref(remote, project_path)
    old_branch = known_head or local_head

    if old_branch and old_branch != remote_head:
        # HEAD changed!
        change = HeadChange(
            project_name=project_name,
            remote=remote,
            old_branch=old_branch,
            new_branch=remote_head,
        )

        error = _update_local_head(remote, remote_head, old_branch, project_path)
        if error:
            change.error = error
            logger.warning(
                "HEAD change detected for %s (%s → %s) but update failed: %s",
                project_name, old_branch, remote_head, error,
            )
        else:
            change.updated = True
            logger.info(
                "HEAD change for %s: %s → %s (updated)",
                project_name, old_branch, remote_head,
            )

        project_state["head_branch"] = remote_head
        tracker[project_name] = project_state
        _save_tracker(instance_dir, tracker)
        return change

    # No change — just update tracker state
    if not known_head:
        project_state["head_branch"] = remote_head
    tracker[project_name] = project_state
    _save_tracker(instance_dir, tracker)
    return None


def check_all_projects(
    projects: List,
    instance_dir: str,
    koan_root: str,
    force: bool = False,
) -> List[HeadChange]:
    """Check all projects for remote HEAD changes.

    Args:
        projects: List of (name, path) tuples.
        instance_dir: Path to instance directory.
        koan_root: Path to KOAN_ROOT.
        force: Skip throttle, check all projects now.

    Returns:
        List of HeadChange objects for projects with detected changes.
    """
    from app.git_prep import get_upstream_remote

    changes = []
    for name, path in projects:
        if not (Path(path) / ".git").exists():
            continue
        try:
            remote = get_upstream_remote(path, name, koan_root)
            change = check_project_head(name, path, remote, instance_dir, force=force)
            if change:
                changes.append(change)
        except Exception as e:
            logger.warning("HEAD check failed for %s: %s", name, e)

    return changes


def format_changes_report(changes: List[HeadChange]) -> str:
    """Format HEAD changes into a human-readable report."""
    if not changes:
        return "No remote HEAD changes detected."

    lines = [f"Remote HEAD changes detected ({len(changes)}):"]
    for c in changes:
        status = "updated" if c.updated else f"FAILED: {c.error}"
        lines.append(
            f"  {c.project_name}: {c.old_branch} → {c.new_branch} ({status})"
        )
    return "\n".join(lines)
