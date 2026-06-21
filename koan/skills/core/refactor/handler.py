"""Kōan refactor skill -- queue a refactoring mission."""

from app.github_url_parser import parse_github_url
from app.github_skill_helpers import extract_github_url, handle_github_skill


def handle(ctx):
    """Handle /refactor command -- queue a refactoring mission.

    Usage:
        /refactor https://github.com/owner/repo/pull/42
        /refactor https://github.com/owner/repo/issues/42
        /refactor src/utils.py
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /refactor <github-url-or-path>\n"
            "Ex: /refactor https://github.com/sukria/koan/pull/42\n"
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

    # No URL found - treat as a file path
    from app.utils import insert_pending_mission

    file_path = args.strip()
    insert_pending_mission(f"/refactor {file_path}")

    return f"Refactor queued for {file_path}"
