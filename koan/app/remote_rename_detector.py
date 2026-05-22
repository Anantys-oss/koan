"""Detect and fix renamed GitHub remotes in workspace projects.

When a GitHub repository is renamed, the origin URL in .git/config becomes
stale. GitHub redirects git operations, but the cached owner/repo slug
diverges from the canonical name — breaking notification matching and
projects.yaml lookups.

This module queries the GitHub API for each project's origin remote,
compares the canonical full_name against the local URL, and updates
both .git/config and projects.yaml when a rename is detected.
"""

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from app.git_utils import run_git
from app.run_log import log

_GITHUB_REMOTE_RE = re.compile(r'github\.com[:/]([^/]+)/([^/\s.]+?)(?:\.git)?$')


def _extract_slug(url: str) -> Optional[str]:
    """Extract 'owner/repo' from a GitHub remote URL (SSH or HTTPS)."""
    match = _GITHUB_REMOTE_RE.search(url)
    if match:
        return f"{match.group(1).lower()}/{match.group(2).lower()}"
    return None


def _get_origin_url(project_path: str) -> Optional[str]:
    """Get the raw origin remote URL from a project."""
    rc, stdout, _ = run_git("remote", "get-url", "origin", cwd=project_path, timeout=5)
    if rc == 0 and stdout:
        return stdout
    return None


def _build_new_url(old_url: str, new_owner: str, new_repo: str) -> str:
    """Build a new remote URL preserving the original format (SSH vs HTTPS)."""
    if old_url.startswith("git@"):
        return f"git@github.com:{new_owner}/{new_repo}.git"
    if ".git" in old_url:
        return f"https://github.com/{new_owner}/{new_repo}.git"
    return f"https://github.com/{new_owner}/{new_repo}"


def _query_canonical_name(slug: str) -> Optional[str]:
    """Query GitHub API for the canonical full_name of a repo.

    GitHub follows redirects for renamed repos, so querying the old
    owner/repo returns the repo object with the new full_name.

    Returns lowercase 'owner/repo' or None on failure.
    """
    try:
        from app.github import api
        result = api(f"repos/{slug}", jq=".full_name")
        if result:
            return result.strip().strip('"').lower()
    except Exception as e:
        print(f"[remote-rename] API query failed for {slug}: {e}", file=sys.stderr)
    return None


def _update_git_remote(project_path: str, new_url: str) -> bool:
    """Update origin remote URL in .git/config."""
    rc, _, stderr = run_git("remote", "set-url", "origin", new_url, cwd=project_path, timeout=5)
    if rc != 0:
        print(f"[remote-rename] git remote set-url failed: {stderr}", file=sys.stderr)
        return False
    return True


def detect_and_fix_renamed_remotes(
    projects: List[Tuple[str, str]],
    koan_root: str,
) -> List[str]:
    """Scan projects for renamed GitHub remotes and fix them.

    Args:
        projects: List of (name, path) tuples.
        koan_root: Root directory for projects.yaml updates.

    Returns:
        List of log messages describing detected renames and fixes.
    """
    messages: List[str] = []
    fixed_projects: dict = {}

    for name, path in projects:
        if not Path(path).is_dir() or not (Path(path) / ".git").exists():
            continue

        origin_url = _get_origin_url(path)
        if not origin_url:
            continue

        old_slug = _extract_slug(origin_url)
        if not old_slug:
            continue

        canonical = _query_canonical_name(old_slug)
        if canonical is None:
            continue

        if canonical == old_slug:
            continue

        # Rename detected
        new_owner, new_repo = canonical.split("/", 1)
        new_url = _build_new_url(origin_url, new_owner, new_repo)

        log("git", f"Rename detected for '{name}': {old_slug} → {canonical}")
        messages.append(f"Rename detected: '{name}' {old_slug} → {canonical}")

        if _update_git_remote(path, new_url):
            log("git", f"Updated origin remote for '{name}' → {new_url}")
            messages.append(f"Updated .git/config for '{name}'")
            fixed_projects[name] = canonical

    if fixed_projects:
        _update_projects_config(koan_root, fixed_projects)
        messages.append(f"Updated projects.yaml for {len(fixed_projects)} project(s)")

    return messages


def _update_projects_config(koan_root: str, fixed: dict):
    """Update github_url and github_urls in projects.yaml for renamed repos.

    Args:
        fixed: dict mapping project name → new canonical slug.
    """
    try:
        from app.projects_config import load_projects_config, save_projects_config
        from app.utils import get_all_github_remotes

        config = load_projects_config(koan_root)
        if config is None:
            return

        projects = config.get("projects", {})
        modified = False

        for name, new_slug in fixed.items():
            project = projects.get(name)
            if not isinstance(project, dict):
                continue

            old_url = project.get("github_url", "")
            if old_url and old_url.lower() != new_slug:
                project["github_url"] = new_slug
                modified = True

            path = project.get("path", "")
            if path and Path(path).is_dir():
                all_urls = get_all_github_remotes(path)
                if all_urls:
                    project["github_urls"] = all_urls
                    modified = True

        if modified:
            save_projects_config(koan_root, config)
    except Exception as e:
        print(f"[remote-rename] projects.yaml update failed: {e}", file=sys.stderr)
