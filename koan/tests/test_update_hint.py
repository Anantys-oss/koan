"""Tests for app.update_hint — upstream update notification with 48 h cooldown (tag-based)."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

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


class TestFormatTagMessage:
    """Message formatting for tag-based hints."""

    def test_includes_tag_name(self):
        from app.update_hint import _format_tag_update_message
        msg = _format_tag_update_message("v1.5.0")
        assert "v1.5.0" in msg
        assert "/update" in msg

    def test_includes_update_arrow(self):
        from app.update_hint import _format_tag_update_message
        msg = _format_tag_update_message("v2.0.0")
        assert "⬆️" in msg  # ⬆️


class TestMaybeSendUpdateHint:
    """Integration: the public maybe_send_update_hint() function."""

    @patch("app.update_hint.send_telegram", return_value=True)
    @patch("app.update_hint.check_for_new_release_tag", return_value="v1.5.0")
    @patch("app.update_hint.check_for_updates", return_value=3)
    def test_sends_when_new_tag_and_no_cooldown(
        self, mock_check, mock_tag, mock_send,
        instance_dir, koan_root,
    ):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is True
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "v1.5.0" in msg
        assert "/update" in msg

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

    @patch("app.update_hint.check_for_new_release_tag", return_value=None)
    @patch("app.update_hint.check_for_updates", return_value=3)
    def test_skips_when_no_new_tag(self, mock_check, mock_tag, instance_dir, koan_root):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False

    @patch("app.update_hint.check_for_new_release_tag", return_value=None)
    @patch("app.update_hint.check_for_updates", return_value=0)
    def test_skips_when_no_commits_no_tag(self, mock_check, mock_tag, instance_dir, koan_root):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False

    @patch("app.update_hint.check_for_updates", return_value=None)
    def test_skips_on_check_error(self, mock_check, instance_dir, koan_root):
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        assert result is False

    @patch("app.update_hint.send_telegram", side_effect=RuntimeError("network"))
    @patch("app.update_hint.check_for_new_release_tag", return_value="v2.0.0")
    @patch("app.update_hint.check_for_updates", return_value=1)
    def test_returns_false_on_send_failure(
        self, mock_check, mock_tag, mock_send,
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

    @patch("app.update_hint.send_telegram", return_value=True)
    @patch("app.update_hint.check_for_new_release_tag", return_value="v3.0.0")
    @patch("app.update_hint.check_for_updates", return_value=None)
    def test_still_checks_tag_even_if_check_returns_none(
        self, mock_check, mock_tag, mock_send,
        instance_dir, koan_root,
    ):
        """check_for_updates returning None means fetch failed — skip."""
        from app.update_hint import maybe_send_update_hint
        result = maybe_send_update_hint(instance_dir, koan_root)
        # check_for_updates returning None is treated as error, bail early
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
