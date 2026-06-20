"""Update manager for Kōan -- pulls latest code from upstream.

Handles the git operations needed to update Kōan to the latest version:
1. Stash any dirty working tree state
2. Checkout main branch
3. Fetch and pull from upstream
4. Report what changed

Used by the /update command to ensure both bridge and run loop
run the latest code after a restart.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.git_utils import run_git as _run_git_core
from app.run_log import log


class _GitResult:
    """Minimal CompletedProcess-like object for backward compat."""
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@dataclass
class UpdateResult:
    """Result of an update operation."""

    success: bool
    old_commit: str  # short SHA before update
    new_commit: str  # short SHA after update
    commits_pulled: int  # number of new commits
    error: Optional[str] = None  # error message if failed
    stashed: bool = False  # whether we stashed dirty work
    stash_error: Optional[str] = None  # non-None when stash pop failed

    @property
    def changed(self) -> bool:
        """True if HEAD moved (covers both forward pulls and tag downgrades)."""
        return self.old_commit != self.new_commit

    def summary(self) -> str:
        """Human-readable summary for Telegram."""
        if not self.success:
            return f"Update failed: {self.error}"
        if not self.changed:
            base = "Already up to date."
        elif self.commits_pulled > 0:
            base = f"Updated: {self.old_commit} → {self.new_commit} ({self.commits_pulled} new commit{'s' if self.commits_pulled != 1 else ''})"
        else:
            base = f"Updated: {self.old_commit} → {self.new_commit}"
        if self.stash_error:
            base += " ⚠️ Stash restore failed — run `git stash pop` manually."
        return base


def _run_git(args: list[str], cwd: Path, timeout: int = 60) -> _GitResult:
    """Run a git command and return the result.

    Thin wrapper around git_utils.run_git() preserving the
    CompletedProcess-like interface for existing callers.
    """
    rc, stdout, stderr = _run_git_core(*args, cwd=str(cwd), timeout=timeout)
    return _GitResult(rc, stdout, stderr)


def _get_current_branch(koan_root: Path) -> Optional[str]:
    """Get the current git branch name."""
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], koan_root)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _get_short_sha(koan_root: Path) -> str:
    """Get the current HEAD short SHA."""
    result = _run_git(["rev-parse", "--short", "HEAD"], koan_root)
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def _is_dirty(koan_root: Path) -> bool:
    """Check if the working tree has uncommitted changes."""
    result = _run_git(["status", "--porcelain"], koan_root)
    return bool(result.stdout.strip())


def find_upstream_remote(koan_root: Path) -> Optional[str]:
    """Find the upstream remote name (prefers 'upstream', falls back to 'origin')."""
    result = _run_git(["remote"], koan_root)
    if result.returncode != 0:
        return None
    remotes = result.stdout.strip().splitlines()
    if "upstream" in remotes:
        return "upstream"
    if "origin" in remotes:
        return "origin"
    return remotes[0] if remotes else None


def _count_commits_between(koan_root: Path, old_sha: str, new_sha: str) -> int:
    """Count commits between two refs."""
    result = _run_git(
        ["rev-list", "--count", f"{old_sha}..{new_sha}"], koan_root
    )
    if result.returncode == 0:
        try:
            return int(result.stdout.strip())
        except ValueError:
            pass
    return 0


def check_update_safety(koan_root: Path) -> Optional[str]:
    """Pre-flight check: refuse update if instance diverged from upstream.

    Returns None if safe to update, or a human-readable message explaining
    why the update was refused.
    """
    branch = _get_current_branch(koan_root)
    # Allow detached HEAD (e.g. after release tag checkout) — pull_upstream
    # does an explicit `checkout main` so detached state is fine.
    if branch is not None and branch not in ("main", "HEAD"):
        return (
            f"⚠️ Update refused — you are on branch `{branch}`, not `main`.\n"
            "Switch back to `main` before updating."
        )

    remote = find_upstream_remote(koan_root)
    if remote is None:
        return None

    fetch_result = _run_git(["fetch", remote, "--quiet"], koan_root)
    if fetch_result.returncode != 0:
        log("update", f"Safety check: fetch {remote} failed, using cached refs")

    result = _run_git(
        ["rev-list", "--oneline", f"{remote}/main..HEAD"],
        koan_root,
    )
    if result.returncode != 0:
        return None

    extra_commits = result.stdout.strip()
    if not extra_commits:
        return None

    count = len(extra_commits.splitlines())
    return (
        f"⚠️ Update refused — local `main` is {count} commit(s) ahead "
        f"of `{remote}/main`.\n"
        f"```\n{extra_commits}\n```\n"
        "Push or reset these commits before updating."
    )


def _restore_stash(koan_root: Path) -> Optional[str]:
    """Pop stash and return error message on failure, None on success."""
    result = _run_git(["stash", "pop"], koan_root)
    if result.returncode != 0:
        msg = result.stderr.strip() or "stash pop failed"
        log("update", f"Warning: stash restore failed: {msg}")
        return msg
    return None


def _get_latest_tag(
    koan_root: Path, remote: str,
) -> tuple[Optional[str], Optional[str]]:
    """Get the latest upstream release tag by version sort order.

    Only considers tags published on the remote AND merged into
    ``{remote}/main`` — local-only tags are excluded.

    Returns ``(tag, None)`` on success, ``(None, error_msg)`` on git
    failure, or ``(None, None)`` when no qualifying tags exist.
    """
    ls_result = _run_git(["ls-remote", "--tags", "--refs", remote], koan_root)
    if ls_result.returncode != 0:
        return None, f"Failed to list remote tags: {ls_result.stderr.strip()}"

    remote_tags: set[str] = set()
    for line in ls_result.stdout.strip().splitlines():
        if "\t" in line:
            ref = line.split("\t", 1)[1]
            remote_tags.add(ref.removeprefix("refs/tags/"))

    if not remote_tags:
        return None, None

    result = _run_git(
        ["tag", "--sort=-version:refname", "--merged", f"{remote}/main"],
        koan_root,
    )
    if result.returncode != 0:
        return None, f"Failed to list merged tags: {result.stderr.strip()}"

    for tag in result.stdout.strip().splitlines():
        if tag in remote_tags:
            return tag, None

    return None, None


def checkout_latest_tag(koan_root: Path, timeout: int = 120) -> UpdateResult:
    """Update to the most recent release tag.

    Similar to pull_upstream() but checks out the latest tag instead of
    fast-forwarding main.  Shares the same stash/restore lifecycle.

    Args:
        koan_root: Path to the koan repository.
        timeout: Total wall-clock timeout in seconds (default: 120).

    Returns an UpdateResult with success/failure info.
    """
    deadline = time.monotonic() + timeout

    def _remaining_timeout() -> int:
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            return 0
        return min(60, remaining)

    old_sha = _get_short_sha(koan_root)
    stashed = False

    remote = find_upstream_remote(koan_root)
    if remote is None:
        return UpdateResult(
            success=False, old_commit=old_sha, new_commit=old_sha,
            commits_pulled=0, error="No git remote found",
        )

    # Stash dirty work if needed
    if _is_dirty(koan_root):
        cmd_timeout = _remaining_timeout()
        if cmd_timeout <= 0:
            return UpdateResult(
                success=False, old_commit=old_sha, new_commit=old_sha,
                commits_pulled=0, error="Update operation timed out",
            )
        result = _run_git(["stash", "push", "-m", "koan-update-auto-stash"], koan_root, timeout=cmd_timeout)
        if result.returncode != 0:
            return UpdateResult(
                success=False, old_commit=old_sha, new_commit=old_sha,
                commits_pulled=0, error=f"Failed to stash: {result.stderr.strip()}",
            )
        stashed = True

    # Fetch upstream (including tags)
    cmd_timeout = _remaining_timeout()
    if cmd_timeout <= 0:
        stash_err = _restore_stash(koan_root) if stashed else None
        return UpdateResult(
            success=False, old_commit=old_sha, new_commit=old_sha,
            commits_pulled=0, error="Update operation timed out",
            stashed=stashed, stash_error=stash_err,
        )
    result = _run_git(["fetch", remote, "--tags"], koan_root, timeout=cmd_timeout)
    if result.returncode != 0:
        stash_err = _restore_stash(koan_root) if stashed else None
        return UpdateResult(
            success=False, old_commit=old_sha, new_commit=old_sha,
            commits_pulled=0, error=f"Failed to fetch {remote}: {result.stderr.strip()}",
            stashed=stashed, stash_error=stash_err,
        )

    # Find latest tag (only tags published on the remote and merged into remote/main)
    latest_tag, tag_error = _get_latest_tag(koan_root, remote)
    if latest_tag is None:
        stash_err = _restore_stash(koan_root) if stashed else None
        return UpdateResult(
            success=False, old_commit=old_sha, new_commit=old_sha,
            commits_pulled=0, error=tag_error or "No release tags found upstream",
            stashed=stashed, stash_error=stash_err,
        )

    # Check if already on this tag
    head_on_tag = _run_git(["merge-base", "--is-ancestor", latest_tag, "HEAD"], koan_root)
    tag_on_head = _run_git(["merge-base", "--is-ancestor", "HEAD", latest_tag], koan_root)
    if head_on_tag.returncode == 0 and tag_on_head.returncode == 0:
        stash_err = _restore_stash(koan_root) if stashed else None
        return UpdateResult(
            success=True, old_commit=old_sha, new_commit=old_sha,
            commits_pulled=0, stashed=stashed, stash_error=stash_err,
        )

    # Checkout the tag (detached HEAD)
    cmd_timeout = _remaining_timeout()
    if cmd_timeout <= 0:
        stash_err = _restore_stash(koan_root) if stashed else None
        return UpdateResult(
            success=False, old_commit=old_sha, new_commit=old_sha,
            commits_pulled=0, error="Update operation timed out",
            stashed=stashed, stash_error=stash_err,
        )
    result = _run_git(["checkout", latest_tag], koan_root, timeout=cmd_timeout)
    if result.returncode != 0:
        stash_err = _restore_stash(koan_root) if stashed else None
        return UpdateResult(
            success=False, old_commit=old_sha, new_commit=old_sha,
            commits_pulled=0,
            error=f"Failed to checkout tag {latest_tag}: {result.stderr.strip()}",
            stashed=stashed, stash_error=stash_err,
        )

    new_sha = _get_short_sha(koan_root)
    commits = _count_commits_between(koan_root, old_sha, new_sha) if old_sha != new_sha else 0

    stash_err = _restore_stash(koan_root) if stashed else None

    log("update", f"Checked out release tag {latest_tag} ({new_sha})")

    return UpdateResult(
        success=True,
        old_commit=old_sha,
        new_commit=new_sha,
        commits_pulled=commits,
        stashed=stashed,
        stash_error=stash_err,
    )


def pull_upstream(koan_root: Path, timeout: int = 120) -> UpdateResult:
    """Pull the latest code from upstream/main.

    Steps:
    1. Stash dirty state if needed
    2. Checkout main branch
    3. Fetch upstream
    4. Pull (fast-forward only)
    5. Report results

    Args:
        koan_root: Path to the koan repository.
        timeout: Total wall-clock timeout in seconds for the entire
            operation (default: 120). Individual git commands get
            the lesser of 60s or the remaining time budget.

    Returns an UpdateResult with success/failure info.
    """
    deadline = time.monotonic() + timeout

    def _remaining_timeout() -> int:
        """Per-command timeout capped by overall deadline."""
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            return 0
        return min(60, remaining)

    old_sha = _get_short_sha(koan_root)
    stashed = False

    # Find upstream remote
    remote = find_upstream_remote(koan_root)
    if remote is None:
        return UpdateResult(
            success=False,
            old_commit=old_sha,
            new_commit=old_sha,
            commits_pulled=0,
            error="No git remote found",
        )

    # Stash dirty work if needed
    if _is_dirty(koan_root):
        cmd_timeout = _remaining_timeout()
        if cmd_timeout <= 0:
            return UpdateResult(
                success=False, old_commit=old_sha, new_commit=old_sha,
                commits_pulled=0, error="Update operation timed out",
            )
        result = _run_git(["stash", "push", "-m", "koan-update-auto-stash"], koan_root, timeout=cmd_timeout)
        if result.returncode != 0:
            return UpdateResult(
                success=False,
                old_commit=old_sha,
                new_commit=old_sha,
                commits_pulled=0,
                error=f"Failed to stash: {result.stderr.strip()}",
            )
        stashed = True

    # Checkout main branch
    current_branch = _get_current_branch(koan_root)
    if current_branch != "main":
        cmd_timeout = _remaining_timeout()
        if cmd_timeout <= 0:
            if stashed:
                _run_git(["stash", "pop"], koan_root)
            return UpdateResult(
                success=False, old_commit=old_sha, new_commit=old_sha,
                commits_pulled=0, error="Update operation timed out", stashed=stashed,
            )
        result = _run_git(["checkout", "main"], koan_root, timeout=cmd_timeout)
        if result.returncode != 0:
            # Try to restore state
            if stashed:
                _run_git(["stash", "pop"], koan_root)
            return UpdateResult(
                success=False,
                old_commit=old_sha,
                new_commit=old_sha,
                commits_pulled=0,
                error=f"Failed to checkout main: {result.stderr.strip()}",
                stashed=stashed,
            )

    # Fetch upstream
    cmd_timeout = _remaining_timeout()
    if cmd_timeout <= 0:
        if current_branch and current_branch != "main":
            _run_git(["checkout", current_branch], koan_root)
        if stashed:
            _run_git(["stash", "pop"], koan_root)
        return UpdateResult(
            success=False, old_commit=old_sha, new_commit=old_sha,
            commits_pulled=0, error="Update operation timed out", stashed=stashed,
        )
    result = _run_git(["fetch", remote], koan_root, timeout=cmd_timeout)
    if result.returncode != 0:
        # Restore previous branch
        if current_branch and current_branch != "main":
            _run_git(["checkout", current_branch], koan_root)
        if stashed:
            _run_git(["stash", "pop"], koan_root)
        return UpdateResult(
            success=False,
            old_commit=old_sha,
            new_commit=old_sha,
            commits_pulled=0,
            error=f"Failed to fetch {remote}: {result.stderr.strip()}",
            stashed=stashed,
        )

    # Pull (fast-forward only for safety)
    cmd_timeout = _remaining_timeout()
    if cmd_timeout <= 0:
        if current_branch and current_branch != "main":
            _run_git(["checkout", current_branch], koan_root)
        if stashed:
            _run_git(["stash", "pop"], koan_root)
        return UpdateResult(
            success=False, old_commit=old_sha, new_commit=old_sha,
            commits_pulled=0, error="Update operation timed out", stashed=stashed,
        )
    result = _run_git(["pull", "--ff-only", remote, "main"], koan_root, timeout=cmd_timeout)
    if result.returncode != 0:
        # Restore previous branch
        if current_branch and current_branch != "main":
            _run_git(["checkout", current_branch], koan_root)
        if stashed:
            _run_git(["stash", "pop"], koan_root)
        return UpdateResult(
            success=False,
            old_commit=old_sha,
            new_commit=old_sha,
            commits_pulled=0,
            error=f"Failed to pull: {result.stderr.strip()}",
            stashed=stashed,
        )

    new_sha = _get_short_sha(koan_root)
    commits = _count_commits_between(koan_root, old_sha, new_sha) if old_sha != new_sha else 0

    # Restore original branch and stash
    if current_branch and current_branch != "main":
        _run_git(["checkout", current_branch], koan_root)
    if stashed:
        _run_git(["stash", "pop"], koan_root)

    return UpdateResult(
        success=True,
        old_commit=old_sha,
        new_commit=new_sha,
        commits_pulled=commits,
        stashed=stashed,
    )
