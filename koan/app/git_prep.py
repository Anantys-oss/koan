"""
Kōan -- Pre-mission git preparation.

Ensures a project starts each mission on a fresh, up-to-date base branch.
Called before every mission execution in the agent loop.

Two public functions:
- get_upstream_remote(): Determines the canonical remote for a project.
- prepare_project_branch(): Full pre-mission git state preparation.
"""

import logging
import re
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
    (e.g., --onto rebase needs both origin/ and upstream/ refs current).
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
        return result

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
            # if a downstream reset --hard is needed
            result.success = False
            result.error = f"stash failed on dirty tree: {stderr}"
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

    # Sync secondary remotes so fork-aware operations (--onto rebase,
    # _is_ancestor checks) see fresh tracking refs for every remote.
    _sync_secondary_remotes(base_branch, remote, project_path)

    return result
