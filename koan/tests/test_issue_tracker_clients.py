"""Tests for the issue tracker client abstraction (GitHub + Jira).

These cover the provider-neutral interface every skill relies on:
read description/comments, add a comment, create issues, and search for an
existing plan issue — without the caller knowing which backend is used.
"""

import json
from unittest.mock import patch

from app.issue_tracker import client_for_project, client_for_url
from app.issue_tracker.base import IssueTracker
from app.issue_tracker.github import GitHubIssueTracker
from app.issue_tracker.jira import JiraIssueTracker


_FACADE = "app.issue_tracker"
_GH = "app.issue_tracker.github"
_JIRA = "app.jira_notifications"


# ---------------------------------------------------------------------------
# Interface conformance
# ---------------------------------------------------------------------------

class TestInterfaceConformance:
    def test_both_clients_are_issue_trackers(self):
        assert isinstance(GitHubIssueTracker(repo="o/r"), IssueTracker)
        assert isinstance(JiraIssueTracker(project_key="PROJ"), IssueTracker)

    def test_provider_identifiers(self):
        assert GitHubIssueTracker().provider == "github"
        assert JiraIssueTracker().provider == "jira"

    def test_label_support_differs(self):
        assert GitHubIssueTracker().supports_labels is True
        assert JiraIssueTracker().supports_labels is False


# ---------------------------------------------------------------------------
# Public factory helpers
# ---------------------------------------------------------------------------

class TestIssueTrackerFactories:
    def test_client_for_project_builds_jira_client_from_tracker_config(self):
        tracker = {
            "provider": "jira",
            "repo": "acme/app",
            "jira_project": "PROJ",
            "jira_issue_type": "Bug",
            "default_branch": "release/1",
        }

        with patch(f"{_FACADE}.get_tracker_for_project", return_value=tracker):
            client = client_for_project(
                "myapp",
                "/tmp/myapp",
                legacy_config={"jira": {"projects": {"PROJ": "myapp"}}},
            )

        assert isinstance(client, JiraIssueTracker)
        assert client.project_name == "myapp"
        assert client.project_key == "PROJ"
        assert client.issue_type == "Bug"
        assert client.default_branch == "release/1"
        assert client.repo == "acme/app"

    def test_client_for_project_builds_github_client_with_resolved_repo(self):
        tracker = {"provider": "github", "repo": "", "default_branch": "main"}

        with patch(f"{_FACADE}.get_tracker_for_project", return_value=tracker), \
             patch(f"{_FACADE}.resolve_code_repository", return_value="acme/app"):
            client = client_for_project("myapp", "/tmp/myapp")

        assert isinstance(client, GitHubIssueTracker)
        assert client.project_name == "myapp"
        assert client.project_path == "/tmp/myapp"
        assert client.repo == "acme/app"
        assert client.default_branch == "main"

    def test_client_for_url_builds_jira_client_from_url_and_project_mapping(self):
        tracker = {
            "provider": "jira",
            "repo": "acme/app",
            "jira_project": "PROJ",
            "jira_issue_type": "Story",
            "default_branch": "main",
        }

        with patch(f"{_FACADE}.find_project_for_jira_key", return_value="myapp"), \
             patch(f"{_FACADE}.get_tracker_for_project", return_value=tracker):
            client = client_for_url("https://org.atlassian.net/browse/PROJ-42")

        assert isinstance(client, JiraIssueTracker)
        assert client.project_name == "myapp"
        assert client.project_key == "PROJ"
        assert client.issue_type == "Story"
        assert client.default_branch == "main"
        assert client.repo == "acme/app"

    def test_client_for_url_resolves_github_project_context_when_possible(self):
        with patch("app.utils.resolve_project_path", return_value="/tmp/myapp"), \
             patch("app.utils.project_name_for_path", return_value="myapp"):
            client = client_for_url("https://github.com/acme/app/issues/42")

        assert isinstance(client, GitHubIssueTracker)
        assert client.project_name == "myapp"
        assert client.project_path == "/tmp/myapp"
        assert client.repo == "acme/app"


# ---------------------------------------------------------------------------
# GitHubIssueTracker
# ---------------------------------------------------------------------------

