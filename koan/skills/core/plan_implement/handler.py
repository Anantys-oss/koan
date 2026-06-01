"""Kōan plan+implement combo skill -- queue /plan then /implement for an issue."""

from app.github_url_parser import is_jira_url, parse_github_url
from app.github_skill_helpers import (
    extract_issue_tracker_url,
    format_project_not_found_error,
    format_success_message,
    queue_github_mission,
    resolve_project_for_repo,
)
from app.issue_tracker import resolve_issue_ref


def handle(ctx):
    """Handle /planimplement (aliases /planimp /planimpl /planit /plandoit).

    Usage:
        /planit https://github.com/owner/repo/issues/42

    Queues two missions in order:
    1. /plan <url> — generates a structured plan as a tracker issue
    2. /implement <url> — implements the plan
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /planit <issue-url>\n"
            "Ex: /planit https://github.com/sukria/koan/issues/42\n\n"
            "Queues /plan then /implement — plan insights feed the implementation."
        )

    result = extract_issue_tracker_url(args, url_type="pr-or-issue")
    if not result:
        return (
            "❌ No valid issue tracker URL found.\n"
            "Ex: /planit https://github.com/owner/repo/issues/123"
        )

    issue_url, context = result

    if is_jira_url(issue_url):
        return _handle_jira(ctx, issue_url, context)

    try:
        owner, repo, url_type, number = parse_github_url(issue_url)
    except ValueError as e:
        return f"❌ {e}"

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo, owner=owner)

    type_label = "PR" if url_type == "pull" else "issue"

    plan_ok = queue_github_mission(ctx, "plan", issue_url, project_name, context)
    impl_ok = queue_github_mission(ctx, "implement", issue_url, project_name, context)

    target = format_success_message(type_label, number, owner, repo)
    if plan_ok is False and impl_ok is False:
        return f"⚠️ Both /plan and /implement already queued or running for {target}."
    if plan_ok is False:
        return f"Implement queued for {target} (plan already queued/running)."
    if impl_ok is False:
        return f"Plan queued for {target} (implement already queued/running)."

    return f"\U0001f9e0\U0001f528 Plan + implement combo queued for {target}"


def _handle_jira(ctx, issue_url, context):
    """Handle Jira issue URLs for plan+implement combo."""
    try:
        ref = resolve_issue_ref(issue_url)
    except ValueError as e:
        return f"❌ {e}"

    if not ref.project_name:
        return (
            f"❌ Could not resolve Koan project for Jira issue {ref.key}.\n"
            "Configure projects.yaml issue_tracker.jira_project."
        )

    plan_ok = queue_github_mission(ctx, "plan", issue_url, ref.project_name, context)
    impl_ok = queue_github_mission(ctx, "implement", issue_url, ref.project_name, context)

    label = f"Jira issue {ref.key}"
    if plan_ok is False and impl_ok is False:
        return f"⚠️ Both /plan and /implement already queued or running for {label}."
    if plan_ok is False:
        return f"Implement queued for {label} (plan already queued/running)."
    if impl_ok is False:
        return f"Plan queued for {label} (implement already queued/running)."

    return f"\U0001f9e0\U0001f528 Plan + implement combo queued for {label}"
