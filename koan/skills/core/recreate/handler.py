"""Kōan recreate skill -- queue a PR recreation mission."""

from app.github_url_parser import parse_pr_url
from app.github_skill_helpers import (
    extract_github_url,
    format_project_not_found_error,
    format_success_message,
    queue_github_mission_once,
    resolve_project_for_repo,
)


def handle(ctx):
    """Handle /recreate command -- queue a mission to recreate a PR from scratch.

    Usage:
        /recreate https://github.com/owner/repo/pull/123

    Unlike /rebase which preserves the branch history, /recreate reads the
    original PR to understand its intent, then reimplements the feature
    from scratch on top of the current upstream target.  Use this when a
    branch has diverged too far for a clean rebase.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /recreate <github-pr-url>\n"
            "Ex: /recreate https://github.com/sukria/koan/pull/42\n\n"
            "Reads the original PR to understand intent, then reimplements "
            "the feature from scratch on current upstream. Use when a branch "
            "has diverged too far for a clean rebase."
        )

    result = extract_github_url(args, url_type="pr")
    if not result:
        return (
            "\u274c No valid GitHub PR URL found.\n"
            "Ex: /recreate https://github.com/owner/repo/pull/123"
        )

    pr_url, _ = result

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as e:
        return f"\u274c {e}"

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo, owner=owner)

    duplicate = queue_github_mission_once(
        ctx, "recreate", pr_url, project_name,
        type_label="PR", number=pr_number, owner=owner, repo=repo,
    )
    if duplicate:
        return duplicate

    return f"Recreate queued for {format_success_message('PR', pr_number, owner, repo)}"
