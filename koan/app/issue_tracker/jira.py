"""Jira issue tracker client."""

from typing import Optional

from app.github_url_parser import parse_jira_url
from app.issue_tracker.base import IssueTracker
from app.issue_tracker.types import IssueContent, IssueRef


class JiraIssueTracker(IssueTracker):
    """Provider-neutral wrapper around Jira REST helpers."""

    provider = "jira"
    supports_labels = False

    def __init__(
        self,
        project_name: str = "",
        project_key: str = "",
        issue_type: str = "Task",
        default_branch: Optional[str] = None,
        repo: str = "",
    ):
        self.project_name = project_name
        self.project_key = project_key
        self.issue_type = issue_type or "Task"
        self.default_branch = default_branch
        self.repo = repo

    def is_configured(self) -> bool:
        return bool(self.project_key)

    def fetch_issue(self, url: str) -> IssueContent:
        issue_key = parse_jira_url(url)
        from app.jira_notifications import fetch_jira_issue

        title, body, comments = fetch_jira_issue(issue_key)
        ref = IssueRef(
            provider="jira",
            url=url,
            key=issue_key,
            project_name=self.project_name,
            repo=self.repo,
            default_branch=self.default_branch,
            issue_type=self.issue_type,
        )
        return IssueContent(ref=ref, title=title, body=body, comments=comments, state="open")

    def add_comment(self, url: str, body: str) -> bool:
        from app.jira_notifications import jira_add_comment

        return jira_add_comment(parse_jira_url(url), body)

    def create_issue(self, title: str, body: str, labels=None) -> str:
        from app.jira_notifications import jira_create_issue

        return jira_create_issue(
            self.project_key,
            title,
            body,
            issue_type=self.issue_type,
        )

    def find_existing_plan_issue(self, idea: str) -> Optional[IssueRef]:
        if not self.project_key:
            return None
        from app.jira_notifications import jira_search_issues

        hits = jira_search_issues(self.project_key, idea, limit=5)
        if not hits:
            return None
        first = hits[0]
        key = first.get("key", "")
        url = first.get("url", "")
        if not key or not url:
            return None
        return IssueRef(
            provider="jira",
            url=url,
            key=key,
            project_name=self.project_name,
            repo=self.repo,
            default_branch=self.default_branch,
            issue_type=self.issue_type,
        )

