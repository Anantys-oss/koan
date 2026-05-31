"""Tests for /check_notifications skill and signal-based notification bypass."""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.signals import CHECK_NOTIFICATIONS_FILE


# --- Signal file tests ---


class TestConsumeCheckNotificationsSignal:
    """Test _consume_check_notifications_signal."""

    def test_returns_true_and_removes_file_when_present(self, tmp_path):
        from app.loop_manager import _consume_check_notifications_signal

        signal = tmp_path / CHECK_NOTIFICATIONS_FILE
        signal.write_text("test")
        assert _consume_check_notifications_signal(str(tmp_path)) is True
        assert not signal.exists()

    def test_returns_false_when_absent(self, tmp_path):
        from app.loop_manager import _consume_check_notifications_signal

        assert _consume_check_notifications_signal(str(tmp_path)) is False


# --- Throttle bypass tests ---


class TestForceNotificationCheck:
    """Test that force=True bypasses throttle in process_*_notifications."""

    @patch("app.loop_manager._load_github_config")
    @patch("app.loop_manager._build_skill_registry")
    @patch("app.loop_manager._get_known_repos_from_projects")
    @patch("app.utils.load_config")
    @patch("app.github_notifications.mark_notification_read")
    def test_github_force_bypasses_throttle(
        self, mock_mark, mock_config, mock_repos, mock_registry, mock_gh_config, tmp_path
    ):
        """force=True should bypass the throttle even if we just checked."""
        from app.github_notifications import FetchResult
        from app.loop_manager import process_github_notifications, reset_github_backoff

        reset_github_backoff()

        mock_config.return_value = {}
        mock_gh_config.return_value = {"bot_username": "bot", "max_age": 24}
        mock_registry.return_value = MagicMock()
        mock_repos.return_value = set()

        with patch("app.projects_config.load_projects_config", return_value={}), \
             patch("app.github_notifications.fetch_unread_notifications",
                   return_value=FetchResult([], [])) as mock_fetch:
            # First call (normal) — should proceed
            process_github_notifications(str(tmp_path), str(tmp_path))
            assert mock_fetch.call_count == 1

            # Second call without force — should be throttled
            result = process_github_notifications(str(tmp_path), str(tmp_path))
            assert result == 0
            assert mock_fetch.call_count == 1  # not called again

            # Third call with force — should bypass throttle
            process_github_notifications(str(tmp_path), str(tmp_path), force=True)
            assert mock_fetch.call_count == 2

    @patch("app.loop_manager._load_github_config")
    @patch("app.loop_manager._build_skill_registry")
    @patch("app.loop_manager._get_known_repos_from_projects")
    @patch("app.utils.load_config")
    @patch("app.github_notifications.mark_notification_read")
    def test_github_force_resets_since_timestamp(
        self, mock_mark, mock_config, mock_repos, mock_registry, mock_gh_config, tmp_path
    ):
        """force=True should reset _last_github_check_iso so the cold-start window is used.

        Without this, /check_notifications after passive mode polls with since=now and
        misses notifications posted during the passive period.
        """
        import app.loop_manager as lm
        from app.github_notifications import FetchResult
        from app.loop_manager import process_github_notifications, reset_github_backoff

        reset_github_backoff()

        mock_config.return_value = {}
        mock_gh_config.return_value = {"bot_username": "bot", "max_age": 24}
        mock_registry.return_value = MagicMock()
        mock_repos.return_value = set()

        with patch("app.projects_config.load_projects_config", return_value={}), \
             patch("app.github_notifications.fetch_unread_notifications",
                   return_value=FetchResult([], [])):
            # Normal call sets _last_github_check_iso to now
            process_github_notifications(str(tmp_path), str(tmp_path))

        with lm._github_state_lock:
            assert lm._last_github_check_iso != ""

        # Advance time to bypass throttle, then force=True
        with lm._github_state_lock:
            lm._last_github_check = 0

        with patch("app.projects_config.load_projects_config", return_value={}), \
             patch("app.github_notifications.fetch_unread_notifications",
                   return_value=FetchResult([], [])) as mock_fetch:
            captured_since: list = []

            def capture_since(*args, **kwargs):
                captured_since.append(kwargs.get("since"))
                return FetchResult([], [])

            mock_fetch.side_effect = capture_since
            process_github_notifications(str(tmp_path), str(tmp_path), force=True)

        # The since value used must be further back than "just now" — confirming
        # the cold-start window was used (not the recent _last_github_check_iso).
        from datetime import datetime, timedelta, timezone
        assert len(captured_since) == 1
        since_dt = datetime.strptime(captured_since[0], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        # Cold-start uses now - max_age_hours (24h); stale window was a few ms ago.
        # If _last_github_check_iso was NOT reset, since would be close to now and
        # the assertion below would fail.
        assert since_dt < datetime.now(timezone.utc) - timedelta(hours=1)

    @patch("app.loop_manager._load_github_config")
    @patch("app.loop_manager._build_skill_registry")
    @patch("app.loop_manager._get_known_repos_from_projects")
    @patch("app.utils.load_config")
    @patch("app.github_notifications.mark_notification_read")
    def test_github_force_resets_backoff(
        self, mock_mark, mock_config, mock_repos, mock_registry, mock_gh_config, tmp_path
    ):
        """force=True should reset the exponential backoff counter."""
        from app.loop_manager import (
            process_github_notifications,
            reset_github_backoff,
            _get_effective_check_interval,
            _GITHUB_CHECK_INTERVAL,
        )
        from app.github_notifications import FetchResult

        reset_github_backoff()

        mock_config.return_value = {}
        mock_gh_config.return_value = {"bot_username": "bot", "max_age": 24}
        mock_registry.return_value = MagicMock()
        mock_repos.return_value = set()

        with patch("app.projects_config.load_projects_config", return_value={}), \
             patch("app.github_notifications.fetch_unread_notifications",
                   return_value=FetchResult([], [])):
            # Do a few normal calls to build up backoff
            process_github_notifications(str(tmp_path), str(tmp_path))

        # Backoff should have increased
        import app.loop_manager as lm
        with lm._github_state_lock:
            assert lm._consecutive_empty_checks > 0

        # Force check should reset backoff
        with patch("app.projects_config.load_projects_config", return_value={}), \
             patch("app.github_notifications.fetch_unread_notifications",
                   return_value=FetchResult([], [])):
            process_github_notifications(str(tmp_path), str(tmp_path), force=True)

        with lm._github_state_lock:
            # After force + empty result, counter is back to 1 (the new empty check)
            # but not the previously accumulated value
            assert lm._consecutive_empty_checks == 1


# --- Skill handler tests ---


class TestCheckNotificationsHandler:
    """Test the /check_notifications skill handler."""

    def test_handler_creates_signal_file(self, tmp_path):
        from importlib import import_module

        handler_mod = import_module("skills.core.check_notifications.handler")

        ctx = MagicMock()
        ctx.koan_root = tmp_path

        result = handler_mod.handle(ctx)

        signal_path = tmp_path / CHECK_NOTIFICATIONS_FILE
        assert signal_path.exists()
        assert "requested at" in signal_path.read_text()
        assert "🔔" in result

    def test_handler_returns_error_on_write_failure(self, tmp_path):
        from importlib import import_module

        handler_mod = import_module("skills.core.check_notifications.handler")

        ctx = MagicMock()
        ctx.koan_root = Path("/nonexistent/path/that/should/fail")

        result = handler_mod.handle(ctx)

        assert "Failed" in result


# --- Integration: interruptible_sleep signal detection ---


class TestInterruptibleSleepForceCheck:
    """Test that interruptible_sleep detects the check-notifications signal."""

    @patch("app.loop_manager.process_jira_notifications", return_value=0)
    @patch("app.loop_manager.process_github_notifications", return_value=0)
    @patch("app.loop_manager._drain_ci_queue_during_sleep")
    @patch("app.loop_manager.check_pending_missions", return_value=False)
    @patch("app.health_check.write_run_heartbeat")
    @patch("app.feature_tips.maybe_send_feature_tip")
    @patch("app.heartbeat.run_stale_mission_check")
    @patch("app.heartbeat.run_disk_space_check")
    def test_signal_passed_as_force_to_both_providers(
        self, _disk, _stale, _tips, _hb, _missions, _ci, mock_gh, mock_jira, tmp_path
    ):
        """When signal file exists, force=True should be passed to both checks."""
        from app.loop_manager import interruptible_sleep

        # Create the signal file
        signal = tmp_path / CHECK_NOTIFICATIONS_FILE
        signal.write_text("test")

        # Run with very short interval so it completes quickly
        interruptible_sleep(1, str(tmp_path), str(tmp_path), check_interval=1)

        # Both should have been called with force=True at least once
        force_calls_gh = [c for c in mock_gh.call_args_list if c.kwargs.get("force")]
        force_calls_jira = [c for c in mock_jira.call_args_list if c.kwargs.get("force")]
        assert len(force_calls_gh) >= 1
        assert len(force_calls_jira) >= 1

        # Signal file should have been consumed
        assert not signal.exists()
