"""Koan explain skill -- queue a PR explanation mission."""

from app.github_url_parser import parse_github_url
from app.missions import extract_now_flag
from app.github_skill_helpers import handle_github_skill


def handle(ctx):
    """Handle /explain command -- queue a PR explanation mission.

    Usage:
        /explain https://github.com/owner/repo/pull/42
    """
    args = ctx.args.strip() if ctx.args else ""

    urgent, args = extract_now_flag(args)
    ctx.args = args

    return handle_github_skill(
        ctx,
        command="explain",
        url_type="pr",
        parse_func=parse_github_url,
        success_prefix="Explanation queued",
        urgent=urgent,
    )
