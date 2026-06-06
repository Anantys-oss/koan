"""Kōan rebase skill -- queue a PR rebase mission."""

from app.config import is_rebase_foreign_prs_allowed
from app.github_url_parser import parse_pr_url
from app.missions import extract_now_flag
import app.github_skill_helpers as _gh_helpers


def _is_own_gogs_pr(owner: str, repo: str, pr_number: str) -> tuple:
    """Check if a Gogs PR was created by this Kōan instance (branch prefix match).

    Returns (is_owned, head_branch).
    """
    from app.config import get_branch_prefix
    from app.forge.gogs import GogsForge

    forge = GogsForge()
    data = forge.pr_view(f"{owner}/{repo}", int(pr_number))
    head_branch = data.get("headRefName", "")
    prefix = get_branch_prefix()
    return head_branch.startswith(prefix), head_branch


def _handle_gogs(ctx, args: str, urgent: bool):
    """Handle /rebase for a Gogs PR URL. Returns None to fall through to GitHub."""
    try:
        from app.gogs_url_parser import search_pr_url, build_pr_url
        from app.gogs_auth import get_gogs_host
    except ImportError:
        return None

    try:
        owner, repo, pr_number = search_pr_url(args)
    except ValueError:
        return None

    host = get_gogs_host()
    pr_url = build_pr_url(host, owner, repo, int(pr_number))

    project_path, project_name = _gh_helpers.resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return _gh_helpers.format_project_not_found_error(repo, owner=owner)

    try:
        owned, head_branch = _is_own_gogs_pr(owner, repo, pr_number)
    except Exception as e:
        return f"❌ Failed to check Gogs PR ownership: {str(e)[:200]}"

    if not owned and not is_rebase_foreign_prs_allowed():
        return (
            f"❌ Not my PR — branch `{head_branch}` was not created by "
            f"this instance. I only rebase my own pull requests."
        )

    duplicate = _gh_helpers.queue_github_mission_once(
        ctx, "rebase", pr_url, project_name, urgent=urgent,
        type_label="PR", number=int(pr_number), owner=owner, repo=repo,
    )
    if duplicate:
        return duplicate

    priority = " (priority)" if urgent else ""
    return f"Rebase queued{priority} for Gogs PR #{pr_number} ({owner}/{repo})."


def handle(ctx):
    """Handle /rebase command -- queue a rebase mission for a PR.

    Usage:
        /rebase https://github.com/owner/repo/pull/123
        /rebase --now https://github.com/owner/repo/pull/123
        /rebase https://git.example.com/owner/repo/pulls/42

    Queues a mission that rebases the PR branch onto its target,
    reads all comments for context, and pushes the result.
    Use --now to queue at the top of the mission queue.
    """
    args = ctx.args.strip()

    urgent, args = extract_now_flag(args)

    if not args:
        return (
            "Usage: /rebase [--now] <pr-url>\n"
            "Ex: /rebase https://github.com/sukria/koan/pull/42\n"
            "Ex: /rebase --now https://github.com/sukria/koan/pull/42\n"
            "Ex: /rebase https://git.example.com/owner/repo/pulls/42\n\n"
            "Queues a mission that rebases the PR branch onto its target, "
            "reads comments for context, and force-pushes the result.\n"
            "Use --now to queue at the top of the mission queue."
        )

    # ── Gogs PR ────────────────────────────────────────────────────────
    gogs_result = _handle_gogs(ctx, args, urgent)
    if gogs_result is not None:
        return gogs_result

    # ── GitHub PR ──────────────────────────────────────────────────────
    result = _gh_helpers.extract_github_url(args, url_type="pr")
    if not result:
        return (
            "❌ No valid PR URL found.\n"
            "Ex: /rebase https://github.com/owner/repo/pull/123\n"
            "Use --now to queue at the top: /rebase --now <url>"
        )

    pr_url, _ = result

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as e:
        return f"❌ {e}"

    project_path, project_name = _gh_helpers.resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return _gh_helpers.format_project_not_found_error(repo, owner=owner)

    try:
        if not hasattr(_gh_helpers, "is_own_pr"):
            import importlib
            importlib.reload(_gh_helpers)
        owned, head_branch = _gh_helpers.is_own_pr(owner, repo, pr_number)
    except Exception as e:
        return f"❌ Failed to check PR ownership: {str(e)[:200]}"

    if not owned and not is_rebase_foreign_prs_allowed():
        return (
            f"❌ Not my PR — branch `{head_branch}` was not created by "
            f"this instance. I only rebase my own pull requests."
        )

    duplicate = _gh_helpers.queue_github_mission_once(
        ctx, "rebase", pr_url, project_name, urgent=urgent,
        type_label="PR", number=pr_number, owner=owner, repo=repo,
    )
    if duplicate:
        return duplicate

    priority = " (priority)" if urgent else ""
    return f"Rebase queued{priority} for {_gh_helpers.format_success_message('PR', pr_number, owner, repo)}"
