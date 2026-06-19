"""Tests for github_command_handler.py — notification-to-mission bridge."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.github_command_handler import (
    NOTIFICATION_OUTCOME_HANDLED_NOOP,
    NOTIFICATION_OUTCOME_KEY,
    NOTIFICATION_OUTCOME_QUEUED,
    _ASSIGNMENT_REASON_TO_COMMAND,
    _REPLY_RATE_FILE,
    _error_replies,
    _expand_combo_mission,
    _closed_reason_from_subject_info,
    _extract_url_from_context,
    _fetch_and_filter_comment,
    _fetch_subject_info,
    _find_all_thread_mentions,
    _handle_help_command,
    _is_bot_still_requested,
    _is_subject_closed,
    _load_reply_timestamps,
    _notify_closed_subject_skipped,
    _notify_github_question,
    _notify_github_reply,
    _post_help_reply,
    _save_reply_timestamps,
    _try_assignment_notification,
    _try_nlp_classification,
    _try_reply,
    _validate_and_parse_command,
    build_mission_from_command,
    extract_issue_number_from_notification,
    format_help_list_message,
    format_help_message,
    get_github_enabled_commands,
    get_github_enabled_commands_with_descriptions,
    _post_command_acknowledgment,
    post_error_reply,
    process_single_notification,
    resolve_project_from_notification,
    validate_command,
)
from app.skills import Skill, SkillCommand, SkillRegistry

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def subject_closed_state():
    """Per-test override hook for the stubbed subject's closed/merged state.

    Defaults to `None` (subject treated as open). Tests that need to
    exercise the closed-subject branch should override this fixture in
    their module/class scope and return ``"merged"`` or ``"closed"``.
    Making the default explicit (rather than hidden inside an autouse
    stub) lets future test authors see the seam without reading the
    fixture body.
    """
    return None


@pytest.fixture
def subject_head_sha():
    """Head SHA returned by the stubbed subject fetch.

    Anchors the ``review_requested`` dedup key. A fixed value keeps the
    key deterministic across repeated calls within a test, so the
    persistent thread tracker can dedup a re-poll of the same PR.
    """
    return "deadbeefcafe0001"


@pytest.fixture(autouse=True)
def _stub_subject_info(subject_closed_state, subject_head_sha):
    """Stub the network-hitting subject fetch shared by both notification paths.

    ``_fetch_subject_info`` is the single seam that calls the GitHub API for a
    subject's state/merged/head SHA. The @mention path reaches it through
    ``_is_subject_closed`` and the assignment path calls it directly, so
    stubbing it here keeps the whole module offline and parallel-safe. The
    returned dict is consistent with ``subject_closed_state`` and carries
    ``subject_head_sha`` so review-request dedup keys are deterministic.
    """
    if subject_closed_state == "merged":
        info = {"state": "closed", "merged": True, "head_sha": subject_head_sha}
    elif subject_closed_state == "closed":
        info = {"state": "closed", "merged": False, "head_sha": subject_head_sha}
    else:
        info = {"state": "open", "merged": False, "head_sha": subject_head_sha}
    with patch(
        "app.github_command_handler._fetch_subject_info",
        return_value=info,
    ):
        yield


@pytest.fixture
def mock_skill():
    """A github-enabled skill."""
    return Skill(
        name="rebase",
        scope="core",
        description="Rebase PR",
        github_enabled=True,
        github_context_aware=False,
        commands=[SkillCommand(name="rebase", aliases=["rb"])],
    )


@pytest.fixture
def mock_context_skill():
    """A github-enabled, context-aware skill."""
    return Skill(
        name="implement",
        scope="core",
        description="Implement issue",
        github_enabled=True,
        github_context_aware=True,
        commands=[SkillCommand(name="implement", aliases=["impl"])],
    )


@pytest.fixture
def registry(mock_skill, mock_context_skill):
    """Registry with test skills."""
    reg = SkillRegistry()
    reg._register(mock_skill)
    reg._register(mock_context_skill)
    return reg


@pytest.fixture
def sample_notification():
    return {
        "id": "12345",
        "reason": "mention",
        "updated_at": "2026-02-11T20:00:00Z",
        "repository": {
            "full_name": "sukria/koan",
        },
        "subject": {
            "type": "PullRequest",
            "url": "https://api.github.com/repos/sukria/koan/pulls/42",
            "latest_comment_url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidateCommand:
    def test_valid_github_enabled(self, registry):
        skill = validate_command("rebase", registry)
        assert skill is not None
        assert skill.name == "rebase"

    def test_valid_via_alias(self, registry):
        skill = validate_command("rb", registry)
        assert skill is not None
        assert skill.name == "rebase"

    def test_unknown_command(self, registry):
        assert validate_command("nonexistent", registry) is None

    def test_not_github_enabled(self):
        skill = Skill(
            name="status",
            scope="core",
            github_enabled=False,
            commands=[SkillCommand(name="status")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        assert validate_command("status", reg) is None


class TestGetGithubEnabledCommands:
    def test_lists_enabled_commands(self, registry):
        commands = get_github_enabled_commands(registry)
        assert "rebase" in commands
        assert "implement" in commands

    def test_excludes_disabled(self):
        skill = Skill(
            name="status", scope="core", github_enabled=False,
            commands=[SkillCommand(name="status")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        assert get_github_enabled_commands(reg) == []

    def test_sorted(self, registry):
        commands = get_github_enabled_commands(registry)
        assert commands == sorted(commands)


class TestGetGithubEnabledCommandsWithDescriptions:
    def test_returns_name_and_description(self, registry):
        result = get_github_enabled_commands_with_descriptions(registry)
        names = [name for name, _ in result]
        assert "rebase" in names
        assert "implement" in names

    def test_uses_command_description(self):
        skill = Skill(
            name="review", scope="core", github_enabled=True,
            description="Skill description",
            commands=[SkillCommand(name="review", description="Review a PR")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        result = get_github_enabled_commands_with_descriptions(reg)
        assert result == [("review", "Review a PR")]

    def test_falls_back_to_skill_description(self):
        skill = Skill(
            name="review", scope="core", github_enabled=True,
            description="Skill-level description",
            commands=[SkillCommand(name="review", description="")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        result = get_github_enabled_commands_with_descriptions(reg)
        assert result == [("review", "Skill-level description")]

    def test_excludes_disabled(self):
        skill = Skill(
            name="status", scope="core", github_enabled=False,
            commands=[SkillCommand(name="status", description="Status check")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        assert get_github_enabled_commands_with_descriptions(reg) == []

    def test_sorted(self, registry):
        result = get_github_enabled_commands_with_descriptions(registry)
        names = [name for name, _ in result]
        assert names == sorted(names)

    def test_deduplicates_command_names(self):
        """Two skills with same command name — first one wins."""
        skill1 = Skill(
            name="review", scope="core", github_enabled=True,
            commands=[SkillCommand(name="review", description="First")],
        )
        skill2 = Skill(
            name="review2", scope="custom", github_enabled=True,
            commands=[SkillCommand(name="review", description="Second")],
        )
        reg = SkillRegistry()
        reg._register(skill1)
        reg._register(skill2)
        result = get_github_enabled_commands_with_descriptions(reg)
        assert len(result) == 1
        assert result[0] == ("review", "First")


class TestFormatHelpMessage:
    def test_contains_invalid_command(self, registry):
        msg = format_help_message("badcmd", registry, "koanbot")
        assert "`badcmd`" in msg

    def test_lists_available_commands(self, registry):
        msg = format_help_message("badcmd", registry, "koanbot")
        assert "`@koanbot rebase`" in msg
        assert "`@koanbot implement`" in msg

    def test_includes_usage_line(self, registry):
        msg = format_help_message("badcmd", registry, "koanbot")
        assert "Usage:" in msg
        assert "`@koanbot <command>`" in msg

    def test_includes_descriptions(self):
        skill = Skill(
            name="rebase", scope="core", github_enabled=True,
            commands=[SkillCommand(name="rebase", description="Rebase a PR")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        msg = format_help_message("badcmd", reg, "koanbot")
        assert "Rebase a PR" in msg

    def test_empty_registry(self):
        reg = SkillRegistry()
        msg = format_help_message("badcmd", reg, "koanbot")
        assert "`badcmd`" in msg
        assert "Usage:" in msg

    def test_suggests_closest_command(self):
        skill = Skill(
            name="rebase", scope="core", github_enabled=True,
            commands=[SkillCommand(name="rebase", description="Rebase a PR")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        msg = format_help_message("rebas", reg, "koanbot")
        assert "Did you mean `rebase`?" in msg

    def test_no_suggestion_for_unrelated_command(self):
        skill = Skill(
            name="rebase", scope="core", github_enabled=True,
            commands=[SkillCommand(name="rebase", description="Rebase a PR")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        msg = format_help_message("xyzzy", reg, "koanbot")
        assert "Did you mean" not in msg


class TestBuildMissionFromCommand:
    def test_simple_command(self, mock_skill, sample_notification):
        mission = build_mission_from_command(
            mock_skill, "rebase", "", sample_notification, "koan"
        )
        assert mission == "- [project:koan] /rebase https://github.com/sukria/koan/pull/42 📬"

    def test_context_aware_with_context(self, mock_context_skill, sample_notification):
        mission = build_mission_from_command(
            mock_context_skill, "implement", "phase 1 only",
            sample_notification, "koan",
        )
        assert "phase 1 only" in mission
        assert "/implement" in mission

    def test_context_ignored_when_not_aware(self, mock_skill, sample_notification):
        mission = build_mission_from_command(
            mock_skill, "rebase", "extra context", sample_notification, "koan"
        )
        assert "extra context" not in mission

    def test_url_in_context_overrides(self, mock_context_skill, sample_notification):
        context = "https://github.com/other/repo/issues/99 phase 2"
        mission = build_mission_from_command(
            mock_context_skill, "implement", context, sample_notification, "koan"
        )
        assert "https://github.com/other/repo/issues/99" in mission

    def test_no_subject_url(self, mock_skill):
        notif = {"subject": {}}
        mission = build_mission_from_command(
            mock_skill, "rebase", "", notif, "myproject"
        )
        assert mission == "- [project:myproject] /rebase 📬"


class TestResolveProjectFromNotification:
    @patch("app.utils.resolve_project_path", return_value="/path/to/koan")
    @patch("app.utils.project_name_for_path", return_value="koan")
    def test_known_repo(self, mock_name, mock_resolve, sample_notification):
        result = resolve_project_from_notification(sample_notification)
        assert result == ("koan", "sukria", "koan")

    @patch("app.utils.resolve_project_path", return_value=None)
    def test_unknown_repo(self, mock_resolve, sample_notification):
        assert resolve_project_from_notification(sample_notification) is None

    def test_missing_repository(self):
        assert resolve_project_from_notification({}) is None


class TestExtractIssueNumber:
    def test_issue_url(self):
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/issues/42"}}
        assert extract_issue_number_from_notification(notif) == "42"

    def test_pr_url(self):
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/pulls/99"}}
        assert extract_issue_number_from_notification(notif) == "99"

    def test_missing_url(self):
        assert extract_issue_number_from_notification({}) is None


class TestPostErrorReply:
    def setup_method(self):
        _error_replies.clear()

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_posts_error(self, mock_api, mock_react):
        mock_api.return_value = ""
        result = post_error_reply("owner", "repo", "42", "123", "Test error")
        assert result is True
        mock_api.assert_called_once()

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_deduplication(self, mock_api, mock_react):
        mock_api.return_value = ""
        post_error_reply("owner", "repo", "42", "123", "Test error")
        result = post_error_reply("owner", "repo", "42", "123", "Test error")
        assert result is False  # Duplicate
        assert mock_api.call_count == 1

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_different_errors_not_deduplicated(self, mock_api, mock_react):
        mock_api.return_value = ""
        post_error_reply("owner", "repo", "42", "123", "Error A")
        post_error_reply("owner", "repo", "42", "123", "Error B")
        assert mock_api.call_count == 2

    @patch("app.github_command_handler.add_reaction", side_effect=RuntimeError("reaction failed"))
    @patch("app.github.api")
    def test_reaction_failure_allows_retry(self, mock_api, mock_react):
        """If add_reaction fails, the error should NOT be marked as sent."""
        mock_api.return_value = ""
        result = post_error_reply("owner", "repo", "42", "123", "Test error")
        # The whole try block catches RuntimeError, so returns False
        assert result is False
        # The error key should NOT be in dedup cache, allowing retry
        assert "123:Test error" not in _error_replies

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_successful_post_marked_as_sent(self, mock_api, mock_react):
        """After both comment and reaction succeed, error is marked as sent."""
        mock_api.return_value = ""
        result = post_error_reply("owner", "repo", "42", "123", "Test error")
        assert result is True
        assert "123:Test error" in _error_replies

    @patch("app.github_command_handler.add_reaction", return_value=False)
    @patch("app.github.api")
    def test_reaction_false_allows_retry(self, mock_api, mock_react):
        """If add_reaction returns False (not exception), error key must NOT
        be added to dedup cache — allowing retry on next poll."""
        mock_api.return_value = ""
        result = post_error_reply("owner", "repo", "42", "123", "Test error")
        assert result is True  # Comment was posted successfully
        # Reaction failed silently — error should NOT be in dedup cache
        assert "123:Test error" not in _error_replies
        # Second call should attempt to post again (not deduplicated)
        result2 = post_error_reply("owner", "repo", "42", "123", "Test error")
        assert result2 is True
        assert mock_api.call_count == 2


class TestPostCommandAcknowledgment:
    """Tests for _post_command_acknowledgment — threaded ack replies."""

    @patch("app.github_reply.post_threaded_reply", return_value=True)
    def test_posts_ack_with_command_name(self, mock_post):
        comment = {
            "id": 123,
            "url": "https://api.github.com/repos/o/r/issues/comments/123",
            "user": {"login": "alice"},
            "body": "@bot review",
        }
        _post_command_acknowledgment("o", "r", "42", "review", comment, "bot")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        body_arg = call_kwargs[0][3]
        assert "`/review`" in body_arg
        assert "queued" in body_arg

    @patch("app.github_reply.post_threaded_reply", return_value=True)
    def test_gh_request_gets_natural_language_message(self, mock_post):
        comment = {
            "id": 456,
            "url": "",
            "user": {"login": "bob"},
            "body": "@bot what do you think?",
        }
        _post_command_acknowledgment("o", "r", "42", "gh_request", comment, "bot")
        body_arg = mock_post.call_args[0][3]
        assert "Got it" in body_arg
        assert "gh_request" not in body_arg

    @patch("app.github_reply.post_threaded_reply", return_value=False)
    def test_failure_does_not_raise(self, mock_post):
        """Ack failures should be silent — not block command dispatch."""
        comment = {"id": 1, "url": "", "user": {"login": "x"}, "body": "y"}
        _post_command_acknowledgment("o", "r", "42", "review", comment, "bot")


class TestProcessSingleNotification:
    """Integration-style tests for the full notification processing pipeline."""

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_happy_path(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        assert error is None
        mock_insert.assert_called_once()
        mock_react.assert_called_once()
        # Notification dict is annotated with parsed command and author
        assert sample_notification["_koan_command"] == "rebase"
        assert sample_notification["_koan_author"] == "alice"

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler._find_all_thread_mentions", return_value=[])
    def test_stale_notification_skipped(self, mock_mentions, mock_read, registry, sample_notification):
        success, error = process_single_notification(
            sample_notification, registry, {}, None, "bot",
        )
        assert success is False
        assert error is None

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler._find_all_thread_mentions", return_value=[])
    def test_no_comment_skipped(self, mock_mentions, mock_read, registry, sample_notification):
        success, error = process_single_notification(
            sample_notification, registry, {}, None, "bot",
        )
        assert success is False

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification", return_value=None)
    def test_unknown_repo_silently_skipped(
        self, mock_resolve, mock_mentions,
        mock_read, registry, sample_notification,
    ):
        """When repo is not in projects.yaml, leave it untouched.

        Marking the notification as read is a write to shared GitHub state.
        With a shared bot identity across multiple Kōan instances, this
        instance must not clear notifications belonging to a sibling
        instance — leaving the notification unread lets the owning
        instance process it on its next poll.
        """
        mock_mentions.return_value = [{
            "id": 99999, "body": "@bot rebase", "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "bot", "authorized_users": ["alice"]}}

        success, error = process_single_notification(
            sample_notification, registry, config, None, "bot",
        )
        assert success is False
        assert error is None
        mock_read.assert_not_called()

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification", return_value=None)
    def test_unknown_repo_bad_fullname_skips(
        self, mock_resolve, mock_mentions,
        mock_read, registry,
    ):
        """Notification with no valid full_name is silently skipped.

        Same contract as ``test_unknown_repo_silently_skipped``: no write to
        shared GitHub state — the notification is left unread.
        """
        notif = {
            "id": "12345", "reason": "mention",
            "updated_at": "2026-02-11T20:00:00Z",
            "repository": {"full_name": ""},
            "subject": {
                "url": "https://api.github.com/repos/x/y/pulls/1",
                "latest_comment_url": "https://api.github.com/repos/x/y/issues/comments/1",
            },
        }
        mock_mentions.return_value = [{
            "id": 99999, "body": "@bot rebase", "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "bot"}}

        success, error = process_single_notification(
            notif, registry, config, None, "bot",
        )
        assert success is False
        assert error is None
        mock_read.assert_not_called()

    @patch("app.github_command_handler.post_error_reply")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_invalid_command_returns_help_error(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_read, mock_error_reply,
        registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": "99999", "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            "body": "@testbot badcmd",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot"}}

        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )
        assert success is False
        # Single-comment errors are delegated to the caller (loop_manager),
        # not posted inline — the message is returned for the caller to post once.
        assert error is not None
        assert "`badcmd`" in error
        mock_error_reply.assert_not_called()

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_invalid_command_marks_notification_read(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_read, registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999, "body": "@testbot badcmd",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot"}}

        process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )
        # Notification should be marked as read for invalid commands
        mock_read.assert_called_with("12345")

    @patch("app.github_command_handler.post_error_reply")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_user_permission", return_value=False)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_permission_denied_returns_error(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_perm, mock_read, mock_error_reply,
        registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": "99999", "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            "body": "@testbot rebase",
            "user": {"login": "eve"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["alice"]}}

        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )
        assert success is False
        # Single-comment errors are delegated to the caller, not posted inline.
        assert error is not None
        assert "Permission denied" in error
        mock_error_reply.assert_not_called()

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_user_permission", return_value=False)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_permission_denied_marks_notification_read(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_perm, mock_read, registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999, "body": "@testbot rebase",
            "user": {"login": "eve"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["alice"]}}

        process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )
        # Permission-denied must mark notification as read to prevent
        # repeated processing on every poll cycle
        mock_read.assert_called_with("12345")

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission", side_effect=OSError("disk full"))
    @patch("app.github_command_handler.post_error_reply")
    def test_insert_mission_failure_marks_read(
        self, mock_error_reply, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """When insert_pending_mission fails, notification must still be marked
        as read to prevent infinite re-processing."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": "99999",
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is False
        # Single-comment errors are delegated to the caller, not posted inline.
        assert error is not None
        assert "Failed to queue mission" in error
        mock_error_reply.assert_not_called()
        mock_read.assert_called_with("12345")
        # Reaction should NOT have been added (mission wasn't persisted)
        mock_react.assert_not_called()

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission", side_effect=PermissionError("read-only"))
    @patch("app.github_command_handler.post_error_reply")
    def test_insert_mission_permission_error_handled(
        self, mock_error_reply, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """PermissionError (subclass of OSError) is also caught gracefully."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": "99999",
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is False
        # Single-comment errors are delegated to the caller, not posted inline.
        assert error is not None
        assert "read-only" in error
        mock_error_reply.assert_not_called()
        mock_read.assert_called_with("12345")

    @patch("app.github_command_handler.post_error_reply")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_empty_koan_root_rejects_mission(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, mock_error_reply,
        registry, sample_notification,
    ):
        """Empty KOAN_ROOT should fail early, not write to relative path."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": "99999",
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": ""}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is False
        # Single-comment errors are delegated to the caller, not posted inline.
        assert error is not None
        assert "KOAN_ROOT" in error
        mock_error_reply.assert_not_called()
        mock_insert.assert_not_called()
        mock_read.assert_called_with("12345")

    @patch("app.github_command_handler.post_error_reply")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_missing_koan_root_rejects_mission(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, mock_error_reply,
        registry, sample_notification, monkeypatch,
    ):
        """Unset KOAN_ROOT should fail early, not write to relative path."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": "99999",
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        monkeypatch.delenv("KOAN_ROOT", raising=False)
        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )

        assert success is False
        # Single-comment errors are delegated to the caller, not posted inline.
        assert error is not None
        assert "KOAN_ROOT" in error
        mock_error_reply.assert_not_called()
        mock_insert.assert_not_called()

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_review_mention_sets_cooldown(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, sample_notification, tmp_path,
    ):
        """A /review mission queued from an @mention records review cooldown."""
        review_registry = SkillRegistry()
        review_registry._register(Skill(
            name="review",
            scope="core",
            description="Review PR",
            github_enabled=True,
            github_context_aware=True,
            commands=[SkillCommand(name="review", aliases=["rv"])],
        ))

        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot review",
            "user": {"login": "alice"},
        }]
        mock_insert.return_value = True

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}), \
             patch("app.github_notification_tracker.set_review_cooldown") as mock_cd:
            success, error = process_single_notification(
                sample_notification, review_registry, config, None, "testbot",
            )

        assert success is True
        assert error is None
        mock_cd.assert_called_once()
        assert mock_cd.call_args[0][1:] == ("sukria", "koan", "42")


class TestProcessNotificationCustomHandler:
    """Custom skills under instance/skills/<scope>/ with a handler.py are
    dispatched in-process by ``external_skill_dispatch.try_dispatch_custom_handler``
    instead of being queued as slash missions without a runner."""

    def _registry_with_custom_skill(self, handler_path: Path):
        skill = Skill(
            name="my_fix",
            scope="my_team",
            description="Team-specific fix",
            handler_path=handler_path,
            skill_dir=handler_path.parent,
            github_enabled=True,
            github_context_aware=True,
            commands=[SkillCommand(name="my_fix", aliases=["myfix"])],
        )
        reg = SkillRegistry()
        reg._register(skill)
        return reg

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_custom_handler_runs_inline_and_no_slash_mission(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, sample_notification, tmp_path,
    ):
        """The custom handler runs and missions.md is NOT touched via
        ``insert_pending_mission`` from the slash-mission branch."""
        # Handler writes a marker file so we can see it ran.
        marker = tmp_path / "ran.txt"
        handler_dir = tmp_path / "skills" / "my_team" / "fix"
        handler_dir.mkdir(parents=True)
        handler = handler_dir / "handler.py"
        handler.write_text(
            f"def handle(ctx):\n"
            f"    with open({str(marker)!r}, 'w') as f:\n"
            f"        f.write(ctx.args)\n"
            f"    return 'inline ok'\n"
        )

        # Notification subject title carries a Jira key that should be
        # auto-fed when the user omits one from the command.
        sample_notification["subject"]["title"] = "Broken login PROJ-123"

        registry = self._registry_with_custom_skill(handler)
        mock_resolve.return_value = ("my_team", "alice", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot myfix",
            "user": {"login": "alice"},
            "url": "https://api.github.com/x",
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        assert error is None
        # Handler ran inline and saw the auto-fed Jira key from the title.
        assert marker.exists()
        assert marker.read_text() == "PROJ-123"
        # The slash-mission path was bypassed — no direct insert_pending_mission
        # call from process_single_notification itself.
        # (The handler may insert its own mission through utils, but that
        # would also hit mock_insert, so assert *either* zero calls or that
        # no GitHub-flavoured slash mission was queued.)
        for call in mock_insert.call_args_list:
            assert "/my_fix" not in str(call), (
                "slash mission /my_fix should NOT have been queued from GitHub path"
            )
            assert "📬" not in str(call), (
                "📬-marked GitHub mission should NOT have been queued"
            )
        # Notification bookkeeping still happened.
        mock_react.assert_called_once()
        assert sample_notification["_koan_command"] == "myfix"
        assert sample_notification["_koan_author"] == "alice"

    @patch("app.github_reply.post_threaded_reply", return_value=True)
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_custom_handler_posts_inline_reply_to_github(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, mock_post, sample_notification, tmp_path,
    ):
        """When the custom handler returns an inline_reply, it should be
        posted to GitHub via post_threaded_reply."""
        handler_dir = tmp_path / "skills" / "my_team" / "fix"
        handler_dir.mkdir(parents=True)
        handler = handler_dir / "handler.py"
        handler.write_text(
            "def handle(ctx):\n"
            "    return 'Fix queued for PROJ-123'\n"
        )

        registry = self._registry_with_custom_skill(handler)
        mock_resolve.return_value = ("my_team", "alice", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot myfix",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/o/r/issues/comments/99999",
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"], "ack_enabled": True}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}), \
             patch("app.utils.insert_pending_mission"):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        mock_post.assert_called_once()
        body_arg = mock_post.call_args[0][3]
        assert "Fix queued for PROJ-123" in body_arg


class TestTryReply:
    """Tests for the _try_reply helper that generates AI replies."""

    @pytest.fixture(autouse=True)
    def _mock_rate_limiter(self):
        """Mock file-backed rate limiter so tests don't leak state across runs."""
        with patch("app.github_command_handler._load_reply_timestamps", return_value={}), \
             patch("app.github_command_handler._save_reply_timestamps"):
            yield

    @pytest.fixture
    def reply_notification(self):
        return {
            "id": "77777",
            "subject": {
                "url": "https://api.github.com/repos/sukria/koan/issues/42",
            },
            "repository": {"full_name": "sukria/koan"},
        }

    @pytest.fixture
    def reply_comment(self):
        return {
            "id": 55555,
            "body": "@bot what do you think about this?",
            "user": {"login": "alice"},
        }

    @pytest.fixture
    def reply_config(self):
        return {
            "github": {
                "nickname": "bot",
                "reply_enabled": True,
                "authorized_users": ["*"],
            }
        }

    def test_disabled_returns_false(self, reply_notification, reply_comment):
        config = {"github": {"reply_enabled": False}}
        result = _try_reply(
            reply_notification, reply_comment, config, None,
            "bot", "sukria", "koan", "koan", "what?",
        )
        assert result is False

    def test_missing_config_returns_false(self, reply_notification, reply_comment):
        result = _try_reply(
            reply_notification, reply_comment, {}, None,
            "bot", "sukria", "koan", "koan", "what?",
        )
        assert result is False

    @patch("app.github_command_handler.check_user_permission", return_value=False)
    def test_unauthorized_user_returns_false(
        self, mock_perm, reply_notification, reply_comment, reply_config,
    ):
        result = _try_reply(
            reply_notification, reply_comment, reply_config, None,
            "bot", "sukria", "koan", "koan", "what?",
        )
        assert result is False

    @patch("app.github_command_handler._notify_github_reply")
    @patch("app.github_command_handler._notify_github_question")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.post_threaded_reply", return_value=True)
    @patch("app.github_reply.generate_reply", return_value="Here is my reply")
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_successful_reply(
        self, mock_resolve, mock_ctx, mock_gen, mock_post,
        mock_perm, mock_react, mock_read,
        mock_notify_q, mock_notify_r,
        reply_notification, reply_comment, reply_config,
    ):
        result = _try_reply(
            reply_notification, reply_comment, reply_config, None,
            "bot", "sukria", "koan", "koan", "what do you think?",
        )
        assert result is True
        mock_gen.assert_called_once()
        mock_post.assert_called_once()
        # Should react with eyes emoji (not thumbs up), using comment API URL
        mock_react.assert_called_once_with(
            "sukria", "koan", "55555", emoji="eyes",
            comment_api_url="",
        )
        # Should send Telegram notifications
        mock_notify_q.assert_called_once_with(
            "alice", "sukria", "koan", "42", "what do you think?",
        )
        mock_notify_r.assert_called_once_with(
            "sukria", "koan", "42", "Here is my reply",
        )

    @patch("app.github_command_handler._notify_github_reply")
    @patch("app.github_command_handler._notify_github_question")
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.generate_reply", return_value=None)
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_generation_failure_returns_false(
        self, mock_resolve, mock_ctx, mock_gen, mock_perm,
        mock_notify_q, mock_notify_r,
        reply_notification, reply_comment, reply_config,
    ):
        result = _try_reply(
            reply_notification, reply_comment, reply_config, None,
            "bot", "sukria", "koan", "koan", "question",
        )
        assert result is False
        # Question notification is sent, but reply notification is NOT
        mock_notify_q.assert_called_once()
        mock_notify_r.assert_not_called()

    @patch("app.github_command_handler._notify_github_reply")
    @patch("app.github_command_handler._notify_github_question")
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.post_threaded_reply", return_value=False)
    @patch("app.github_reply.generate_reply", return_value="reply text")
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_post_failure_returns_false(
        self, mock_resolve, mock_ctx, mock_gen, mock_post, mock_perm,
        mock_notify_q, mock_notify_r,
        reply_notification, reply_comment, reply_config,
    ):
        result = _try_reply(
            reply_notification, reply_comment, reply_config, None,
            "bot", "sukria", "koan", "koan", "question",
        )
        assert result is False
        # Question notification sent, but reply notification NOT (post failed)
        mock_notify_q.assert_called_once()
        mock_notify_r.assert_not_called()

    def test_no_issue_number_returns_false(self, reply_comment, reply_config):
        notif = {"id": "1", "subject": {"url": ""}, "repository": {"full_name": "o/r"}}
        with patch("app.github_command_handler.check_user_permission", return_value=True):
            result = _try_reply(
                notif, reply_comment, reply_config, None,
                "bot", "o", "r", "proj", "q",
            )
        assert result is False

    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.utils.resolve_project_path", return_value=None)
    def test_no_project_path_returns_false(
        self, mock_resolve, mock_perm, reply_notification, reply_comment, reply_config,
    ):
        result = _try_reply(
            reply_notification, reply_comment, reply_config, None,
            "bot", "sukria", "koan", "koan", "q",
        )
        assert result is False


class TestProcessNotificationWithReply:
    """Tests for reply integration in process_single_notification."""

    @pytest.fixture(autouse=True)
    def _mock_rate_limiter(self):
        with patch("app.github_command_handler._load_reply_timestamps", return_value={}), \
             patch("app.github_command_handler._save_reply_timestamps"):
            yield

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.github_reply.post_threaded_reply", return_value=True)
    @patch("app.github_reply.generate_reply", return_value="AI reply here")
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_unknown_command_triggers_reply_when_enabled(
        self, mock_resolve_path, mock_ctx, mock_gen, mock_post,
        mock_resolve, mock_mentions,
        mock_processed, mock_perm, mock_react, mock_read,
        registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999, "body": "@testbot what do you think about this PR?",
            "user": {"login": "alice"},
        }]
        config = {
            "github": {
                "nickname": "testbot",
                "reply_enabled": True,
                "authorized_users": ["*"],
            }
        }

        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )

        # Reply was posted, no error message
        assert success is False
        assert error is None
        mock_gen.assert_called_once()
        mock_post.assert_called_once()

    @patch("app.github_command_handler.post_error_reply")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_unknown_command_falls_back_to_help_when_reply_disabled(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_read, mock_error_reply,
        registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": "99999", "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            "body": "@testbot what do you think?",
            "user": {"login": "alice"},
        }]
        config = {
            "github": {
                "nickname": "testbot",
                "reply_enabled": False,
            }
        }

        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )

        assert success is False
        # Single-comment errors are delegated to the caller, not posted inline.
        assert error is not None
        assert "`what`" in error
        mock_error_reply.assert_not_called()


