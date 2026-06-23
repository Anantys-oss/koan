"""Koan /orphans skill — recover orphan branches via rebase + draft PR."""

import logging
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


def handle(ctx):
    """Handle /orphans command.

    Finds orphan branches (unmerged, no open PR) for a project, rebases
    each onto the default branch, and creates a draft PR.
    """
    args = ctx.args.strip() if ctx.args else ""

    project_name, project_path = _resolve_project(args, ctx)
    if not project_path:
        if project_name and project_name.startswith("_prompt_"):
            names = project_name[len("_prompt_"):]
            return f"Which project? Usage: /orphans <project>\nAvailable: {names}"
        return "No project found. Usage: /orphans <project_name>"

    orphans = _find_orphans(project_name, project_path, str(ctx.instance_dir))
    if orphans is None:
        return f"❌ Failed to check orphan branches for {project_name}"
    if not orphans:
        return f"✅ No orphan branches found for {project_name}"

    results = _recover_orphans(orphans, project_path)
    return _format_results(project_name, results)


def _resolve_project(args: str, ctx) -> Tuple[str, Optional[str]]:
    """Resolve project name and path from args or context."""
    from app.utils import get_known_projects, resolve_project_from_list

    projects = get_known_projects()
    if not projects:
        return "", None

    proj_dict = dict(projects)

    if args:
        name, path = resolve_project_from_list(list(proj_dict.items()), args)
        if name:
            return name, path
        return args, None

    if len(proj_dict) == 1:
        name = next(iter(proj_dict))
        return name, proj_dict[name]

    names = ", ".join(sorted(proj_dict.keys()))
    return f"_prompt_{names}", None


def _find_orphans(
    project_name: str, project_path: str, instance_dir: str,
) -> Optional[List[str]]:
    """Find orphan branches for a project. Returns None on error."""
    from app.git_sync import GitSync
    from app.git_utils import run_git

    rc, _, _ = run_git("fetch", "--prune", cwd=project_path, timeout=60)
    if rc != 0:
        log.warning("git fetch --prune failed for %s, results may be stale", project_name)

    sync = GitSync(instance_dir, project_name, project_path)
    try:
        return sync.get_orphan_branches()
    except Exception as exc:
        log.warning("orphan detection failed for %s: %s", project_name, exc)
        return None


def _recover_orphans(
    orphans: List[str], project_path: str,
) -> List[Dict]:
    """Rebase each orphan branch onto main and create a draft PR."""
    from app.git_prep import detect_remote_default_branch
    from app.git_utils import run_git

    rc, original_branch, _ = run_git(
        "rev-parse", "--abbrev-ref", "HEAD", cwd=project_path,
    )
    if rc != 0:
        original_branch = detect_remote_default_branch("origin", project_path)

    default_branch = detect_remote_default_branch("origin", project_path)

    results = []
    for branch in orphans:
        result = _recover_one(branch, project_path, default_branch)
        results.append(result)

    rc, _, _ = run_git("checkout", original_branch, cwd=project_path)
    if rc != 0:
        log.warning("failed to restore branch %s — repo may be on wrong branch", original_branch)
    return results


def _recover_one(branch: str, project_path: str, default_branch: str) -> Dict:
    """Rebase a single orphan branch and create a draft PR."""
    from app.git_utils import run_git
    from app.github import pr_create

    result = {"branch": branch, "rebased": False, "pr_url": None, "error": None}

    rc, _, stderr = run_git("checkout", branch, cwd=project_path)
    if rc != 0:
        result["error"] = f"checkout failed: {stderr[:200]}"
        return result

    rc, _, _ = run_git(
        "rebase", f"origin/{default_branch}", cwd=project_path, timeout=120,
    )
    if rc == 0:
        result["rebased"] = True
    else:
        abort_rc, _, _ = run_git("rebase", "--abort", cwd=project_path)
        if abort_rc != 0:
            result["error"] = "rebase failed and abort failed — repo may be in broken state"
            return result

    push_args = ["push", "-u", "origin", branch]
    if result["rebased"]:
        push_args.insert(1, "--force-with-lease")
    rc, _, stderr = run_git(*push_args, cwd=project_path, timeout=60)
    if rc != 0:
        result["error"] = f"push failed: {stderr[:200]}"
        return result

    rebase_note = "Rebased onto" if result["rebased"] else "Could not rebase onto"
    body = (
        f"Recovered orphan branch `{branch}`.\n\n"
        f"{rebase_note} `{default_branch}` — branch pushed as-is."
    )

    short_branch = branch.split("/", 1)[-1] if "/" in branch else branch
    title = f"fix: recover orphan {short_branch}"

    try:
        pr_url = pr_create(
            title=title,
            body=body,
            draft=True,
            base=default_branch,
            cwd=project_path,
        )
        result["pr_url"] = pr_url.strip() if pr_url else None
    except Exception as exc:
        result["error"] = f"PR creation failed: {str(exc)[:200]}"

    return result


def _format_results(project_name: str, results: List[Dict]) -> str:
    """Format recovery results for display."""
    succeeded = [r for r in results if r.get("pr_url")]
    failed = [r for r in results if not r.get("pr_url")]

    lines = [f"🌿 Orphan recovery for {project_name} — {len(results)} branch(es):"]
    lines.append("")

    for r in succeeded:
        status = "rebased" if r["rebased"] else "as-is"
        lines.append(f"  ✅ {r['branch']} ({status})")
        lines.append(f"     → {r['pr_url']}")

    lines.extend(
        f"  ❌ {r['branch']}: {r.get('error', 'unknown error')}" for r in failed
    )

    if succeeded:
        lines.append(f"\n{len(succeeded)} draft PR(s) created.")

    return "\n".join(lines)
