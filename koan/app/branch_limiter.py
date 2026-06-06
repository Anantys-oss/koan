"""Branch saturation limiter — caps unreviewed work per project.

Counts "pending branches" as the union (deduplicated by branch name) of:
1. Local unmerged koan/* branches (via GitSync)
2. Open PR branches on GitHub (via gh CLI)

When the count reaches ``max_pending_branches``, the project is
considered branch-saturated: no new missions are picked up and
exploration is blocked until branches are reviewed/merged.

Provides:
- count_pending_branches(project_path, github_urls, author) -> int
"""

import logging
from typing import List, Set

log = logging.getLogger(__name__)


def _get_local_unmerged_branches(instance_dir: str, project_name: str,
                                  project_path: str) -> Set[str]:
    """Return set of local unmerged koan/* branch names."""
    try:
        from app.git_sync import GitSync
        sync = GitSync(instance_dir, project_name, project_path)
        return set(sync.get_unmerged_branches())
    except Exception as e:
        log.debug("Failed to get local unmerged branches for %s: %s",
                  project_name, e)
        return set()


def _get_open_pr_branches(
    project_name: str,
    project_path: str,
    github_urls: List[str],
    author: str,
) -> Set[str]:
    """Return set of branch names from open PRs for the project.

    Routes through the project's forge. The GitHub path is unchanged (iterate
    the configured repo URLs via ``gh``). Non-GitHub forges (Gogs, etc.)
    resolve the repo slug from the checkout and query the forge API — without
    this, open PRs on a self-hosted forge are never counted, so merged work is
    invisible to the saturation accounting.
    """
    if not author:
        return set()

    from app.forge import get_forge
    forge = get_forge(project_name)

    pr_branches: Set[str] = set()

    if forge.name != "github":
        try:
            repo = forge.repo_slug(project_path) or ""
            if repo:
                pr_branches.update(
                    forge.list_open_pr_branches(repo, author, cwd=project_path)
                )
        except Exception as e:
            log.debug("Failed to list open PR branches (forge=%s) for %s: %s",
                      forge.name, project_name, e)
        return pr_branches

    if not github_urls:
        return pr_branches

    from app.github import list_open_pr_branches
    for url in github_urls:
        try:
            branches = list_open_pr_branches(url, author)
            pr_branches.update(branches)
        except Exception as e:
            log.debug("Failed to list open PR branches for %s: %s", url, e)
    return pr_branches


def count_pending_branches(
    instance_dir: str,
    project_name: str,
    project_path: str,
    github_urls: List[str],
    author: str,
) -> int:
    """Count pending (unreviewed) branches for a project.

    Returns the size of the union of local unmerged branches and open
    PR branches, deduplicated by branch name.

    On GitHub API errors, falls back to local-only count.
    """
    local_branches = _get_local_unmerged_branches(
        instance_dir, project_name, project_path,
    )
    pr_branches = _get_open_pr_branches(
        project_name, project_path, github_urls, author,
    )

    # Union: a branch with both a local copy and an open PR counts once
    return len(local_branches | pr_branches)