class TestTryReplyAuthorizedUsers:
    """Tests for separate reply_authorized_users permission in _try_reply."""

    @pytest.fixture(autouse=True)
    def _mock_rate_limiter(self):
        with patch("app.github_command_handler._load_reply_timestamps", return_value={}), \
             patch("app.github_command_handler._save_reply_timestamps"):
            yield

    @pytest.fixture
    def reply_notification(self):
        return {
            "id": "77777",
            "subject": {
                "url": "https://api.github.com/repos/sukria/koan/issues/42",
            },
            "repository": {"full_name": "sukria/koan"},
        }

    @pytest.fixture
    def reply_comment(self):
        return {
            "id": 55555,
            "body": "@bot what do you think about this?",
            "user": {"login": "unprivileged_user"},
        }

    @pytest.fixture
    def base_config(self):
        return {
            "github": {
                "nickname": "bot",
                "reply_enabled": True,
                "authorized_users": ["admin_only"],
            }
        }

    @patch("app.github_command_handler._notify_github_reply")
    @patch("app.github_command_handler._notify_github_question")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.post_threaded_reply", return_value=True)
    @patch("app.github_reply.generate_reply", return_value="Here is my reply")
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_reply_authorized_users_wildcard_checks_write_access(
        self, mock_resolve, mock_ctx, mock_gen, mock_post,
        mock_perm, mock_react, mock_read,
        mock_notify_q, mock_notify_r,
        reply_notification, reply_comment,
    ):
        """When reply_authorized_users: ["*"], check_user_permission is still
        called to verify GitHub write access (same as command wildcard)."""
        config = {
            "github": {
                "nickname": "bot",
                "reply_enabled": True,
                "authorized_users": ["admin_only"],
                "reply_authorized_users": ["*"],
            }
        }
        result = _try_reply(
            reply_notification, reply_comment, config, None,
            "bot", "sukria", "koan", "koan", "what do you think?",
        )
        assert result is True
        mock_perm.assert_called_once()
        assert mock_perm.call_args[0][3] == ["*"]
        mock_gen.assert_called_once()

    @patch("app.github_command_handler.check_user_permission", return_value=False)
    def test_fallback_to_authorized_users_when_not_configured(
        self, mock_perm, reply_notification, reply_comment, base_config,
    ):
        """When reply_authorized_users is not set, falls back to authorized_users."""
        result = _try_reply(
            reply_notification, reply_comment, base_config, None,
            "bot", "sukria", "koan", "koan", "what?",
        )
        assert result is False
        # Should have called check_user_permission with the authorized_users list
        mock_perm.assert_called_once()
        call_args = mock_perm.call_args
        assert call_args[0][3] == ["admin_only"]

    @patch("app.github_command_handler._notify_github_reply")
    @patch("app.github_command_handler._notify_github_question")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.post_threaded_reply", return_value=True)
    @patch("app.github_reply.generate_reply", return_value="reply")
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_explicit_reply_authorized_users_list(
        self, mock_resolve, mock_ctx, mock_gen, mock_post,
        mock_perm, mock_react, mock_read,
        mock_notify_q, mock_notify_r,
        reply_notification, reply_comment,
    ):
        """When reply_authorized_users is an explicit list, use it for permission check."""
        config = {
            "github": {
                "nickname": "bot",
                "reply_enabled": True,
                "authorized_users": ["admin_only"],
                "reply_authorized_users": ["unprivileged_user", "another"],
            }
        }
        result = _try_reply(
            reply_notification, reply_comment, config, None,
            "bot", "sukria", "koan", "koan", "what?",
        )
        assert result is True
        # check_user_permission called with the reply_authorized_users list
        mock_perm.assert_called_once()
        call_args = mock_perm.call_args
        assert call_args[0][3] == ["unprivileged_user", "another"]

    def test_empty_reply_authorized_users_denies_all(
        self, reply_notification, reply_comment,
    ):
        """Explicit empty list means no one can get replies."""
        config = {
            "github": {
                "nickname": "bot",
                "reply_enabled": True,
                "authorized_users": ["*"],
                "reply_authorized_users": [],
            }
        }
        with patch("app.github_command_handler.check_user_permission", return_value=False) as mock_perm:
            result = _try_reply(
                reply_notification, reply_comment, config, None,
                "bot", "sukria", "koan", "koan", "what?",
            )
        assert result is False
        # Should call check_user_permission with empty list, which returns False
        mock_perm.assert_called_once()


