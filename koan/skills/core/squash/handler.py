"""Koan squash skill -- queue a PR squash mission."""

from app.github_url_parser import parse_pr_url
from app.github_skill_helpers import (
    extract_github_url,
    format_project_not_found_error,
    format_success_message,
    queue_github_mission_once,
    resolve_project_for_repo,
    try_extract_gogs_pr,
)


def handle(ctx):
    """Handle /squash command -- queue a squash mission for a PR.

    Usage:
        /squash https://github.com/owner/repo/pull/123
        /squash https://git.example.com/owner/repo/pulls/42

    Squashes all commits on the PR into a single commit with a clean
    message, force-pushes, and updates the PR title and description.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /squash <pr-url>\n"
            "Ex: /squash https://github.com/sukria/koan/pull/42\n"
            "Ex: /squash https://git.example.com/owner/repo/pulls/42\n\n"
            "Squashes all commits into one, updates the commit message, "
            "PR title, and description, then force-pushes."
        )

    # ── Gogs PR ───────────────────────────────────────────────────────
    gogs = try_extract_gogs_pr(args)
    if gogs:
        owner, repo, pr_number, pr_url = gogs
        project_path, project_name = resolve_project_for_repo(repo, owner=owner)
        if not project_path:
            return format_project_not_found_error(repo, owner=owner)
        duplicate = queue_github_mission_once(
            ctx, "squash", pr_url, project_name,
            type_label="PR", number=pr_number, owner=owner, repo=repo,
        )
        if duplicate:
            return duplicate
        return f"Squash queued for Gogs PR #{pr_number} ({owner}/{repo})"

    # ── GitHub PR ─────────────────────────────────────────────────────
    result = extract_github_url(args, url_type="pr")
    if not result:
        return (
            "❌ No valid PR URL found.\n"
            "Ex: /squash https://github.com/owner/repo/pull/123"
        )

    pr_url, _ = result

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as e:
        return f"❌ {e}"

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo, owner=owner)

    duplicate = queue_github_mission_once(
        ctx, "squash", pr_url, project_name,
        type_label="PR", number=pr_number, owner=owner, repo=repo,
    )
    if duplicate:
        return duplicate

    return f"Squash queued for {format_success_message('PR', pr_number, owner, repo)}"
