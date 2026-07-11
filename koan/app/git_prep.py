"""
Kōan -- Pre-mission git preparation.

Ensures a project starts each mission on a fresh, up-to-date base branch.
Called before every mission execution in the agent loop.

Two public functions:
- get_upstream_remote(): Determines the canonical remote for a project.
- prepare_project_branch(): Full pre-mission git state preparation.
"""

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from app.git_utils import run_git
from app.projects_config import (
    _find_project_entry,
    get_project_auto_merge,
    get_project_submit_to_repository,
    load_projects_config,
)

logger = logging.getLogger(__name__)

_HTTPS_GITHUB_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


def _get_remote_url(remote: str, project_path: str) -> str:
    """Return the URL for a named git remote, or empty string."""
    rc, url, _ = run_git("remote", "get-url", remote, cwd=project_path)
    return url.strip() if rc == 0 else ""


def _authenticated_fetch_url(
    remote_url: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Build a token-authenticated HTTPS URL from a plain HTTPS GitHub remote.

    Returns (authenticated_url, token) or (None, None) when the remote is
    not an HTTPS GitHub URL or no token is available.
    """
    m = _HTTPS_GITHUB_RE.match(remote_url)
    if not m:
        return None, None
    try:
        from app.github import run_gh
        token = run_gh("auth", "token").strip()
    except Exception as e:
        logger.debug("gh auth token failed: %s", e)
        token = ""
    if not token:
        return None, None
    owner, repo = m.group("owner"), m.group("repo")
    return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git", token


# Matches the stderr GitHub returns when the credential lacks access to a repo
# (private repo + token without rights, expired/wrong token, etc.).
_AUTH_ERROR_RE = re.compile(
    r"\b403\b|not granted|access denied|permission denied|authentication failed"
    r"|could not read Username|terminal prompts disabled|invalid username or password",
    re.IGNORECASE,
)


def _auth_diagnostics() -> str:
    """Build an operator-facing diagnostic block for a git auth failure.

    Reveals *which* credential git is using without leaking the secret: the
    env var that supplied the token plus its short prefix, and the output of
    ``gh auth status`` (which masks the token itself). Intended to be appended
    to a fetch error so the operator can tell, e.g., that Railway's injected
    ``GH_TOKEN`` is being used instead of their ``KOAN_GH_TOKEN`` bot token.
    """
    lines = ["", "── GitHub auth diagnostics ──"]

    # Which env var supplied the token, and its non-secret prefix.
    src = ""
    for name in ("KOAN_GH_TOKEN", "GH_TOKEN"):
        val = os.environ.get(name, "").strip()
        if val:
            src = f"{name} = {val[:7]}…({len(val)} chars)"
            break
    lines.append(f"token source: {src or 'no GH token set in environment'}")

    # gh's own view of the active token + account (gh masks the token).
    try:
        out = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL,
        )
        status = (out.stdout + out.stderr).strip()
        lines.append("gh auth status:\n" + (status or "(no output)"))
    except Exception as e:  # noqa: BLE001 - diagnostics must never raise
        lines.append(f"gh auth status failed: {e}")

    lines.append(
        "Hint: 403/'not granted' means the active token's account lacks write "
        "access to this repo. Add the bot as a collaborator, or set "
        "KOAN_GH_TOKEN (it overrides GH_TOKEN) to a token with repo access."
    )
    return "\n".join(lines)


def _fetch_with_https_fallback(
    remote: str,
    refspec: str,
    project_path: str,
    timeout: int = 30,
) -> Tuple[int, str, str]:
    """Fetch a refspec, retrying with token auth when HTTPS remote lacks credentials.

    Returns the same (rc, stdout, stderr) tuple as run_git.
    """
    rc, stdout, stderr = run_git(
        "fetch", remote, refspec, cwd=project_path, timeout=timeout
    )
    if rc == 0:
        return rc, stdout, stderr

    remote_url = _get_remote_url(remote, project_path)
    auth_url, token = _authenticated_fetch_url(remote_url)
    if not auth_url:
        return rc, stdout, stderr

    logger.info("HTTPS fetch failed; retrying with gh token for %s", remote)
    rc2, stdout2, stderr2 = run_git(
        "fetch", auth_url, refspec, cwd=project_path, timeout=timeout
    )
    if token and stderr2:
        stderr2 = stderr2.replace(token, "***")
    return rc2, stdout2, stderr2


def _fetch_branch_refspec(
    remote: str, branch: str, project_path: str, timeout: int = 15
) -> bool:
    """Fetch a branch using an explicit refspec to guarantee tracking ref update.

    Returns True on success.
    """
    refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    rc, _, _ = _fetch_with_https_fallback(
        remote, refspec, project_path, timeout=timeout
    )
    return rc == 0


def _sync_secondary_remotes(
    base_branch: str, primary_remote: str, project_path: str
) -> None:
    """Fetch base branch from all remotes besides the primary.

    Ensures remote tracking refs are fresh for fork-aware operations
    (e.g., locating a PR head branch across fork remotes).
    Non-fatal — failures are logged but never abort the mission.
    """
    rc, stdout, _ = run_git("remote", cwd=project_path)
    if rc != 0 or not stdout:
        return
    for remote in stdout.splitlines():
        remote = remote.strip()
        if not remote or remote == primary_remote:
            continue
        if not _fetch_branch_refspec(remote, base_branch, project_path):
            logger.debug(
                "Secondary fetch %s/%s failed (non-fatal)", remote, base_branch
            )


def detect_remote_default_branch(remote: str, project_path: str) -> str:
    """Detect the default branch for a remote.

    Resolution order:
    1. Local symbolic ref (refs/remotes/<remote>/HEAD) — fast, no network
    2. git ls-remote --symref — requires network but always accurate
    3. Falls back to "main"
    """
    # 1. Try local symbolic ref (set after clone or fetch with --set-head)
    rc, stdout, _ = run_git(
        "symbolic-ref", f"refs/remotes/{remote}/HEAD", cwd=project_path
    )
    if rc == 0 and stdout:
        # Output: refs/remotes/origin/master → extract "master"
        branch = stdout.strip().rsplit("/", 1)[-1]
        if branch:
            return branch

    # 2. Query remote (network call) — try named remote first, then
    #    fall back to token-authenticated URL for HTTPS remotes.
    targets = [remote]
    remote_url = _get_remote_url(remote, project_path)
    auth_url, _ = _authenticated_fetch_url(remote_url)
    if auth_url:
        targets.append(auth_url)

    for target in targets:
        rc, stdout, _ = run_git(
            "ls-remote", "--symref", target, "HEAD",
            cwd=project_path, timeout=15,
        )
        if rc == 0 and stdout:
            for line in stdout.splitlines():
                if line.startswith("ref:") and "HEAD" in line:
                    ref_part = line.split()[1]
                    branch = ref_part.rsplit("/", 1)[-1]
                    if branch:
                        return branch

    return "main"


@dataclass
class PrepResult:
    """Result of pre-mission git preparation."""

    remote_used: str = "origin"
    base_branch: str = "main"
    stashed: bool = False
    previous_branch: str = ""
    success: bool = True
    error: Optional[str] = None
    healed: Optional[str] = None  # description of any self-heal performed


def _is_same_dir(path_a: str, path_b: str) -> bool:
    """Return True when two paths resolve to the same directory.

    Uses realpath so symlinks and trailing slashes compare equal. This is a
    same-directory check, not a same-git-repository check: a subdirectory of
    a repo will not compare equal to the repo root.
    """
    if not path_a or not path_b:
        return False
    try:
        return os.path.realpath(path_a) == os.path.realpath(path_b)
    except OSError as e:
        logger.warning(
            "realpath failed comparing '%s' and '%s': %s", path_a, path_b, e
        )
        return False


def get_upstream_remote(
    project_path: str, project_name: str, koan_root: str
) -> str:
    """Determine the canonical remote for a project.

    Resolution order:
    1. Explicit submit_to_repository.remote from projects.yaml
    2. 'upstream' remote if it exists (common fork pattern)
    3. 'origin' fallback (default for non-fork repos)
    """
    # 1. Check explicit config
    try:
        config = load_projects_config(koan_root)
        if config:
            submit_cfg = get_project_submit_to_repository(config, project_name)
            if submit_cfg.get("remote"):
                return submit_cfg["remote"]
    except Exception as e:
        logger.warning("config load error for remote: %s", e)

    # 2. Probe for 'upstream' remote
    rc, _, _ = run_git("remote", "get-url", "upstream", cwd=project_path)
    if rc == 0:
        return "upstream"

    # 3. Fall back to 'origin'
    return "origin"


# Two-char porcelain-v1 status codes that indicate an unresolved merge
# conflict (unmerged paths). See `git status --porcelain` docs.
_UNMERGED_STATUS_CODES = frozenset(
    {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
)

# In-progress operation markers → the abort command that clears them.
# Resolved via `git rev-parse --git-path` so worktrees/submodules (where
# .git is a file) work correctly.
_INPROGRESS_MARKERS = (
    ("MERGE_HEAD", ("merge", "--abort")),
    ("rebase-merge", ("rebase", "--abort")),
    ("rebase-apply", ("rebase", "--abort")),
    ("CHERRY_PICK_HEAD", ("cherry-pick", "--abort")),
    ("REVERT_HEAD", ("revert", "--abort")),
)

# A git index.lock older than this is treated as orphaned (left by a killed
# git process) and removed. Younger locks are left alone to avoid racing a
# legitimately-active git invocation.
_STALE_LOCK_AGE_SECONDS = 30

# Max lines of `git status --porcelain` to include in a stash-failure error.
_MAX_STATUS_LINES = 8


def _git_path(name: str, project_path: str) -> str:
    """Resolve a path inside the git dir (worktree/submodule safe).

    Returns an absolute path, or "" when resolution fails.
    """
    rc, out, _ = run_git("rev-parse", "--git-path", name, cwd=project_path)
    if rc != 0 or not out:
        return ""
    path = out.strip()
    if not os.path.isabs(path):
        path = os.path.join(project_path, path)
    return path


def _has_unmerged_paths(project_path: str) -> bool:
    """Return True when the working tree has unresolved merge conflicts."""
    rc, porcelain, _ = run_git("status", "--porcelain", cwd=project_path)
    if rc != 0 or not porcelain:
        return False
    return any(
        line[:2] in _UNMERGED_STATUS_CODES for line in porcelain.splitlines()
    )


def _detect_interrupted_operation(project_path: str):
    """Return (marker, abort_cmd) for an interrupted op, or None.

    Falls back to treating bare unmerged paths (no marker file) as a merge
    to abort — this covers conflict states left by a killed `stash pop` or
    `checkout` that carry no MERGE_HEAD.
    """
    for marker, abort_cmd in _INPROGRESS_MARKERS:
        marker_path = _git_path(marker, project_path)
        if marker_path and os.path.exists(marker_path):
            return marker, abort_cmd
    if _has_unmerged_paths(project_path):
        return "unmerged-paths", ("merge", "--abort")
    return None


def _remove_stale_index_lock(project_path: str) -> bool:
    """Remove a lingering .git/index.lock left by a killed git process.

    Only removes a lock older than _STALE_LOCK_AGE_SECONDS. Returns True
    when a lock was removed.
    """
    lock_path = _git_path("index.lock", project_path)
    if not lock_path or not os.path.exists(lock_path):
        return False
    try:
        age = time.time() - os.path.getmtime(lock_path)
    except OSError:
        return False
    if age < _STALE_LOCK_AGE_SECONDS:
        return False
    try:
        os.remove(lock_path)
        logger.warning(
            "Removed stale git index.lock in %s (age %.0fs)",
            project_path, age,
        )
        return True
    except OSError as e:
        logger.warning(
            "Failed to remove stale index.lock in %s: %s", project_path, e
        )
        return False


def _heal_interrupted_operation(project_path: str) -> Optional[str]:
    """Abort an interrupted merge/rebase/cherry-pick/revert if present.

    Returns a human-readable description of what was healed, or None when
    the tree was clean. Strictly safe: the caller's next steps discard
    local divergence to match the remote base branch, so nothing worth
    preserving exists in a conflicted intermediate state.
    """
    actions = []
    # A stale index.lock blocks every subsequent git write, including the
    # aborts below — clear it first.
    if _remove_stale_index_lock(project_path):
        actions.append("removed stale index.lock")

    detected = _detect_interrupted_operation(project_path)
    if detected:
        marker, abort_cmd = detected
        logger.warning(
            "Interrupted git operation in %s (%s); auto-aborting",
            project_path, marker,
        )
        run_git(*abort_cmd, cwd=project_path)
        actions.append(f"aborted {marker} via `git {' '.join(abort_cmd)}`")
        # If the abort could not clear the conflict (e.g. no MERGE_HEAD to
        # abort against), hard-reset. The downstream ff/reset to the remote
        # base finishes the job regardless.
        if _has_unmerged_paths(project_path):
            run_git("reset", "--hard", "HEAD", cwd=project_path)
            actions.append("reset --hard to clear residual conflicts")

    return "; ".join(actions) if actions else None


def _diagnose_stash_failure(project_path: str, stderr: str) -> str:
    """Build an informative error for a failed pre-mission stash.

    Names the concrete blocker (unmerged paths / disk full / quota / stale
    index.lock) and appends a truncated `git status --porcelain` snippet.
    Always begins with the historical "stash failed on dirty tree:" prefix
    so existing callers/tests keep matching.
    """
    stderr = stderr or ""
    low = stderr.lower()
    causes = []
    if "no space" in low:
        causes.append("disk full (No space left on device)")
    if "quota exceeded" in low or "disk quota" in low:
        causes.append("disk quota exceeded")
    if _has_unmerged_paths(project_path):
        causes.append("unmerged paths (conflict state)")
    lock_path = _git_path("index.lock", project_path)
    if "index.lock" in low or (lock_path and os.path.exists(lock_path)):
        causes.append("index.lock present")

    parts = [f"stash failed on dirty tree: {stderr}"]
    if causes:
        parts.append("cause(s): " + "; ".join(causes))

    rc, porcelain, _ = run_git("status", "--porcelain", cwd=project_path)
    if rc == 0 and porcelain:
        lines = porcelain.splitlines()
        snippet = "\n".join(lines[:_MAX_STATUS_LINES])
        extra = len(lines) - _MAX_STATUS_LINES
        if extra > 0:
            snippet += f"\n… (+{extra} more)"
        parts.append("git status --porcelain:\n" + snippet)
    return "\n".join(parts)


def prepare_project_branch(
    project_path: str, project_name: str, koan_root: str
) -> PrepResult:
    """Prepare a project for mission execution.

    Fetches the latest refs, stashes dirty state, checks out the base
    branch, and fast-forwards it to match the remote. Non-fatal — returns
    a PrepResult with success=False on errors rather than raising.
    """
    result = PrepResult()

    # Record current branch before any changes
    rc, current_branch, _ = run_git(
        "rev-parse", "--abbrev-ref", "HEAD", cwd=project_path
    )
    result.previous_branch = current_branch if rc == 0 else ""

    # Determine remote and base branch
    remote = get_upstream_remote(project_path, project_name, koan_root)
    result.remote_used = remote

    config_explicit = False
    try:
        config = load_projects_config(koan_root)
        if config:
            am = get_project_auto_merge(config, project_name)
            result.base_branch = am.get("base_branch", "main")
            # Check if the project explicitly configures base_branch.
            # Only project-level overrides count as explicit — the defaults
            # section provides a generic fallback that should NOT prevent
            # auto-detection for repos whose default branch differs (e.g.
            # "master" repos when defaults say "main").
            projects = config.get("projects", {}) or {}
            proj_cfg = _find_project_entry(projects, project_name) or {}
            proj_am = proj_cfg.get("git_auto_merge", {}) or {}
            if proj_am.get("base_branch"):
                config_explicit = True
    except Exception as e:
        logger.warning("config load error for base_branch: %s", e)

    base_branch = result.base_branch

    # Launching-repo guard: when the project being prepared IS the repo that
    # launched the service (project_path == koan_root) and it is currently on a
    # custom branch, leave it where it is. Switching it back to the base branch
    # would discard the development branch the operator is testing. This guard
    # applies ONLY to the launching repo — every other managed project still
    # resets to its base branch before a mission, which is the intended behavior.
    #
    # Note: this compares against the config base_branch, not the auto-detected
    # remote default (which is resolved only after the fetch below). The guard
    # intentionally trusts the config value — a self-hosted operator on a dev
    # branch does not want an auto-reset regardless of the real default branch.
    if (
        result.previous_branch
        and result.previous_branch not in (base_branch, "HEAD")
        and _is_same_dir(project_path, koan_root)
    ):
        logger.info(
            "Project %s is the launching repo on custom branch '%s'; "
            "staying put instead of switching to '%s'",
            project_name, result.previous_branch, base_branch,
        )
        result.base_branch = result.previous_branch
        return result

    # Fetch latest refs (with HTTPS token fallback for repos cloned via
    # gh with an unauthenticated HTTPS remote URL)
    rc, _, stderr = _fetch_with_https_fallback(
        remote, base_branch, project_path, timeout=30
    )
    if rc != 0 and not config_explicit:
        # Base branch was not explicitly configured — detect remote default
        detected = detect_remote_default_branch(remote, project_path)
        if detected != base_branch:
            logger.info(
                "Default branch for %s/%s is '%s', not '%s'",
                remote, project_name, detected, base_branch,
            )
            base_branch = detected
            result.base_branch = detected
            rc, _, stderr = _fetch_with_https_fallback(
                remote, base_branch, project_path, timeout=30
            )
    if rc != 0:
        result.success = False
        result.error = f"fetch failed: {stderr}"
        if _AUTH_ERROR_RE.search(stderr or ""):
            result.error += _auth_diagnostics()
        return result

    # Self-heal an interrupted merge/rebase/cherry-pick/revert left by a
    # previously-killed mission (restart, OOM, stagnation-kill, deploy).
    # git refuses to stash while conflicts are unresolved, so without this
    # every subsequent mission loops forever on "stash failed on dirty
    # tree". Safe: the ff-only/reset-hard below discards local divergence
    # to match <remote>/<base> anyway.
    healed = _heal_interrupted_operation(project_path)
    if healed:
        result.healed = healed
        logger.info("git prep self-heal for %s: %s", project_name, healed)

    # Stash dirty state if needed
    rc, porcelain, _ = run_git("status", "--porcelain", cwd=project_path)
    if rc == 0 and porcelain:
        rc, _, stderr = run_git(
            "stash", "--include-untracked", cwd=project_path
        )
        if rc == 0:
            result.stashed = True
        else:
            # Abort: continuing with a dirty tree risks data loss
            # if a downstream reset --hard is needed. Enrich the error with
            # the concrete blocker so operators don't have to guess.
            result.success = False
            result.error = _diagnose_stash_failure(project_path, stderr)
            return result

    # Checkout base branch
    rc, _, stderr = run_git("checkout", base_branch, cwd=project_path)
    if rc != 0:
        # Branch may not exist locally — create from remote tracking
        rc, _, stderr = run_git(
            "checkout", "-b", base_branch, f"{remote}/{base_branch}",
            cwd=project_path,
        )
        if rc != 0:
            result.success = False
            result.error = f"checkout failed: {stderr}"
            return result

    # Fast-forward to match remote
    rc, _, stderr = run_git(
        "merge", "--ff-only", f"{remote}/{base_branch}", cwd=project_path
    )
    if rc != 0:
        # Local diverged — log what will be discarded, then reset
        rc_log, diverged, _ = run_git(
            "log", f"{remote}/{base_branch}..HEAD", "--oneline",
            cwd=project_path,
        )
        if rc_log == 0 and diverged:
            logger.warning(
                "Discarding local commits on %s to match %s/%s:\n%s",
                base_branch, remote, base_branch, diverged,
            )

        rc, _, stderr = run_git(
            "reset", "--hard", f"{remote}/{base_branch}", cwd=project_path
        )
        if rc != 0:
            result.success = False
            result.error = f"reset failed: {stderr}"
            return result

    # Sync secondary remotes so fork-aware operations (e.g. locating a PR
    # head branch across forks) see fresh tracking refs for every remote.
    _sync_secondary_remotes(base_branch, remote, project_path)

    return result
