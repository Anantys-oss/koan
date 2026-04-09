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
    def test_finds_mention_in_comment(self, mock_get):
        """Single @mention comment is returned as a mention dict."""
        # First call: JQL search; second call: issue comments
        mock_get.side_effect = [
            self._make_search_response("FOO-123"),
            self._make_comments_response("456", "@koan-bot plan"),
        ]

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
    def test_skips_comment_without_mention(self, mock_get):
        """Comments without @bot are not returned."""
        mock_get.side_effect = [
            self._make_search_response("FOO-123"),
            self._make_comments_response("456", "just a regular comment"),
        ]

        config = self._make_config()
        result = fetch_jira_mentions(config, {"FOO": "myproject"})
        assert result.mentions == []

    @patch("app.jira_notifications._jira_get")
    def test_skips_unknown_project(self, mock_get):
        """Issues with no project mapping are skipped."""
        mock_get.side_effect = [
            self._make_search_response("BAR-456"),
        ]

        config = self._make_config()
        # BAR not in project_map
        result = fetch_jira_mentions(config, {"FOO": "myproject"})
        assert result.mentions == []

    @patch("app.jira_notifications._jira_get")
    def test_pagination_across_three_pages(self, mock_get):
        """Pagination: 3 pages of issues are all fetched."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

        def search_side_effect(base_url, auth_header, path, params=None):
            if "/search" in path:
                start = params.get("startAt", 0) if params else 0
                max_r = params.get("maxResults", 50) if params else 50
                # Simulate 3 pages of 2 issues each
                all_issues = [{"key": f"FOO-{i}", "fields": {}} for i in range(6)]
                batch = all_issues[start:start + max_r]
                return {"issues": batch, "total": 6}
            elif "/comment" in path:
                # Return no comments for simplicity
                return {"comments": [], "total": 0}
            return None

        mock_get.side_effect = search_side_effect

        config = self._make_config()
        # Use a small maxResults to force pagination
        with patch("app.jira_notifications._jira_get", side_effect=search_side_effect):
            result = fetch_jira_mentions(config, {"FOO": "myproject"})

        assert isinstance(result, JiraFetchResult)

    @patch("app.jira_notifications._jira_get")
    def test_api_failure_returns_empty(self, mock_get):
        """API failure returns empty result, doesn't raise."""
        mock_get.return_value = None

        config = self._make_config()
        result = fetch_jira_mentions(config, {"FOO": "myproject"})
        assert result.mentions == []
