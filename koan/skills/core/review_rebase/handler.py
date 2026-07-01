"""Kōan review+rebase combo skill -- queue /review then /rebase for a PR."""

from app.github_url_parser import parse_pr_url
from app.github_skill_helpers import (
    extract_github_url,
    format_project_not_found_error,
    format_success_message,
    queue_github_missions,
    resolve_project_for_repo,
)


def handle(ctx):
    """Handle /reviewrebase (alias /rr) -- queue review then rebase for a PR.

    Usage:
        /rr https://github.com/owner/repo/pull/123

    Queues two missions in order:
    1. /review <url> — generates review insights and learnings
    2. /rebase <url> — rebases the PR, informed by the fresh review

    By default the combo is appended to the end of the pending queue. Pass
    --now (e.g. /rr --now <url>) to jump the queue and run it next.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /rr [--now] <github-pr-url>\n"
            "Ex: /rr https://github.com/sukria/koan/pull/42\n\n"
            "Queues /review then /rebase — review insights feed the rebase.\n"
            "Add --now to jump the queue and run the combo next."
        )

    # --now jumps the queue; default appends at the end. Strip the flag
    # wherever it appears so it never leaks into the URL or review context.
    urgent = False
    tokens = [t for t in args.split() if t != "--now"]
    if len(tokens) != len(args.split()):
        urgent = True
    args = " ".join(tokens)

    result = extract_github_url(args, url_type="pr")
    if not result:
        return (
            "\u274c No valid GitHub PR URL found.\n"
            "Ex: /rr https://github.com/owner/repo/pull/123"
        )

    pr_url, context = result

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as e:
        return f"\u274c {e}"

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo, owner=owner)

    # Queue review above rebase — review learnings inform the rebase. One
    # atomic batch keeps the order intact (the run loop never sees rebase
    # queued before review). urgent=True (via --now) puts the block at the
    # top; otherwise it appends at the end of the pending queue.
    review_ok, rebase_ok = queue_github_missions(
        ctx,
        [
            ("review", pr_url, project_name, context),
            ("rebase", pr_url, project_name, None),
        ],
        urgent=urgent,
    )

    target = format_success_message('PR', pr_number, owner, repo)
    if not review_ok and not rebase_ok:
        return f"\u26a0\ufe0f Both /review and /rebase already queued or running for {target}."
    if not review_ok:
        return f"Rebase queued for {target} (review already queued/running)."
    if not rebase_ok:
        return f"Review queued for {target} (rebase already queued/running)."

    return f"Review + rebase combo queued for {target}"
