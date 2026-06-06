#!/usr/bin/env python3
"""
Kōan — Git sync awareness

Checks what changed in the repo since last run:
- Which koan/* branches were merged or deleted
- Recent commits on main by the human
- Current branch state

Writes a summary to the journal so Kōan stays aware of repo evolution
between runs. Called from run.py periodically (every N runs).

Usage:
    python3 git_sync.py <instance_dir> <project_name> <project_path>
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.git_utils import run_git as _run_git_core

log = logging.getLogger(__name__)

# Branches updated within this many days are shown in detail;
# older branches are collapsed into a summary line.
RECENT_BRANCH_DAYS = 7

CLEANUP_TRACKER_FILE = ".branch-cleanup-tracker.json"


def _load_cleanup_tracker(instance_dir: str) -> dict:
    path = Path(instance_dir) / CLEANUP_TRACKER_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cleanup_tracker(instance_dir: str, data: dict) -> None:
    from app.utils import atomic_write_json
    path = Path(instance_dir) / CLEANUP_TRACKER_FILE
    atomic_write_json(path, data, indent=2)


# ---------------------------------------------------------------------------
# Low-level git helpers (stateless)
# ---------------------------------------------------------------------------

def run_git(cwd: str, *args: str) -> str:
    """Run a git command and return stdout, or empty string on failure.

    Thin wrapper around git_utils.run_git() preserving the original
    string-return interface for backward compatibility.
    """
    rc, stdout, _ = _run_git_core(*args, cwd=cwd, timeout=10)
    return stdout if rc == 0 else ""


def _get_prefix() -> str:
    """Get the configured branch prefix (lazy import to avoid circular deps)."""
    from app.config import get_branch_prefix
    return get_branch_prefix()


def get_branch_cleanup_config() -> dict:
    """Get branch cleanup configuration (lazy import to avoid circular deps)."""
    from app.config import get_branch_cleanup_config as _get_cfg
    return _get_cfg()


def _normalize_branch(line: str, prefix: str = "") -> str:
    """Extract agent branch name from git branch output line.

    Args:
        line: Raw line from git branch output.
        prefix: Branch prefix to match (e.g., 'koan/'). If empty, uses config.
    """
    if not prefix:
        prefix = _get_prefix()
    name = line.strip().lstrip("* ")
    if "remotes/origin/" in name:
        name = name.replace("remotes/origin/", "")
    return name if name.startswith(prefix) else ""


# ---------------------------------------------------------------------------
# GitSync class — encapsulates project context
# ---------------------------------------------------------------------------

class GitSync:
    """Tracks git state changes between koan runs for a specific project.

    Encapsulates the project_path and instance_dir so callers don't need
    to thread them through every function call.
    """

    def __init__(self, instance_dir: str, project_name: str, project_path: str):
        self.instance_dir = instance_dir
        self.project_name = project_name
        self.project_path = project_path

    def get_koan_branches(self) -> List[str]:
        """List all agent branches (local and remote)."""
        prefix = _get_prefix()
        glob_pattern = f"{prefix}*"
        output = run_git(self.project_path, "branch", "-a", "--list", glob_pattern)
        branches = []
        for line in output.splitlines():
            name = line.strip().lstrip("* ")
            if "remotes/origin/" in name:
                name = name.replace("remotes/origin/", "")
            if name.startswith(prefix):
                branches.append(name)
        return sorted(set(branches))

    def get_recent_main_commits(self, since_hours: int = 12) -> List[str]:
        """Get recent commits on main (last N hours)."""
        output = run_git(
            self.project_path, "log", "origin/main",
            f"--since={since_hours} hours ago",
            "--oneline", "--no-merges", "-20"
        )
        return [line for line in output.splitlines() if line.strip()]

    def _get_target_branches(self) -> List[str]:
        """Return remote target branches that exist in this repo."""
        candidates = ["origin/main", "origin/master", "origin/staging", "origin/develop", "origin/production"]
        existing = [
            ref for ref in candidates
            if run_git(self.project_path, "rev-parse", "--verify", ref)
        ]
        return existing or ["origin/main"]

    def get_merged_branches(self) -> List[str]:
        """List agent branches merged into any target branch."""
        prefix = _get_prefix()
        glob_pattern = f"{prefix}*"
        targets = self._get_target_branches()
        merged = set()
        for target in targets:
            output = run_git(self.project_path, "branch", "-a", "--merged", target,
                             "--list", glob_pattern)
            for line in output.splitlines():
                name = _normalize_branch(line, prefix)
                if name:
                    merged.add(name)
        return sorted(merged)

    def get_unmerged_branches(self) -> List[str]:
        """List koan/* branches NOT merged into any target branch."""
        all_koan = set(self.get_koan_branches())
        merged = set(self.get_merged_branches())
        return sorted(all_koan - merged)

    def _should_run_cleanup(self) -> bool:
        """Check if enough time has passed since last cleanup for this project."""
        cleanup_cfg = get_branch_cleanup_config()
        interval_hours = cleanup_cfg.get("cleanup_interval_hours", 24)
        tracker = _load_cleanup_tracker(self.instance_dir)
        project_data = tracker.get(self.project_name, {})
        last_ts = project_data.get("last_cleanup_ts", 0)
        elapsed_hours = (time.time() - last_ts) / 3600
        return elapsed_hours >= interval_hours

    def _record_cleanup(self) -> None:
        """Record that cleanup ran for this project."""
        tracker = _load_cleanup_tracker(self.instance_dir)
        if self.project_name not in tracker:
            tracker[self.project_name] = {}
        tracker[self.project_name]["last_cleanup_ts"] = time.time()
        _save_cleanup_tracker(self.instance_dir, tracker)

    def get_orphan_branches(self) -> List[str]:
        """Find unmerged agent branches that have no open PR.

        "Orphans" are branches with the agent prefix that are neither merged
        nor backing an open pull request — likely leftovers from aborted or
        forgotten work.

        Returns empty list on GitHub API errors (fail-safe: never false-positive).
        """
        unmerged = self.get_unmerged_branches()
        if not unmerged:
            return []

        prefix = _get_prefix()
        try:
            from app.github import run_gh
            raw = run_gh(
                "pr", "list",
                "--state", "open",
                "--limit", "200",
                "--json", "headRefName",
                cwd=self.project_path,
                timeout=30,
            )
        except (RuntimeError, OSError):
            return []

        try:
            prs = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            return []

        if not isinstance(prs, list):
            return []

        pr_branches = {
            pr["headRefName"]
            for pr in prs
            if isinstance(pr, dict) and pr.get("headRefName", "").startswith(prefix)
        }

        return sorted(b for b in unmerged if b not in pr_branches)

    def _get_current_branch(self) -> str:
        """Return the current branch name, or empty string on failure."""
        return run_git(self.project_path, "rev-parse", "--abbrev-ref", "HEAD")

    def _get_local_branches(self, prefix: str) -> List[str]:
        """List local-only branches matching prefix (excludes remotes)."""
        output = run_git(self.project_path, "branch", "--list", f"{prefix}*")
        branches = []
        for line in output.splitlines():
            name = line.strip().lstrip("* ")
            if name.startswith(prefix):
                branches.append(name)
        return branches

    def get_branch_ages(self, branches: List[str]) -> Dict[str, int]:
        """Get the age in days for a list of branches.

        Uses ``git for-each-ref`` with a single subprocess call for
        efficiency, then falls back to per-branch ``git log`` for any
        branches not found (e.g. remote-only refs with different naming).

        Args:
            branches: List of branch names to look up.

        Returns:
            Dict mapping branch name to age in days. Branches whose age
            could not be determined are omitted.
        """
        if not branches:
            return {}

        prefix = _get_prefix()
        output = run_git(
            self.project_path,
            "for-each-ref",
            "--format=%(committerdate:unix) %(refname:short)",
            f"refs/heads/{prefix}*",
            f"refs/remotes/origin/{prefix}*",
        )

        now = datetime.now().timestamp()
        # Parse for-each-ref output: "1708000000 koan/fix-bug"
        # Remote refs show as "origin/koan/fix-bug", normalize them.
        ref_timestamps: Dict[str, float] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                ts = float(parts[0])
            except ValueError:
                continue
            ref_name = parts[1]
            if ref_name.startswith("origin/"):
                ref_name = ref_name[len("origin/"):]
            # Keep the most recent timestamp for each branch name
            if ref_name not in ref_timestamps or ts > ref_timestamps[ref_name]:
                ref_timestamps[ref_name] = ts

        ages: Dict[str, int] = {}
        for branch in branches:
            if branch in ref_timestamps:
                age_secs = now - ref_timestamps[branch]
                ages[branch] = max(0, int(age_secs / 86400))

        return ages

    def _split_branches_by_recency(
        self,
        branches: List[str],
        max_age_days: int = RECENT_BRANCH_DAYS,
    ) -> Tuple[List[str], List[str]]:
        """Split branches into recent and stale lists.

        Args:
            branches: Sorted list of branch names.
            max_age_days: Threshold in days; branches updated more
                recently than this are "recent".

        Returns:
            (recent, stale) tuple of sorted branch name lists.
        """
        ages = self.get_branch_ages(branches)
        recent = []
        stale = []
        for branch in branches:
            age = ages.get(branch)
            if age is not None and age > max_age_days:
                stale.append(branch)
            else:
                # Unknown age → show it (conservative: don't hide branches)
                recent.append(branch)
        return recent, stale

    def get_github_merged_branches(self) -> List[str]:
        """Find agent branches whose GitHub PRs have been merged.

        Uses ``gh pr list --state merged`` to batch-detect branches that
        were squash-merged or rebase-merged — invisible to
        ``git branch --merged`` since commit SHAs change.

        Returns:
            Sorted list of branch names whose PRs are merged on GitHub.
            Returns empty list on error (no gh CLI, not a GitHub repo, etc.).
        """
        prefix = _get_prefix()
        try:
            from app.github import run_gh
            raw = run_gh(
                "pr", "list",
                "--state", "merged",
                "--limit", "200",
                "--json", "headRefName",
                cwd=self.project_path,
                timeout=30,
            )
        except (RuntimeError, OSError) as e:
            log.debug("GitHub API: failed to list merged PRs: %s", e)
            return []

        try:
            prs = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            return []

        if not isinstance(prs, list):
            return []

        return sorted({
            pr["headRefName"]
            for pr in prs
            if isinstance(pr, dict) and pr.get("headRefName", "").startswith(prefix)
        })

    def cleanup_merged_branches(
        self,
        merged: List[str],
        github_merged: Optional[List[str]] = None,
        delete_remote: bool = True,
    ) -> List[str]:
        """Delete local (and optionally remote) branches that are confirmed merged.

        Only deletes branches matching the agent prefix. Never deletes
        the current branch.

        For git-detected merges (``merged``), uses ``git branch -d``
        (safe delete — refuses if not fully merged).

        For GitHub-detected merges (``github_merged``), uses
        ``git branch -D`` (force delete — git doesn't recognize
        squash/rebase merges as ancestors, but GitHub confirms the PR
        was merged).

        When ``delete_remote`` is True (the default), each successfully
        deleted local branch is also removed from the remote with
        ``git push origin --delete``. Remote deletion failures are
        tolerated silently — the remote branch may already be gone if
        GitHub auto-deleted it after merge.

        Args:
            merged: Branch names from get_merged_branches() (git ancestry).
            github_merged: Branch names from get_github_merged_branches()
                (GitHub API). Branches already in *merged* are skipped
                (already handled by safe delete).
            delete_remote: When True, also delete the branch on the remote
                after successful local deletion.

        Returns:
            List of successfully deleted branch names (local deletions).
        """
        current = self._get_current_branch()
        prefix = _get_prefix()
        local_branches = set(self._get_local_branches(prefix))

        deleted = []

        # Phase 1: safe delete for git-detected merges
        for branch in merged or []:
            if branch not in local_branches or branch == current:
                continue
            result = run_git(self.project_path, "branch", "-d", branch)
            if result:
                deleted.append(branch)

        # Phase 2: force delete for GitHub-detected merges (squash/rebase)
        git_merged_set = set(merged or [])
        for branch in github_merged or []:
            if branch in git_merged_set:
                continue  # Already handled in phase 1
            if branch not in local_branches or branch == current:
                continue
            if branch in deleted:
                continue  # Already deleted
            result = run_git(self.project_path, "branch", "-D", branch)
            if result:
                deleted.append(branch)
                log.debug("Cleaned up squash-merged branch: %s", branch)

        # Phase 3: delete remote tracking refs for all locally-deleted branches
        if delete_remote:
            for branch in deleted:
                result = run_git(self.project_path, "push", "origin", "--delete", branch)
                if not result:
                    log.debug("Remote deletion failed (may already be gone): %s", branch)

        return deleted

    def build_sync_report(self) -> str:
        """Build a human-readable git sync report.

        Branch cleanup (deletion of merged branches + orphan detection) is
        time-throttled: it only runs once per ``cleanup_interval_hours``
        (default 24h) per project, with timestamps persisted in
        ``.branch-cleanup-tracker.json`` so restarts don't re-trigger it.
        """
        run_git(self.project_path, "fetch", "--prune")

        merged = self.get_merged_branches()
        github_merged = self.get_github_merged_branches()
        unmerged = self.get_unmerged_branches()
        recent = self.get_recent_main_commits(since_hours=12)

        cleanup_cfg = get_branch_cleanup_config()
        cleanup_due = cleanup_cfg["enabled"] and self._should_run_cleanup()

        if cleanup_due:
            cleaned = self.cleanup_merged_branches(
                merged,
                github_merged,
                delete_remote=cleanup_cfg["delete_remote_branches"],
            )
            orphans = (
                self.get_orphan_branches()
                if cleanup_cfg.get("notify_orphans", True) else []
            )
            self._record_cleanup()
        else:
            cleaned = []
            orphans = []

        if cleaned:
            cleaned_set = set(cleaned)
            unmerged = [b for b in unmerged if b not in cleaned_set]

        parts = []
        now = datetime.now().strftime("%H:%M")
        parts.append(f"Git sync @ {now}")

        prefix = _get_prefix()
        label = f"{prefix}*"

        git_merged_set = set(merged)
        github_only = [b for b in (github_merged or []) if b not in git_merged_set]
        all_merged = merged + github_only

        if all_merged:
            parts.append(f"\nMerged {label} branches ({len(all_merged)}):")
            for b in all_merged:
                suffix = " (cleaned up)" if b in (cleaned or []) else ""
                parts.append(f"  ✓ {b}{suffix}")

        if cleaned:
            parts.append(f"\nCleaned up {len(cleaned)} merged local branch(es).")

        if orphans:
            parts.append(f"\nOrphan {label} branches ({len(orphans)}) — unmerged, no open PR:")
            parts.extend(f"  ⚠ {b}" for b in orphans)

        if unmerged:
            recent_branches, stale_branches = self._split_branches_by_recency(unmerged)
            parts.append(f"\nUnmerged {label} branches ({len(unmerged)}):")
            parts.extend(f"  → {b}" for b in recent_branches)
            if stale_branches:
                parts.append(
                    f"  ... and {len(stale_branches)} older branch(es) "
                    f"(>{RECENT_BRANCH_DAYS}d, run /list_branches to see all)"
                )

        if recent:
            parts.append(f"\nRecent main commits ({len(recent)}):")
            parts.extend(f"  {c}" for c in recent[:10])

        if not all_merged and not unmerged and not recent:
            parts.append("\nNo notable changes since last sync.")

        self._last_cleaned = cleaned
        self._last_orphans = orphans

        return "\n".join(parts)

    def write_sync_to_journal(self, report: str):
        """Append git sync report to today's journal."""
        from app.journal import append_to_journal
        entry = f"\n## Git Sync — {datetime.now().strftime('%H:%M')}\n\n{report}\n"
        append_to_journal(Path(self.instance_dir), self.project_name, entry)

    def _notify_cleanup_results(self) -> None:
        """Send outbox notification when branches were cleaned or orphans found."""
        cleaned = getattr(self, "_last_cleaned", [])
        orphans = getattr(self, "_last_orphans", [])
        if not cleaned and not orphans:
            return

        from app.utils import append_to_outbox
        parts = [f"🧹 [{self.project_name}]"]
        if cleaned:
            parts.append(f"Cleaned {len(cleaned)} merged branch(es): {', '.join(cleaned)}")
        if orphans:
            parts.append(
                f"⚠️ {len(orphans)} orphan branch(es) (no PR): {', '.join(orphans)}"
            )
        msg = " ".join(parts) + "\n"
        outbox_path = Path(self.instance_dir) / "outbox.md"
        append_to_outbox(outbox_path, msg)

    def sync_and_report(self) -> str:
        """Full sync: build report, write to journal, notify if needed."""
        report = self.build_sync_report()
        self.write_sync_to_journal(report)
        self._notify_cleanup_results()
        return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <instance_dir> <project_name> <project_path>",
              file=sys.stderr)
        sys.exit(1)

    instance_dir = sys.argv[1]
    project_name = sys.argv[2]
    project_path = sys.argv[3]

    sync = GitSync(instance_dir, project_name, project_path)
    report = sync.sync_and_report()
    print(report)
