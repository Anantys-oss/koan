"""Kōan refactor skill -- queue a PR refactor mission."""

from app.github_url_parser import parse_pr_url
from app.missions import extract_now_flag
import app.github_skill_helpers as _gh_helpers


def handle(ctx):
    """Handle /refactor command -- queue a refactor mission for a PR.

    Usage:
        /refactor https://github.com/owner/repo/pull/123
        /refactor --now https://github.com/owner/repo/pull/123
        /refactor https://github.com/owner/repo/pull/123 focus on the tests

    Queues a mission that refactors the PR branch's changed code for
    simplicity and clarity (preserving behavior), makes one clean commit,
    pushes to the existing PR branch, and comments on the PR. Any text after
    the URL becomes extra focus for the refactor. Use --now to queue at the
    top of the mission queue.
    """
    args = ctx.args.strip() if ctx.args else ""

    # Extract --now flag for priority queuing
    urgent, args = extract_now_flag(args)

    if not args:
        return (
            "Usage: /refactor [--now] <github-pr-url> [focus area]\n"
            "Ex: /refactor https://github.com/sukria/koan/pull/42\n"
            "Ex: /refactor --now https://github.com/sukria/koan/pull/42\n"
            "Ex: /refactor https://github.com/sukria/koan/pull/42 focus on the tests\n\n"
            "Refactors the PR's changed code for clarity (preserving behavior), "
            "commits, pushes, and comments on the PR.\n"
            "Use --now to queue at the top of the mission queue."
        )

    result = _gh_helpers.extract_github_url(args, url_type="pr")
    if not result:
        return (
            "❌ No valid GitHub PR URL found.\n"
            "Ex: /refactor https://github.com/owner/repo/pull/123\n"
            "Use --now to queue at the top: /refactor --now <url>"
        )

    pr_url, context = result

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as e:
        return f"❌ {e}"

    project_path, project_name = _gh_helpers.resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return _gh_helpers.format_project_not_found_error(repo, owner=owner)

    duplicate = _gh_helpers.queue_github_mission_once(
        ctx, "refactor", pr_url, project_name, context, urgent=urgent,
        type_label="PR", number=pr_number, owner=owner, repo=repo,
    )
    if duplicate:
        return duplicate

    priority = " (priority)" if urgent else ""
    return f"Refactor queued{priority} for {_gh_helpers.format_success_message('PR', pr_number, owner, repo)}"
