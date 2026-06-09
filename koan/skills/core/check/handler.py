"""Koan /check skill -- queue a check mission for a PR or issue."""

from app.github_url_parser import parse_github_url
from app.github_skill_helpers import (
    extract_github_url,
    resolve_project_for_repo,
    try_extract_gogs_pr_or_issue,
)


def handle(ctx):
    """Handle /check command -- queue a check mission for a PR or issue.

    Usage:
        /check <github-pr-or-issue-url>
        /check <gogs-pr-or-issue-url>

    Queues a mission that inspects the PR/issue and takes action
    (rebase, review, plan) as needed.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /check <pr-or-issue-url>\n"
            "Ex: /check https://github.com/sukria/koan/pull/85\n"
            "Ex: /check https://git.example.com/owner/repo/pulls/42\n\n"
            "Queues a mission that checks rebase/review status for PRs, "
            "or triggers /plan for updated issues."
        )

    # ── Gogs PR or issue ─────────────────────────────────────────────
    gogs = try_extract_gogs_pr_or_issue(args)
    if gogs:
        owner, repo, number, url, type_label = gogs
        label = f"Gogs {type_label} #{number} ({owner}/{repo})"
        project_path, project_name = resolve_project_for_repo(repo, owner=owner)
        if not project_name:
            project_name = repo
        from app.utils import insert_pending_mission
        mission_entry = f"- [project:{project_name}] /check {url}"
        missions_path = ctx.instance_dir / "missions.md"
        insert_pending_mission(missions_path, mission_entry)
        return f"\U0001f50d Check queued for {label}"

    # ── GitHub PR or issue ────────────────────────────────────────────
    result = extract_github_url(args, url_type="pr-or-issue")
    if not result:
        return (
            "❌ No valid PR or issue URL found.\n"
            "Expected: https://github.com/owner/repo/pull/123\n"
            "      or: https://github.com/owner/repo/issues/123"
        )

    url, _context = result

    try:
        owner, repo, url_type, number = parse_github_url(url)
    except ValueError as e:
        return f"❌ {e}"

    type_label = "PR" if url_type == "pull" else "issue"
    label = f"{type_label} #{number} ({owner}/{repo})"

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_name:
        project_name = repo

    from app.utils import insert_pending_mission

    mission_entry = f"- [project:{project_name}] /check {url}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"\U0001f50d Check queued for {label}"