class TestGitHubIssueTracker:
    def test_is_configured_with_explicit_repo(self):
        assert GitHubIssueTracker(repo="owner/repo").is_configured() is True

    def test_is_configured_false_when_unresolvable(self):
        with patch(f"{_GH}.resolve_code_repository", return_value=""):
            assert GitHubIssueTracker(project_name="x").is_configured() is False

    def test_fetch_issue_returns_normalized_content(self):
        with patch(f"{_GH}.fetch_issue_with_comments",
                   return_value=("Title", "Body", [{"author": "a", "body": "c"}])), \
             patch(f"{_GH}.fetch_issue_state", return_value="open"):
            content = GitHubIssueTracker(repo="o/r").fetch_issue(
                "https://github.com/o/r/issues/42",
            )
        assert content.ref.provider == "github"
        assert content.ref.key == "42"
        assert content.ref.label == "#42"
        assert content.title == "Title"
        assert content.body == "Body"
        assert content.state == "open"
        assert content.comments[0]["author"] == "a"

    def test_add_comment_posts_via_api(self):
        with patch(f"{_GH}.api") as mock_api, \
             patch(f"{_GH}.sanitize_github_comment", side_effect=lambda b: b):
            ok = GitHubIssueTracker(repo="o/r").add_comment(
                "https://github.com/o/r/issues/42", "hello",
            )
        assert ok is True
        endpoint = mock_api.call_args[0][0]
        assert endpoint == "repos/o/r/issues/42/comments"

    def test_create_issue_delegates_to_issue_create(self):
        with patch("app.github.issue_create",
                   return_value="https://github.com/o/r/issues/7") as mock_create:
            url = GitHubIssueTracker(repo="o/r").create_issue(
                "T", "B", labels=["plan"],
            )
        assert url == "https://github.com/o/r/issues/7"
        assert mock_create.call_args[1]["labels"] == ["plan"]
        assert mock_create.call_args[1]["repo"] == "o/r"

    def test_find_existing_plan_issue_returns_ref(self):
        results = json.dumps([
            {"number": 42, "title": "Add dark mode",
             "html_url": "https://github.com/o/r/issues/42"},
        ])
        with patch(f"{_GH}.api", return_value=results):
            ref = GitHubIssueTracker(repo="o/r").find_existing_plan_issue(
                "dark mode feature",
            )
        assert ref is not None
        assert ref.key == "42"
        assert ref.provider == "github"

    def test_find_existing_plan_issue_none_without_repo(self):
        with patch(f"{_GH}.resolve_code_repository", return_value=""):
            ref = GitHubIssueTracker(project_name="x").find_existing_plan_issue(
                "idea",
            )
        assert ref is None

    def test_find_existing_plan_issue_handles_api_error(self):
        with patch(f"{_GH}.api", side_effect=RuntimeError("boom")):
            ref = GitHubIssueTracker(repo="o/r").find_existing_plan_issue("idea")
        assert ref is None


# ---------------------------------------------------------------------------
# JiraIssueTracker
# ---------------------------------------------------------------------------

class TestJiraIssueTracker:
    def test_is_configured_requires_project_key(self):
        assert JiraIssueTracker(project_key="PROJ").is_configured() is True
        assert JiraIssueTracker(project_key="").is_configured() is False

    def test_fetch_issue_returns_normalized_content(self):
        with patch(f"{_JIRA}.fetch_jira_issue",
                   return_value=("Title", "Body", [{"author": "a", "body": "c"}])):
            content = JiraIssueTracker(project_key="PROJ").fetch_issue(
                "https://org.atlassian.net/browse/PROJ-42",
            )
        assert content.ref.provider == "jira"
        assert content.ref.key == "PROJ-42"
        assert content.ref.label == "PROJ-42"
        assert content.title == "Title"

    def test_add_comment_delegates_to_jira(self):
        with patch(f"{_JIRA}.jira_add_comment", return_value=True) as mock_add:
            ok = JiraIssueTracker(project_key="PROJ").add_comment(
                "https://org.atlassian.net/browse/PROJ-42", "hi",
            )
        assert ok is True
        assert mock_add.call_args[0][0] == "PROJ-42"

    def test_create_issue_uses_project_key_and_type(self):
        with patch(f"{_JIRA}.jira_create_issue",
                   return_value="https://org.atlassian.net/browse/PROJ-9") as mock_create:
            url = JiraIssueTracker(
                project_key="PROJ", issue_type="Bug",
            ).create_issue("T", "B", labels=["ignored"])
        assert url.endswith("PROJ-9")
        assert mock_create.call_args[0][0] == "PROJ"
        assert mock_create.call_args[1]["issue_type"] == "Bug"

    def test_find_existing_plan_issue_returns_ref(self):
        with patch(f"{_JIRA}.jira_search_issues", return_value=[
            {"key": "PROJ-3", "title": "Caching",
             "url": "https://org.atlassian.net/browse/PROJ-3"},
        ]):
            ref = JiraIssueTracker(project_key="PROJ").find_existing_plan_issue(
                "improve caching",
            )
        assert ref is not None
        assert ref.key == "PROJ-3"
        assert ref.provider == "jira"

    def test_find_existing_plan_issue_none_without_key(self):
        ref = JiraIssueTracker(project_key="").find_existing_plan_issue("idea")
        assert ref is None
