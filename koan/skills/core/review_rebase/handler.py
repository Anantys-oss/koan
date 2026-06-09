"""Kōan review+rebase combo skill -- queue /review then /rebase for a PR."""

from app.github_url_parser import parse_pr_url
from app.github_skill_helpers import (
    extract_github_url,
    format_project_not_found_error,
    format_success_message,
    queue_github_mission,
    resolve_project_for_repo,
)


def _try_extract_gogs_pr(args: str):
    """Try to extract a Gogs PR URL from args.

    Returns (owner, repo, pr_number, pr_url) or None.
    """
    try:
        from app.gogs_url_parser import search_pr_url, build_pr_url
        from app.gogs_auth import get_gogs_host
    except ImportError:
        return None

    try:
        owner, repo, number = search_pr_url(args)
    except ValueError:
        return None

    host = get_gogs_host()
    pr_url = build_pr_url(host, owner, repo, int(number))
    return owner, repo, number, pr_url


def handle(ctx):
    """Handle /reviewrebase (alias /rr) -- queue review then rebase for a PR.

    Usage:
        /rr https://github.com/owner/repo/pull/123
        /rr https://git.example.com/owner/repo/pulls/42

    Queues two missions in order:
    1. /review <url> — generates review insights and learnings
    2. /rebase <url> — rebases the PR, informed by the fresh review
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /rr <pr-url>\n"
            "Ex: /rr https://github.com/sukria/koan/pull/42\n"
            "Ex: /rr https://git.example.com/owner/repo/pulls/42\n\n"
            "Queues /review then /rebase — review insights feed the rebase."
        )

    # ── Gogs PR ────────────────────────────────────────────────────────
    gogs = _try_extract_gogs_pr(args)
    if gogs:
        owner, repo, pr_number, pr_url = gogs
        project_path, project_name = resolve_project_for_repo(repo, owner=owner)
        if not project_path:
            return format_project_not_found_error(repo, owner=owner)

        review_ok = queue_github_mission(ctx, "review", pr_url, project_name)
        rebase_ok = queue_github_mission(ctx, "rebase", pr_url, project_name)

        target = f"Gogs PR #{pr_number} ({owner}/{repo})"
        if not review_ok and not rebase_ok:
            return f"⚠️ Both /review and /rebase already queued or running for {target}."
        if not review_ok:
            return f"Rebase queued for {target} (review already queued/running)."
        if not rebase_ok:
            return f"Review queued for {target} (rebase already queued/running)."
        return f"Review + rebase combo queued for {target}."

    # ── GitHub PR ──────────────────────────────────────────────────────
    result = extract_github_url(args, url_type="pr")
    if not result:
        return (
            "❌ No valid PR URL found.\n"
            "Ex: /rr https://github.com/owner/repo/pull/123"
        )

    pr_url, context = result

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as e:
        return f"❌ {e}"

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo, owner=owner)

    review_ok = queue_github_mission(ctx, "review", pr_url, project_name, context)
    rebase_ok = queue_github_mission(ctx, "rebase", pr_url, project_name)

    target = format_success_message('PR', pr_number, owner, repo)
    if not review_ok and not rebase_ok:
        return f"⚠️ Both /review and /rebase already queued or running for {target}."
    if not review_ok:
        return f"Rebase queued for {target} (review already queued/running)."
    if not rebase_ok:
        return f"Review queued for {target} (rebase already queued/running)."

    return f"Review + rebase combo queued for {target}"
