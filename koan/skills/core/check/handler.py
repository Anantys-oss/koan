"""Koan /check skill -- queue a check mission for a PR or issue."""

from app.github_url_parser import parse_github_url
from app.github_skill_helpers import extract_github_url, resolve_project_for_repo


def handle(ctx):
    """Handle /check command -- queue a mission to check a PR or issue.

    Usage:
        /check <github-url>

    Queues a mission that inspects the PR/issue via GitHub API and
    takes action (rebase, review, plan) as needed.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /check <github-pr-or-issue-url>\n"
            "Ex: /check https://github.com/sukria/koan/pull/85\n\n"
            "Queues a mission that checks rebase/review status for PRs, "
            "or triggers /plan for updated issues."
        )

    # Extract and validate URL
    result = extract_github_url(args, url_type="pr-or-issue")
    if not result:
        return (
            "\u274c No valid GitHub PR or issue URL found.\n"
            "Expected: https://github.com/owner/repo/pull/123\n"
            "      or: https://github.com/owner/repo/issues/123"
        )

    url, _context = result

    # Parse URL to get owner/repo/type/number
    try:
        owner, repo, url_type, number = parse_github_url(url)
    except ValueError as e:
        return f"\u274c {e}"

    type_label = "PR" if url_type == "pull" else "issue"
    label = f"{type_label} #{number} ({owner}/{repo})"

    # Resolve project name for the mission tag
    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_name:
        project_name = repo

    # Queue the mission with clean format
    from app.utils import insert_pending_mission

    insert_pending_mission(f"/check {url}", project_name)

    return f"\U0001f50d Check queued for {label}"
