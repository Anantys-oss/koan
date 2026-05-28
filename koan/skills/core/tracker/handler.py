"""Kōan tracker skill — inspect and configure issue tracker routing."""

import os
import re

from app.issue_tracker.config import (
    DEFAULT_ISSUE_TYPE,
    get_tracker_for_project,
    normalize_github_repo,
    set_project_tracker,
)

_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+$")


def handle(ctx):
    """Handle /tracker command."""
    args = (ctx.args or "").strip()
    if not args or args == "list":
        return _list_trackers()
    if args.startswith("set "):
        return _set_tracker(ctx, args[4:].strip())
    return (
        "Usage:\n"
        "  /tracker\n"
        "  /tracker set <project> github [repo:owner/repo] [branch:main]\n"
        "  /tracker set <project> jira key:PROJ [type:Task] [branch:11.126]"
    )


def _list_trackers() -> str:
    from app.utils import get_known_projects

    projects = get_known_projects()
    if not projects:
        return "No projects configured."

    lines = ["Issue trackers:"]
    for name, _path in projects:
        tracker = get_tracker_for_project(name)
        provider = tracker.get("provider", "github")
        if provider == "jira":
            details = [
                f"jira:{tracker.get('jira_project') or '?'}",
                f"type:{tracker.get('jira_issue_type') or DEFAULT_ISSUE_TYPE}",
            ]
        else:
            repo = tracker.get("repo") or "auto"
            details = [f"github:{repo}"]
        branch = tracker.get("default_branch")
        if branch:
            details.append(f"branch:{branch}")
        lines.append(f"  - {name}: {' '.join(details)}")
    return "\n".join(lines)


def _set_tracker(ctx, args: str) -> str:
    parts = args.split()
    if len(parts) < 2:
        return "Usage: /tracker set <project> github|jira ..."

    project_name, provider = parts[0], parts[1].lower()
    if provider not in ("github", "jira"):
        return "Provider must be 'github' or 'jira'."

    from app.utils import is_known_project

    if not is_known_project(project_name):
        return f"Unknown project: {project_name}. Use /projects to see configured projects."

    tokens = _parse_tokens(parts[2:])
    tracker = {"provider": provider}

    if provider == "github":
        repo = tokens.get("repo", "")
        if repo:
            tracker["repo"] = normalize_github_repo(repo)
    else:
        jira_key = tokens.get("key", "").upper()
        if not jira_key:
            return "Jira tracker requires key:PROJ."
        if not _JIRA_KEY_RE.match(jira_key):
            return f"Invalid Jira project key: {jira_key}"
        tracker["jira_project"] = jira_key
        tracker["jira_issue_type"] = tokens.get("type", DEFAULT_ISSUE_TYPE)

    if tokens.get("branch"):
        tracker["default_branch"] = tokens["branch"]

    try:
        # set_project_tracker writes projects.yaml and invalidates the
        # in-process projects.yaml cache so the next command sees the update.
        set_project_tracker(str(ctx.koan_root), project_name, tracker)
    except (OSError, ValueError) as e:
        return f"Failed to update projects.yaml: {e}"

    os.environ.setdefault("KOAN_ROOT", str(ctx.koan_root))
    return _format_set_result(project_name, tracker)


def _parse_tokens(tokens):
    result = {}
    for token in tokens:
        if ":" not in token:
            continue
        key, value = token.split(":", 1)
        key = key.lower()
        if key in ("repo", "branch", "key", "type"):
            result[key] = value.strip()
    return result


def _format_set_result(project_name: str, tracker: dict) -> str:
    provider = tracker["provider"]
    if provider == "jira":
        msg = (
            f"Tracker set for {project_name}: "
            f"jira key:{tracker['jira_project']} "
            f"type:{tracker.get('jira_issue_type', DEFAULT_ISSUE_TYPE)}"
        )
    else:
        repo = tracker.get("repo", "auto")
        msg = f"Tracker set for {project_name}: github repo:{repo}"
    if tracker.get("default_branch"):
        msg += f" branch:{tracker['default_branch']}"
    return msg

