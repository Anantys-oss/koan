"""Provider-neutral issue tracker helpers."""

import os
from pathlib import Path
from typing import Optional

from app.github_url_parser import is_jira_url, parse_github_url, parse_jira_url
from app.issue_tracker.base import IssueTracker
from app.issue_tracker.config import (
    DEFAULT_ISSUE_TYPE,
    find_project_for_jira_key,
    get_tracker_for_project,
    resolve_code_repository,
)
from app.issue_tracker.github import GitHubIssueTracker
from app.issue_tracker.jira import JiraIssueTracker
from app.issue_tracker.types import IssueContent, IssueRef


class UnresolvedJiraProjectError(ValueError):
    """Raised when a Jira issue key is not mapped to a Koan project."""


def _koan_root() -> str:
    return os.environ.get("KOAN_ROOT", "")


def _ignore_legacy_config(_legacy_config: Optional[dict]) -> None:
    """Accept deprecated legacy config arguments without changing behavior."""


def project_name_for_path(project_path: str) -> str:
    """Resolve a Koan project name from a local path."""
    try:
        from app.utils import project_name_for_path as _project_name_for_path

        return _project_name_for_path(project_path)
    except (ImportError, OSError, ValueError):
        return Path(project_path).name if project_path else ""


def _tracker_for_project(project_name: str) -> dict:
    return get_tracker_for_project(project_name, koan_root=_koan_root())


def _client_from_tracker_config(
    project_name: str,
    project_path: str,
    tracker: dict,
) -> IssueTracker:
    provider = tracker.get("provider", "github")
    default_branch = tracker.get("default_branch") or None
    repo = tracker.get("repo") or resolve_code_repository(project_name, project_path)

    if provider == "jira":
        return JiraIssueTracker(
            project_name=project_name,
            project_key=tracker.get("jira_project", ""),
            issue_type=tracker.get("jira_issue_type", DEFAULT_ISSUE_TYPE),
            default_branch=default_branch,
            repo=repo,
        )

    return GitHubIssueTracker(
        project_name=project_name,
        project_path=project_path,
        repo=repo,
        default_branch=default_branch,
    )


def _resolve_github_project_context(
    owner: str,
    repo: str,
    project_name: str,
    project_path: str,
) -> tuple[str, str]:
    if project_name:
        return project_name, project_path

    try:
        from app.utils import project_name_for_path as _project_name_for_path
        from app.utils import resolve_project_path

        resolved_path = resolve_project_path(repo, owner=owner)
        if resolved_path:
            return _project_name_for_path(resolved_path), project_path or resolved_path
    except (ImportError, OSError, ValueError):
        pass

    return project_name, project_path


def _github_client_for_url(
    url: str,
    project_name: str,
    project_path: str,
) -> GitHubIssueTracker:
    owner, repo, _url_type, _number = parse_github_url(url)
    project_name, project_path = _resolve_github_project_context(
        owner, repo, project_name, project_path,
    )
    return GitHubIssueTracker(
        project_name=project_name,
        project_path=project_path,
        repo=f"{owner}/{repo}",
    )


def _jira_client_for_url(
    url: str,
    project_name: str,
    project_path: str,
) -> JiraIssueTracker:
    issue_key = parse_jira_url(url)
    resolved_project = project_name or find_project_for_jira_key(
        issue_key,
        koan_root=_koan_root(),
    )
    if not resolved_project:
        project_key = issue_key.split("-", 1)[0].upper()
        raise UnresolvedJiraProjectError(
            "Unmapped Jira issue "
            f"'{issue_key}': no Koan project was resolved. "
            "Add this mapping in projects.yaml under "
            "projects.<name>.issue_tracker with "
            "provider: jira and jira_project: "
            f"{project_key}."
        )
    tracker = _tracker_for_project(resolved_project)
    repo = tracker.get("repo") or resolve_code_repository(
        resolved_project, project_path,
    )
    return JiraIssueTracker(
        project_name=resolved_project,
        project_key=issue_key.split("-", 1)[0].upper(),
        issue_type=tracker.get("jira_issue_type", DEFAULT_ISSUE_TYPE),
        default_branch=tracker.get("default_branch") or None,
        repo=repo,
    )


def client_for_project(
    project_name: str,
    project_path: str = "",
    legacy_config: Optional[dict] = None,
) -> IssueTracker:
    """Return an issue tracker client for a Koan project.

    Prefer the service functions in this module for skill code. This factory
    stays public for lower-level tests and tooling.
    """
    _ignore_legacy_config(legacy_config)
    return _client_from_tracker_config(
        project_name,
        project_path,
        _tracker_for_project(project_name),
    )


def client_for_url(
    url: str,
    project_name: str = "",
    project_path: str = "",
    legacy_config: Optional[dict] = None,
) -> IssueTracker:
    """Return the client that owns a tracker URL."""
    _ignore_legacy_config(legacy_config)
    if is_jira_url(url):
        return _jira_client_for_url(url, project_name, project_path)
    return _github_client_for_url(url, project_name, project_path)


def resolve_issue_ref(
    url: str,
    project_name: str = "",
    project_path: str = "",
) -> IssueRef:
    """Parse a tracker URL into an IssueRef without fetching body content."""
    if is_jira_url(url):
        client = client_for_url(url, project_name=project_name, project_path=project_path)
        issue_key = parse_jira_url(url)
        return IssueRef(
            provider="jira",
            url=url,
            key=issue_key,
            project_name=client.project_name,
            repo=client.repo,
            default_branch=client.default_branch,
            issue_type=client.issue_type,
        )

    owner, repo, url_type, number = parse_github_url(url)
    return IssueRef(
        provider="github",
        url=url,
        key=number,
        project_name=project_name,
        repo=f"{owner}/{repo}",
        url_type=url_type,
    )


def fetch_issue(url: str, project_name: str = "", project_path: str = "") -> IssueContent:
    """Fetch issue title, description/body, comments, and reference metadata."""
    return client_for_url(url, project_name=project_name, project_path=project_path).fetch_issue(url)


def add_comment(
    url: str,
    body: str,
    project_name: str = "",
    project_path: str = "",
) -> bool:
    """Add a comment to a GitHub or Jira issue URL."""
    return client_for_url(url, project_name=project_name, project_path=project_path).add_comment(url, body)


def create_issue(
    project_name: str,
    project_path: str,
    title: str,
    body: str,
    labels=None,
) -> str:
    """Create an issue in the project's configured tracker."""
    return client_for_project(project_name, project_path).create_issue(title, body, labels=labels)


def find_existing_plan_issue(
    project_name: str,
    project_path: str,
    idea: str,
) -> Optional[IssueRef]:
    """Find an existing open plan issue in the project's configured tracker."""
    return client_for_project(project_name, project_path).find_existing_plan_issue(idea)


def tracker_is_configured(project_name: str, project_path: str = "") -> bool:
    """Return whether the configured tracker can read/create issues."""
    return client_for_project(project_name, project_path).is_configured()


def tracker_supports_labels(project_name: str, project_path: str = "") -> bool:
    """Return whether the configured tracker supports labels."""
    return client_for_project(project_name, project_path).supports_labels


def tracker_provider(project_name: str, project_path: str = "") -> str:
    """Return the configured provider name for a project."""
    return client_for_project(project_name, project_path).provider