class TestTryReplyRateLimit:
    """Tests for per-user rate limiting in _try_reply."""

    @pytest.fixture
    def reply_notification(self):
        return {
            "id": "77777",
            "subject": {
                "url": "https://api.github.com/repos/sukria/koan/issues/42",
            },
            "repository": {"full_name": "sukria/koan"},
        }

    @pytest.fixture
    def reply_comment(self):
        return {
            "id": 55555,
            "body": "@bot question?",
            "user": {"login": "alice"},
        }

    @pytest.fixture
    def rate_config(self):
        return {
            "github": {
                "nickname": "bot",
                "reply_enabled": True,
                "reply_authorized_users": ["*"],
                "reply_rate_limit": 2,
            }
        }

    @patch("app.github_command_handler._save_reply_timestamps")
    @patch("app.github_command_handler._load_reply_timestamps", return_value={})
    @patch("app.github_command_handler._notify_github_reply")
    @patch("app.github_command_handler._notify_github_question")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.post_threaded_reply", return_value=True)
    @patch("app.github_reply.generate_reply", return_value="reply")
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_rate_limit_allows_under_limit(
        self, mock_resolve, mock_ctx, mock_gen, mock_post,
        mock_perm, mock_react, mock_read,
        mock_notify_q, mock_notify_r,
        mock_load, mock_save,
        reply_notification, reply_comment, rate_config,
    ):
        """Replies succeed when user is under the rate limit."""
        result = _try_reply(
            reply_notification, reply_comment, rate_config, None,
            "bot", "sukria", "koan", "koan", "question?",
        )
        assert result is True
        mock_save.assert_called_once()

    @patch("app.github_command_handler._load_reply_timestamps")
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.generate_reply", return_value="reply")
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_rate_limit_blocks_when_exceeded(
        self, mock_resolve, mock_gen, mock_perm, mock_load,
        reply_notification, reply_comment, rate_config,
    ):
        """Reply is denied when user exceeds rate limit."""
        import time
        now = time.time()
        mock_load.return_value = {"alice": [now - 60, now - 30]}

        result = _try_reply(
            reply_notification, reply_comment, rate_config, None,
            "bot", "sukria", "koan", "koan", "question?",
        )
        assert result is False
        mock_gen.assert_not_called()

    @patch("app.github_command_handler._save_reply_timestamps")
    @patch("app.github_command_handler._load_reply_timestamps")
    @patch("app.github_command_handler._notify_github_reply")
    @patch("app.github_command_handler._notify_github_question")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.post_threaded_reply", return_value=True)
    @patch("app.github_reply.generate_reply", return_value="reply")
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_rate_limit_stale_timestamps_cleaned_by_loader(
        self, mock_resolve, mock_ctx, mock_gen, mock_post,
        mock_perm, mock_react, mock_read,
        mock_notify_q, mock_notify_r,
        mock_load, mock_save,
        reply_notification, reply_comment, rate_config,
    ):
        """_load_reply_timestamps strips stale entries — result is under limit."""
        mock_load.return_value = {}

        result = _try_reply(
            reply_notification, reply_comment, rate_config, None,
            "bot", "sukria", "koan", "koan", "question?",
        )
        assert result is True
        mock_gen.assert_called_once()


class TestGitHubTelegramNotifications:
    """Tests for ❓ and 💬 Telegram notifications from GitHub interactions."""

    @patch("app.notify.send_telegram", return_value=True)
    def test_question_notification_sends_telegram(self, mock_send):
        _notify_github_question("alice", "sukria", "koan", "42", "What about this?")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "❓" in msg
        assert "@alice" in msg
        assert "sukria/koan#42" in msg
        assert "What about this?" in msg

    @patch("app.notify.send_telegram", return_value=True)
    def test_question_notification_truncates_long_text(self, mock_send):
        long_question = "x" * 300
        _notify_github_question("bob", "owner", "repo", "7", long_question)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "…" in msg
        # Should truncate to 200 chars + ellipsis
        assert len(long_question) > len(msg)

    @patch("app.notify.send_telegram", return_value=True)
    def test_reply_notification_sends_telegram(self, mock_send):
        _notify_github_reply("sukria", "koan", "42", "Here is my analysis.")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "💬" in msg
        assert "sukria/koan#42" in msg
        assert "Here is my analysis." in msg

    @patch("app.notify.send_telegram", return_value=True)
    def test_reply_notification_truncates_long_text(self, mock_send):
        long_reply = "y" * 300
        _notify_github_reply("owner", "repo", "7", long_reply)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "…" in msg

    @patch("app.notify.send_telegram", side_effect=Exception("network error"))
    def test_question_notification_swallows_errors(self, mock_send):
        # Should not raise — error is caught internally
        _notify_github_question("alice", "o", "r", "1", "question")

    @patch("app.notify.send_telegram", side_effect=Exception("network error"))
    def test_reply_notification_swallows_errors(self, mock_send):
        # Should not raise — error is caught internally
        _notify_github_reply("o", "r", "1", "reply")


# ---------------------------------------------------------------------------
# _extract_url_from_context — URL regex precision
# ---------------------------------------------------------------------------


class TestExtractUrlFromContext:
    """Tests for URL extraction regex that shouldn't capture trailing punctuation."""

    def test_clean_pr_url(self):
        result = _extract_url_from_context(
            "rebase https://github.com/owner/repo/pull/42"
        )
        assert result is not None
        url, remaining = result
        assert url == "https://github.com/owner/repo/pull/42"
        assert remaining == "rebase"

    def test_clean_issue_url(self):
        result = _extract_url_from_context(
            "fix https://github.com/owner/repo/issues/10"
        )
        assert result is not None
        url, _ = result
        assert url == "https://github.com/owner/repo/issues/10"

    def test_url_with_trailing_paren(self):
        """Trailing ')' should NOT be captured."""
        result = _extract_url_from_context(
            "see (https://github.com/owner/repo/pull/5)"
        )
        assert result is not None
        url, _ = result
        assert not url.endswith(")")

    def test_url_with_trailing_bracket(self):
        """Trailing ']' should NOT be captured."""
        result = _extract_url_from_context(
            "[https://github.com/owner/repo/pull/5]"
        )
        assert result is not None
        url, _ = result
        assert not url.endswith("]")

    def test_repo_url_without_path_ignored(self):
        """Bare repo URL (no /pull/N or /issues/N) should NOT be extracted."""
        result = _extract_url_from_context("check https://github.com/owner/repo")
        assert result is None

    def test_no_url_returns_none(self):
        result = _extract_url_from_context("just some text without urls")
        assert result is None

    def test_url_with_dots_in_repo_name(self):
        result = _extract_url_from_context(
            "fix https://github.com/perl-actions/perl-versions/issues/31"
        )
        assert result is not None
        url, _ = result
        assert url == "https://github.com/perl-actions/perl-versions/issues/31"

    def test_url_with_hyphens_in_owner(self):
        result = _extract_url_from_context(
            "rebase https://github.com/my-org/my-repo/pull/99"
        )
        assert result is not None
        url, _ = result
        assert url == "https://github.com/my-org/my-repo/pull/99"


class TestCommentApiUrlThreading:
    """Tests that comment_api_url is properly threaded through the pipeline."""

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_pr_review_comment_url_threaded(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """PR review comment URL is passed to add_reaction for correct endpoint."""
        pr_comment_url = "https://api.github.com/repos/sukria/koan/pulls/comments/42"
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 42,
            "url": pr_comment_url,
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        # Verify comment_api_url was passed to add_reaction
        mock_react.assert_called_once_with(
            "sukria", "koan", "42",
            comment_api_url=pr_comment_url,
        )

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_comment_url_passed_to_check_already_processed(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """comment_api_url is passed to check_already_processed."""
        issue_comment_url = "https://api.github.com/repos/sukria/koan/issues/comments/99"
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99,
            "url": issue_comment_url,
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        # check_already_processed should receive the URL
        mock_processed.assert_called_once_with(
            "99", "testbot", "sukria", "koan",
            comment_api_url=issue_comment_url,
        )


# ---------------------------------------------------------------------------
# _fetch_and_filter_comment — direct unit tests
# ---------------------------------------------------------------------------


class TestFetchAndFilterComment:
    """Direct tests for _fetch_and_filter_comment helper."""

    @pytest.fixture
    def notification(self):
        return {
            "id": "7777",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "url": "https://api.github.com/repos/owner/repo/issues/10",
                "latest_comment_url": "https://api.github.com/repos/owner/repo/issues/comments/555",
            },
        }

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.is_notification_stale", return_value=True)
    def test_stale_notification_returns_none_and_marks_read(
        self, mock_stale, mock_read, notification,
    ):
        result = _fetch_and_filter_comment(notification, "bot", max_age_hours=24)
        assert result is None
        mock_read.assert_called_once_with("7777")

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.find_mention_in_thread", return_value=None)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification", return_value=None)
    def test_no_comment_falls_back_to_thread_search(self, mock_comment, mock_stale, mock_find, mock_read, notification):
        """When latest_comment_url fails, search thread before giving up."""
        result = _fetch_and_filter_comment(notification, "bot", max_age_hours=24)
        assert result is None
        mock_find.assert_called_once_with(notification, "bot")
        mock_read.assert_called_once_with("7777")

    @patch("app.github_command_handler.find_mention_in_thread")
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification", return_value=None)
    def test_no_comment_thread_search_finds_mention(self, mock_comment, mock_stale, mock_find, notification):
        """When latest_comment_url fails but thread has an unprocessed @mention."""
        real_mention = {"id": 999, "body": "@bot rebase", "user": {"login": "alice"}}
        mock_find.return_value = real_mention
        result = _fetch_and_filter_comment(notification, "bot", max_age_hours=24)
        assert result == real_mention
        mock_find.assert_called_once_with(notification, "bot")

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.find_mention_in_thread", return_value=None)
    @patch("app.github_command_handler.is_self_mention", return_value=False)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    def test_no_mention_in_body_searches_thread(self, mock_comment, mock_stale, mock_self, mock_find, mock_read, notification):
        """When latest comment doesn't mention bot (URL shifted), search thread."""
        mock_comment.return_value = {
            "id": 555, "body": "CI passed", "user": {"login": "ci-bot"},
        }
        result = _fetch_and_filter_comment(notification, "bot", max_age_hours=24)
        assert result is None
        mock_find.assert_called_once_with(notification, "bot")
        mock_read.assert_called_once_with("7777")

    @patch("app.github_command_handler.find_mention_in_thread")
    @patch("app.github_command_handler.is_self_mention", return_value=False)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    def test_no_mention_in_body_thread_search_finds_mention(self, mock_comment, mock_stale, mock_self, mock_find, notification):
        """When latest comment doesn't mention bot, but thread has the real @mention."""
        mock_comment.return_value = {
            "id": 555, "body": "CI passed", "user": {"login": "ci-bot"},
        }
        real_mention = {"id": 888, "body": "@bot rebase", "user": {"login": "alice"}}
        mock_find.return_value = real_mention
        result = _fetch_and_filter_comment(notification, "bot", max_age_hours=24)
        assert result == real_mention

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.find_mention_in_thread", return_value=None)
    @patch("app.github_command_handler.is_self_mention", return_value=True)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    def test_self_mention_searches_thread_then_skips(
        self, mock_comment, mock_stale, mock_self, mock_find, mock_read, notification,
    ):
        """When latest_comment_url is bot's own, search thread; skip if no mention found."""
        mock_comment.return_value = {
            "id": 555, "body": "my fix", "user": {"login": "bot"},
        }
        result = _fetch_and_filter_comment(notification, "bot", max_age_hours=24)
        assert result is None
        mock_find.assert_called_once_with(notification, "bot")
        mock_read.assert_called_once_with("7777")

    @patch("app.github_command_handler.find_mention_in_thread")
    @patch("app.github_command_handler.is_self_mention", return_value=True)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    def test_self_mention_fallback_returns_real_mention(
        self, mock_comment, mock_stale, mock_self, mock_find, notification,
    ):
        """When latest_comment_url is bot's own, but thread has an unprocessed @mention."""
        mock_comment.return_value = {
            "id": 555, "body": "my fix", "user": {"login": "bot"},
        }
        real_mention = {"id": 888, "body": "@bot rebase", "user": {"login": "alice"}}
        mock_find.return_value = real_mention
        result = _fetch_and_filter_comment(notification, "bot", max_age_hours=24)
        assert result == real_mention

    @patch("app.github_command_handler.is_self_mention", return_value=False)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    def test_valid_comment_returned(self, mock_comment, mock_stale, mock_self, notification):
        comment = {"id": 555, "body": "@bot rebase", "user": {"login": "alice"}}
        mock_comment.return_value = comment
        result = _fetch_and_filter_comment(notification, "bot", max_age_hours=24)
        assert result == comment

    @patch("app.github_command_handler.is_self_mention", return_value=False)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    def test_missing_notification_id_still_works(self, mock_comment, mock_stale, mock_self):
        """Notification with missing id field should not crash."""
        notification = {"repository": {"full_name": "o/r"}, "subject": {}}
        mock_comment.return_value = {
            "id": 1, "body": "@bot test", "user": {"login": "alice"},
        }
        result = _fetch_and_filter_comment(notification, "bot", max_age_hours=24)
        assert result is not None


# ---------------------------------------------------------------------------
# _validate_and_parse_command — direct unit tests
# ---------------------------------------------------------------------------


