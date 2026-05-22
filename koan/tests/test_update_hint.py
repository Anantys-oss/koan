"""Tests for app.update_hint — upstream update notification with 48 h cooldown."""

import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def instance_dir(tmp_path):
    """Provide a temp instance directory."""
    return str(tmp_path)


@pytest.fixture
def koan_root(tmp_path):
    """Provide a temp koan root (distinct from instance)."""
    root = tmp_path / "koan-repo"
    root.mkdir()
    return str(root)


class TestCooldown:
    """Cooldown file reading/writing."""

    def test_no_state_file_means_not_in_cooldown(self, tmp_path):
        from app.update_hint import _is_within_cooldown
        assert _is_within_cooldown(tmp_path / ".update-hint.json") is False

    def test_recent_timestamp_means_in_cooldown(self, tmp_path):
        from app.update_hint import _is_within_cooldown, _write_last_notified
        state = tmp_path / ".update-hint.json"
        _write_last_notified(state)
        assert _is_within_cooldown(state) is True

    def test_old_timestamp_means_not_in_cooldown(self, tmp_path):
        from app.update_hint import _is_within_cooldown, _HINT_INTERVAL_SECONDS
        state = tmp_path / ".update-hint.json"
        old = datetime.now(timezone.utc) - timedelta(seconds=_HINT_INTERVAL_SECONDS + 100)
        state.write_text(json.dumps({"last_notified_at": old.isoformat()}))
        assert _is_within_cooldown(state) is False

    def test_corrupt_state_file_means_not_in_cooldown(self, tmp_path):
        from app.update_hint import _is_within_cooldown
        state = tmp_path / ".update-hint.json"
        state.write_text("not json")
        assert _is_within_cooldown(state) is False

    def test_empty_state_file_means_not_in_cooldown(self, tmp_path):
        from app.update_hint import _is_within_cooldown
        state = tmp_path / ".update-hint.json"
        state.write_text("{}")
        assert _is_within_cooldown(state) is False


class TestFormatMessage:
    """Message formatting."""

    def test_single_commit(self):
        from app.update_hint import _format_update_message
        msg = _format_update_message(["abc1234 fix: something"])
        assert "1 new commit" in msg
        assert "abc1234 fix: something" in msg
        assert "/update" in msg
        # No plural
        assert "commits" not in msg

    def test_multiple_commits(self):
        from app.update_hint import _format_update_message
        commits = [f"abc{i:04d} commit {i}" for i in range(5)]
        msg = _format_update_message(commits)
        assert "5 new commits" in msg
        assert "abc0000" in msg
        assert "abc0004" in msg

    def test_truncates_at_20(self):
        from app.update_hint import _format_update_message
        commits = [f"abc{i:04d} commit {i}" for i in range(25)]
        msg = _format_update_message(commits)
        assert "and 5 more" in msg
        assert "abc0019" in msg  # last shown
        assert "abc0020" not in msg  # truncated

    def test_unicode_prefix(self):
        from app.update_hint import _format_update_message
        msg = _format_update_message(["abc fix"])
        assert "\u2b06\ufe0f" in msg  # ⬆️


