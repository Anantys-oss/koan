"""Tests for PR-review issue tracker enrichment."""

import json
import subprocess
from unittest.mock import patch

from app.issue_tracker.enrichment import (
    MAX_EXCERPT_CHARS,
    MAX_TOTAL_CHARS,
    fetch_github_issues,
    fetch_issue_context,
    fetch_jira_issues,
    parse_github_issue_refs,
    parse_jira_ticket_ids,
)


class TestParseJiraTicketIds:
    def test_extracts_keys(self):
        assert parse_jira_ticket_ids("Fixes PROJ-42 and ABC-7") == ["PROJ-42", "ABC-7"]

    def test_dedupes_preserving_order(self):
        assert parse_jira_ticket_ids("PROJ-1 then PROJ-1 again PROJ-2") == [
            "PROJ-1",
            "PROJ-2",
        ]

    def test_empty(self):
        assert parse_jira_ticket_ids("") == []
        assert parse_jira_ticket_ids(None) == []

    def test_no_match(self):
        assert parse_jira_ticket_ids("just some prose, no keys") == []


class TestParseGithubIssueRefs:
    def test_extracts_cross_repo_ref(self):
        assert parse_github_issue_refs("see myorg/myrepo#99") == [("myorg", "myrepo", 99)]

    def test_ignores_in_repo_ref(self):
        assert parse_github_issue_refs("relates to #123") == []

    def test_dedupes(self):
        assert parse_github_issue_refs("a/b#1 and a/b#1") == [("a", "b", 1)]

    def test_empty(self):
        assert parse_github_issue_refs("") == []
        assert parse_github_issue_refs(None) == []


class TestFetchJiraIssues:
    def test_formats_summary_and_excerpt(self):
        with patch(
            "app.jira_notifications.fetch_jira_issue",
            return_value=("Fix login timeout", "Users reported timeouts.", []),
        ):
            out = fetch_jira_issues(["PROJ-42"])
        assert "## Issue Tracker Context" in out
        assert "- PROJ-42: Fix login timeout" in out
        assert "> Users reported timeouts." in out

    def test_returns_empty_on_failure(self):
        with patch(
            "app.jira_notifications.fetch_jira_issue",
            side_effect=RuntimeError("404"),
        ):
            assert fetch_jira_issues(["PROJ-42"]) == ""

    def test_excerpt_capped(self):
        long_body = "x" * 5000
        with patch(
            "app.jira_notifications.fetch_jira_issue",
            return_value=("T", long_body, []),
        ):
            out = fetch_jira_issues(["PROJ-1"])
        # excerpt capped at MAX_EXCERPT_CHARS (+ ellipsis), total at MAX_TOTAL
        assert len(out) <= MAX_TOTAL_CHARS + len("\n## Issue Tracker Context\n\n") + 5
        assert "…" in out

    def test_total_capped_across_tickets(self):
        body = "y" * 400
        with patch(
            "app.jira_notifications.fetch_jira_issue",
            return_value=("Title", body, []),
        ):
            out = fetch_jira_issues([f"PROJ-{i}" for i in range(20)])
        # The formatted block body must be capped at MAX_TOTAL_CHARS.
        assert "…" in out

    def test_empty_list(self):
        assert fetch_jira_issues([]) == ""


def _gh_result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestFetchGithubIssues:
    def test_formats_summary(self):
        payload = json.dumps({"title": "Add feature", "body": "Details here."})
        with patch(
            "app.issue_tracker.enrichment.subprocess.run",
            return_value=_gh_result(stdout=payload),
        ):
            out = fetch_github_issues([("o", "r", 5)])
        assert "- o/r#5: Add feature" in out
        assert "> Details here." in out

    def test_returns_empty_on_nonzero(self):
        with patch(
            "app.issue_tracker.enrichment.subprocess.run",
            return_value=_gh_result(returncode=1, stderr="not found"),
        ):
            assert fetch_github_issues([("o", "r", 5)]) == ""

    def test_returns_empty_when_gh_missing(self):
        with patch(
            "app.issue_tracker.enrichment.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            assert fetch_github_issues([("o", "r", 5)]) == ""

    def test_returns_empty_on_timeout(self):
        with patch(
            "app.issue_tracker.enrichment.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5),
        ):
            assert fetch_github_issues([("o", "r", 5)]) == ""

    def test_empty_list(self):
        assert fetch_github_issues([]) == ""


class TestFetchIssueContext:
    def test_empty_body_no_fetch(self):
        assert fetch_issue_context("", project_name="p") == ""

    def test_dispatches_to_jira(self):
        tracker = {"provider": "jira", "jira_project": "PROJ"}
        with patch(
            "app.issue_tracker.config.get_tracker_for_project",
            return_value=tracker,
        ), patch(
            "app.issue_tracker.enrichment.fetch_jira_issues",
            return_value="JIRA_BLOCK",
        ) as jira_mock, patch(
            "app.issue_tracker.enrichment.fetch_github_issues",
        ) as gh_mock:
            out = fetch_issue_context("Fixes PROJ-1", project_name="p")
        assert out == "JIRA_BLOCK"
        jira_mock.assert_called_once_with(["PROJ-1"])
        gh_mock.assert_not_called()

    def test_jira_without_project_mapping_skipped(self):
        tracker = {"provider": "jira", "jira_project": ""}
        with patch(
            "app.issue_tracker.config.get_tracker_for_project",
            return_value=tracker,
        ), patch(
            "app.issue_tracker.enrichment.fetch_jira_issues",
        ) as jira_mock:
            out = fetch_issue_context("Fixes PROJ-1", project_name="p")
        assert out == ""
        jira_mock.assert_not_called()

    def test_dispatches_to_github(self):
        tracker = {"provider": "github"}
        with patch(
            "app.issue_tracker.config.get_tracker_for_project",
            return_value=tracker,
        ), patch(
            "app.issue_tracker.enrichment.fetch_github_issues",
            return_value="GH_BLOCK",
        ) as gh_mock, patch(
            "app.issue_tracker.enrichment.fetch_jira_issues",
        ) as jira_mock:
            out = fetch_issue_context("see o/r#9", project_name="p")
        assert out == "GH_BLOCK"
        gh_mock.assert_called_once_with([("o", "r", 9)])
        jira_mock.assert_not_called()

    def test_never_raises_on_config_error(self):
        with patch(
            "app.issue_tracker.config.get_tracker_for_project",
            side_effect=RuntimeError("boom"),
        ):
            assert fetch_issue_context("PROJ-1", project_name="p") == ""
