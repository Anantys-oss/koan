"""Handler for the /check_need skill.

Queues a mission to analyze whether a PR or issue is still needed
given the current state of the repository. Posts a detailed comment
to the forge (GitHub or Gogs) with the analysis.
"""

import re
from typing import Optional


_PR_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
_ISSUE_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)"
)


def handle(ctx) -> Optional[str]:
    """Handle /check_need — queue a relevance analysis for a PR or issue."""
    args = ctx.args.strip() if ctx.args else ""

    if not args:
        return (
            "Usage: /check_need <pr-or-issue-url>\n"
            "Ex: /check_need https://github.com/owner/repo/pull/42\n"
            "Ex: /need https://github.com/owner/repo/issues/99\n"
            "Ex: /need https://git.example.com/owner/repo/pulls/42\n\n"
            "Analyzes whether the PR changes or issue request is still "
            "relevant given the current state of the repo, then posts "
            "a detailed comment."
        )

    # ── Gogs PR or issue ─────────────────────────────────────────────
    from app.github_skill_helpers import try_extract_gogs_pr_or_issue
    gogs = try_extract_gogs_pr_or_issue(args)
    if gogs:
        owner, repo, number, url, type_label = gogs
        label = f"Gogs {type_label} #{number} ({owner}/{repo})"
        project_name = _resolve_project_name(repo, owner)
        from app.utils import insert_pending_mission
        mission_entry = f"- [project:{project_name}] /check_need {url}"
        missions_path = ctx.instance_dir / "missions.md"
        insert_pending_mission(missions_path, mission_entry)
        return f"\U0001f50e Relevance check queued for {label}"

    # ── GitHub PR or issue ────────────────────────────────────────────
    pr_match = _PR_URL_RE.search(args)
    issue_match = _ISSUE_URL_RE.search(args)

    if not pr_match and not issue_match:
        return (
            "❌ No valid PR or issue URL found.\n"
            "Expected: https://github.com/owner/repo/pull/123\n"
            "      or: https://github.com/owner/repo/issues/123"
        )

    if pr_match:
        owner = pr_match.group("owner")
        repo = pr_match.group("repo")
        number = pr_match.group("number")
        url = f"https://github.com/{owner}/{repo}/pull/{number}"
        label = f"PR #{number} ({owner}/{repo})"
    else:
        owner = issue_match.group("owner")
        repo = issue_match.group("repo")
        number = issue_match.group("number")
        url = f"https://github.com/{owner}/{repo}/issues/{number}"
        label = f"issue #{number} ({owner}/{repo})"

    project_name = _resolve_project_name(repo, owner)

    from app.utils import insert_pending_mission

    mission_entry = f"- [project:{project_name}] /check_need {url}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"\U0001f50e Relevance check queued for {label}"


def _resolve_project_name(repo, owner=None):
    """Resolve a repo name to a known project name."""
    from app.utils import project_name_for_path, resolve_project_path

    project_path = resolve_project_path(repo, owner=owner)
    if project_path:
        return project_name_for_path(project_path)
    return repo
