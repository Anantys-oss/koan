"""Kōan ultrareview skill -- queue an ultra-thorough code review mission.

An ultra review combines the architecture-focused main pass with the
silent-failure-hunter pass, producing the most thorough single review
Kōan can run. It reuses the /review pipeline (review_runner) with the
``--ultra`` flag; only PRs are supported.
"""

from app.github_url_parser import parse_pr_url
from app.missions import extract_now_flag


def handle(ctx):
    """Handle /ultrareview (alias /urv) -- queue an ultra review for a PR.

    Usage:
        /ultrareview https://github.com/owner/repo/pull/42
        /ultrareview --now https://github.com/owner/repo/pull/42
    """
    from app.github_skill_helpers import handle_github_skill

    args = ctx.args.strip() if ctx.args else ""

    # Extract --now flag for priority queuing
    urgent, args = extract_now_flag(args)
    ctx.args = args

    return handle_github_skill(
        ctx,
        command="ultrareview",
        url_type="pr",
        parse_func=parse_pr_url,
        success_prefix="Ultra review queued",
        urgent=urgent,
    )
