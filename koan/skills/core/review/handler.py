"""Kōan review skill -- queue a code review mission."""

import re
from typing import Optional, Tuple

from app.github_url_parser import parse_github_url
from app.missions import extract_now_flag
from app.github_skill_helpers import (
    handle_github_skill,
    parse_limit,
    parse_repo_url,
    resolve_project_for_repo,
    format_project_not_found_error,
    queue_github_mission,
)

_GOGS_SUBPATH_NAMES = frozenset(("issues", "pulls", "releases", "wiki", "settings"))


def _list_open_prs(owner: str, repo: str, limit: Optional[int] = None) -> list:
    """List open pull requests from a GitHub repo using gh CLI."""
    import json
    from app.github import run_gh

    gh_limit = str(limit) if limit else "100"
    output = run_gh(
        "pr", "list",
        "--repo", f"{owner}/{repo}",
        "--state", "open",
        "--limit", gh_limit,
        "--json", "number,title,url",
    )
    if not output.strip():
        return []
    return json.loads(output)


def _list_gogs_open_prs(owner: str, repo: str, limit: Optional[int] = None) -> list:
    """List open pull requests from a Gogs repo via the API."""
    from app.forge.gogs import GogsForge
    from app.gogs_auth import get_gogs_host

    forge = GogsForge()
    items = forge._api(
        "GET",
        f"repos/{owner}/{repo}/pulls",
        params={"state": "open", "limit": str(limit or 100)},
    )
    if not isinstance(items, list):
        return []

    host = get_gogs_host().rstrip("/")
    result = []
    for pr in items:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if not number:
            continue
        url = pr.get("html_url") or f"{host}/{owner}/{repo}/pulls/{number}"
        result.append({
            "number": number,
            "title": pr.get("title", ""),
            "url": url,
        })
    return result


def _try_extract_gogs_pr_or_issue(args: str):
    """Try to extract a Gogs PR or issue URL from args.

    Returns (owner, repo, number, type_label) or None.
    """
    try:
        from app.gogs_url_parser import search_pr_url
        owner, repo, number = search_pr_url(args)
        return owner, repo, number, "PR"
    except ValueError:
        pass

    try:
        from app.gogs_url_parser import search_issue_url
        owner, repo, number = search_issue_url(args)
        return owner, repo, number, "issue"
    except ValueError:
        pass

    return None


def _parse_gogs_repo_url(args: str) -> Optional[Tuple[str, str, str]]:
    """Extract a bare Gogs repo URL (no PR/issue number) from args.

    Returns (url, owner, repo) or None.
    """
    try:
        from app.gogs_auth import get_gogs_host
    except ImportError:
        return None

    host = get_gogs_host()
    if not host:
        return None

    host_escaped = re.escape(host.rstrip("/"))

    if re.search(rf'{host_escaped}/[^/\s]+/[^/\s]+/(?:pulls|issues)/\d+', args):
        return None

    match = re.search(rf'({host_escaped}/([^/\s]+)/([^/\s]+?)(?:\.git)?)(?=/|\s|$)', args)
    if not match:
        return None

    url = match.group(1)
    owner = match.group(2)
    repo = match.group(3)

    if repo in _GOGS_SUBPATH_NAMES:
        return None

    return url, owner, repo