class TestValidateAndParseCommand:
    """Direct tests for _validate_and_parse_command helper."""

    @pytest.fixture
    def notification(self):
        return {
            "id": "8888",
            "repository": {"full_name": "sukria/koan"},
            "subject": {
                "url": "https://api.github.com/repos/sukria/koan/pulls/42",
            },
        }

    @pytest.fixture
    def config(self):
        return {"github": {"nickname": "testbot"}}

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=True)
    def test_already_processed_returns_none_tuple(
        self, mock_processed, mock_read, notification, config, registry,
    ):
        comment = {"id": 42, "url": "", "body": "@testbot rebase", "user": {"login": "a"}}
        skill, cmd, ctx = _validate_and_parse_command(
            notification, comment, config, registry, "testbot", "sukria", "koan",
        )
        assert skill is None
        assert cmd is None
        mock_read.assert_called_once_with("8888")

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler.parse_mention_command", return_value=None)
    def test_no_mention_returns_none_tuple(
        self, mock_parse, mock_processed, mock_read, notification, config, registry,
    ):
        comment = {"id": 42, "url": "", "body": "some text", "user": {"login": "a"}}
        skill, cmd, ctx = _validate_and_parse_command(
            notification, comment, config, registry, "testbot", "sukria", "koan",
        )
        assert skill is None
        assert cmd is None
        mock_read.assert_called_once_with("8888")

    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler.parse_mention_command", return_value=("rebase", ""))
    def test_valid_command_returns_skill(
        self, mock_parse, mock_processed, notification, config, registry,
    ):
        comment = {"id": 42, "url": "", "body": "@testbot rebase", "user": {"login": "a"}}
        skill, cmd, ctx = _validate_and_parse_command(
            notification, comment, config, registry, "testbot", "sukria", "koan",
        )
        assert skill is not None
        assert skill.name == "rebase"
        assert cmd == "rebase"

    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler.parse_mention_command", return_value=("badcmd", "extra"))
    def test_invalid_command_returns_none_skill_with_name(
        self, mock_parse, mock_processed, notification, config, registry,
    ):
        comment = {"id": 42, "url": "", "body": "@testbot badcmd extra", "user": {"login": "a"}}
        skill, cmd, ctx = _validate_and_parse_command(
            notification, comment, config, registry, "testbot", "sukria", "koan",
        )
        assert skill is None
        assert cmd == "badcmd"
        assert ctx == "extra"

    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler.parse_mention_command", return_value=("implement", "do it"))
    def test_context_passed_through(
        self, mock_parse, mock_processed, notification, config, registry,
    ):
        comment = {"id": 42, "url": "", "body": "@testbot implement do it", "user": {"login": "a"}}
        skill, cmd, ctx = _validate_and_parse_command(
            notification, comment, config, registry, "testbot", "sukria", "koan",
        )
        assert skill is not None
        assert cmd == "implement"
        assert ctx == "do it"


# ---------------------------------------------------------------------------
# Additional edge cases for process_single_notification
# ---------------------------------------------------------------------------


class TestProcessNotificationEdgeCases:
    """Edge cases for process_single_notification not covered by existing tests."""

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler._find_all_thread_mentions", return_value=[])
    def test_self_mention_skipped_when_no_thread_mention(
        self, mock_mentions, mock_read,
        registry, sample_notification,
    ):
        """Self-mentions with no unprocessed @mention in thread should be silently skipped."""
        success, error = process_single_notification(
            sample_notification, registry, {}, None, "bot",
        )
        assert success is False
        assert error is None

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=True)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_already_processed_skipped(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_read, registry, sample_notification,
    ):
        """Already-processed comments should be silently skipped."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99, "url": "", "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot"}}
        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )
        assert success is False
        assert error is None

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_context_aware_command_includes_context_in_mission(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """Context-aware commands should include context in mission text."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99, "body": "@testbot implement focus on API layer",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        mission_arg = mock_insert.call_args[0][1]
        assert "focus on API layer" in mission_arg
        assert "/implement" in mission_arg

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_non_context_aware_command_excludes_context(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """Non-context-aware commands should NOT include extra context."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99, "body": "@testbot rebase this needs fixing",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        mission_arg = mock_insert.call_args[0][1]
        assert "this needs fixing" not in mission_arg
        assert "/rebase" in mission_arg

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_url_in_context_overrides_notification_url(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """URL in user context should override notification's subject URL."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99,
            "body": "@testbot rebase https://github.com/other/repo/pull/7",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        mission_arg = mock_insert.call_args[0][1]
        assert "https://github.com/other/repo/pull/7" in mission_arg
        # Original notification URL should not appear
        assert "sukria/koan/pull/42" not in mission_arg

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_reaction_called_after_successful_insert(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """Reaction should only happen AFTER mission is successfully persisted."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99, "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        call_order = []
        mock_insert.side_effect = lambda *a, **kw: call_order.append("insert")
        mock_react.side_effect = lambda *a, **kw: call_order.append("react")

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert call_order == ["insert", "react"]


# ---------------------------------------------------------------------------
# Additional edge cases for post_error_reply
# ---------------------------------------------------------------------------


class TestPostErrorReplyEdgeCases:
    """Edge cases for post_error_reply."""

    def setup_method(self):
        _error_replies.clear()

    @patch("app.github.api", side_effect=RuntimeError("API error"))
    def test_api_failure_returns_false(self, mock_api):
        result = post_error_reply("owner", "repo", "42", "123", "error msg")
        assert result is False

    @patch("app.github.api", side_effect=RuntimeError("API error"))
    def test_api_failure_not_cached_as_sent(self, mock_api):
        """API failures should NOT be cached — allow retry."""
        post_error_reply("owner", "repo", "42", "123", "error msg")
        assert "123:error msg" not in _error_replies

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_comment_api_url_passed_to_reaction(self, mock_api, mock_react):
        mock_api.return_value = ""
        post_error_reply(
            "owner", "repo", "42", "123", "error",
            comment_api_url="https://api.github.com/repos/o/r/pulls/comments/123",
        )
        mock_react.assert_called_once_with(
            "owner", "repo", "123",
            comment_api_url="https://api.github.com/repos/o/r/pulls/comments/123",
        )

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_error_body_has_emoji_prefix(self, mock_api, mock_react):
        """Error reply body should start with ❌."""
        mock_api.return_value = ""
        post_error_reply("owner", "repo", "42", "123", "Test error")
        call_args = mock_api.call_args
        # Check -f body= arg
        extra_args = call_args[1].get("extra_args", call_args[0][1] if len(call_args[0]) > 1 else [])
        body_arg = [a for a in extra_args if a.startswith("body=")]
        assert body_arg
        assert body_arg[0].startswith("body=❌")

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_different_comments_same_error_not_deduplicated(self, mock_api, mock_react):
        """Same error text on different comments should not be deduplicated."""
        mock_api.return_value = ""
        post_error_reply("owner", "repo", "42", "111", "same error")
        post_error_reply("owner", "repo", "42", "222", "same error")
        assert mock_api.call_count == 2


# ---------------------------------------------------------------------------
# Additional edge cases for _extract_url_from_context
# ---------------------------------------------------------------------------


class TestExtractUrlFromContextEdgeCases:
    """Additional edge cases for URL extraction."""

    def test_url_at_beginning_of_context(self):
        result = _extract_url_from_context("https://github.com/owner/repo/pull/1 please fix")
        assert result is not None
        url, remaining = result
        assert url == "https://github.com/owner/repo/pull/1"
        assert remaining == "please fix"

    def test_url_at_end_of_context(self):
        result = _extract_url_from_context("please fix https://github.com/owner/repo/pull/1")
        assert result is not None
        url, remaining = result
        assert url == "https://github.com/owner/repo/pull/1"
        assert remaining == "please fix"

    def test_url_only(self):
        result = _extract_url_from_context("https://github.com/owner/repo/pull/1")
        assert result is not None
        url, remaining = result
        assert url == "https://github.com/owner/repo/pull/1"
        assert remaining == ""

    def test_http_url(self):
        """HTTP URLs should be extracted too."""
        result = _extract_url_from_context("fix http://github.com/owner/repo/issues/5")
        assert result is not None
        url, _ = result
        assert url == "http://github.com/owner/repo/issues/5"

    def test_multiple_urls_returns_first(self):
        text = "see https://github.com/a/b/pull/1 and https://github.com/c/d/pull/2"
        result = _extract_url_from_context(text)
        assert result is not None
        url, _ = result
        assert url == "https://github.com/a/b/pull/1"

    def test_non_github_url_ignored(self):
        result = _extract_url_from_context("check https://example.com/page")
        assert result is None

    def test_empty_string(self):
        assert _extract_url_from_context("") is None

    def test_underscore_in_repo_name(self):
        result = _extract_url_from_context("https://github.com/org/my_repo/issues/3")
        assert result is not None
        url, _ = result
        assert url == "https://github.com/org/my_repo/issues/3"

    def test_bare_repo_url_with_extra_text_ignored(self):
        """Bare repo URL with surrounding text should NOT match."""
        result = _extract_url_from_context(
            "check https://github.com/other/repo for reference"
        )
        assert result is None

    def test_repo_url_with_tree_path_ignored(self):
        """Repo URL with /tree/ or /blob/ path should NOT match."""
        result = _extract_url_from_context(
            "see https://github.com/owner/repo/tree/main/src"
        )
        assert result is None

    def test_pull_url_still_matches(self):
        """PR URL should still be extracted correctly."""
        result = _extract_url_from_context(
            "rebase https://github.com/owner/repo/pull/42 please"
        )
        assert result is not None
        url, remaining = result
        assert url == "https://github.com/owner/repo/pull/42"
        assert remaining == "rebase please"


# ---------------------------------------------------------------------------
# Additional edge cases for extract_issue_number_from_notification
# ---------------------------------------------------------------------------


class TestExtractIssueNumberEdgeCases:

    def test_commits_url_no_number(self):
        """Commit URLs should return None (no issues/pulls pattern)."""
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/commits/abc123"}}
        assert extract_issue_number_from_notification(notif) is None

    def test_empty_subject(self):
        notif = {"subject": {}}
        assert extract_issue_number_from_notification(notif) is None

    def test_large_issue_number(self):
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/issues/99999"}}
        assert extract_issue_number_from_notification(notif) == "99999"


# ---------------------------------------------------------------------------
# Additional edge cases for resolve_project_from_notification
# ---------------------------------------------------------------------------


class TestResolveProjectEdgeCases:

    def test_empty_full_name(self):
        notif = {"repository": {"full_name": ""}}
        assert resolve_project_from_notification(notif) is None

    def test_no_slash_in_full_name(self):
        notif = {"repository": {"full_name": "noowner"}}
        assert resolve_project_from_notification(notif) is None

    def test_missing_repository_key(self):
        assert resolve_project_from_notification({"id": "1"}) is None


# ---------------------------------------------------------------------------
# Telegram notification truncation boundary tests
# ---------------------------------------------------------------------------


class TestTelegramNotificationBoundaries:
    """Exact boundary tests for 200-char truncation."""

    @patch("app.notify.send_telegram", return_value=True)
    def test_question_exactly_200_chars_no_truncation(self, mock_send):
        text = "x" * 200
        _notify_github_question("alice", "o", "r", "1", text)
        msg = mock_send.call_args[0][0]
        assert "…" not in msg

    @patch("app.notify.send_telegram", return_value=True)
    def test_question_201_chars_truncated(self, mock_send):
        text = "x" * 201
        _notify_github_question("alice", "o", "r", "1", text)
        msg = mock_send.call_args[0][0]
        assert "…" in msg

    @patch("app.notify.send_telegram", return_value=True)
    def test_reply_exactly_200_chars_no_truncation(self, mock_send):
        text = "y" * 200
        _notify_github_reply("o", "r", "1", text)
        msg = mock_send.call_args[0][0]
        assert "…" not in msg

    @patch("app.notify.send_telegram", return_value=True)
    def test_reply_201_chars_truncated(self, mock_send):
        text = "y" * 201
        _notify_github_reply("o", "r", "1", text)
        msg = mock_send.call_args[0][0]
        assert "…" in msg


# ---------------------------------------------------------------------------
# _try_reply — additional edge cases
# ---------------------------------------------------------------------------


class TestTryReplyEdgeCases:

    @pytest.fixture(autouse=True)
    def _mock_rate_limiter(self):
        with patch("app.github_command_handler._load_reply_timestamps", return_value={}), \
             patch("app.github_command_handler._save_reply_timestamps"):
            yield

    @pytest.fixture
    def reply_notification(self):
        return {
            "id": "77777",
            "subject": {
                "url": "https://api.github.com/repos/sukria/koan/issues/42",
            },
            "repository": {"full_name": "sukria/koan"},
        }

    @pytest.fixture
    def reply_comment(self):
        return {
            "id": 55555,
            "body": "@bot what about this?",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/55555",
        }

    @pytest.fixture
    def reply_config(self):
        return {
            "github": {
                "nickname": "bot",
                "reply_enabled": True,
                "authorized_users": ["*"],
            }
        }

    @patch("app.github_command_handler._notify_github_question")
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.generate_reply", return_value=None)
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_question_notification_sent_before_generation(
        self, mock_resolve, mock_ctx, mock_gen, mock_perm,
        mock_notify_q,
        reply_notification, reply_comment, reply_config,
    ):
        """Question notification should be sent BEFORE generation (even if it fails)."""
        _try_reply(
            reply_notification, reply_comment, reply_config, None,
            "bot", "sukria", "koan", "koan", "question?",
        )
        mock_notify_q.assert_called_once()

    @patch("app.github_command_handler._notify_github_reply")
    @patch("app.github_command_handler._notify_github_question")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_reply.post_threaded_reply", return_value=True)
    @patch("app.github_reply.generate_reply", return_value="reply text")
    @patch("app.github_reply.fetch_thread_context", return_value={
        "title": "T", "body": "B", "comments": [], "is_pr": False, "diff_summary": "",
    })
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_eyes_reaction_used_for_replies(
        self, mock_resolve, mock_ctx, mock_gen, mock_post,
        mock_perm, mock_react, mock_read,
        mock_notify_q, mock_notify_r,
        reply_notification, reply_comment, reply_config,
    ):
        """Reply reactions should use 'eyes' emoji, not the default 'thumbsup'."""
        _try_reply(
            reply_notification, reply_comment, reply_config, None,
            "bot", "sukria", "koan", "koan", "question?",
        )
        mock_react.assert_called_once()
        assert mock_react.call_args[1].get("emoji") == "eyes"

    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.utils.resolve_project_path", return_value="/tmp/koan")
    def test_projects_config_passed_to_authorized_users(
        self, mock_resolve, mock_perm,
        reply_notification, reply_comment, reply_config,
    ):
        """projects_config should be passed through to get_github_authorized_users."""
        projects_cfg = {"koan": {"github": {"authorized_users": ["bob"]}}}
        with patch("app.github_command_handler.get_github_authorized_users") as mock_auth:
            mock_auth.return_value = ["bob"]
            with patch("app.github_command_handler.extract_issue_number_from_notification", return_value="42"):
                with patch("app.github_reply.fetch_thread_context"):
                    with patch("app.github_reply.generate_reply", return_value=None):
                        with patch("app.github_command_handler._notify_github_question"):
                            _try_reply(
                                reply_notification, reply_comment, reply_config,
                                projects_cfg, "bot", "sukria", "koan", "koan", "q",
                            )
            mock_auth.assert_called_once_with(reply_config, "koan", projects_cfg)


# ---------------------------------------------------------------------------
# build_mission_from_command — additional edge cases
# ---------------------------------------------------------------------------


class TestBuildMissionEdgeCases:

    def test_context_url_but_not_context_aware(self):
        """URL in context should still be used even if skill is not context-aware."""
        skill = Skill(
            name="rebase", scope="core", github_enabled=True,
            github_context_aware=False,
            commands=[SkillCommand(name="rebase")],
        )
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/pulls/1"}}
        mission = build_mission_from_command(
            skill, "rebase",
            "https://github.com/other/repo/pull/5 extra text",
            notif, "proj",
        )
        # URL override happens before context_aware check
        assert "https://github.com/other/repo/pull/5" in mission
        # Context text should NOT be included (not context-aware)
        assert "extra text" not in mission

    def test_empty_context_no_trailing_space(self):
        """Empty context should not add trailing spaces."""
        skill = Skill(
            name="fix", scope="core", github_enabled=True,
            github_context_aware=True,
            commands=[SkillCommand(name="fix")],
        )
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/issues/3"}}
        mission = build_mission_from_command(skill, "fix", "", notif, "myproj")
        assert mission == "- [project:myproj] /fix https://github.com/o/r/issues/3 📬"

    def test_no_web_url_and_no_context(self):
        """Mission with no URL and no context."""
        skill = Skill(
            name="check", scope="core", github_enabled=True,
            github_context_aware=False,
            commands=[SkillCommand(name="check")],
        )
        notif = {"subject": {}}
        mission = build_mission_from_command(skill, "check", "", notif, "proj")
        assert mission == "- [project:proj] /check 📬"

    @patch("app.github_command_handler._resolve_project_from_url", return_value="grep")
    def test_context_url_cross_repo_resolves_project(self, mock_resolve):
        """When context URL points to a different repo, project tag should update."""
        skill = Skill(
            name="plan", scope="core", github_enabled=True,
            github_context_aware=True,
            commands=[SkillCommand(name="plan")],
        )
        # Notification from investmindr (project: backend)
        notif = {"subject": {"url": "https://api.github.com/repos/Anantys/investmindr/pulls/100"}}
        # Context URL points to metacpan (project: grep)
        context = "https://github.com/metacpan/metacpan-grep-front-end/pull/75"
        mission = build_mission_from_command(
            skill, "plan", context, notif, "backend"
        )
        # Project should be re-resolved to grep, not stay as backend
        assert "[project:grep]" in mission
        assert "[project:backend]" not in mission
        assert "metacpan-grep-front-end/pull/75" in mission

    @patch("app.github_command_handler._resolve_project_from_url", return_value=None)
    def test_context_url_cross_repo_unknown_keeps_original(self, mock_resolve):
        """When context URL repo is unknown, keep the original project."""
        skill = Skill(
            name="plan", scope="core", github_enabled=True,
            github_context_aware=True,
            commands=[SkillCommand(name="plan")],
        )
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/pulls/1"}}
        context = "https://github.com/unknown/repo/issues/5"
        mission = build_mission_from_command(
            skill, "plan", context, notif, "original"
        )
        # Should keep original project since URL repo is unknown
        assert "[project:original]" in mission

    def test_no_context_url_keeps_original_project(self):
        """Without a context URL, project tag stays from notification."""
        skill = Skill(
            name="rebase", scope="core", github_enabled=True,
            github_context_aware=False,
            commands=[SkillCommand(name="rebase")],
        )
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/pulls/1"}}
        mission = build_mission_from_command(
            skill, "rebase", "", notif, "backend"
        )
        assert "[project:backend]" in mission


# ---------------------------------------------------------------------------
# _resolve_project_from_url
# ---------------------------------------------------------------------------


class TestResolveProjectFromUrl:
    @patch("app.utils.resolve_project_path", return_value="/path/to/grep")
    @patch("app.utils.project_name_for_path", return_value="grep")
    def test_resolves_from_pr_url(self, mock_name, mock_resolve):
        from app.github_command_handler import _resolve_project_from_url
        result = _resolve_project_from_url(
            "https://github.com/metacpan/metacpan-grep-front-end/pull/75"
        )
        assert result == "grep"
        mock_resolve.assert_called_with("metacpan-grep-front-end", owner="metacpan")

    @patch("app.utils.resolve_project_path", return_value="/path/to/proj")
    @patch("app.utils.project_name_for_path", return_value="proj")
    def test_resolves_from_issue_url(self, mock_name, mock_resolve):
        from app.github_command_handler import _resolve_project_from_url
        result = _resolve_project_from_url(
            "https://github.com/owner/myrepo/issues/42"
        )
        assert result == "proj"

    @patch("app.utils.resolve_project_path", return_value=None)
    def test_unknown_repo_returns_none(self, mock_resolve):
        from app.github_command_handler import _resolve_project_from_url
        result = _resolve_project_from_url(
            "https://github.com/unknown/repo/pull/1"
        )
        assert result is None

    def test_non_github_url_returns_none(self):
        from app.github_command_handler import _resolve_project_from_url
        assert _resolve_project_from_url("https://example.com/foo") is None

    def test_empty_string_returns_none(self):
        from app.github_command_handler import _resolve_project_from_url
        assert _resolve_project_from_url("") is None


# ---------------------------------------------------------------------------
# validate_command — additional edge cases
# ---------------------------------------------------------------------------


class TestValidateCommandEdgeCases:

    def test_empty_command_name(self, registry):
        assert validate_command("", registry) is None

    def test_case_sensitive(self, registry):
        """Command lookup should be case-sensitive."""
        assert validate_command("REBASE", registry) is None

    def test_empty_registry(self):
        reg = SkillRegistry()
        assert validate_command("rebase", reg) is None


# ---------------------------------------------------------------------------
# format_help_list_message — clean help (no "Unknown command" prefix)
# ---------------------------------------------------------------------------


class TestFormatHelpListMessage:
    def test_does_not_contain_unknown_command(self, registry):
        msg = format_help_list_message(registry, "koanbot")
        assert "Unknown command" not in msg

    def test_lists_available_commands(self, registry):
        msg = format_help_list_message(registry, "koanbot")
        assert "`@koanbot rebase`" in msg
        assert "`@koanbot implement`" in msg

    def test_includes_help_entry(self, registry):
        msg = format_help_list_message(registry, "koanbot")
        assert "`@koanbot help`" in msg

    def test_includes_usage_line(self, registry):
        msg = format_help_list_message(registry, "koanbot")
        assert "Usage:" in msg
        assert "`@koanbot <command>`" in msg

    def test_starts_with_friendly_intro(self, registry):
        msg = format_help_list_message(registry, "koanbot")
        assert msg.startswith("Here are the commands I support:")

    def test_empty_registry(self):
        reg = SkillRegistry()
        msg = format_help_list_message(reg, "koanbot")
        assert "help" in msg
        assert "Usage:" in msg

    def test_grouped_by_category(self):
        """Commands are grouped by their SKILL.md group field."""
        review = Skill(
            name="review", scope="core", group="code", emoji="🔍",
            github_enabled=True,
            commands=[SkillCommand(name="review", description="Review a PR", aliases=["rv"])],
        )
        rebase = Skill(
            name="rebase", scope="core", group="pr", emoji="🔄",
            github_enabled=True,
            commands=[SkillCommand(name="rebase", description="Rebase a PR", aliases=["rb"])],
        )
        reg = SkillRegistry()
        reg._register(review)
        reg._register(rebase)
        msg = format_help_list_message(reg, "koanbot")
        # Group headers present
        assert "### Code & Development" in msg
        assert "### Pull Requests" in msg
        # Emoji present
        assert "🔍" in msg
        assert "🔄" in msg
        # Aliases shown
        assert "`rv`" in msg
        assert "`rb`" in msg
        # Code section appears before PR section
        code_pos = msg.index("### Code & Development")
        pr_pos = msg.index("### Pull Requests")
        assert code_pos < pr_pos

    def test_integrations_group_renders(self):
        """Custom skills with group=integrations get a dedicated section."""
        custom = Skill(
            name="my_fix", scope="my_team", group="integrations", emoji="🐛",
            github_enabled=True, github_context_aware=True,
            commands=[SkillCommand(name="my_fix", description="Fix a team bug", aliases=["myfix"])],
        )
        core = Skill(
            name="rebase", scope="core", group="pr", emoji="🔄",
            github_enabled=True,
            commands=[SkillCommand(name="rebase", description="Rebase a PR")],
        )
        reg = SkillRegistry()
        reg._register(core)
        reg._register(custom)
        msg = format_help_list_message(reg, "koanbot")
        assert "### Integrations" in msg
        assert "`@koanbot my_fix`" in msg
        assert "`myfix`" in msg
        # Integrations section comes after core groups (placed last in _GROUP_LABELS).
        assert msg.index("### Pull Requests") < msg.index("### Integrations")

    def test_ungrouped_skills_go_to_other(self):
        """Skills without a recognized group appear under their group name."""
        skill = Skill(
            name="custom", scope="core", group="", github_enabled=True,
            commands=[SkillCommand(name="custom", description="A custom command")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        msg = format_help_list_message(reg, "koanbot")
        assert "`@koanbot custom`" in msg
        assert "A custom command" in msg


# ---------------------------------------------------------------------------
# _post_help_reply
# ---------------------------------------------------------------------------


class TestPostHelpReply:
    @patch("app.github.api")
    def test_posts_comment(self, mock_api):
        result = _post_help_reply("owner", "repo", "42", "Help text here")
        assert result is True
        mock_api.assert_called_once()
        call_args = mock_api.call_args
        assert "repos/owner/repo/issues/42/comments" in call_args[0][0]
        extra_args = call_args[1].get("extra_args", [])
        body_arg = [a for a in extra_args if a.startswith("body=")]
        assert body_arg
        assert "Help text here" in body_arg[0]

    @patch("app.github.api", side_effect=RuntimeError("API error"))
    def test_api_failure_returns_false(self, mock_api):
        result = _post_help_reply("owner", "repo", "42", "Help text")
        assert result is False


# ---------------------------------------------------------------------------
# _handle_help_command
# ---------------------------------------------------------------------------


class TestHandleHelpCommand:
    @pytest.fixture
    def help_notification(self):
        return {
            "id": "55555",
            "subject": {
                "url": "https://api.github.com/repos/sukria/koan/pulls/42",
            },
            "repository": {"full_name": "sukria/koan"},
        }

    @pytest.fixture
    def help_comment(self):
        return {
            "id": 77777,
            "body": "@koanbot help",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/77777",
        }

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler._post_help_reply", return_value=True)
    def test_posts_help_and_reacts(
        self, mock_post, mock_react, mock_read,
        help_notification, help_comment, registry,
    ):
        result = _handle_help_command(
            help_notification, help_comment, registry, "koanbot",
            "sukria", "koan",
        )
        assert result is True
        mock_post.assert_called_once()
        # Help message should list commands
        help_text = mock_post.call_args[0][3]
        assert "rebase" in help_text
        assert "implement" in help_text
        # Reaction should use eyes emoji
        mock_react.assert_called_once()
        assert mock_react.call_args[1].get("emoji") == "eyes"
        # Notification marked as read
        mock_read.assert_called_once_with("55555")

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler._post_help_reply", return_value=False)
    def test_post_failure_returns_false(
        self, mock_post, mock_read,
        help_notification, help_comment, registry,
    ):
        result = _handle_help_command(
            help_notification, help_comment, registry, "koanbot",
            "sukria", "koan",
        )
        assert result is False
        mock_read.assert_called_once()

    @patch("app.github_command_handler.mark_notification_read")
    def test_no_issue_number_returns_false(self, mock_read, help_comment, registry):
        notif = {"id": "55555", "subject": {}}
        result = _handle_help_command(
            notif, help_comment, registry, "koanbot", "sukria", "koan",
        )
        assert result is False
        mock_read.assert_called_once()


# ---------------------------------------------------------------------------
# process_single_notification — help command integration
# ---------------------------------------------------------------------------


class TestProcessNotificationHelpCommand:
    """Integration tests for the built-in help command in process_single_notification."""

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler._post_help_reply", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_help_command_posts_reply(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_post, mock_react, mock_read,
        registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot help",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
        }]
        config = {"github": {"nickname": "testbot"}}

        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )
        # Help does not create a mission, so success=False, error=None
        assert success is False
        assert error is None
        # Help reply was posted
        mock_post.assert_called_once()

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler._post_help_reply", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_help_command_does_not_queue_mission(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_post, mock_react, mock_read,
        registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot help",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
        }]
        config = {"github": {"nickname": "testbot"}}

        with patch("app.utils.insert_pending_mission") as mock_insert:
            process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )
            mock_insert.assert_not_called()

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler._post_help_reply", return_value=False)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_help_post_failure_still_no_error(
        self, mock_resolve, mock_mentions,
        mock_processed, mock_post, mock_read,
        registry, sample_notification,
    ):
        """Even if help reply fails to post, no error is returned to avoid error reply."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot help",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
        }]
        config = {"github": {"nickname": "testbot"}}

        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )
        assert success is False
        assert error is None