class TestMaybeSendUpdateHint:
    """Integration: the public maybe_send_update_hint() function."""

    @patch("app.update_hint.send_telegram", return_value=True)
    @patch("app.update_hint._get_missing_commits", return_value=["abc1 fix: thing"])
    @patch("app.update_hint.find_upstream_remote", return_value="origin")
    @patch("app.update_hint.check_for_updates", return_value=3)
    def test_sends_when_behind_and_no_cooldown(
        self, mock_check, mock_remote, mock_commits, mock_send,
        instance_dir, koan_root,
    ):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is True
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "abc1 fix: thing" in msg

        # State file written
        state = Path(instance_dir) / ".update-hint.json"
        assert state.exists()

    @patch("app.update_hint.check_for_updates", return_value=3)
    def test_skips_when_in_cooldown(self, mock_check, instance_dir, koan_root):
        from app.update_hint import maybe_send_update_hint, _write_last_notified
        _write_last_notified(Path(instance_dir) / ".update-hint.json")
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False
        mock_check.assert_not_called()

    @patch("app.update_hint.check_for_updates", return_value=0)
    def test_skips_when_up_to_date(self, mock_check, instance_dir, koan_root):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False

    @patch("app.update_hint.check_for_updates", return_value=None)
    def test_skips_on_check_error(self, mock_check, instance_dir, koan_root):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False

    @patch("app.update_hint.find_upstream_remote", return_value=None)
    @patch("app.update_hint.check_for_updates", return_value=5)
    def test_skips_when_no_remote(self, mock_check, mock_remote, instance_dir, koan_root):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False

    @patch("app.update_hint._get_missing_commits", return_value=[])
    @patch("app.update_hint.find_upstream_remote", return_value="upstream")
    @patch("app.update_hint.check_for_updates", return_value=2)
    def test_skips_when_no_commit_subjects(
        self, mock_check, mock_remote, mock_commits,
        instance_dir, koan_root,
    ):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False

    @patch("app.update_hint.send_telegram", side_effect=RuntimeError("network"))
    @patch("app.update_hint._get_missing_commits", return_value=["abc fix"])
    @patch("app.update_hint.find_upstream_remote", return_value="origin")
    @patch("app.update_hint.check_for_updates", return_value=1)
    def test_returns_false_on_send_failure(
        self, mock_check, mock_remote, mock_commits, mock_send,
        instance_dir, koan_root,
    ):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False
        # State file NOT written on failure
        state = Path(instance_dir) / ".update-hint.json"
        assert not state.exists()

    @patch("app.update_hint.check_for_updates", side_effect=Exception("fetch failed"))
    def test_returns_false_on_check_exception(self, mock_check, instance_dir, koan_root):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False

    @patch("app.update_hint.find_upstream_remote", side_effect=Exception("git broken"))
    @patch("app.update_hint.check_for_updates", return_value=5)
    def test_returns_false_on_remote_exception(
        self, mock_check, mock_remote, instance_dir, koan_root,
    ):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False


class TestNaiveDatetimeCooldown:
    """Cover the naive-datetime branch in _is_within_cooldown (line 58)."""

    def test_naive_timestamp_treated_as_utc(self, tmp_path):
        from app.update_hint import _is_within_cooldown
        state = tmp_path / ".update-hint.json"
        # Write a naive (no timezone) ISO timestamp — should be treated as UTC.
        recent = datetime.now(timezone.utc).replace(tzinfo=None)
        state.write_text(json.dumps({"last_notified_at": recent.isoformat()}))
        assert _is_within_cooldown(state) is True

    def test_naive_old_timestamp_not_in_cooldown(self, tmp_path):
        from app.update_hint import _is_within_cooldown, _HINT_INTERVAL_SECONDS
        state = tmp_path / ".update-hint.json"
        old = datetime.now(timezone.utc) - timedelta(seconds=_HINT_INTERVAL_SECONDS + 100)
        old_naive = old.replace(tzinfo=None)
        state.write_text(json.dumps({"last_notified_at": old_naive.isoformat()}))
        assert _is_within_cooldown(state) is False


class TestGetMissingCommits:
    """Cover _get_missing_commits (lines 68-82)."""

    @patch("app.update_hint.subprocess.run")
    def test_returns_commit_list(self, mock_run, tmp_path):
        from app.update_hint import _get_missing_commits
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="abc123 fix: first\ndef456 feat: second\n",
        )
        result = _get_missing_commits(tmp_path, "upstream")
        assert result == ["abc123 fix: first", "def456 feat: second"]

    @patch("app.update_hint.subprocess.run")
    def test_returns_empty_when_up_to_date(self, mock_run, tmp_path):
        from app.update_hint import _get_missing_commits
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = _get_missing_commits(tmp_path, "origin")
        assert result == []

    @patch("app.update_hint.subprocess.run")
    def test_returns_none_on_nonzero_exit(self, mock_run, tmp_path):
        from app.update_hint import _get_missing_commits
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        result = _get_missing_commits(tmp_path, "origin")
        assert result is None

    @patch("app.update_hint.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 15))
    def test_returns_none_on_timeout(self, mock_run, tmp_path):
        from app.update_hint import _get_missing_commits
        result = _get_missing_commits(tmp_path, "origin")
        assert result is None

    @patch("app.update_hint.subprocess.run", side_effect=OSError("no git"))
    def test_returns_none_on_oserror(self, mock_run, tmp_path):
        from app.update_hint import _get_missing_commits
        result = _get_missing_commits(tmp_path, "origin")
        assert result is None

    @patch("app.update_hint.subprocess.run")
    def test_strips_blank_lines(self, mock_run, tmp_path):
        from app.update_hint import _get_missing_commits
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\n  abc123 fix: thing  \n\n  def456 feat: other \n\n",
        )
        result = _get_missing_commits(tmp_path, "upstream")
        assert result == ["abc123 fix: thing", "def456 feat: other"]