def handle(ctx):
    """Handle /review command -- queue a code review mission.

    Usage:
        /review https://github.com/owner/repo/pull/42
        /review https://github.com/owner/repo/issues/42
        /review https://github.com/owner/repo              (batch: all open PRs)
        /review https://github.com/owner/repo --limit=5    (batch: 5 most recent)
        /review https://git.example.com/owner/repo/pulls/42
        /review https://git.example.com/owner/repo/issues/42
        /review https://git.example.com/owner/repo         (batch: all open Gogs PRs)
    """
    args = ctx.args.strip() if ctx.args else ""

    urgent, args = extract_now_flag(args)
    ctx.args = args

    # ── Gogs batch mode ───────────────────────────────────────────────
    gogs_repo = _parse_gogs_repo_url(args)
    if gogs_repo:
        return _handle_gogs_batch(ctx, args, gogs_repo, urgent)

    # ── GitHub batch mode ─────────────────────────────────────────────
    repo_match = parse_repo_url(args)
    if repo_match:
        return _handle_batch(ctx, args, repo_match)

    # ── Gogs single PR/issue ──────────────────────────────────────────
    gogs = _try_extract_gogs_pr_or_issue(args)
    if gogs:
        owner, repo, number, type_label = gogs
        return _handle_gogs_single(ctx, owner, repo, number, type_label, urgent)

    # ── GitHub single PR/issue ────────────────────────────────────────
    return handle_github_skill(
        ctx,
        command="review",
        url_type="pr-or-issue",
        parse_func=parse_github_url,
        success_prefix="Review queued",
        urgent=urgent,
    )


def _handle_batch(ctx, args: str, repo_match: Tuple[str, str, str]) -> str:
    """Handle batch /review: list open PRs from GitHub repo and queue a review for each."""
    url, owner, repo = repo_match
    limit = parse_limit(args)

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo, owner=owner)

    try:
        prs = _list_open_prs(owner, repo, limit=limit)
    except (RuntimeError, ValueError) as e:
        return f"❌ Failed to list PRs for {owner}/{repo}: {e}"

    if not prs:
        return f"No open PRs found in {owner}/{repo}."

    queued = 0
    for pr in prs:
        pr_url = pr.get("url") or f"https://github.com/{owner}/{repo}/pull/{pr['number']}"
        if queue_github_mission(ctx, "review", pr_url, project_name):
            queued += 1

    limit_note = f" (limited to {limit})" if limit else ""
    if queued == 0:
        return f"All PRs from {owner}/{repo} already queued or running{limit_note}."
    return f"Queued {queued} /review missions for {owner}/{repo}{limit_note}."


def _handle_gogs_single(
    ctx, owner: str, repo: str, number: str, type_label: str, urgent: bool
) -> str:
    """Handle /review for a single Gogs PR or issue."""
    from app.gogs_url_parser import build_pr_url, build_issue_url
    from app.gogs_auth import get_gogs_host

    host = get_gogs_host()

    if type_label == "PR":
        url = build_pr_url(host, owner, repo, int(number))
    else:
        url = build_issue_url(host, owner, repo, int(number))

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo, owner=owner)

    inserted = queue_github_mission(ctx, "review", url, project_name, urgent=urgent)
    if not inserted:
        return (
            f"⚠️ Duplicate ignored — /review already queued "
            f"or running for Gogs {type_label} #{number} ({owner}/{repo})."
        )

    priority = " (priority)" if urgent else ""
    return f"Review queued{priority} for Gogs {type_label} #{number} ({owner}/{repo})."


def _handle_gogs_batch(
    ctx, args: str, repo_match: Tuple[str, str, str], urgent: bool
) -> str:
    """Handle batch /review for a Gogs repo: queue a review for each open PR."""
    url, owner, repo = repo_match
    limit = parse_limit(args)

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo, owner=owner)

    try:
        prs = _list_gogs_open_prs(owner, repo, limit=limit)
    except (RuntimeError, ValueError) as e:
        return f"❌ Failed to list Gogs PRs for {owner}/{repo}: {e}"

    if not prs:
        return f"No open PRs found in Gogs repo {owner}/{repo}."

    queued = 0
    for pr in prs:
        pr_url = pr.get("url", "")
        if pr_url and queue_github_mission(ctx, "review", pr_url, project_name, urgent=urgent):
            queued += 1

    limit_note = f" (limited to {limit})" if limit else ""
    if queued == 0:
        return f"All Gogs PRs from {owner}/{repo} already queued or running{limit_note}."
    return f"Queued {queued} /review missions for Gogs repo {owner}/{repo}{limit_note}."
