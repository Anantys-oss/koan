"""Tests for jira_command_handler.py — command parsing and mission creation."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.jira_command_handler import (
    _check_user_permission,
    _extract_repo_override,
    build_jira_mission,
    process_jira_mention,
    validate_command,
)


@pytest.fixture
def skill_registry():
    """Minimal SkillRegistry with a github_enabled 'plan' skill."""
    registry = MagicMock()

    # plan skill — github_enabled, context_aware
    plan_skill = MagicMock()
    plan_skill.github_enabled = True
    plan_skill.github_context_aware = True

    # noop skill — NOT github_enabled
    noop_skill = MagicMock()
    noop_skill.github_enabled = False

    def find_by_command(cmd):
        if cmd == "plan":
            return plan_skill
        if cmd == "rebase":
            rebase = MagicMock()
            rebase.github_enabled = True
            rebase.github_context_aware = False
            return rebase
        if cmd == "noop":
            return noop_skill
        return None

    registry.find_by_command.side_effect = find_by_command
    return registry


@pytest.fixture
def basic_config():
    return {
        "jira": {
            "enabled": True,
            "base_url": "https://test.atlassian.net",
            "email": "bot@example.com",
            "api_token": "secret",
            "nickname": "koan-bot",
            "authorized_users": ["*"],
            "max_age_hours": 24,
        }
    }


_mention_counter = 0


@pytest.fixture
def mention():
    """Generate a mention dict with a unique comment_id per test.

    Uses a module-level counter to avoid cross-test contamination in the
    in-memory _processed_comments BoundedSet.
    """
    global _mention_counter
    _mention_counter += 1
    cid = str(100000 + _mention_counter)

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    return {
        "comment_id": cid,
        "issue_key": "FOO-123",
        "project_name": "myproject",
        "author_email": "user@example.com",
        "author_name": "Test User",
        "body_text": "@koan-bot plan",
        "updated": now,
        "issue_url": "https://test.atlassian.net/browse/FOO-123",
        "comment_url": f"https://test.atlassian.net/browse/FOO-123?focusedCommentId={cid}",
    }


class TestExtractRepoOverride:
    def test_no_override(self):
        name, ctx = _extract_repo_override("some context text")
        assert name is None
        assert ctx == "some context text"

    def test_basic_override(self):
        name, ctx = _extract_repo_override("repo:myproject some context")
        assert name == "myproject"
        assert "repo:" not in ctx
        assert "some context" in ctx

    def test_override_at_end(self):
        name, ctx = _extract_repo_override("context text repo:aproject")
        assert name == "aproject"
        assert "repo:" not in ctx

    def test_override_only(self):
        name, ctx = _extract_repo_override("repo:onlyproject")
        assert name == "onlyproject"
        assert ctx.strip() == ""

    def test_case_insensitive(self):
        name, ctx = _extract_repo_override("REPO:myproject")
        assert name == "myproject"


class TestCheckUserPermission:
    def test_wildcard_allows_all(self):
        assert _check_user_permission("anyone@example.com", ["*"]) is True

    def test_email_in_list(self):
        assert _check_user_permission(
            "alice@example.com", ["alice@example.com", "bob@example.com"]
        ) is True

    def test_email_not_in_list(self):
        assert _check_user_permission(
            "eve@example.com", ["alice@example.com", "bob@example.com"]
        ) is False

    def test_empty_list_denies(self):
        assert _check_user_permission("alice@example.com", []) is False


class TestValidateCommand:
    def test_github_enabled_command(self, skill_registry):
        skill = validate_command("plan", skill_registry)
        assert skill is not None
        assert skill.github_enabled is True

    def test_unknown_command_returns_none(self, skill_registry):
        assert validate_command("unknown_cmd", skill_registry) is None

    def test_non_github_enabled_returns_none(self, skill_registry):
        assert validate_command("noop", skill_registry) is None


class TestBuildJiraMission:
    def test_basic_mission(self):
        skill = MagicMock()
        skill.github_context_aware = False
        result = build_jira_mission(
            skill, "plan", "", "FOO-123",
            "https://test.atlassian.net/browse/FOO-123", "myproject",
        )
        assert result == "- [project:myproject] /plan https://test.atlassian.net/browse/FOO-123 🎫"

    def test_mission_with_context_aware(self):
        skill = MagicMock()
        skill.github_context_aware = True
        result = build_jira_mission(
            skill, "plan", "add tests", "FOO-123",
            "https://test.atlassian.net/browse/FOO-123", "myproject",
        )
        assert "add tests" in result
        assert "🎫" in result

    def test_context_ignored_when_not_context_aware(self):
        skill = MagicMock()
        skill.github_context_aware = False
        result = build_jira_mission(
            skill, "rebase", "some context", "FOO-123",
            "https://test.atlassian.net/browse/FOO-123", "myproject",
        )
        assert "some context" not in result

    def test_context_truncated_at_500_chars(self):
        skill = MagicMock()
        skill.github_context_aware = True
        long_context = "x" * 600
        result = build_jira_mission(
            skill, "plan", long_context, "FOO-123",
            "https://test.atlassian.net/browse/FOO-123", "myproject",
        )
        assert "x" * 600 not in result

    def test_mission_format(self):
        skill = MagicMock()
        skill.github_context_aware = False
        result = build_jira_mission(
            skill, "plan", "", "FOO-123",
            "https://example.com/FOO-123", "proj",
        )
        assert result.startswith("- [project:proj]")
        assert "/plan" in result
        assert "🎫" in result


class TestProcessJiraMention:
    """End-to-end mission creation tests."""

    def test_creates_mission_from_mention(
        self, tmp_path, monkeypatch, mention, skill_registry, basic_config
    ):
        """Valid @mention creates a mission entry in missions.md."""
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        missions_path = instance_dir / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        with patch("app.jira_command_handler.get_jira_nickname", return_value="koan-bot"), \
             patch("app.jira_command_handler.get_jira_authorized_users", return_value=["*"]), \
             patch("app.jira_config.get_jira_max_age_hours", return_value=24), \
             patch("app.jira_command_handler.acknowledge_jira_comment", return_value=True), \
             patch("app.jira_command_handler._notify_mission_from_jira"):

            processed_set = set()
            success, error = process_jira_mention(
                mention, skill_registry, basic_config, processed_set,
            )

        assert success is True
        assert error is None
        content = missions_path.read_text()
        assert "[project:myproject]" in content
        assert "/plan" in content
        assert "🎫" in content

    def test_dedup_already_processed(
        self, tmp_path, monkeypatch, mention, skill_registry, basic_config
    ):
        """Same comment processed twice produces only one mission."""
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        missions_path = instance_dir / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        # Mark the comment as already processed using its actual ID
        comment_id = mention["comment_id"]
        processed_set = {comment_id}

        success, error = process_jira_mention(
            mention, skill_registry, basic_config, processed_set,
        )

        assert success is False
        # missions.md unchanged
        content = missions_path.read_text()
        assert "🎫" not in content

    def test_repo_override_changes_project(
        self, tmp_path, monkeypatch, mention, skill_registry, basic_config
    ):
        """repo: token in context overrides the project name in the mission."""
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        missions_path = instance_dir / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        override_mention = dict(mention, body_text="@koan-bot plan repo:override-project")

        with patch("app.jira_command_handler.get_jira_nickname", return_value="koan-bot"), \
             patch("app.jira_command_handler.get_jira_authorized_users", return_value=["*"]), \
             patch("app.jira_config.get_jira_max_age_hours", return_value=24), \
             patch("app.jira_command_handler.acknowledge_jira_comment", return_value=True), \
             patch("app.jira_command_handler._notify_mission_from_jira"):

            processed_set = set()
            success, error = process_jira_mention(
                override_mention, skill_registry, basic_config, processed_set,
            )

        assert success is True
        content = missions_path.read_text()
        assert "[project:override-project]" in content
        # Original project name not used
        assert "[project:myproject]" not in content

    def test_unknown_command_skipped(
        self, tmp_path, monkeypatch, mention, skill_registry, basic_config
    ):
        """Unknown command is skipped with error message."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        unknown_mention = dict(mention, body_text="@koan-bot unknowncmd")

        with patch("app.jira_command_handler.get_jira_nickname", return_value="koan-bot"), \
             patch("app.jira_command_handler.get_jira_authorized_users", return_value=["*"]), \
             patch("app.jira_config.get_jira_max_age_hours", return_value=24):

            processed_set = set()
            success, error = process_jira_mention(
                unknown_mention, skill_registry, basic_config, processed_set,
            )

        assert success is False
        assert error is not None
        assert "unknown" in error.lower() or "unknowncmd" in error.lower()

    def test_permission_denied(
        self, tmp_path, monkeypatch, mention, skill_registry, basic_config
    ):
        """Non-authorized user is denied."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        with patch("app.jira_command_handler.get_jira_nickname", return_value="koan-bot"), \
             patch("app.jira_command_handler.get_jira_authorized_users",
                   return_value=["allowed@example.com"]), \
             patch("app.jira_config.get_jira_max_age_hours", return_value=24):

            processed_set = set()
            success, error = process_jira_mention(
                mention, skill_registry, basic_config, processed_set,
            )

        assert success is False
        assert error is not None
        assert "denied" in error.lower()

    def test_missing_comment_id_skipped(
        self, skill_registry, basic_config
    ):
        """Mentions without a comment ID are silently skipped."""
        bad_mention = {"issue_key": "FOO-123", "body_text": "@koan-bot plan"}
        success, error = process_jira_mention(bad_mention, skill_registry, basic_config, set())
        assert success is False
        assert error is None
