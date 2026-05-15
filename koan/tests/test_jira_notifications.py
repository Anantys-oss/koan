"""Tests for jira_notifications.py — Jira API client and mention parsing."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.jira_notifications import (
    JiraFetchResult,
    _adf_to_text,
    _extract_comment_text,
    _get_comment_age_hours,
    _load_processed_tracker,
    _save_processed_tracker,
    check_jira_already_processed,
    fetch_jira_mentions,
    mark_jira_comment_processed,
    parse_jira_mention_command,
    resolve_project_from_jira_key,
)


class TestAdfToText:
    def test_plain_text_node(self):
        node = {"type": "text", "text": "hello world"}
        assert _adf_to_text(node) == "hello world"

    def test_doc_with_paragraph(self):
        node = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "@koan-bot plan"}
                    ]
                }
            ]
        }
        assert "@koan-bot plan" in _adf_to_text(node)

    def test_skips_code_blocks(self):
        node = {
            "type": "doc",
            "content": [
                {
                    "type": "codeBlock",
                    "content": [{"type": "text", "text": "@koan-bot plan"}]
                }
            ]
        }
        assert "@koan-bot" not in _adf_to_text(node)

    def test_mention_node(self):
        node = {
            "type": "mention",
            "attrs": {"text": "@koan-bot", "id": "123"}
        }
        assert "@koan-bot" in _adf_to_text(node)

    def test_hard_break(self):
        node = {"type": "hardBreak"}
        assert _adf_to_text(node) == " "

    def test_empty_node(self):
        assert _adf_to_text({}) == ""
        assert _adf_to_text(None) == ""
        assert _adf_to_text([]) == ""

    def test_nested_structure(self):
        node = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Please "},
                        {"type": "mention", "attrs": {"text": "@koan-bot"}},
                        {"type": "text", "text": " plan"},
                    ]
                }
            ]
        }
        text = _adf_to_text(node)
        assert "@koan-bot" in text
        assert "plan" in text


class TestExtractCommentText:
    def test_string_passthrough(self):
        assert _extract_comment_text("hello @koan-bot plan") == "hello @koan-bot plan"

    def test_adf_dict(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "@koan-bot plan"}]
                }
            ]
        }
        result = _extract_comment_text(adf)
        assert "@koan-bot plan" in result

    def test_none_returns_empty(self):
        assert _extract_comment_text(None) == ""


class TestParseJiraMentionCommand:
    def test_basic_command(self):
        result = parse_jira_mention_command("@koan-bot plan", "koan-bot")
        assert result == ("plan", "")

    def test_command_with_context(self):
        result = parse_jira_mention_command("@koan-bot rebase please fix conflicts", "koan-bot")
        assert result is not None
        cmd, ctx = result
        assert cmd == "rebase"
        assert "please fix conflicts" in ctx

    def test_command_with_slash_prefix(self):
        result = parse_jira_mention_command("@koan-bot /plan", "koan-bot")
        assert result is not None
        assert result[0] == "plan"

    def test_case_insensitive_mention(self):
        result = parse_jira_mention_command("@KOAN-BOT plan", "koan-bot")
        assert result is not None
        assert result[0] == "plan"

    def test_no_mention_returns_none(self):
        assert parse_jira_mention_command("just a comment", "koan-bot") is None

    def test_empty_text(self):
        assert parse_jira_mention_command("", "koan-bot") is None

    def test_empty_nickname(self):
        assert parse_jira_mention_command("@koan-bot plan", "") is None

    def test_command_lowercased(self):
        result = parse_jira_mention_command("@koan-bot PLAN", "koan-bot")
        assert result is not None
        assert result[0] == "plan"

    def test_strips_jira_code_block(self):
        text = "{{@koan-bot plan}}\n@koan-bot rebase"
        result = parse_jira_mention_command(text, "koan-bot")
        assert result is not None
        assert result[0] == "rebase"


class TestResolveProjectFromJiraKey:
    def test_basic_mapping(self):
        project_map = {"FOO": "myproject", "BAR": "another"}
        assert resolve_project_from_jira_key("FOO-123", project_map) == "myproject"

    def test_unknown_key_returns_none(self):
        project_map = {"FOO": "myproject"}
        assert resolve_project_from_jira_key("BAR-456", project_map) is None

    def test_case_insensitive_key(self):
        project_map = {"FOO": "myproject"}
        assert resolve_project_from_jira_key("foo-123", project_map) == "myproject"

    def test_invalid_key_no_dash(self):
        project_map = {"FOO": "myproject"}
        assert resolve_project_from_jira_key("FOOBAD", project_map) is None

    def test_empty_key(self):
        project_map = {"FOO": "myproject"}
        assert resolve_project_from_jira_key("", project_map) is None


class TestProcessedTracker:
    def test_load_nonexistent_file(self, tmp_path):
        tracker = tmp_path / ".jira-processed.json"
        result = _load_processed_tracker(tracker)
        assert result == set()

    def test_load_and_save_roundtrip(self, tmp_path):
        tracker = tmp_path / ".jira-processed.json"
        ids = {"comment-1", "comment-2", "comment-3"}
        _save_processed_tracker(tracker, ids)
        loaded = _load_processed_tracker(tracker)
        assert loaded == ids

    def test_load_invalid_json(self, tmp_path):
        tracker = tmp_path / ".jira-processed.json"
        tracker.write_text("not-json")
        result = _load_processed_tracker(tracker)
        assert result == set()

    def test_save_trims_to_5000(self, tmp_path):
        tracker = tmp_path / ".jira-processed.json"
        ids = {str(i) for i in range(6000)}
        _save_processed_tracker(tracker, ids)
        loaded = _load_processed_tracker(tracker)
        assert len(loaded) == 5000


class TestCheckAlreadyProcessed:
    def test_not_processed(self):
        assert check_jira_already_processed("new-id", set()) is False

    def test_in_persistent_set(self):
        assert check_jira_already_processed("known-id", {"known-id"}) is True

    def test_marks_in_memory_after_persistent_hit(self):
        processed_set = {"cached-id"}
        # First call hits persistent set
        assert check_jira_already_processed("cached-id", processed_set) is True
        # Second call hits in-memory set (bounded set)
        assert check_jira_already_processed("cached-id", set()) is True


class TestMarkJiraCommentProcessed:
    def test_adds_to_both_sets(self):
        processed_set = set()
        mark_jira_comment_processed("new-id", processed_set)
        assert "new-id" in processed_set
        assert check_jira_already_processed("new-id", set()) is True


class TestGetCommentAgeHours:
    def test_recent_comment(self):
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        age = _get_comment_age_hours(now_iso)
        assert age is not None
        assert age < 0.1  # Less than 6 minutes

    def test_invalid_timestamp(self):
        assert _get_comment_age_hours("not-a-timestamp") is None

    def test_empty_string(self):
        assert _get_comment_age_hours("") is None


class TestFetchJiraMentions:
    """Tests for the main fetch function using mocked HTTP."""

    def _make_config(self, nickname="koan-bot"):
        return {
            "jira": {
                "enabled": True,
                "base_url": "https://test.atlassian.net",
                "email": "bot@example.com",
                "api_token": "secret",
                "nickname": nickname,
                "max_age_hours": 24,
            }
        }

    def _make_search_response(self, issue_key="FOO-123"):
        return {
            "issues": [{"key": issue_key, "fields": {"summary": "Test issue"}}],
            "total": 1,
        }

    def _make_comments_response(self, comment_id="456", body="@koan-bot plan"):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        return {
            "comments": [
                {
                    "id": comment_id,
                    "body": body,
                    "author": {
                        "emailAddress": "user@example.com",
                        "displayName": "Test User",
                    },
                    "updated": now,
                }
            ],
            "total": 1,
        }

    def test_no_project_map_returns_empty(self):
        config = self._make_config()
        result = fetch_jira_mentions(config, {})
        assert isinstance(result, JiraFetchResult)
        assert result.mentions == []

    def test_missing_config_returns_empty(self):
        result = fetch_jira_mentions({}, {"FOO": "myproject"})
        assert result.mentions == []

    @patch("app.jira_notifications._jira_get")
    @patch("app.jira_notifications._jira_post")
    def test_finds_mention_in_comment(self, mock_post, mock_get):
        """Single @mention comment is returned as a mention dict."""
        # POST for JQL search; GET for issue comments
        mock_post.return_value = self._make_search_response("FOO-123")
        mock_get.return_value = self._make_comments_response("456", "@koan-bot plan")

        config = self._make_config()
        project_map = {"FOO": "myproject"}
        result = fetch_jira_mentions(config, project_map)

        assert len(result.mentions) == 1
        mention = result.mentions[0]
        assert mention["issue_key"] == "FOO-123"
        assert mention["project_name"] == "myproject"
        assert mention["comment_id"] == "456"
        assert mention["author_email"] == "user@example.com"

    @patch("app.jira_notifications._jira_get")
    @patch("app.jira_notifications._jira_post")
    def test_skips_comment_without_mention(self, mock_post, mock_get):
        """Comments without @bot are not returned."""
        mock_post.return_value = self._make_search_response("FOO-123")
        mock_get.return_value = self._make_comments_response("456", "just a regular comment")

        config = self._make_config()
        result = fetch_jira_mentions(config, {"FOO": "myproject"})
        assert result.mentions == []

    @patch("app.jira_notifications._jira_post")
    def test_skips_unknown_project(self, mock_post):
        """Issues with no project mapping are skipped."""
        mock_post.return_value = self._make_search_response("BAR-456")

        config = self._make_config()
        # BAR not in project_map
        result = fetch_jira_mentions(config, {"FOO": "myproject"})
        assert result.mentions == []

    def test_pagination_across_three_pages(self):
        """Pagination: 3 pages of issues are all fetched via nextPageToken."""
        call_count = [0]

        def post_side_effect(base_url, auth_header, path, body=None):
            if "/search" in path:
                call_count[0] += 1
                all_issues = [{"key": f"FOO-{i}", "fields": {}} for i in range(6)]
                # Page 1: items 0-1, page 2: items 2-3, page 3: items 4-5
                page = call_count[0]
                start = (page - 1) * 2
                batch = all_issues[start:start + 2]
                is_last = page >= 3
                result = {"issues": batch, "isLast": is_last}
                if not is_last:
                    result["nextPageToken"] = f"token-page-{page + 1}"
                return result
            return None

        def get_side_effect(base_url, auth_header, path, params=None):
            if "/comment" in path:
                return {"comments": [], "total": 0}
            return None

        config = self._make_config()
        with patch("app.jira_notifications._jira_post", side_effect=post_side_effect), \
             patch("app.jira_notifications._jira_get", side_effect=get_side_effect):
            result = fetch_jira_mentions(config, {"FOO": "myproject"})

        assert isinstance(result, JiraFetchResult)
        assert call_count[0] == 3

    def test_pagination_halts_at_cap(self):
        """_search_issues_with_comments stops paginating once the cap is reached.

        Regression: previously the search paginated through *all* matching
        issues without bound, even though the caller only inspected the first
        N. With max_issues plumbed through, an unbounded result set must not
        cause unbounded API calls.
        """
        # Simulate 1000 available issues across 20 pages of 50. With a cap of
        # 200, pagination should stop after the 4th page (200 issues).
        page_size = 50
        cap = 200
        call_count = [0]

        def post_side_effect(base_url, auth_header, path, body=None):
            if "/search" in path:
                call_count[0] += 1
                page = call_count[0]
                start = (page - 1) * page_size
                batch = [
                    {"key": f"FOO-{i:04}", "fields": {}}
                    for i in range(start, start + page_size)
                ]
                # Server always says "more available"
                return {
                    "issues": batch,
                    "isLast": False,
                    "nextPageToken": f"token-page-{page + 1}",
                }
            return None

        def get_side_effect(base_url, auth_header, path, params=None):
            if "/comment" in path:
                return {"comments": [], "total": 0}
            return None

        config = self._make_config()
        with patch("app.jira_notifications._jira_post", side_effect=post_side_effect), \
             patch("app.jira_notifications._jira_get", side_effect=get_side_effect):
            result = fetch_jira_mentions(config, {"FOO": "myproject"})

        # 4 pages of 50 = 200 issues; pagination must stop there.
        assert call_count[0] == cap // page_size
        assert isinstance(result, JiraFetchResult)

    @patch("app.jira_notifications._jira_get")
    @patch("app.jira_notifications._jira_post")
    def test_api_failure_returns_empty(self, mock_post, mock_get):
        """API failure returns empty result, doesn't raise."""
        mock_post.return_value = None
        mock_get.return_value = None

        config = self._make_config()
        result = fetch_jira_mentions(config, {"FOO": "myproject"})
        assert result.mentions == []

    @patch("app.jira_notifications._get_issue_comments")
    @patch("app.jira_notifications._search_issues_with_comments")
    def test_mention_deep_in_results_is_found(self, mock_search, mock_comments):
        """Regression: a mention on an issue ranked deep in the result set
        (observed at rank 46 of 100 in production) must still be picked up.
        Previously _MAX_ISSUES_PER_CYCLE = 20 silently dropped it.
        """
        # 100 issues; the only one whose comments mention the bot is at index 46
        issues = [{"key": f"FOO-{i:03}", "fields": {"summary": f"i{i}"}} for i in range(100)]
        issues[46] = {"key": "FOO-046", "fields": {"summary": "deep target"}}
        mock_search.return_value = issues

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

        def comments_side_effect(base_url, auth_header, issue_key, since):
            # Only the deep-ranked issue has a body that triggers the mention
            if issue_key == "FOO-046":
                return [{
                    "id": "999",
                    "body": "@koan-bot plan",
                    "author": {"emailAddress": "u@example.com", "displayName": "U"},
                    "updated": now_iso,
                }]
            return []

        mock_comments.side_effect = comments_side_effect

        config = self._make_config()
        result = fetch_jira_mentions(config, {"FOO": "myproject"})

        assert len(result.mentions) == 1
        assert result.mentions[0]["issue_key"] == "FOO-046"