# ---------------------------------------------------------------------------
# process_single_notification — plan command via github_enabled
# ---------------------------------------------------------------------------


class TestProcessNotificationPlanCommand:
    """Test that plan works as a github_enabled command via @mention."""

    @pytest.fixture
    def plan_registry(self):
        """Registry with plan skill github_enabled."""
        plan_skill = Skill(
            name="plan",
            scope="core",
            description="Plan an idea or iterate on an existing GitHub issue",
            github_enabled=True,
            github_context_aware=True,
            commands=[SkillCommand(name="plan", description="Plan an idea or iterate on an existing GitHub issue")],
        )
        reg = SkillRegistry()
        reg._register(plan_skill)
        return reg

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_plan_queues_mission_with_url(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, plan_registry, tmp_path,
    ):
        notif = {
            "id": "44444",
            "subject": {
                "url": "https://api.github.com/repos/sukria/koan/issues/100",
            },
            "repository": {"full_name": "sukria/koan"},
        }
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 88888,
            "body": "@testbot plan",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                notif, plan_registry, config, None, "testbot",
            )

        assert success is True
        assert error is None
        mock_insert.assert_called_once()
        mission = mock_insert.call_args[0][1]
        assert "/plan" in mission
        assert "https://github.com/sukria/koan/issues/100" in mission

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_plan_includes_context(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, plan_registry, tmp_path,
    ):
        notif = {
            "id": "44444",
            "subject": {
                "url": "https://api.github.com/repos/sukria/koan/issues/100",
            },
            "repository": {"full_name": "sukria/koan"},
        }
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 88888,
            "body": "@testbot plan focus on auth module",
            "user": {"login": "alice"},
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                notif, plan_registry, config, None, "testbot",
            )

        assert success is True
        mission = mock_insert.call_args[0][1]
        assert "focus on auth module" in mission


# ---------------------------------------------------------------------------
# NLP intent classification tests
# ---------------------------------------------------------------------------


class TestTryNlpClassification:
    """Unit tests for _try_nlp_classification."""

    def test_disabled_by_default(self, registry):
        comment = {"body": "@bot fix this bug", "user": {"login": "alice"}}
        config = {"github": {"nickname": "bot"}}
        result = _try_nlp_classification(
            comment, config, None, registry, "bot", "myproject", "owner", "repo",
        )
        assert result is None

    @patch("app.github_intent.classify_intent")
    @patch("app.github_reply.extract_mention_text", return_value="fix this bug")
    @patch("app.utils.resolve_project_path", return_value="/tmp/myproject")
    def test_successful_classification(
        self, mock_resolve, mock_extract, mock_classify, registry,
    ):
        mock_classify.return_value = {"command": "implement", "context": "the auth bug"}
        comment = {"body": "@bot fix this bug", "user": {"login": "alice"}}
        config = {"github": {"nickname": "bot", "natural_language": True}}

        result = _try_nlp_classification(
            comment, config, None, registry, "bot", "myproject", "owner", "repo",
        )
        assert result is not None
        skill, cmd, ctx = result
        assert skill.name == "implement"
        assert cmd == "implement"
        assert ctx == "the auth bug"

    @patch("app.github_intent.classify_intent")
    @patch("app.github_reply.extract_mention_text", return_value="do something")
    @patch("app.utils.resolve_project_path", return_value="/tmp/myproject")
    def test_no_match_returns_none(
        self, mock_resolve, mock_extract, mock_classify, registry,
    ):
        mock_classify.return_value = {"command": None, "context": ""}
        comment = {"body": "@bot do something", "user": {"login": "alice"}}
        config = {"github": {"nickname": "bot", "natural_language": True}}

        result = _try_nlp_classification(
            comment, config, None, registry, "bot", "myproject", "owner", "repo",
        )
        assert result is None

    @patch("app.github_intent.classify_intent", return_value=None)
    @patch("app.github_reply.extract_mention_text", return_value="test")
    @patch("app.utils.resolve_project_path", return_value="/tmp/myproject")
    def test_cli_failure_returns_none(
        self, mock_resolve, mock_extract, mock_classify, registry,
    ):
        comment = {"body": "@bot test", "user": {"login": "alice"}}
        config = {"github": {"nickname": "bot", "natural_language": True}}

        result = _try_nlp_classification(
            comment, config, None, registry, "bot", "myproject", "owner", "repo",
        )
        assert result is None

    @patch("app.github_intent.classify_intent")
    @patch("app.github_reply.extract_mention_text", return_value="do status check")
    @patch("app.utils.resolve_project_path", return_value="/tmp/myproject")
    def test_classified_non_github_command_returns_none(
        self, mock_resolve, mock_extract, mock_classify,
    ):
        """If NLP classifies to a command that isn't github_enabled, return None."""
        mock_classify.return_value = {"command": "status", "context": ""}
        # Registry with only a non-github-enabled skill
        skill = Skill(
            name="status", scope="core", github_enabled=False,
            commands=[SkillCommand(name="status")],
        )
        reg = SkillRegistry()
        reg._register(skill)

        comment = {"body": "@bot do status check", "user": {"login": "alice"}}
        config = {"github": {"nickname": "bot", "natural_language": True}}

        result = _try_nlp_classification(
            comment, config, None, reg, "bot", "myproject", "owner", "repo",
        )
        assert result is None

    def test_per_project_override_disables(self, registry):
        """Per-project natural_language=false overrides global true."""
        comment = {"body": "@bot fix this", "user": {"login": "alice"}}
        config = {"github": {"nickname": "bot", "natural_language": True}}
        projects_config = {
            "projects": {
                "myproject": {
                    "path": "/tmp/myproject",
                    "github": {"natural_language": False},
                }
            }
        }

        result = _try_nlp_classification(
            comment, config, projects_config, registry, "bot",
            "myproject", "owner", "repo",
        )
        assert result is None


