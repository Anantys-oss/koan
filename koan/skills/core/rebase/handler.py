"""Kōan rebase skill -- queue a PR rebase mission."""

from app.config import is_rebase_foreign_prs_allowed
from app.github_url_parser import parse_pr_url
from app.missions import extract_now_flag
from app import rebase_transition
import app.github_skill_helpers as _gh_helpers


def handle(ctx):
    """Handle /rebase command -- queue a rebase mission for a PR.

    Usage:
        /rebase https://github.com/owner/repo/pull/123
        /rebase --now https://github.com/owner/repo/pull/123
        /rebase https://github.com/owner/repo/pull/123 <focus area>

    Queues a mission that rebases the PR branch onto its target,
    reads all comments for context, and pushes the result. Any text
    after the URL is threaded into the mission as extra focus context.
    Use --now to queue at the top of the mission queue.
    """
    args = ctx.args.strip()

    # Extract --now flag for priority queuing
    urgent, args = extract_now_flag(args)

    # Extract --fix (opt into addressing review feedback after the rebase).
    # Strip it here — position-independent, like --now — and re-attach it to the
    # queued mission text below so it survives to the dispatcher.
    fix_tokens = [t for t in args.split() if t != "--fix"]
    has_fix = len(fix_tokens) != len(args.split())
    args = " ".join(fix_tokens)

    if not args:
        return (
            "Usage: /rebase [--now] [--fix] <github-pr-url> [focus area]\n"
            "Ex: /rebase https://github.com/sukria/koan/pull/42\n"
            "Ex: /rebase --now https://github.com/sukria/koan/pull/42\n"
            "Ex: /rebase --fix https://github.com/sukria/koan/pull/42\n"
            "Ex: /rebase https://github.com/sukria/koan/pull/42 address the security concern\n\n"
            "Rebases the PR branch onto its target and force-pushes the result. "
            "Add --fix to also address review feedback (implied when you add a "
            "focus area or severity after the URL).\n"
            "Use --now to queue at the top of the mission queue."
        )

    result = _gh_helpers.extract_github_url(args, url_type="pr")
    if not result:
        return (
            "\u274c No valid GitHub PR URL found.\n"
            "Ex: /rebase https://github.com/owner/repo/pull/123\n"
            "Use --now to queue at the top: /rebase --now <url>"
        )

    pr_url, context = result

    # Whether the user opted into the feedback leg. Any trailing text after the
    # URL (a focus area or severity) implies it, matching the dispatcher. Compute
    # before re-attaching --fix below.
    feedback_requested = has_fix or bool((context or "").strip())

    # Re-attach --fix so it survives into the queued mission text; the
    # dispatcher (_build_rebase_cmd) turns it into the runner's --fix flag.
    if has_fix:
        context = f"{(context or '').strip()} --fix".strip()

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as e:
        return f"\u274c {e}"

    project_path, project_name = _gh_helpers.resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return _gh_helpers.format_project_not_found_error(repo, owner=owner)

    try:
        if not hasattr(_gh_helpers, "is_own_pr"):
            import importlib
            importlib.reload(_gh_helpers)
        owned, head_branch = _gh_helpers.is_own_pr(owner, repo, pr_number)
    except Exception as e:
        return f"\u274c Failed to check PR ownership: {str(e)[:200]}"

    if not owned and not is_rebase_foreign_prs_allowed():
        return (
            f"\u274c Not my PR \u2014 branch `{head_branch}` was not created by "
            f"this instance. I only rebase my own pull requests."
        )

    duplicate = _gh_helpers.queue_github_mission_once(
        ctx, "rebase", pr_url, project_name, context, urgent=urgent,
        type_label="PR", number=pr_number, owner=owner, repo=repo,
    )
    if duplicate:
        return duplicate

    priority = " (priority)" if urgent else ""
    reply = f"Rebase queued{priority} for {_gh_helpers.format_success_message('PR', pr_number, owner, repo)}"
    # On the bare-rebase path, announce the /rebase default change while the
    # transition window is open (the notice disappears after the deadline).
    if not feedback_requested and rebase_transition.notice_active():
        reply += "\n\n" + rebase_transition.chat_notice()
    return reply
