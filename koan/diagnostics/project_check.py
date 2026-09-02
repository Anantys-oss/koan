"""
Kōan diagnostic — Project health checks.

Validates project paths, git repo status, worktree branch ownership, and remote
reachability. Remote checks are behind the --full flag (slow).
"""

import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from diagnostics import CheckResult, FixResult

logger = logging.getLogger(__name__)


def run(koan_root: str, instance_dir: str, full: bool = False) -> List[CheckResult]:
    """Run project health diagnostic checks."""
    results = []

    # Load projects config
    try:
        from app.projects_config import load_projects_config, get_projects_from_config
        config = load_projects_config(koan_root)
    except Exception as e:
        results.append(CheckResult(
            name="projects_load",
            severity="error",
            message=f"Could not load projects config: {e}",
        ))
        return results

    if config is None:
        results.append(CheckResult(
            name="projects_config",
            severity="warn",
            message="No projects.yaml found",
            hint="Run /projects add to register projects",
        ))
        return results

    projects = get_projects_from_config(config)
    if not projects:
        results.append(CheckResult(
            name="projects_config",
            severity="warn",
            message="No projects configured in projects.yaml",
            hint="Run /projects add to register projects",
        ))
        return results

    for name, path in projects:
        project_path = Path(path)

        # Check path exists
        if not project_path.is_dir():
            results.append(CheckResult(
                name=f"project_{name}",
                severity="error",
                message=f"Project '{name}' path missing: {path}",
                hint="Update projects.yaml or recreate the directory",
            ))
            continue

        # Check it's a git repo
        git_dir = project_path / ".git"
        if not git_dir.exists():
            results.append(CheckResult(
                name=f"project_{name}",
                severity="error",
                message=f"Project '{name}' is not a git repo: {path}",
                hint="Initialize with 'git init' or re-clone",
            ))
            continue

        # Check the base branch is not held hostage by another worktree
        try:
            holder = _branch_holder(koan_root, name, str(project_path))
        except Exception as e:
            logger.warning(
                "Worktree collision check failed for project %s: %s",
                name,
                e,
            )
            results.append(CheckResult(
                name=f"project_{name}_worktree",
                severity="error",
                message=f"Project '{name}' worktree collision check failed: {e}",
                hint="Check logs and project git configuration before retrying /doctor",
            ))
            holder = None
        if holder:
            branch, holder_path = holder
            results.append(CheckResult(
                name=f"project_{name}_worktree",
                severity="error",
                message=(
                    f"Project '{name}' base branch '{branch}' is held by another "
                    f"worktree: {holder_path}"
                ),
                hint=(
                    "Missions for this project fail in git prep until the branch is "
                    "released. Run /doctor --fix, or detach it by hand: "
                    f"git -C {holder_path} checkout --detach"
                ),
                fixable=True,
            ))

        # Check for uncommitted changes on main (warn only)
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=path, capture_output=True, text=True, timeout=10,
            )
            dirty_count = len([l for l in result.stdout.splitlines() if l.strip()])
            if dirty_count > 0:
                results.append(CheckResult(
                    name=f"project_{name}",
                    severity="ok",
                    message=f"Project '{name}' ok ({dirty_count} uncommitted change(s))",
                ))
            else:
                results.append(CheckResult(
                    name=f"project_{name}",
                    severity="ok",
                    message=f"Project '{name}' ok (clean)",
                ))
        except Exception:
            results.append(CheckResult(
                name=f"project_{name}",
                severity="ok",
                message=f"Project '{name}' exists (git status unavailable)",
            ))

        # Remote reachability — only with --full
        if full:
            try:
                result = subprocess.run(
                    ["git", "ls-remote", "--exit-code", "--quiet", "origin"],
                    cwd=path, capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    results.append(CheckResult(
                        name=f"project_{name}_remote",
                        severity="ok",
                        message=f"Project '{name}' remote is reachable",
                    ))
                else:
                    results.append(CheckResult(
                        name=f"project_{name}_remote",
                        severity="warn",
                        message=f"Project '{name}' remote not reachable",
                        hint="Check git remote configuration and network",
                    ))
            except subprocess.TimeoutExpired:
                results.append(CheckResult(
                    name=f"project_{name}_remote",
                    severity="warn",
                    message=f"Project '{name}' remote check timed out",
                ))
            except Exception as e:
                results.append(CheckResult(
                    name=f"project_{name}_remote",
                    severity="warn",
                    message=f"Project '{name}' remote check failed: {e}",
                ))

    return results


def _base_branch(koan_root: str, project_name: str, project_path: str) -> str:
    """Return the branch git prep will try to check out for this project.

    Mirrors prepare_project_branch(): an explicit project-level
    git_auto_merge.base_branch wins, otherwise the remote's default branch is
    detected. Reporting a different branch than prep uses would make this check
    worse than useless.

    Resolution is **local only**. `/doctor` without --full keeps every network
    probe out of the default path, and detect_remote_default_branch() falls
    through to `git ls-remote` (15s, twice) whenever refs/remotes/<remote>/HEAD is
    unset — which a `git init` + `remote add` + `fetch` project never sets. With up
    to 50 projects that turns an interactive command into minutes. When the ref is
    unset we return "" and the caller skips the check rather than going to the wire.
    """
    from app.git_prep import (
        _find_project_entry,
        get_upstream_remote,
        load_projects_config,
        local_remote_default_branch,
    )

    config = load_projects_config(koan_root)
    projects = (config or {}).get("projects", {}) or {}
    project_config = _find_project_entry(projects, project_name) or {}
    explicit = (project_config.get("git_auto_merge", {}) or {}).get("base_branch")
    if explicit:
        return explicit
    remote = get_upstream_remote(project_path, project_name, koan_root)
    return local_remote_default_branch(remote, project_path) or ""


def _branch_holder(
    koan_root: str, project_name: str, project_path: str,
) -> Optional[Tuple[str, str]]:
    """Return (branch, worktree_path) when another worktree owns the base branch.

    This is the shape that took 90 consecutive missions down: an agent ran
    `git worktree add /tmp/base140 140`, and git allows a branch in at most one
    worktree, so the project's own checkout could not return to it. Returns None
    when nothing holds it, or when the holder is `locked` — that is someone's
    live workspace, and neither the check nor the fix disturbs it.
    """
    branch = _base_branch(koan_root, project_name, project_path)
    if not branch:
        return None
    from app.git_prep import _find_branch_holder
    holder = _find_branch_holder(project_path, branch)
    return (branch, holder) if holder else None


def fix(koan_root: str, instance_dir: str) -> List[FixResult]:
    """Detach any worktree holding a project's base branch.

    Detach, never remove: this frees the branch while leaving the worktree, its
    files and any uncommitted changes exactly where they are. Reclaiming the disk
    belongs to the bridge's foreign-worktree sweep.
    """
    results: List[FixResult] = []
    try:
        from app.projects_config import get_projects_from_config, load_projects_config
        config = load_projects_config(koan_root)
    except Exception as e:
        return [FixResult(
            name="projects_load", success=False,
            message=f"Could not load projects config: {e}",
        )]
    if config is None:
        return results

    for name, path in get_projects_from_config(config):
        if not Path(path).is_dir():
            continue
        try:
            holder = _branch_holder(koan_root, name, str(path))
        except Exception as e:
            logger.warning(
                "Worktree collision fix check failed for project %s: %s",
                name,
                e,
            )
            results.append(FixResult(
                name=f"project_{name}_worktree",
                success=False,
                message=f"Could not check worktree collision for {name}: {e}",
            ))
            continue
        if not holder:
            continue
        branch, holder_path = holder
        try:
            from app.git_prep import _release_branch_from_worktree
            freed = _release_branch_from_worktree(holder_path, branch)
            detail = holder_path
        except Exception as e:
            freed, detail = False, f"{holder_path} ({e})"
        results.append(FixResult(
            name=f"project_{name}_worktree",
            success=freed,
            message=(
                f"Detached {detail}, releasing '{branch}' for {name}"
                if freed else
                f"Could not detach {detail} holding '{branch}' for {name}"
            ),
        ))
    return results