class TestProcessNotificationWithNLP:
    """Integration tests for NLP fallback in process_single_notification."""

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    @patch("app.github_command_handler._try_nlp_classification")
    def test_nlp_fallback_creates_mission(
        self, mock_nlp, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """NLP classification succeeds → mission is created."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot this is a bug for you, fix it",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
        }]

        # Rigid parse will produce command_name="this" which is invalid
        # NLP fallback should kick in
        impl_skill = registry.find_by_command("implement")
        mock_nlp.return_value = (impl_skill, "implement", "fix the login bug")

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        assert error is None
        mock_insert.assert_called_once()
        mission = mock_insert.call_args[0][1]
        assert "/implement" in mission

    @patch("app.github_command_handler.post_error_reply")
    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.github_command_handler._try_nlp_classification", return_value=None)
    def test_nlp_fails_falls_through_to_error(
        self, mock_nlp, mock_resolve, mock_mentions,
        mock_processed, mock_read, mock_error_reply,
        registry, sample_notification,
    ):
        """NLP returns None → help-message error returned for the caller to post."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": "99999",
            "body": "@testbot blahblah",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
        }]
        config = {"github": {"nickname": "testbot"}}

        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )

        assert success is False
        # Single-comment errors are delegated to the caller, not posted inline.
        assert error is not None
        assert "`blahblah`" in error
        mock_error_reply.assert_not_called()

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    @patch("app.github_command_handler._try_nlp_classification")
    def test_rigid_parse_takes_priority(
        self, mock_nlp, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """Rigid parse succeeds → NLP is NOT called."""
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot rebase",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
        }]
        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        mock_nlp.assert_not_called()  # NLP should not be invoked


# ---------------------------------------------------------------------------
# Phase 1: github_context_aware flag on PR-manipulation core skills
# ---------------------------------------------------------------------------


class TestContextAwareCoreSkills:
    """Verify that rebase, recreate, and refactor have github_context_aware=True.

    These tests load the actual SKILL.md files via the registry so that any
    future edit to those files will be caught immediately.
    """

    @pytest.fixture
    def core_registry(self):
        """Registry loaded from the real core skills directory."""
        from pathlib import Path

        skills_dir = Path(__file__).parent.parent / "skills" / "core"
        return SkillRegistry(skills_dir)

    @pytest.mark.parametrize("command_name", ["rebase", "recreate", "refactor", "squash"])
    def test_skill_is_context_aware(self, core_registry, command_name):
        """Each PR-manipulation skill must have github_context_aware=True."""
        skill = core_registry.find_by_command(command_name)
        assert skill is not None, f"Skill '{command_name}' not found in core registry"
        assert skill.github_context_aware is True, (
            f"Skill '{command_name}' must have github_context_aware: true in SKILL.md"
        )

    @pytest.mark.parametrize("command_name", ["rebase", "recreate", "refactor", "squash"])
    def test_context_included_in_mission(self, core_registry, command_name):
        """When context is provided, it should appear in the built mission."""
        skill = core_registry.find_by_command(command_name)
        assert skill is not None

        notif = {"subject": {"url": "https://api.github.com/repos/o/r/pulls/42"}}
        context = "please squash into one commit"
        mission = build_mission_from_command(skill, command_name, context, notif, "myproject")

        assert context in mission, (
            f"Mission for '{command_name}' should contain context text"
        )
        assert f"/{command_name}" in mission

    @pytest.mark.parametrize("command_name", ["rebase", "recreate", "refactor", "squash"])
    def test_no_context_mission_unchanged(self, core_registry, command_name):
        """Without extra context, mission format should be unchanged."""
        skill = core_registry.find_by_command(command_name)
        assert skill is not None

        notif = {"subject": {"url": "https://api.github.com/repos/o/r/pulls/42"}}
        mission = build_mission_from_command(skill, command_name, "", notif, "myproject")

        assert mission == f"- [project:myproject] /{command_name} https://github.com/o/r/pull/42 📬"


class TestExpandComboMission:
    """Tests for _expand_combo_mission — expanding /rr into /review + /rebase."""

    def test_rr_expands_to_review_and_rebase(self):
        """The /rr combo should expand into /review and /rebase missions."""
        mission = "- [project:koan] /rr https://github.com/o/r/pull/42 📬"
        result = _expand_combo_mission("rr", mission, "koan")
        assert len(result) == 2
        assert "/review https://github.com/o/r/pull/42 📬" in result[0]
        assert "/rebase https://github.com/o/r/pull/42 📬" in result[1]

    def test_reviewrebase_expands(self):
        """The /reviewrebase alias should also expand."""
        mission = "- [project:koan] /reviewrebase https://github.com/o/r/pull/42 📬"
        result = _expand_combo_mission("reviewrebase", mission, "koan")
        assert len(result) == 2
        assert "/review" in result[0]
        assert "/rebase" in result[1]

    def test_non_combo_passthrough(self):
        """Non-combo commands should return the original mission unchanged."""
        mission = "- [project:koan] /rebase https://github.com/o/r/pull/42 📬"
        result = _expand_combo_mission("rebase", mission, "koan")
        assert result == [mission]

    def test_preserves_project_tag(self):
        """Expanded missions should keep the [project:] tag."""
        mission = "- [project:myproj] /rr https://github.com/o/r/pull/42 📬"
        result = _expand_combo_mission("rr", mission, "myproj")
        for entry in result:
            assert "[project:myproj]" in entry

    def test_preserves_url_and_context(self):
        """URL and trailing markers should be preserved in expanded missions."""
        mission = "- [project:koan] /rr https://github.com/o/r/pull/42 focus on security 📬"
        result = _expand_combo_mission("rr", mission, "koan")
        for entry in result:
            assert "https://github.com/o/r/pull/42" in entry
            assert "focus on security" in entry
            assert "📬" in entry


class TestComboSkillGithubIntegration:
    """Integration test: @bot rr via GitHub @mention expands into sub-missions."""

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_rr_mention_inserts_two_sub_missions(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, tmp_path,
    ):
        """@bot rr on a PR should insert /review and /rebase, not /rr."""
        # Build a registry that includes the review_rebase skill
        from app.skills import build_registry
        registry = build_registry()

        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            "body": "@testbot rr",
            "user": {"login": "alice"},
        }]

        notification = {
            "id": "12345",
            "reason": "mention",
            "updated_at": "2026-02-11T20:00:00Z",
            "repository": {"full_name": "sukria/koan"},
            "subject": {
                "type": "PullRequest",
                "url": "https://api.github.com/repos/sukria/koan/pulls/42",
                "latest_comment_url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            },
        }

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                notification, registry, config, None, "testbot",
            )

        assert success is True
        assert error is None
        # Should have inserted TWO missions, not one
        assert mock_insert.call_count == 2
        calls = [c[0][1] for c in mock_insert.call_args_list]
        assert any("/review" in c for c in calls), f"Expected /review in {calls}"
        assert any("/rebase" in c for c in calls), f"Expected /rebase in {calls}"
        # Neither should contain /rr
        for c in calls:
            assert "/rr " not in c, f"Found unexpanded /rr in mission: {c}"

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_regular_command_still_inserts_one_mission(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, tmp_path,
    ):
        """Non-combo commands like @bot rebase should still insert exactly one mission."""
        from app.skills import build_registry
        registry = build_registry()

        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]

        notification = {
            "id": "12345",
            "reason": "mention",
            "updated_at": "2026-02-11T20:00:00Z",
            "repository": {"full_name": "sukria/koan"},
            "subject": {
                "type": "PullRequest",
                "url": "https://api.github.com/repos/sukria/koan/pulls/42",
                "latest_comment_url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
            },
        }

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                notification, registry, config, None, "testbot",
            )

        assert success is True
        assert mock_insert.call_count == 1


# ---------------------------------------------------------------------------
# _try_assignment_notification — review_requested and assign
# ---------------------------------------------------------------------------


