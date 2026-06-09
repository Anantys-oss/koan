"""Kōan PR review skill — review and update pull requests (GitHub and Gogs)."""

from pathlib import Path

from app.github_skill_helpers import extract_github_url


def handle(ctx):
    """Handle /pr command — review and update a pull request.

    Usage:
        /pr https://github.com/owner/repo/pull/123
        /pr https://git.example.com/owner/repo/pulls/42

    Performs a full pipeline: rebase, address feedback, refactor, review,
    test, push, and comment on the PR.

    Supports both GitHub and self-hosted Gogs instances.
    """
    args = ctx.args
    send = ctx.send_message

    if not args:
        return (
            "Usage: /pr <pr-url>\n"
            "Ex: /pr https://github.com/owner/repo/pull/123\n"
            "Ex: /pr https://git.example.com/owner/repo/pulls/42\n\n"
            "Full pipeline: rebase → address feedback → refactor → "
            "review → test → push → comment."
        )

    # ── Try GitHub URL first ──────────────────────────────────────────
    github_result = extract_github_url(args, url_type="pr")
    if github_result:
        pr_url = github_result[0]
        return _handle_github_pr(pr_url, send)

    # ── Try Gogs URL ──────────────────────────────────────────────────
    gogs = _try_extract_gogs_pr(args)
    if gogs:
        owner, repo, pr_number = gogs
        return _handle_gogs_pr(owner, repo, pr_number, send)

    return (
        "❌ No valid PR URL found.\n"
        "Ex: /pr https://github.com/owner/repo/pull/123\n"
        "Ex: /pr https://git.example.com/owner/repo/pulls/42"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _try_extract_gogs_pr(args: str):
    """Return (owner, repo, pr_number) from a Gogs PR URL, or None."""
    try:
        from app.gogs_url_parser import search_pr_url
        return search_pr_url(args)
    except ValueError:
        return None


def _handle_github_pr(pr_url: str, send):
    """Review pipeline for a GitHub PR."""
    from app.github_url_parser import parse_pr_url
    from app.utils import resolve_project_path
    from app.pr_review import run_pr_review

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as exc:
        return str(exc)

    project_path = resolve_project_path(repo, owner=owner)
    if not project_path:
        from app.utils import get_known_projects
        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        return (
            f"❌ Could not find local project matching repo '{repo}'.\n"
            f"Known projects: {known}"
        )

    if send:
        send(f"\U0001f504 Starting PR review pipeline for #{pr_number} ({owner}/{repo})...")

    try:
        success, summary = run_pr_review(
            owner, repo, pr_number, project_path,
            skill_dir=Path(__file__).parent,
        )
        if success:
            if send:
                send(f"✅ PR #{pr_number} updated.\n\n{summary[:400]}")
            return None
        else:
            return f"❌ PR #{pr_number} review failed: {summary[:400]}"
    except Exception as exc:
        return f"⚠️ PR review error: {str(exc)[:300]}"


def _handle_gogs_pr(owner: str, repo: str, pr_number: str, send):
    """Review pipeline for a Gogs PR."""
    from app.utils import resolve_project_path
    from app.gogs_pr_review import run_pr_review_gogs

    project_path = resolve_project_path(repo, owner=owner)
    if not project_path:
        from app.utils import get_known_projects
        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        return (
            f"❌ Could not find local project matching repo '{repo}'.\n"
            f"Known projects: {known}"
        )

    if send:
        send(f"\U0001f504 Starting Gogs PR review pipeline for #{pr_number} ({owner}/{repo})...")

    try:
        success, summary = run_pr_review_gogs(
            owner, repo, pr_number, project_path,
            skill_dir=Path(__file__).parent,
        )
        if success:
            if send:
                send(f"✅ Gogs PR #{pr_number} updated.\n\n{summary[:400]}")
            return None
        else:
            return f"❌ Gogs PR #{pr_number} review failed: {summary[:400]}"
    except Exception as exc:
        return f"⚠️ Gogs PR review error: {str(exc)[:300]}"
