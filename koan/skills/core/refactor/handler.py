"""Kōan refactor skill -- queue a refactoring mission."""

from app.github_url_parser import parse_github_url
from app.github_skill_helpers import extract_github_url, handle_github_skill, try_extract_gogs_pr


def handle(ctx):
    """Handle /refactor command -- queue a refactoring mission.

    Usage:
        /refactor https://github.com/owner/repo/pull/42
        /refactor https://github.com/owner/repo/issues/42
        /refactor https://git.example.com/owner/repo/pulls/42
        /refactor src/utils.py
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /refactor <url-or-path>\n"
            "Ex: /refactor https://github.com/sukria/koan/pull/42\n"
            "Ex: /refactor https://git.example.com/owner/repo/pulls/42\n"
            "Ex: /refactor src/utils.py\n\n"
            "Queues a refactoring mission."
        )

    # Try to extract a GitHub URL first
    result = extract_github_url(args, url_type="pr-or-issue")
    if result:
        return handle_github_skill(
            ctx,
            command="refactor",
            url_type="pr-or-issue",
            parse_func=parse_github_url,
            success_prefix="Refactor queued",
        )

    # Try Gogs URL
    gogs = try_extract_gogs_pr(args)
    if gogs:
        from app.github_skill_helpers import (
            format_project_not_found_error,
            queue_github_mission_once,
            resolve_project_for_repo,
        )
        owner, repo, number, pr_url = gogs
        project_path, project_name = resolve_project_for_repo(repo, owner=owner)
        if not project_path:
            return format_project_not_found_error(repo, owner=owner)
        duplicate = queue_github_mission_once(
            ctx, "refactor", pr_url, project_name,
            type_label="PR", number=number, owner=owner, repo=repo,
        )
        if duplicate:
            return duplicate
        return f"Refactor queued for Gogs PR #{number} ({owner}/{repo})"

    # No URL found - treat as a file path
    from app.utils import insert_pending_mission

    file_path = args.strip()
    mission_entry = f"- /refactor {file_path}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"Refactor queued for {file_path}"
