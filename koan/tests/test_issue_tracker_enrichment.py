"""Tests for PR-review issue tracker enrichment."""

import json
import subprocess
from unittest.mock import patch

from app.issue_tracker.enrichment import (
    MAX_EXCERPT_CHARS,
    MAX_REFS,
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
            "app.jira_notifications.fetch_jira_issue_summary",
            return_value=("Fix login timeout", "Users reported timeouts."),
        ):
            out = fetch_jira_issues(["PROJ-42"])
        assert "## Issue Tracker Context" in out
        assert "- PROJ-42: Fix login timeout" in out
        assert "> Users reported timeouts." in out

    def test_uses_lightweight_fetch_with_timeout(self):
        # Enrichment must use the title/body-only fetch (no comment pagination)
        # and pass the module's bounded timeout, not the heavyweight 30s fetch.
        from app.issue_tracker.enrichment import JIRA_TIMEOUT_SECONDS

        with patch(
            "app.jira_notifications.fetch_jira_issue_summary",
            return_value=("T", "b"),
        ) as mock:
            fetch_jira_issues(["PROJ-7"])
        mock.assert_called_once_with("PROJ-7", timeout=JIRA_TIMEOUT_SECONDS)

    def test_returns_empty_on_failure(self):
        with patch(
            "app.jira_notifications.fetch_jira_issue_summary",
            side_effect=RuntimeError("404"),
        ):
            assert fetch_jira_issues(["PROJ-42"]) == ""

    def test_excerpt_capped(self):
        long_body = "x" * 5000
        with patch(
            "app.jira_notifications.fetch_jira_issue_summary",
            return_value=("T", long_body),
        ):
            out = fetch_jira_issues(["PROJ-1"])
        # excerpt capped at MAX_EXCERPT_CHARS (+ ellipsis), total at MAX_TOTAL
        assert len(out) <= MAX_TOTAL_CHARS + len("\n## Issue Tracker Context\n\n") + 5
        assert "…" in out

    def test_total_capped_across_tickets(self):
        body = "y" * 400
        with patch(
            "app.jira_notifications.fetch_jira_issue_summary",
            return_value=("Title", body),
        ):
            out = fetch_jira_issues([f"PROJ-{i}" for i in range(20)])
        # The formatted block body must be capped at MAX_TOTAL_CHARS.
        assert "…" in out

    def test_empty_list(self):
        assert fetch_jira_issues([]) == ""

    def test_fetch_count_capped_at_max_refs(self):
        with patch(
            "app.jira_notifications.fetch_jira_issue_summary",
            return_value=("T", "b"),
        ) as mock:
            fetch_jira_issues([f"PROJ-{i}" for i in range(MAX_REFS + 10)])
        # Only MAX_REFS network round-trips, regardless of how many were parsed.
        assert mock.call_count == MAX_REFS


class TestFetchGithubIssues:
    def test_formats_summary(self):
        payload = json.dumps({"title": "Add feature", "body": "Details here."})
        with patch("app.github.run_gh", return_value=payload):
            out = fetch_github_issues([("o", "r", 5)])
        assert "- o/r#5: Add feature" in out
        assert "> Details here." in out

    def test_returns_empty_on_gh_error(self):
        # run_gh raises RuntimeError on non-zero exit (and SSOAuthRequired,
        # its subclass); best-effort enrichment swallows it.
        with patch("app.github.run_gh", side_effect=RuntimeError("gh failed: not found")):
            assert fetch_github_issues([("o", "r", 5)]) == ""

    def test_returns_empty_when_gh_missing(self):
        with patch("app.github.run_gh", side_effect=FileNotFoundError()):
            assert fetch_github_issues([("o", "r", 5)]) == ""

    def test_returns_empty_on_timeout(self):
        with patch(
            "app.github.run_gh",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5),
        ):
            assert fetch_github_issues([("o", "r", 5)]) == ""

    def test_empty_list(self):
        assert fetch_github_issues([]) == ""

    def test_fetch_count_capped_at_max_refs(self):
        payload = json.dumps({"title": "T", "body": "b"})
        with patch("app.github.run_gh", return_value=payload) as mock:
            fetch_github_issues([("o", "r", i) for i in range(MAX_REFS + 10)])
        # Only MAX_REFS gh round-trips happen, regardless of how many refs parse.
        assert mock.call_count == MAX_REFS


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
        # Fetched tracker text is third-party data; it must be fenced.
        assert "JIRA_BLOCK" in out
        assert "BEGIN EXTERNAL DATA (tracker issue context)" in out
        assert "END EXTERNAL DATA (tracker issue context)" in out
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
        assert "GH_BLOCK" in out
        assert "BEGIN EXTERNAL DATA (tracker issue context)" in out
        gh_mock.assert_called_once_with([("o", "r", 9)])
        jira_mock.assert_not_called()

    def test_empty_block_is_not_fenced(self):
        tracker = {"provider": "github"}
        with patch(
            "app.issue_tracker.config.get_tracker_for_project",
            return_value=tracker,
        ), patch(
            "app.issue_tracker.enrichment.fetch_github_issues",
            return_value="",
        ):
            out = fetch_issue_context("see o/r#9", project_name="p")
        assert out == ""

    def test_never_raises_on_config_error(self):
        with patch(
            "app.issue_tracker.config.get_tracker_for_project",
            side_effect=RuntimeError("boom"),
        ):
            assert fetch_issue_context("PROJ-1", project_name="p") == ""
