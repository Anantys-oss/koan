"""Tests for Jira end-of-mission outcome publishing."""

from unittest.mock import patch


class TestPublishJiraMissionOutcome:
    def test_skips_when_no_jira_url(self):
        from app.jira_outcome_publish import publish_jira_mission_outcome

        with (
            patch("app.jira_outcome_publish.jira_list_comments") as mock_list,
            patch("app.jira_outcome_publish.jira_add_comment") as mock_add,
        ):
            result = publish_jira_mission_outcome(
                mission_title="/fix https://github.com/o/r/issues/1",
                pending_content="Draft PR: https://github.com/o/r/pull/1",
                exit_code=0,
            )

        assert result["published"] == "false"
        assert result["reason"] == "no_jira_url"
        mock_list.assert_not_called()
        mock_add.assert_not_called()

    def test_success_with_pr_posts_comment(self):
        from app.jira_outcome_publish import publish_jira_mission_outcome

        with (
            patch("app.jira_outcome_publish.jira_list_comments", return_value=[]),
            patch("app.jira_outcome_publish.jira_add_comment", return_value=True) as mock_add,
            patch("app.jira_outcome_publish._fetch_pr_details", return_value=("", "")),
        ):
            result = publish_jira_mission_outcome(
                mission_title="/fix https://org.atlassian.net/browse/PROJ-42 branch:main",
                pending_content="Fix complete.\nDraft PR: https://github.com/o/r/pull/123",
                exit_code=0,
                base_branch="main",
            )

        assert result["published"] == "true"
        assert result["outcome"] == "pr_success"
        assert result["pr_url"] == "https://github.com/o/r/pull/123"
        mock_add.assert_called_once()
        body = mock_add.call_args.args[1]
        assert "Pull request: https://github.com/o/r/pull/123" in body
        assert "Mission: /fix" in body

    def test_success_comment_enriched_from_pr_body(self):
        # The publisher fetches the PR body from GitHub so the agent-path
        # comment includes the What/Why summary, matching the skill path.
        from app.jira_outcome_publish import publish_jira_mission_outcome

        pr_body = "## Summary\n\n- Reworked parser\n\n## Why\n\nFixes the crash"
        with (
            patch("app.jira_outcome_publish.jira_list_comments", return_value=[]),
            patch("app.jira_outcome_publish.jira_add_comment", return_value=True) as mock_add,
            patch("app.jira_outcome_publish._fetch_pr_details",
                  return_value=("fix: crash", pr_body)) as mock_fetch,
        ):
            publish_jira_mission_outcome(
                mission_title="/fix https://org.atlassian.net/browse/PROJ-42",
                pending_content="Draft PR: https://github.com/o/r/pull/123",
                exit_code=0,
            )

        mock_fetch.assert_called_once_with("https://github.com/o/r/pull/123")
        body = mock_add.call_args.args[1]
        assert "What changed:" in body
        assert "Reworked parser" in body
        assert "Why: Fixes the crash" in body

    def test_success_with_pr_updates_existing_comment(self):
        from app.jira_outcome_publish import _marker_for, publish_jira_mission_outcome

        marker = _marker_for("PROJ-42", "fix")
        existing = [{"id": "99", "body": f"old\n\n{marker}"}]
        with (
            patch("app.jira_outcome_publish.jira_list_comments", return_value=existing),
            patch("app.jira_outcome_publish.jira_edit_comment", return_value=True) as mock_edit,
            patch("app.jira_outcome_publish.jira_add_comment", return_value=True) as mock_add,
            patch("app.jira_outcome_publish._fetch_pr_details", return_value=("", "")),
        ):
            result = publish_jira_mission_outcome(
                mission_title="/fix https://org.atlassian.net/browse/PROJ-42",
                pending_content="Draft PR: https://github.com/o/r/pull/123",
                exit_code=0,
            )

        assert result["published"] == "true"
        assert result["reason"] == "updated"
        mock_edit.assert_called_once()
        mock_add.assert_not_called()

    def test_failure_posts_comment_without_pr(self):
        from app.jira_outcome_publish import publish_jira_mission_outcome

        with (
            patch("app.jira_outcome_publish.jira_list_comments", return_value=[]),
            patch("app.jira_outcome_publish.jira_add_comment", return_value=True) as mock_add,
        ):
            result = publish_jira_mission_outcome(
                mission_title="/implement https://org.atlassian.net/browse/PROJ-99",
                pending_content="Implementation failed: test suite crashed",
                exit_code=1,
            )

        assert result["published"] == "true"
        assert result["outcome"] == "failure"
        body = mock_add.call_args.args[1]
        assert "Pull request creation failed" in body
        assert "Mission: /implement" in body
