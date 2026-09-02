"""Kōan — Git worktree lifecycle management.

Manages isolated git worktrees for parallel agent sessions:
- create_worktree(): create an isolated working directory with its own branch
- remove_worktree(): clean up worktree and associated state
- list_worktrees(): enumerate active worktrees
- cleanup_stale_worktrees(): prune worktrees whose sessions are gone
- git_retry(): retry wrapper for git commands that hit lock contention

Worktrees are stored under <project>/.worktrees/<session-id>/ to keep
them project-relative and easy to clean up. Each worktree gets a unique
branch named <prefix>/session-<uuid>.
"""

import contextlib
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# Git lock retry configuration (per m13v's production finding)
GIT_RETRY_MAX = 3
GIT_RETRY_MIN_DELAY = 0.1
GIT_RETRY_MAX_DELAY = 0.5

# Default worktree directory name (relative to project root)
WORKTREE_DIR = ".worktrees"
# Directories inside a project that belong to other code and must never be reaped.
# Everything else under the project — notably `<project>/tmp/`, where agents and
# skills drop scratch checkouts — has no owner and is in scope.
OWNED_WORKTREE_DIRS = (".worktrees", os.path.join(".claude", "worktrees"))

# How long a worktree registered outside the project must sit untouched before
# reap_foreign_worktrees() will remove it. Two days is comfortably longer than any single
# review run, so a live job is never stripped of its working tree.
FOREIGN_WORKTREE_MAX_AGE_DAYS = 2.0
# Foreign worktree cleanup is best-effort maintenance. It must never consume an
# unbounded amount of time on a large repository or delay other daemon work.
FOREIGN_WORKTREE_REAP_BUDGET_SECONDS = 120.0
FOREIGN_WORKTREE_GIT_TIMEOUT_SECONDS = 15.0
_ACTIVITY_BUDGET_CHECK_EVERY = 128


@dataclass
class WorktreeInfo:
    """Information about a single git worktree."""
    path: str
    branch: str
    session_id: str
    project_path: str
    commit: str = ""
    is_main: bool = False
    # True when git reports the worktree as `locked` — someone else's live workspace
    # (e.g. a Claude Code agent worktree). Never reap these.
    locked: bool = False


def _get_branch_prefix() -> str:
    """Get the configured branch prefix (default: 'koan').

    Returns the prefix WITHOUT trailing slash (e.g., 'koan').
    """
    try:
        from app.config import get_branch_prefix
        prefix = get_branch_prefix()
        return prefix.rstrip("/")
    except Exception as e:
        print(f"[worktree_manager] branch prefix config error: {e}", file=sys.stderr)
        return "koan"


def _worktrees_dir(project_path: str) -> Path:
    """Return the .worktrees directory for a project."""
    return Path(project_path) / WORKTREE_DIR