class TestTryAssignmentNotification:
    """Tests for assignment-based notification handling."""

    @pytest.fixture
    def review_notification(self):
        return {
            "id": "77001",
            "reason": "review_requested",
            "updated_at": "2026-03-21T01:00:00Z",
            "repository": {"full_name": "sukria/koan"},
            "subject": {
                "type": "PullRequest",
                "url": "https://api.github.com/repos/sukria/koan/pulls/99",
            },
        }

    @pytest.fixture
    def assign_notification(self):
        return {
            "id": "77002",
            "reason": "assign",
            "updated_at": "2026-03-21T01:00:00Z",
            "repository": {"full_name": "sukria/koan"},
            "subject": {
                "type": "Issue",
                "url": "https://api.github.com/repos/sukria/koan/issues/55",
            },
        }

    @pytest.fixture
    def review_registry(self):
        """Registry with review and implement skills (both github_enabled)."""
        reg = SkillRegistry()
        reg._register(Skill(
            name="review",
            scope="core",
            description="Review PR",
            github_enabled=True,
            github_context_aware=True,
            commands=[SkillCommand(name="review", aliases=["rv"])],
        ))
        reg._register(Skill(
            name="implement",
            scope="core",
            description="Implement issue",
            github_enabled=True,
            github_context_aware=True,
            commands=[SkillCommand(name="implement", aliases=["impl"])],
        ))
        return reg

    def test_review_requested_queues_review_mission(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """review_requested notification queues /review <PR URL>."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"):
            result = _try_assignment_notification(
                review_notification, review_registry, {},
            )

        assert result is True
        assert review_notification[NOTIFICATION_OUTCOME_KEY] == NOTIFICATION_OUTCOME_QUEUED
        content = missions_path.read_text()
        assert "/review https://github.com/sukria/koan/pull/99" in content
        assert "[project:koan]" in content

    def test_assign_queues_implement_mission(
        self, assign_notification, review_registry, tmp_path, monkeypatch,
    ):
        """assign notification queues /implement <issue URL>."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"):
            result = _try_assignment_notification(
                assign_notification, review_registry, {},
            )

        assert result is True
        content = missions_path.read_text()
        assert "/implement https://github.com/sukria/koan/issues/55" in content
        assert "[project:koan]" in content

    def test_assign_does_not_requeue_on_comment_activity(
        self, assign_notification, review_registry, tmp_path, monkeypatch,
    ):
        """An issue has no head SHA, so assign dedups on notif_id alone. A
        bumped updated_at (someone commented on the issue) MUST NOT re-queue
        /implement, even with a cold cache and the mission out of Pending."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"):
            _try_assignment_notification(assign_notification, review_registry, {})
            # Move mission out of Pending so only the thread tracker decides.
            missions_path.write_text(
                "# Pending\n\n# In Progress\n\n"
                "- [project:koan] /implement https://github.com/sukria/koan/issues/55 \U0001f4ec\n"
                "\n# Done\n"
            )
            bumped = dict(assign_notification)
            bumped["updated_at"] = "2026-03-22T05:00:00Z"
            result = _try_assignment_notification(bumped, review_registry, {})

        assert result is True  # idempotent — handled, not failed
        assert bumped[NOTIFICATION_OUTCOME_KEY] == NOTIFICATION_OUTCOME_HANDLED_NOOP
        content = missions_path.read_text()
        assert content.count("/implement https://github.com/sukria/koan/issues/55") == 1

    def test_irrelevant_reason_returns_false(self, review_registry):
        """Notifications with non-assignment reasons are ignored."""
        notif = {"reason": "mention", "id": "1"}
        result = _try_assignment_notification(notif, review_registry, {})
        assert result is False

    def test_stale_notification_skipped(
        self, review_notification, review_registry,
    ):
        """Stale assignment notifications are marked read and skipped."""
        with patch("app.github_command_handler.is_notification_stale", return_value=True), \
             patch("app.github_command_handler.mark_notification_read") as mock_mark:
            result = _try_assignment_notification(
                review_notification, review_registry, {},
            )

        assert result is False
        mock_mark.assert_called_once()

    def test_unknown_repo_skipped(
        self, review_notification, review_registry,
    ):
        """Notifications from unknown repos are skipped without side effects.

        Marking the notification as read is a write to shared GitHub state.
        With a shared bot identity, this instance must leave foreign repos
        untouched so the sibling instance that owns the repo can process
        them on its next poll.
        """
        with patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=None), \
             patch("app.github_command_handler.mark_notification_read") as mock_mark:
            result = _try_assignment_notification(
                review_notification, review_registry, {},
            )

        assert result is False
        mock_mark.assert_not_called()

    def test_no_subject_url_skipped(self, review_registry):
        """Notifications without a subject URL are skipped."""
        notif = {
            "id": "77003",
            "reason": "review_requested",
            "updated_at": "2026-03-21T01:00:00Z",
            "repository": {"full_name": "sukria/koan"},
            "subject": {"type": "PullRequest"},
        }
        with patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.mark_notification_read") as mock_mark:
            result = _try_assignment_notification(
                notif, review_registry, {},
            )

        assert result is False
        mock_mark.assert_called_once()

    def test_command_not_github_enabled(self):
        """If the mapped command is not github_enabled, skip."""
        reg = SkillRegistry()
        reg._register(Skill(
            name="review",
            scope="core",
            description="Review PR",
            github_enabled=False,  # not enabled
            commands=[SkillCommand(name="review")],
        ))
        notif = {
            "id": "77004",
            "reason": "review_requested",
            "updated_at": "2026-03-21T01:00:00Z",
            "repository": {"full_name": "sukria/koan"},
            "subject": {"url": "https://api.github.com/repos/sukria/koan/pulls/10"},
        }
        result = _try_assignment_notification(notif, reg, {})
        assert result is False

    def test_persistent_dedup_blocks_duplicate_across_restart(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """After a /review mission has been queued once, a second call for the
        same PR head SHA MUST NOT insert a duplicate — even when the in-memory
        _notif_cache is cold (simulating a restart) AND the mission has moved
        out of Pending (so the missions.md dedup cannot fire). The persistent
        thread tracker (keyed on notif_id:head_sha) is the only thing
        protecting us. The autouse subject-info stub returns a fixed head SHA,
        so both calls compute the same key.
        """
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"):
            first = _try_assignment_notification(
                review_notification, review_registry, {},
            )
            # Simulate the runner picking up the mission: move it out of Pending
            # so the in-process missions.md dedup can no longer catch a dup.
            missions_path.write_text(
                "# Pending\n\n# In Progress\n\n"
                "- [project:koan] /review https://github.com/sukria/koan/pull/99 \U0001f4ec\n"
                "\n# Done\n"
            )
            second = _try_assignment_notification(
                review_notification, review_registry, {},
            )

        assert first is True
        assert second is True  # idempotent — handled, not failed
        assert review_notification[NOTIFICATION_OUTCOME_KEY] == NOTIFICATION_OUTCOME_HANDLED_NOOP
        content = missions_path.read_text()
        # Exactly one mission line for this URL across the whole file
        assert content.count("/review https://github.com/sukria/koan/pull/99") == 1

    def test_bumped_updated_at_does_not_requeue_review(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """A bumped updated_at with the SAME head SHA MUST NOT re-queue /review.

        This is the regression guard for the runaway-review loop: the bot's own
        posted review (and CI-bot comments) bump the PR's updated_at, but they
        do not change the head SHA, so no fresh mission must be created.
        """
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"):
            _try_assignment_notification(review_notification, review_registry, {})
            # Move first mission out of Pending so the in-flight dedup
            # doesn't fire — only the thread tracker decides here.
            missions_path.write_text(
                "# Pending\n\n# In Progress\n\n"
                "- [project:koan] /review https://github.com/sukria/koan/pull/99 \U0001f4ec\n"
                "\n# Done\n"
            )
            renewed = dict(review_notification)
            renewed["updated_at"] = "2026-03-22T05:00:00Z"
            result = _try_assignment_notification(renewed, review_registry, {})

        assert result is True  # idempotent — handled, not failed
        content = missions_path.read_text()
        assert content.count("/review https://github.com/sukria/koan/pull/99") == 1

    def test_new_head_sha_queues_new_review(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """A new PR head SHA (commits pushed) MUST queue a fresh /review — the
        head SHA is the renew signal that replaced updated_at.

        The review-cooldown guard is bypassed here (patched to False) because
        this test is focused on the head-SHA dedup tracker, not the cooldown
        interaction. Cooldown behavior is covered by dedicated tests.
        """
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_command_handler._fetch_subject_info",
                   return_value={"state": "open", "merged": False, "head_sha": "sha-aaaa"}), \
             patch("app.github_notification_tracker.is_review_on_cooldown", return_value=False):
            _try_assignment_notification(review_notification, review_registry, {})
            # Move first mission out of Pending so only the tracker decides.
            missions_path.write_text(
                "# Pending\n\n# In Progress\n\n"
                "- [project:koan] /review https://github.com/sukria/koan/pull/99 \U0001f4ec\n"
                "\n# Done\n"
            )

        # New commits pushed → new head SHA → fresh dedup key → re-review.
        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_command_handler._fetch_subject_info",
                   return_value={"state": "open", "merged": False, "head_sha": "sha-bbbb"}), \
             patch("app.github_notification_tracker.is_review_on_cooldown", return_value=False):
            result = _try_assignment_notification(
                review_notification, review_registry, {},
            )

        assert result is True
        content = missions_path.read_text()
        assert content.count("/review https://github.com/sukria/koan/pull/99") == 2

    def test_empty_notif_id_skips_tracker_to_avoid_useless_key(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """If notification.id is missing, the composite key would be useless
        (a ':<updated_at>' record never matches future polls). The mission
        must still queue, but track_thread MUST NOT be called with a junk key.
        """
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        notif = dict(review_notification)
        notif["id"] = ""  # malformed: no id

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_notification_tracker.track_thread") as mock_track:
            result = _try_assignment_notification(notif, review_registry, {})

        assert result is True
        content = missions_path.read_text()
        assert "/review https://github.com/sukria/koan/pull/99" in content
        mock_track.assert_not_called()

    def test_assignment_reason_mapping(self):
        """Verify the reason-to-command mapping."""
        assert _ASSIGNMENT_REASON_TO_COMMAND["review_requested"] == "review"
        assert _ASSIGNMENT_REASON_TO_COMMAND["assign"] == "implement"

    def test_process_single_notification_routes_review_requested(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """process_single_notification routes review_requested to assignment handler."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler._find_all_thread_mentions", return_value=[]), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"):
            success, error = process_single_notification(
                review_notification, review_registry, {}, None, "koan-bot",
            )

        assert success is True
        assert error is None
        content = missions_path.read_text()
        assert "/review" in content

    def test_process_single_notification_routes_assign(
        self, assign_notification, review_registry, tmp_path, monkeypatch,
    ):
        """process_single_notification routes assign to assignment handler."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler._find_all_thread_mentions", return_value=[]), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"):
            success, error = process_single_notification(
                assign_notification, review_registry, {}, None, "koan-bot",
            )

        assert success is True
        assert error is None
        content = missions_path.read_text()
        assert "/implement" in content

    def test_review_requested_on_cooldown_skipped(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """A review_requested notification for a PR on cooldown is skipped."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read") as mock_read, \
             patch("app.github_notification_tracker.is_review_on_cooldown", return_value=True):
            result = _try_assignment_notification(
                review_notification, review_registry, {},
            )

        assert result is True
        assert review_notification[NOTIFICATION_OUTCOME_KEY] == NOTIFICATION_OUTCOME_HANDLED_NOOP
        mock_read.assert_called_once()
        # No mission should have been queued
        content = missions_path.read_text()
        assert "/review" not in content

    def test_review_requested_sets_cooldown_after_queue(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """After queuing a review mission, the cooldown is recorded."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_notification_tracker.set_review_cooldown") as mock_cd:
            result = _try_assignment_notification(
                review_notification, review_registry, {},
            )

        assert result is True
        mock_cd.assert_called_once()
        assert mock_cd.call_args[0][1:] == ("sukria", "koan", "99")

    def test_assign_does_not_set_review_cooldown(
        self, assign_notification, review_registry, tmp_path, monkeypatch,
    ):
        """Non-review assignments must not touch the review cooldown tracker."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_notification_tracker.set_review_cooldown") as mock_cd:
            result = _try_assignment_notification(
                assign_notification, review_registry, {},
            )

        assert result is True
        mock_cd.assert_not_called()

    def test_review_requested_cooldown_bypassed_when_bot_still_requested(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """A review re-request bypasses cooldown when bot is in requested_reviewers."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        config = {"github": {"nickname": "koan-bot"}}

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_notification_tracker.is_review_on_cooldown", return_value=True), \
             patch("app.github_command_handler._is_bot_still_requested", return_value=True), \
             patch("app.github_notification_tracker.clear_review_cooldown") as mock_clear:
            result = _try_assignment_notification(
                review_notification, review_registry, config,
            )

        assert result is True
        assert review_notification[NOTIFICATION_OUTCOME_KEY] == NOTIFICATION_OUTCOME_QUEUED
        mock_clear.assert_called_once()
        content = missions_path.read_text()
        assert "/review" in content

    def test_review_requested_cooldown_not_bypassed_when_bot_not_requested(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """Cooldown blocks when bot is NOT in requested_reviewers."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        config = {"github": {"nickname": "koan-bot"}}

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read") as mock_read, \
             patch("app.github_notification_tracker.is_review_on_cooldown", return_value=True), \
             patch("app.github_command_handler._is_bot_still_requested", return_value=False):
            result = _try_assignment_notification(
                review_notification, review_registry, config,
            )

        assert result is True
        assert review_notification[NOTIFICATION_OUTCOME_KEY] == NOTIFICATION_OUTCOME_HANDLED_NOOP
        mock_read.assert_called_once()
        content = missions_path.read_text()
        assert "/review" not in content

    def test_review_requested_cooldown_no_bypass_without_nickname(
        self, review_notification, review_registry, tmp_path, monkeypatch,
    ):
        """Without github.nickname configured, cooldown is not bypassed."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        missions_path = tmp_path / "instance" / "missions.md"
        missions_path.parent.mkdir(parents=True)
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        config = {}

        with patch("app.github_command_handler.resolve_project_from_notification",
                    return_value=("koan", "sukria", "koan")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read") as mock_read, \
             patch("app.github_notification_tracker.is_review_on_cooldown", return_value=True):
            result = _try_assignment_notification(
                review_notification, review_registry, config,
            )

        assert result is True
        assert review_notification[NOTIFICATION_OUTCOME_KEY] == NOTIFICATION_OUTCOME_HANDLED_NOOP


class TestIsBotStillRequested:
    """Tests for _is_bot_still_requested — requested_reviewers API check."""

    def test_bot_in_requested_reviewers(self):
        with patch("app.github.api", return_value="koan-bot\nother-user"):
            assert _is_bot_still_requested("owner", "repo", "42", "koan-bot") is True

    def test_bot_not_in_requested_reviewers(self):
        with patch("app.github.api", return_value="other-user\nsomeone-else"):
            assert _is_bot_still_requested("owner", "repo", "42", "koan-bot") is False

    def test_empty_reviewers_list(self):
        with patch("app.github.api", return_value=""):
            assert _is_bot_still_requested("owner", "repo", "42", "koan-bot") is False

    def test_case_insensitive_match(self):
        with patch("app.github.api", return_value="Koan-Bot"):
            assert _is_bot_still_requested("owner", "repo", "42", "koan-bot") is True

    def test_api_failure_returns_false(self):
        with patch("app.github.api", side_effect=RuntimeError("network")):
            assert _is_bot_still_requested("owner", "repo", "42", "koan-bot") is False

    def test_no_bot_username_returns_false(self):
        assert _is_bot_still_requested("owner", "repo", "42", "") is False


class TestFetchSubjectInfo:
    """Tests for _fetch_subject_info — the single network seam for subject state.

    These call the real helper directly (the module-top import binding is not
    affected by the autouse stub, which only replaces the module attribute), so
    the GitHub API call is exercised through a mocked ``app.github.api``.
    """

    def test_parses_state_merged_and_head_sha(self):
        notification = {
            "subject": {
                "url": "https://api.github.com/repos/owner/repo/pulls/1",
            },
        }
        with patch("app.github.api",
                    return_value='{"state": "open", "merged": false, "head_sha": "abc123"}'):
            info = _fetch_subject_info(notification)
        assert info == {"state": "open", "merged": False, "head_sha": "abc123"}

    def test_issue_has_null_head_sha(self):
        notification = {
            "subject": {
                "url": "https://api.github.com/repos/owner/repo/issues/1",
            },
        }
        with patch("app.github.api",
                    return_value='{"state": "open", "merged": null, "head_sha": null}'):
            info = _fetch_subject_info(notification)
        assert info["head_sha"] is None

    def test_returns_empty_on_api_failure(self):
        notification = {
            "subject": {
                "url": "https://api.github.com/repos/owner/repo/pulls/1",
            },
        }
        with patch("app.github.api", side_effect=RuntimeError("network")):
            assert _fetch_subject_info(notification) == {}

    def test_returns_empty_when_no_subject_url(self):
        assert _fetch_subject_info({"subject": {}}) == {}
        assert _fetch_subject_info({}) == {}

    def test_returns_empty_for_non_api_url(self):
        notification = {
            "subject": {"url": "https://github.com/owner/repo/pull/1"},
        }
        assert _fetch_subject_info(notification) == {}


class TestClosedReasonFromSubjectInfo:
    """Tests for _closed_reason_from_subject_info — pure derivation, no network."""

    def test_merged_takes_priority(self):
        assert _closed_reason_from_subject_info(
            {"state": "closed", "merged": True},
        ) == "merged"

    def test_closed_state(self):
        assert _closed_reason_from_subject_info(
            {"state": "closed", "merged": False},
        ) == "closed"

    def test_open_state(self):
        assert _closed_reason_from_subject_info(
            {"state": "open", "merged": False},
        ) is None

    def test_empty_info(self):
        assert _closed_reason_from_subject_info({}) is None


class TestIsSubjectClosed:
    """_is_subject_closed composes the fetch + the closed-reason derivation."""

    def test_merged(self):
        with patch(
            "app.github_command_handler._fetch_subject_info",
            return_value={"state": "closed", "merged": True, "head_sha": "x"},
        ):
            assert _is_subject_closed(
                {"subject": {"url": "https://api.github.com/repos/o/r/pulls/1"}},
            ) == "merged"

    def test_open(self):
        with patch(
            "app.github_command_handler._fetch_subject_info",
            return_value={"state": "open", "merged": False, "head_sha": "x"},
        ):
            assert _is_subject_closed(
                {"subject": {"url": "https://api.github.com/repos/o/r/pulls/1"}},
            ) is None

    def test_unfetchable_subject_is_open(self):
        with patch(
            "app.github_command_handler._fetch_subject_info",
            return_value={},
        ):
            assert _is_subject_closed({"subject": {}}) is None


class TestNotifyClosedSubjectSkipped:
    """Tests for _notify_closed_subject_skipped helper."""

    def test_sends_telegram_notification(self):
        notification = {
            "subject": {
                "type": "PullRequest",
                "url": "https://api.github.com/repos/owner/repo/pulls/42",
            },
        }
        with patch("app.notify.send_telegram") as mock_send:
            _notify_closed_subject_skipped(
                "owner", "repo", "Fix bug", "merged", notification,
            )
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "merged" in msg
            assert "owner/repo" in msg
            assert "Fix bug" in msg
            assert "pullrequest" in msg.lower()

    def test_handles_send_failure_gracefully(self):
        notification = {"subject": {"type": "Issue", "url": ""}}
        with patch("app.notify.send_telegram",
                    side_effect=Exception("boom")):
            # Should not raise
            _notify_closed_subject_skipped(
                "owner", "repo", "Title", "closed", notification,
            )


class TestProcessSingleNotificationClosedSubject:
    """Tests for closed/merged subject detection in process_single_notification."""

    @pytest.fixture
    def closed_pr_notification(self):
        return {
            "id": "99999",
            "reason": "mention",
            "updated_at": "2026-05-13T12:00:00Z",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "type": "PullRequest",
                "title": "Some closed PR",
                "url": "https://api.github.com/repos/owner/repo/pulls/10",
                "latest_comment_url": "https://api.github.com/repos/owner/repo/issues/comments/555",
            },
        }

    def test_skips_closed_pr_and_notifies(
        self, closed_pr_notification, registry, monkeypatch,
    ):
        """Notifications on closed PRs are skipped with Telegram notification."""
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")

        comment = {
            "id": "555",
            "url": "https://api.github.com/repos/owner/repo/issues/comments/555",
            "body": "@bot rebase",
            "user": {"login": "someone"},
        }

        with patch("app.github_command_handler._find_all_thread_mentions",
                    return_value=[comment]), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._is_subject_closed",
                   return_value="merged") as mock_closed, \
             patch("app.github_command_handler._notify_closed_subject_skipped") as mock_notify, \
             patch("app.github_command_handler.add_reaction") as mock_react, \
             patch("app.github_command_handler.mark_notification_read") as mock_read:

            success, error = process_single_notification(
                closed_pr_notification, registry, {}, None, "bot",
            )

        assert success is False
        assert error is None
        mock_closed.assert_called_once()
        mock_notify.assert_called_once_with(
            "owner", "repo", "Some closed PR", "merged", closed_pr_notification,
        )
        mock_react.assert_called_once_with(
            "owner", "repo", "555", emoji="eyes",
            comment_api_url="https://api.github.com/repos/owner/repo/issues/comments/555",
        )
        mock_read.assert_called_once()

    def test_does_not_skip_open_pr(
        self, closed_pr_notification, registry, monkeypatch,
    ):
        """Notifications on open PRs proceed normally."""
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")

        comment = {
            "id": "555",
            "url": "https://api.github.com/repos/owner/repo/issues/comments/555",
            "body": "@bot rebase",
            "user": {"login": "someone"},
        }

        with patch("app.github_command_handler._find_all_thread_mentions",
                    return_value=[comment]), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._is_subject_closed",
                   return_value=None), \
             patch("app.github_command_handler._validate_and_parse_command",
                   return_value=(None, None, "")), \
             patch("app.github_command_handler._notify_closed_subject_skipped") as mock_notify:

            success, error = process_single_notification(
                closed_pr_notification, registry, {}, None, "bot",
            )

        # Should NOT have called the skip notification
        mock_notify.assert_not_called()


class TestTryAssignmentNotificationClosedSubject:
    """Tests for closed subject detection in _try_assignment_notification."""

    @pytest.fixture
    def subject_closed_state(self):
        # Drives the autouse _fetch_subject_info stub to report a merged PR.
        return "merged"

    def test_skips_review_request_on_merged_pr(self, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")
        notification = {
            "id": "77777",
            "reason": "review_requested",
            "updated_at": "2026-05-13T12:00:00Z",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "type": "PullRequest",
                "title": "Merged PR",
                "url": "https://api.github.com/repos/owner/repo/pulls/5",
            },
        }
        registry = SkillRegistry()
        skill = Skill(
            name="review", scope="core", description="Review",
            commands=[SkillCommand(name="review")],
            github_enabled=True,
        )
        registry._register(skill)

        with patch("app.github_command_handler.is_notification_stale",
                    return_value=False), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._notify_closed_subject_skipped") as mock_notify, \
             patch("app.github_command_handler.mark_notification_read"):

            result = _try_assignment_notification(notification, registry, {})

        assert result is False
        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][3] == "merged"  # subject_state


class TestSkipIfForeignRepo:
    """_skip_if_foreign_repo centralizes the resolve-or-log boilerplate."""

    def test_returns_project_info_for_owned_repo(self):
        from app.github_command_handler import _skip_if_foreign_repo

        notif = {"repository": {"full_name": "owner/repo"}}
        with patch(
            "app.github_command_handler.resolve_project_from_notification",
            return_value=("repo", "owner", "repo"),
        ):
            result = _skip_if_foreign_repo(notif, "GitHub")

        assert result == ("repo", "owner", "repo")

    def test_logs_and_returns_none_for_foreign_repo(self, caplog):
        import logging
        from app.github_command_handler import _skip_if_foreign_repo

        notif = {
            "repository": {"full_name": "stranger/repo"},
            "reason": "mention",
        }
        with patch(
            "app.github_command_handler.resolve_project_from_notification",
            return_value=None,
        ), caplog.at_level(logging.DEBUG, logger="app.github_command_handler"):
            result = _skip_if_foreign_repo(notif, "GitHub assign")

        assert result is None
        assert "GitHub assign" in caplog.text
        assert "stranger/repo" in caplog.text
        assert "reason=mention" in caplog.text


# ---------------------------------------------------------------------------
# --now priority flag in GitHub @mentions
# ---------------------------------------------------------------------------


class TestNowPriorityFlag:
    """Tests that --now in GitHub @mention context inserts missions at top of queue."""

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_now_flag_passes_urgent_true(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        mock_resolve.return_value = ("koan", "owner", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot implement --now https://github.com/owner/repo/issues/42",
            "user": {"login": "alice"},
        }]

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        assert error is None
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        assert kwargs.get("urgent") is True

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_without_now_flag_urgent_is_false(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        mock_resolve.return_value = ("koan", "owner", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }]

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        assert kwargs.get("urgent") is False

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler._find_all_thread_mentions")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_now_flag_stripped_from_mission_entry(
        self, mock_insert, mock_resolve, mock_mentions,
        mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        """--now should not appear in the queued mission text."""
        mock_resolve.return_value = ("koan", "owner", "koan")
        mock_mentions.return_value = [{
            "id": 99999,
            "body": "@testbot implement --now https://github.com/owner/repo/issues/42",
            "user": {"login": "alice"},
        }]

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        call_args = mock_insert.call_args[0]
        mission_text = call_args[1]  # second positional arg is the entry
        assert "--now" not in mission_text


class TestLoadReplyTimestamps:
    """Tests for _load_reply_timestamps — file-backed rate-limit state."""

    def test_returns_empty_when_file_missing(self, tmp_path):
        result = _load_reply_timestamps(str(tmp_path))
        assert result == {}

    def test_returns_empty_on_corrupt_json(self, tmp_path):
        (tmp_path / _REPLY_RATE_FILE).write_text("not json")
        result = _load_reply_timestamps(str(tmp_path))
        assert result == {}

    def test_strips_stale_entries(self, tmp_path):
        import time
        now = time.time()
        data = {"alice": [now - 7200, now - 30, now - 10]}
        (tmp_path / _REPLY_RATE_FILE).write_text(json.dumps(data))
        result = _load_reply_timestamps(str(tmp_path))
        assert len(result["alice"]) == 2
        assert all(t > now - 3600 for t in result["alice"])

    def test_removes_user_with_only_stale_entries(self, tmp_path):
        import time
        now = time.time()
        data = {"alice": [now - 7200, now - 3700]}
        (tmp_path / _REPLY_RATE_FILE).write_text(json.dumps(data))
        result = _load_reply_timestamps(str(tmp_path))
        assert "alice" not in result

    def test_ignores_non_numeric_timestamps(self, tmp_path):
        import time
        now = time.time()
        data = {"alice": ["bad", now - 10, None]}
        (tmp_path / _REPLY_RATE_FILE).write_text(json.dumps(data))
        result = _load_reply_timestamps(str(tmp_path))
        assert len(result["alice"]) == 1


class TestSaveReplyTimestamps:
    """Tests for _save_reply_timestamps — atomic file write."""

    def test_saves_and_round_trips(self, tmp_path):
        import time
        now = time.time()
        data = {"alice": [now - 30, now - 10]}
        _save_reply_timestamps(str(tmp_path), data)
        loaded = _load_reply_timestamps(str(tmp_path))
        assert len(loaded["alice"]) == 2


class TestWarnReplyWildcard:
    """Tests for startup warning when reply + wildcard are configured."""

    def test_warns_on_wildcard_reply_auth(self, caplog):
        import logging
        from app.github_config import warn_reply_wildcard
        config = {
            "github": {
                "reply_enabled": True,
                "reply_authorized_users": ["*"],
            }
        }
        with caplog.at_level(logging.WARNING, logger="app.github_config"):
            warn_reply_wildcard(config)
        assert "authorized to ALL" in caplog.text

    def test_no_warning_when_reply_disabled(self, caplog):
        import logging
        from app.github_config import warn_reply_wildcard
        config = {"github": {"reply_enabled": False, "reply_authorized_users": ["*"]}}
        with caplog.at_level(logging.WARNING, logger="app.github_config"):
            warn_reply_wildcard(config)
        assert caplog.text == ""

    def test_no_warning_with_named_users(self, caplog):
        import logging
        from app.github_config import warn_reply_wildcard
        config = {
            "github": {
                "reply_enabled": True,
                "reply_authorized_users": ["alice"],
            }
        }
        with caplog.at_level(logging.WARNING, logger="app.github_config"):
            warn_reply_wildcard(config)
        assert caplog.text == ""

    def test_warns_on_fallback_wildcard(self, caplog):
        import logging
        from app.github_config import warn_reply_wildcard
        config = {
            "github": {
                "reply_enabled": True,
                "authorized_users": ["*"],
            }
        }
        with caplog.at_level(logging.WARNING, logger="app.github_config"):
            warn_reply_wildcard(config)
        assert "authorized to ALL" in caplog.text


class TestMultiMentionPerThread:
    """Tests for processing multiple @mentions from a single notification."""

    @pytest.fixture
    def registry(self):
        registry = SkillRegistry()
        review_skill = Skill(
            name="review", scope="core", description="Review",
            commands=[SkillCommand(name="review")],
            github_enabled=True, group="pr",
        )
        rebase_skill = Skill(
            name="rebase", scope="core", description="Rebase",
            commands=[SkillCommand(name="rebase")],
            github_enabled=True, group="pr",
        )
        registry._register(review_skill)
        registry._register(rebase_skill)
        return registry

    @pytest.fixture
    def notification(self):
        return {
            "id": "99",
            "reason": "mention",
            "updated_at": "2026-06-18T10:05:00Z",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "type": "PullRequest",
                "title": "My PR",
                "url": "https://api.github.com/repos/owner/repo/pulls/42",
                "latest_comment_url": "https://api.github.com/repos/owner/repo/issues/comments/202",
            },
        }

    def test_multiple_mentions_all_queued_in_order(
        self, notification, registry, tmp_path, monkeypatch,
    ):
        """Multiple @mentions on the same thread are all queued in chronological order."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(parents=True)
        missions_path = instance_dir / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        comment_review = {
            "id": "201",
            "url": "https://api.github.com/repos/owner/repo/issues/comments/201",
            "body": "@bot review",
            "user": {"login": "alice"},
            "created_at": "2026-06-18T10:00:00Z",
        }
        comment_rebase = {
            "id": "202",
            "url": "https://api.github.com/repos/owner/repo/issues/comments/202",
            "body": "@bot rebase",
            "user": {"login": "alice"},
            "created_at": "2026-06-18T10:01:00Z",
        }

        with patch("app.github_command_handler._find_all_thread_mentions",
                    return_value=[comment_review, comment_rebase]), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._is_subject_closed", return_value=None), \
             patch("app.github_command_handler.check_already_processed", return_value=False), \
             patch("app.github_command_handler.check_user_permission", return_value=True), \
             patch("app.github_command_handler.add_reaction"), \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_command_handler.api_url_to_web_url",
                   return_value="https://github.com/owner/repo/pull/42"), \
             patch("app.github_command_handler.get_github_nickname", return_value="bot"), \
             patch("app.github_command_handler.get_github_ack_enabled", return_value=False):
            success, error = process_single_notification(
                notification, registry, {}, None, "bot",
            )

        assert success is True
        assert error is None
        content = missions_path.read_text()
        assert "/review" in content
        assert "/rebase" in content
        # Verify chronological order: /review appears before /rebase in Pending
        review_pos = content.index("/review")
        rebase_pos = content.index("/rebase")
        assert review_pos < rebase_pos, "review should be queued before rebase (chronological)"
        assert notification.get(NOTIFICATION_OUTCOME_KEY) == NOTIFICATION_OUTCOME_QUEUED

    def test_single_mention_still_works(
        self, notification, registry, tmp_path, monkeypatch,
    ):
        """A single @mention still works correctly (no regression)."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(parents=True)
        missions_path = instance_dir / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        comment = {
            "id": "201",
            "url": "https://api.github.com/repos/owner/repo/issues/comments/201",
            "body": "@bot review",
            "user": {"login": "alice"},
            "created_at": "2026-06-18T10:00:00Z",
        }

        with patch("app.github_command_handler._find_all_thread_mentions",
                    return_value=[comment]), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._is_subject_closed", return_value=None), \
             patch("app.github_command_handler.check_already_processed", return_value=False), \
             patch("app.github_command_handler.check_user_permission", return_value=True), \
             patch("app.github_command_handler.add_reaction"), \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_command_handler.api_url_to_web_url",
                   return_value="https://github.com/owner/repo/pull/42"), \
             patch("app.github_command_handler.get_github_nickname", return_value="bot"), \
             patch("app.github_command_handler.get_github_ack_enabled", return_value=False):
            success, error = process_single_notification(
                notification, registry, {}, None, "bot",
            )

        assert success is True
        assert error is None
        content = missions_path.read_text()
        assert "/review" in content

    def test_empty_mentions_falls_through_to_assignment(
        self, notification, registry, tmp_path, monkeypatch,
    ):
        """No @mentions falls through to assignment/subscription paths."""
        notification["reason"] = "review_requested"
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(parents=True)
        missions_path = instance_dir / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        with patch("app.github_command_handler._find_all_thread_mentions", return_value=[]), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.mark_notification_read"):
            success, error = process_single_notification(
                notification, registry, {}, None, "bot",
            )

        assert success is True
        content = missions_path.read_text()
        assert "/review" in content

    def test_find_all_thread_mentions_returns_sorted(self):
        """_find_all_thread_mentions returns comments sorted by created_at."""
        comment_a = {"id": "1", "body": "@bot review", "user": {"login": "x"},
                     "created_at": "2026-06-18T10:02:00Z"}
        comment_b = {"id": "2", "body": "@bot rebase", "user": {"login": "x"},
                     "created_at": "2026-06-18T10:00:00Z"}
        comment_c = {"id": "3", "body": "@bot fix", "user": {"login": "x"},
                     "created_at": "2026-06-18T10:01:00Z"}

        notification = {
            "id": "77",
            "updated_at": "2026-06-18T10:05:00Z",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "url": "https://api.github.com/repos/owner/repo/pulls/5",
            },
        }

        with patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.find_all_mentions_in_thread",
                   return_value=[comment_b, comment_c, comment_a]):
            result = _find_all_thread_mentions(notification, "bot")

        assert len(result) == 3
        assert result[0]["id"] == "2"
        assert result[1]["id"] == "3"
        assert result[2]["id"] == "1"

    def test_fallback_warns_on_malformed_full_name(self, caplog):
        """Fallback path warns when repository.full_name is malformed."""
        notification = {
            "id": "77",
            "updated_at": "2026-06-18T10:05:00Z",
            "repository": {"full_name": "no-slash"},
            "subject": {
                "url": "https://api.github.com/repos/owner/repo/pulls/5",
                "latest_comment_url": "https://api.github.com/repos/owner/repo/issues/comments/1",
            },
        }

        comment = {
            "id": "1", "body": "@bot review",
            "user": {"login": "alice"},
            "url": "https://api.github.com/repos/owner/repo/issues/comments/1",
        }

        import logging
        with patch("app.github_command_handler.is_notification_stale", return_value=False), \
             patch("app.github_command_handler.find_all_mentions_in_thread", return_value=[]), \
             patch("app.github_command_handler.get_comment_from_notification", return_value=comment), \
             patch("app.github_command_handler.is_self_mention", return_value=False), \
             caplog.at_level(logging.WARNING):
            result = _find_all_thread_mentions(notification, "bot")

        assert result == []
        assert any("malformed repository.full_name" in r.message for r in caplog.records)

    def test_closed_subject_reacts_to_all_comments(
        self, notification, registry, monkeypatch,
    ):
        """All comments get an eyes reaction when subject is closed."""
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")

        comments = [
            {"id": "201", "url": "https://api.github.com/repos/o/r/issues/comments/201",
             "body": "@bot review", "user": {"login": "alice"}},
            {"id": "202", "url": "https://api.github.com/repos/o/r/issues/comments/202",
             "body": "@bot rebase", "user": {"login": "alice"}},
        ]

        with patch("app.github_command_handler._find_all_thread_mentions",
                    return_value=comments), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._is_subject_closed",
                   return_value="merged"), \
             patch("app.github_command_handler._notify_closed_subject_skipped"), \
             patch("app.github_command_handler.add_reaction") as mock_react, \
             patch("app.github_command_handler.mark_notification_read"):
            success, error = process_single_notification(
                notification, registry, {}, None, "bot",
            )

        assert success is False
        assert mock_react.call_count == 2

    def test_multiple_mentions_accumulates_koan_commands(
        self, notification, registry, tmp_path, monkeypatch,
    ):
        """Multiple @mentions accumulate _koan_commands list for Telegram reporting."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(parents=True)
        missions_path = instance_dir / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        comment_review = {
            "id": "201",
            "url": "https://api.github.com/repos/owner/repo/issues/comments/201",
            "body": "@bot review",
            "user": {"login": "alice"},
            "created_at": "2026-06-18T10:00:00Z",
        }
        comment_rebase = {
            "id": "202",
            "url": "https://api.github.com/repos/owner/repo/issues/comments/202",
            "body": "@bot rebase",
            "user": {"login": "bob"},
            "created_at": "2026-06-18T10:01:00Z",
        }

        with patch("app.github_command_handler._find_all_thread_mentions",
                    return_value=[comment_review, comment_rebase]), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._is_subject_closed", return_value=None), \
             patch("app.github_command_handler.check_already_processed", return_value=False), \
             patch("app.github_command_handler.check_user_permission", return_value=True), \
             patch("app.github_command_handler.add_reaction"), \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_command_handler.api_url_to_web_url",
                   return_value="https://github.com/owner/repo/pull/42"), \
             patch("app.github_command_handler.get_github_nickname", return_value="bot"), \
             patch("app.github_command_handler.get_github_ack_enabled", return_value=False):
            process_single_notification(
                notification, registry, {}, None, "bot",
            )

        cmds = notification.get("_koan_commands", [])
        assert len(cmds) == 2
        assert cmds[0] == {"command": "review", "author": "alice"}
        assert cmds[1] == {"command": "rebase", "author": "bob"}

    def test_single_errored_comment_delegated_not_posted_inline(
        self, notification, registry, monkeypatch,
    ):
        """A single errored comment is delegated to the caller's retry path
        (via the returned error), not posted inline — so it is posted exactly
        once, never doubly. The comment is still durably tracked."""
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")

        comment = {
            "id": "303",
            "url": "https://api.github.com/repos/o/r/issues/comments/303",
            "body": "@bot badcmd", "user": {"login": "alice"},
        }

        with patch("app.github_command_handler._find_all_thread_mentions",
                    return_value=[comment]), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._is_subject_closed",
                   return_value=None), \
             patch("app.github_command_handler._process_mention_comment",
                   return_value=(False, "Unknown command badcmd")), \
             patch("app.github_reply._enforce_reply_budget", return_value=True), \
             patch("app.github_command_handler.extract_issue_number_from_notification",
                   return_value="99"), \
             patch("app.github_command_handler.post_error_reply") as mock_err, \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_notification_tracker.track_comment") as mock_track:
            success, error = process_single_notification(
                notification, registry, {}, None, "bot",
            )

        # Not posted inline (the caller owns the single-comment post); the
        # error is returned for the caller to post once, and the comment is
        # durably tracked regardless.
        mock_err.assert_not_called()
        assert success is False
        assert error == "Unknown command badcmd"
        mock_track.assert_any_call("/tmp/test-koan/instance", "303")

    def test_multi_errored_comments_posted_inline(
        self, notification, registry, monkeypatch,
    ):
        """Each errored comment in a multi-mention thread gets its own inline
        error reply (the caller's single-error return path can't cover them),
        and all are durably tracked."""
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")

        comments = [
            {"id": "501", "url": "https://api.github.com/repos/o/r/issues/comments/501",
             "body": "@bot badcmd", "user": {"login": "alice"}},
            {"id": "502", "url": "https://api.github.com/repos/o/r/issues/comments/502",
             "body": "@bot worsecmd", "user": {"login": "bob"}},
        ]

        with patch("app.github_command_handler._find_all_thread_mentions",
                    return_value=comments), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._is_subject_closed",
                   return_value=None), \
             patch("app.github_command_handler._process_mention_comment",
                   return_value=(False, "Unknown command")), \
             patch("app.github_reply._enforce_reply_budget", return_value=True), \
             patch("app.github_command_handler.extract_issue_number_from_notification",
                   return_value="99"), \
             patch("app.github_command_handler.post_error_reply") as mock_err, \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_notification_tracker.track_comment") as mock_track:
            success, error = process_single_notification(
                notification, registry, {}, None, "bot",
            )

        # One inline reply per errored comment; nothing delegated to the caller
        # (multi-comment returns no single error).
        assert mock_err.call_count == 2
        assert success is False
        assert error is None
        mock_track.assert_any_call("/tmp/test-koan/instance", "501")
        mock_track.assert_any_call("/tmp/test-koan/instance", "502")

    def test_multi_errored_comments_suppressed_when_breaker_tripped(
        self, notification, registry, monkeypatch,
    ):
        """When the per-thread breaker is tripped, inline error replies for a
        multi-mention thread are suppressed, but the comments are still
        durably tracked."""
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")

        comments = [
            {"id": "404", "url": "https://api.github.com/repos/o/r/issues/comments/404",
             "body": "@bot badcmd", "user": {"login": "alice"}},
            {"id": "405", "url": "https://api.github.com/repos/o/r/issues/comments/405",
             "body": "@bot worsecmd", "user": {"login": "bob"}},
        ]

        with patch("app.github_command_handler._find_all_thread_mentions",
                    return_value=comments), \
             patch("app.github_command_handler.resolve_project_from_notification",
                   return_value=("myproject", "owner", "repo")), \
             patch("app.github_command_handler._is_subject_closed",
                   return_value=None), \
             patch("app.github_command_handler._process_mention_comment",
                   return_value=(False, "Unknown command badcmd")), \
             patch("app.github_reply._enforce_reply_budget", return_value=False), \
             patch("app.github_command_handler.extract_issue_number_from_notification",
                   return_value="99"), \
             patch("app.github_command_handler.post_error_reply") as mock_err, \
             patch("app.github_command_handler.mark_notification_read"), \
             patch("app.github_notification_tracker.track_comment") as mock_track:
            process_single_notification(notification, registry, {}, None, "bot")

        mock_err.assert_not_called()
        mock_track.assert_any_call("/tmp/test-koan/instance", "404")
        mock_track.assert_any_call("/tmp/test-koan/instance", "405")
