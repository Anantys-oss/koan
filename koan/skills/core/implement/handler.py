"""Kōan implement skill -- queue an implementation mission for a GitHub issue or PR."""

from app.github_url_parser import parse_github_url
from app.github_skill_helpers import handle_github_skill
from app.missions import extract_now_flag


def handle(ctx):
    """Handle /implement command -- queue a mission to implement a GitHub issue or PR.

    Usage:
        /implement https://github.com/owner/repo/issues/42
        /implement --now https://github.com/owner/repo/pull/42
        /implement https://github.com/owner/repo/issues/42 phase 1 only
    """
    args = ctx.args.strip() if ctx.args else ""

    urgent, args = extract_now_flag(args)
    ctx.args = args

    return handle_github_skill(
        ctx,
        command="implement",
        url_type="pr-or-issue",
        parse_func=parse_github_url,
        success_prefix="Implementation queued",
        urgent=urgent,
    )