def git_retry(
    cmd: List[str],
    cwd: str,
    max_retries: int = GIT_RETRY_MAX,
    min_delay: float = GIT_RETRY_MIN_DELAY,
    max_delay: float = GIT_RETRY_MAX_DELAY,
) -> subprocess.CompletedProcess:
    """Run a git command with retry logic for lock contention.

    Concurrent git operations across worktrees sharing .git/objects can
    hit LOCK_EX errors. This wrapper retries with random jitter.

    Raises subprocess.CalledProcessError after all retries exhausted.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            last_error = e
            stderr = e.stderr or ""
            # Only retry on lock-related errors
            if "lock" not in stderr.lower() and "index.lock" not in stderr.lower():
                raise
            if attempt < max_retries:
                delay = random.uniform(min_delay, max_delay)
                time.sleep(delay)
    raise last_error  # type: ignore[misc]


def create_worktree(
    project_path: str,
    branch_name: str = "",
    session_id: str = "",
    base_branch: str = "main",
) -> WorktreeInfo:
    """Create a new git worktree for a parallel session.

    Args:
        project_path: Path to the main project repository.
        branch_name: Branch name for the worktree. Auto-generated if empty.
        session_id: Unique session identifier. Auto-generated if empty.
        base_branch: Branch to base the worktree on (default: main).

    Returns:
        WorktreeInfo with path, branch, and session details.

    Raises:
        subprocess.CalledProcessError: If git worktree add fails.
        FileExistsError: If worktree directory already exists.
    """
    if not session_id:
        session_id = uuid.uuid4().hex[:12]

    if not branch_name:
        prefix = _get_branch_prefix()
        branch_name = f"{prefix}/session-{session_id}"

    # Ensure .worktrees directory exists
    wt_base = _worktrees_dir(project_path)
    wt_base.mkdir(parents=True, exist_ok=True)

    # Ensure .worktrees is gitignored
    _ensure_gitignored(project_path)

    wt_path = wt_base / session_id
    if wt_path.exists():
        raise FileExistsError(f"Worktree path already exists: {wt_path}")

    # Determine the actual base branch/commit
    base_ref = _resolve_base_ref(project_path, base_branch)

    # Create the worktree with a new branch
    git_retry(
        ["git", "worktree", "add", "-b", branch_name, str(wt_path), base_ref],
        cwd=project_path,
    )

    # Get the HEAD commit of the new worktree
    commit = ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
            check=True,
        )
        commit = result.stdout.strip()
    except subprocess.CalledProcessError:
        pass

    # Copy project CLAUDE.md to worktree if it exists
    _copy_claude_md(project_path, str(wt_path))

    return WorktreeInfo(
        path=str(wt_path),
        branch=branch_name,
        session_id=session_id,
        project_path=project_path,
        commit=commit,
    )


def inject_worktree_claude_md(worktree_path: str, mission_text: str):
    """Append mission-specific context to the worktree's CLAUDE.md.

    Creates a section at the end of CLAUDE.md with the mission scope,
    so the agent knows what it's working on and that it's in a worktree.
    """
    claude_md = Path(worktree_path) / "CLAUDE.md"
    section = (
        "\n\n## Worktree Session Context\n\n"
        "This is an isolated worktree session. Changes here will be "
        "merged back via PR after completion.\n\n"
        f"**Current mission**: {mission_text}\n"
    )
    try:
        if claude_md.exists():
            existing = claude_md.read_text()
            claude_md.write_text(existing + section)
        else:
            claude_md.write_text(f"# CLAUDE.md\n{section}")
    except OSError as e:
        print(f"[worktree_manager] CLAUDE.md injection failed for {worktree_path}: {e}", file=sys.stderr)


def remove_worktree(
    project_path: str,
    session_id: str = "",
    worktree_path: str = "",
    force: bool = False,
    timeout: Optional[float] = None,
    fallback_remove: bool = True,
):
    """Remove a git worktree and clean up associated state.

    Args:
        project_path: Path to the main project repository.
        session_id: Session identifier (used to derive worktree path).
        worktree_path: Direct path to the worktree (alternative to session_id).
        force: If True, use --force flag for stubborn worktrees.
        timeout: Optional subprocess timeout in seconds.
        fallback_remove: Remove the directory directly if Git removal fails.

    Either session_id or worktree_path must be provided.
    """
    if not worktree_path and session_id:
        worktree_path = str(_worktrees_dir(project_path) / session_id)
    elif not worktree_path:
        raise ValueError("Either session_id or worktree_path must be provided")

    wt = Path(worktree_path)

    # Remove via git worktree remove (handles git bookkeeping)
    cmd = ["git", "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(wt))

    try:
        subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            check=True,
            **({"timeout": timeout} if timeout is not None else {}),
        )
    except subprocess.TimeoutExpired:
        print(f"[worktree_manager] git worktree remove timed out for {wt}", file=sys.stderr)
        return
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        print(
            f"[worktree_manager] git worktree remove failed for {wt}: {stderr}",
            file=sys.stderr,
        )
        # Maintenance reaping disables this fallback: shutil.rmtree() has no
        # interruptible deadline and could violate the sweep's hard budget.
        if fallback_remove and wt.exists():
            shutil.rmtree(str(wt), ignore_errors=True)

    # Prune any stale worktree references
    with contextlib.suppress(subprocess.CalledProcessError, subprocess.TimeoutExpired):
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=project_path,
            capture_output=True,
            text=True,
            **({"timeout": timeout} if timeout is not None else {}),
        )

    # Delete the branch if it still exists
    # (only session branches — don't delete user branches)
    if session_id:
        prefix = _get_branch_prefix()
        branch = f"{prefix}/session-{session_id}"
        try:
            result = subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=project_path,
                capture_output=True,
                text=True,
                **({"timeout": timeout} if timeout is not None else {}),
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                print(
                    f"[worktree_manager] git branch -D failed for {branch}: {stderr}",
                    file=sys.stderr,
                )
        except subprocess.TimeoutExpired:
            print(f"[worktree_manager] git branch -D timed out for {branch}", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            print(
                f"[worktree_manager] git branch -D failed for {branch}: {stderr}",
                file=sys.stderr,
            )


def list_worktrees(project_path: str, timeout: Optional[float] = None) -> List[WorktreeInfo]:
    """List all git worktrees for a project.

    Returns a list of WorktreeInfo, including the main worktree.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=project_path,
            capture_output=True,
            text=True,
            check=True,
            **({"timeout": timeout} if timeout is not None else {}),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    worktrees = []
    current: dict = {}

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(_parse_worktree_entry(current, project_path))
            current = {"path": line[9:]}
        elif line.startswith("HEAD "):
            current["commit"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line == "bare":
            current["bare"] = True
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = True
        elif line == "":
            if current:
                worktrees.append(_parse_worktree_entry(current, project_path))
                current = {}

    if current:
        worktrees.append(_parse_worktree_entry(current, project_path))

    return worktrees


def cleanup_stale_worktrees(project_path: str, active_session_ids: Optional[List[str]] = None):
    """Remove worktrees whose sessions are no longer active.

    Args:
        project_path: Path to the main project repository.
        active_session_ids: List of currently active session IDs.
            If None, removes all worktrees in .worktrees/.
    """
    if active_session_ids is None:
        active_session_ids = []

    active_set = set(active_session_ids)
    wt_base = _worktrees_dir(project_path)
    if not wt_base.exists():
        return

    for entry in wt_base.iterdir():
        if not entry.is_dir():
            continue
        session_id = entry.name
        if session_id not in active_set:
            try:
                remove_worktree(
                    project_path,
                    session_id=session_id,
                    force=True,
                )
            except Exception as e:
                print(f"[worktree_manager] stale worktree cleanup error for {session_id}: {e}", file=sys.stderr)

    # Final prune
    prune_worktrees(project_path)


def reap_foreign_worktrees(
    project_path: str,
    max_age_days: float = FOREIGN_WORKTREE_MAX_AGE_DAYS,
    dry_run: bool = False,
    deadline: Optional[float] = None,
    start_index: int = 0,
) -> List[str]:
    """Remove stale worktrees registered in this project but living outside it.

    Agents with an unrestricted shell check revisions out ad hoc — `git worktree add
    /tmp/base140 140` — improvising, not following any skill instruction. (`/review` runs
    in a worktree Kōan creates and removes, behind a read-only shell guard that denies
    `git worktree` outright; the leak comes from `/fix`-class missions, whose shell is
    unhooked.) Those worktrees are registered in the project's repo but sit outside
    `<project>/.worktrees/`, so `cleanup_stale_worktrees()` (which only walks
    `.worktrees/`) never sees them, and `prune_worktrees()` only drops registrations whose
    directory is *already* gone. Nothing reclaims a leaked worktree whose directory still
    exists.

    On a large checkout each one costs hundreds of megabytes. On 2026-07-30 this filled a
    50G root filesystem on the `koan` host: 14 leaked ~410M cPanel review worktrees plus 5
    phantom registrations.

    Guards, in order — each one exists because skipping it would destroy work:
      * never a worktree owned by other code — `<project>/.worktrees/` belongs to
        cleanup_stale_worktrees(), `<project>/.claude/worktrees/` to the Claude Code
        harness. Scratch worktrees under `<project>/tmp/` have no owner and ARE in
        scope: the OS temp sweeper does not reach inside a project either, so a blanket
        "skip anything inside the project" rule left them unreclaimable by anything.
      * never the main worktree
      * never a `locked` worktree — that is someone's live workspace
      * never one with tracked or untracked file activity within max_age_days — a review
        that started minutes ago is still running, and removing its tree kills the job
      * never one on a branch holding commits absent from its upstream
      * never one whose working tree is dirty when no durable ref reaches its HEAD

    ``deadline`` is a monotonic deadline shared by callers that sweep several
    projects. When omitted, the fixed maintenance budget applies. A timed-out
    safety check retains the worktree. ``start_index`` rotates candidates so a
    large early worktree cannot starve every later one across capped sweeps.

    Returns the list of paths removed (or, with dry_run, the paths that would be).
    """
    project_real = os.path.realpath(project_path)
    removed: List[str] = []
    cutoff = time.time() - (max_age_days * 86400)
    if deadline is None:
        deadline = time.monotonic() + FOREIGN_WORKTREE_REAP_BUDGET_SECONDS

    # Drop registrations whose directory is already gone before looking at what remains.
    # These accumulate whenever something deletes a worktree without telling git — the 10d
    # systemd-tmpfiles sweep of /tmp does exactly that — and nothing else clears them. A
    # fleet survey on 2026-07-30 found ~20 across six hosts, including 10 on one repo.
    if not dry_run:
        try:
            prune_worktrees(project_path, timeout=_reap_timeout(deadline))
        except subprocess.TimeoutExpired:
            _log_reap_deadline(project_path)
            return removed
    if _reap_expired(deadline):
        _log_reap_deadline(project_path)
        return removed

    candidates = []
    try:
        list_timeout = _reap_timeout(deadline)
    except subprocess.TimeoutExpired:
        _log_reap_deadline(project_path)
        return removed
    for wt in list_worktrees(project_path, timeout=list_timeout):
        if wt.is_main or not wt.path:
            continue

        wt_real = os.path.realpath(wt.path)
        if wt_real == project_real or _is_owned_by_other_code(wt_real, project_real):
            continue
        candidates.append((wt, wt_real))

    candidates.sort(key=lambda item: item[0].path)
    if candidates:
        offset = start_index % len(candidates)
        candidates = candidates[offset:] + candidates[:offset]

    for wt, wt_real in candidates:
        if _reap_expired(deadline):
            _log_reap_deadline(project_path)
            break
        if wt.locked:
            print(f"[worktree_manager] skip (locked) {wt.path}", file=sys.stderr)
            continue

        if not os.path.isdir(wt_real):
            continue  # already gone — prune_worktrees() drops the registration

        if _has_recent_worktree_activity(wt_real, cutoff, deadline=deadline):
            # Log it: `locked` and `unpushed commits` both report, so a silent skip
            # here made a retained worktree impossible to explain from the logs.
            print(
                f"[worktree_manager] skip (recent activity) {wt.path}",
                file=sys.stderr,
            )
            continue  # possibly a live review, or activity could not be verified

        if _has_unpushed_commits(wt_real, deadline=deadline):
            print(
                f"[worktree_manager] skip (unpushed commits) {wt.path}",
                file=sys.stderr,
            )
            continue

        if dry_run:
            removed.append(wt.path)
            continue

        try:
            # force=True: reviews routinely leave incidental edits behind (a regenerated
            # lockfile, a touched test file), which would otherwise block removal.
            remove_worktree(
                project_path,
                worktree_path=wt.path,
                force=True,
                timeout=_reap_timeout(deadline),
                fallback_remove=False,
            )
            if os.path.exists(wt.path):
                print(
                    f"[worktree_manager] foreign worktree reap incomplete for {wt.path}",
                    file=sys.stderr,
                )
                continue
            removed.append(wt.path)
            print(f"[worktree_manager] reaped foreign worktree {wt.path}", file=sys.stderr)
        except Exception as e:
            print(
                f"[worktree_manager] foreign worktree reap failed for {wt.path}: {e}",
                file=sys.stderr,
            )

    return removed


def _reap_expired(deadline: float) -> bool:
    return time.monotonic() >= deadline


def _reap_timeout(deadline: float) -> float:
    """Return a bounded Git timeout or raise when no sweep time remains."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("foreign-worktree-reap", 0)
    return min(FOREIGN_WORKTREE_GIT_TIMEOUT_SECONDS, remaining)


def _log_reap_deadline(project_path: str) -> None:
    print(
        f"[worktree_manager] foreign worktree reap budget reached in {project_path}; "
        "remaining worktrees will be retried next sweep",
        file=sys.stderr,
    )


def _is_owned_by_other_code(wt_real: str, project_real: str) -> bool:
    """Return True when this worktree belongs to code other than the sweep.

    Only two directories inside a project have an owner: `.worktrees/` (managed by
    ``cleanup_stale_worktrees``) and `.claude/worktrees/` (managed by the Claude Code
    harness). A worktree living anywhere else inside the project — in practice
    `<project>/tmp/` — is unowned scratch and must stay in scope, because nothing else
    on the host reclaims it: the OS temp sweeper only walks the system temp directory.
    """
    if not wt_real.startswith(project_real + os.sep):
        return False  # outside the project entirely — foreign, always in scope
    relative = wt_real[len(project_real) + 1:]
    return any(
        relative == owned or relative.startswith(owned + os.sep)
        for owned in OWNED_WORKTREE_DIRS
    )


def _has_uncommitted_changes(
    worktree_path: str,
    deadline: Optional[float] = None,
) -> bool:
    """Return True when the worktree has tracked or untracked local changes.

    Any inspection failure reports True, so an unreadable worktree is retained.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            **({"timeout": _reap_timeout(deadline)} if deadline is not None else {}),
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def _head_moved_since_worktree_creation(
    worktree_path: str,
    head: str,
    deadline: Optional[float] = None,
) -> bool:
    """Return True when detached HEAD differs from its worktree-add commit.

    A linked worktree has its own HEAD reflog. Its oldest entry records the commit
    supplied to ``git worktree add``; a later detached commit changes HEAD while a
    closed pull-request checkout does not. Any inspection failure returns True so
    unique committed work is retained.
    """
    try:
        result = subprocess.run(
            ["git", "reflog", "show", "--format=%H%x00%gs", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            **({"timeout": _reap_timeout(deadline)} if deadline is not None else {}),
        )
    except (OSError, subprocess.SubprocessError):
        return True
    entries = [entry for entry in result.stdout.splitlines() if entry]
    if result.returncode != 0 or not entries:
        return True
    creation = entries[-1].split("\0", 1)
    if len(creation) != 2 or creation[1]:
        # The initial worktree-add entry has no reflog subject. If it expired,
        # the oldest visible entry cannot prove which commit created the tree.
        return True
    return creation[0] != head


def _has_recent_worktree_activity(
    worktree_path: str,
    cutoff: float,
    deadline: Optional[float] = None,
) -> bool:
    """Return True when non-ignored worktree content changed after ``cutoff``.

    Root directory mtime alone misses edits to existing files. Ask git for tracked and
    non-ignored untracked files, then inspect those files and their parent directories
    (the latter catches recent deletions). Ignored dependency trees stay outside the scan.
    Any inspection failure keeps the worktree.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            **({"timeout": _reap_timeout(deadline)} if deadline is not None else {}),
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True

    root = os.path.realpath(worktree_path)
    directories = {root}
    for index, relative_path in enumerate(result.stdout.split("\0")):
        if deadline is not None and index % _ACTIVITY_BUDGET_CHECK_EVERY == 0:
            if _reap_expired(deadline):
                return True
        if not relative_path:
            continue
        path = os.path.join(root, relative_path)
        try:
            if os.lstat(path).st_mtime > cutoff:
                return True
        except FileNotFoundError:
            pass  # A tracked deletion updates its parent directory mtime.
        except OSError:
            return True

        parent = os.path.dirname(path)
        while parent != root:
            directories.add(parent)
            parent = os.path.dirname(parent)

    try:
        for index, path in enumerate(directories):
            if deadline is not None and index % _ACTIVITY_BUDGET_CHECK_EVERY == 0:
                if _reap_expired(deadline):
                    return True
            if os.lstat(path).st_mtime > cutoff:
                return True
        return False
    except OSError:
        return True


def _has_unpushed_commits(
    worktree_path: str,
    deadline: Optional[float] = None,
) -> bool:
    """Return True when removing the worktree could make commits unreachable.

    Tracking branches are unsafe when HEAD exceeds their upstream. Local branches without
    upstreams remain durable after path-based removal.

    For a detached HEAD, reachability alone cannot decide. A durable ref — local branch,
    tag, or remote-tracking ref — reaching HEAD proves the commits survive removal, so the
    worktree is safe to reap. But the converse does not hold. A review worktree sits at a
    pull-request head commit, durable on its remote branch only while that PR is open; once
    the PR is squash-merged the remote branch is deleted and nothing reaches that commit any
    more. Retaining on unreachability therefore made every *completed* review immortal —
    measured on the koan host, three of four leaked worktrees were permanently retained for
    exactly this reason while the reaper reported "0 reclaimed" every hour for days.

    So an unreachable detached HEAD is removable only when it still equals the commit used
    to create the worktree and its tree is clean. A changed HEAD may contain committed but
    never-pushed work, while a dirty tree contains uncommitted work. Age is already covered
    by the caller's activity guard. Any git or verification failure keeps the worktree.
    """
    try:
        branch = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            **({"timeout": _reap_timeout(deadline)} if deadline is not None else {}),
        )
    except (OSError, subprocess.SubprocessError):
        return True

    if branch.returncode == 0:
        branch_name = branch.stdout.strip()
        try:
            remote = subprocess.run(
                ["git", "config", "--get", f"branch.{branch_name}.remote"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                **({"timeout": _reap_timeout(deadline)} if deadline is not None else {}),
            )
            merge = subprocess.run(
                ["git", "config", "--get", f"branch.{branch_name}.merge"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                **({"timeout": _reap_timeout(deadline)} if deadline is not None else {}),
            )
        except (OSError, subprocess.SubprocessError):
            return True

        if remote.returncode == 1 and merge.returncode == 1:
            return False  # The local branch itself keeps HEAD reachable.
        if remote.returncode != 0 or merge.returncode != 0:
            return True

        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "@{u}..HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                **({"timeout": _reap_timeout(deadline)} if deadline is not None else {}),
            )
        except (OSError, subprocess.SubprocessError):
            return True
        if result.returncode != 0:
            return True
        try:
            return int(result.stdout.strip()) > 0
        except ValueError:
            return True

    if branch.returncode != 1:
        return True

    # One revision walk, negated by every durable ref, instead of a containment scan per
    # ref. --max-count=1 stops at the first commit that no ref reaches, so the cost is the
    # divergence rather than the ref count.
    try:
        result = subprocess.run(
            ["git", "rev-list", "--max-count=1", "HEAD", "--not", "--branches", "--tags", "--remotes"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            **({"timeout": _reap_timeout(deadline)} if deadline is not None else {}),
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    if not result.stdout.strip():
        return False  # a durable ref reaches HEAD — removal loses nothing
    head = result.stdout.strip().splitlines()[0]
    # Unreachable: a closed pull request, or genuinely unique work. A closed-PR
    # checkout remains at its creation commit; locally committed work moves HEAD.
    return (
        _head_moved_since_worktree_creation(
            worktree_path,
            head,
            deadline=deadline,
        )
        or _has_uncommitted_changes(worktree_path, deadline=deadline)
    )


def prune_worktrees(project_path: str, timeout: Optional[float] = None):
    """Run git worktree prune to clear stale worktree references.

    Intended to be called on startup to clean up leftover refs from
    sessions that were killed without proper cleanup.
    """
    try:
        result = subprocess.run(
            # --expire now: the default gc.worktreePruneExpire is 3.months.ago, which
            # would leave a freshly orphaned registration in place for a quarter.
            ["git", "worktree", "prune", "--verbose", "--expire", "now"],
            cwd=project_path,
            capture_output=True,
            text=True,
            **({"timeout": timeout} if timeout is not None else {}),
        )
        output = (result.stdout or "").strip()
        if output:
            print(f"[worktree_manager] pruned stale worktrees:\n{output}", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        print(f"[worktree_manager] git worktree prune failed: {stderr}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"[worktree_manager] git worktree prune timed out for {project_path}", file=sys.stderr)
    except FileNotFoundError:
        pass  # git not available


def setup_shared_deps(worktree_path: str, project_path: str, shared_deps: List[str]):
    """Symlink heavy dependency directories from main project to worktree.

    Args:
        worktree_path: Path to the worktree.
        project_path: Path to the main project.
        shared_deps: List of relative paths to symlink (e.g., ["node_modules", ".venv"]).
    """
    for dep in shared_deps:
        src = Path(project_path) / dep
        dst = Path(worktree_path) / dep
        if src.exists() and not dst.exists():
            try:
                # Ensure parent directory exists
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(str(src), str(dst))
            except OSError:
                pass  # Non-fatal — build may just take longer


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_base_ref(project_path: str, base_branch: str) -> str:
    """Resolve the base reference for worktree creation.

    Tries the specified base_branch, falls back to 'main', then 'master',
    then HEAD.
    """
    for ref in [base_branch, "main", "master", "HEAD"]:
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", ref],
                cwd=project_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return ref
        except subprocess.CalledProcessError:
            continue
    return "HEAD"


def _copy_claude_md(project_path: str, worktree_path: str):
    """Copy CLAUDE.md from main project to worktree if it exists."""
    src = Path(project_path) / "CLAUDE.md"
    dst = Path(worktree_path) / "CLAUDE.md"
    if src.exists() and not dst.exists():
        try:
            shutil.copy2(str(src), str(dst))
        except OSError as e:
            print(f"[worktree_manager] CLAUDE.md copy failed from {project_path} to {worktree_path}: {e}", file=sys.stderr)


def _ensure_gitignored(project_path: str):
    """Ensure .worktrees/ is in .gitignore."""
    gitignore = Path(project_path) / ".gitignore"
    pattern = f"/{WORKTREE_DIR}/"
    try:
        if gitignore.exists():
            content = gitignore.read_text()
            if pattern in content or WORKTREE_DIR in content:
                return
            # Append to existing .gitignore
            if not content.endswith("\n"):
                content += "\n"
            content += f"{pattern}\n"
            gitignore.write_text(content)
        # Don't create .gitignore if it doesn't exist — that's the project's choice
    except OSError:
        pass


def _parse_worktree_entry(entry: dict, project_path: str) -> WorktreeInfo:
    """Parse a porcelain worktree entry into WorktreeInfo."""
    path = entry.get("path", "")
    branch = entry.get("branch", "").removeprefix("refs/heads/")
    commit = entry.get("commit", "")

    # Extract session_id from path (last component of .worktrees/<session-id>)
    session_id = ""
    wt_dir = str(_worktrees_dir(project_path))
    if path.startswith(wt_dir):
        remainder = path[len(wt_dir):].lstrip(os.sep)
        session_id = remainder.split(os.sep)[0] if remainder else ""

    # Detect if this is the main worktree
    is_main = os.path.normpath(path) == os.path.normpath(project_path)

    return WorktreeInfo(
        path=path,
        branch=branch,
        session_id=session_id,
        project_path=project_path,
        commit=commit,
        is_main=is_main,
        locked=bool(entry.get("locked", False)),
    )
