"""Tests for the shutdown_manager module and /shutdown skill."""

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.shutdown_manager import (
    request_shutdown,
    is_shutdown_requested,
    clear_shutdown,
    SHUTDOWN_FILE,
)


# ---------------------------------------------------------------------------
# request_shutdown tests
# ---------------------------------------------------------------------------


class TestRequestShutdown:
    def test_creates_shutdown_file(self, tmp_path):
        request_shutdown(str(tmp_path))
        assert (tmp_path / SHUTDOWN_FILE).exists()

    def test_shutdown_file_contains_timestamp(self, tmp_path):
        before = int(time.time())
        request_shutdown(str(tmp_path))
        content = (tmp_path / SHUTDOWN_FILE).read_text().strip()
        after = int(time.time())
        ts = int(content)
        assert before <= ts <= after

    def test_overwrites_existing_file(self, tmp_path):
        shutdown_file = tmp_path / SHUTDOWN_FILE
        shutdown_file.write_text("old content")
        request_shutdown(str(tmp_path))
        content = shutdown_file.read_text()
        assert "old content" not in content


# ---------------------------------------------------------------------------
# is_shutdown_requested tests
# ---------------------------------------------------------------------------


class TestIsShutdownRequested:
    def test_returns_false_when_no_file(self, tmp_path):
        assert is_shutdown_requested(str(tmp_path), time.time()) is False

    def test_returns_true_when_file_after_start(self, tmp_path):
        """Shutdown requested after process started → valid."""
        start_time = int(time.time()) - 10
        (tmp_path / SHUTDOWN_FILE).write_text(str(int(time.time())))
        assert is_shutdown_requested(str(tmp_path), start_time) is True

    def test_returns_false_when_file_before_start(self, tmp_path):
        """Shutdown requested before process started → stale, ignored."""
        old_time = int(time.time()) - 100
        (tmp_path / SHUTDOWN_FILE).write_text(str(old_time))
        start_time = int(time.time())
        assert is_shutdown_requested(str(tmp_path), start_time) is False

    def test_stale_file_gets_cleaned_up(self, tmp_path):
        """Stale shutdown file is removed automatically."""
        old_time = int(time.time()) - 100
        (tmp_path / SHUTDOWN_FILE).write_text(str(old_time))
        start_time = int(time.time())
        is_shutdown_requested(str(tmp_path), start_time)
        assert not (tmp_path / SHUTDOWN_FILE).exists()

    def test_returns_false_on_empty_file(self, tmp_path):
        """Empty or corrupt file → treated as invalid."""
        (tmp_path / SHUTDOWN_FILE).write_text("")
        assert is_shutdown_requested(str(tmp_path), 0) is False

    def test_returns_false_on_non_numeric_file(self, tmp_path):
        """Non-numeric content → treated as invalid."""
        (tmp_path / SHUTDOWN_FILE).write_text("not-a-number")
        assert is_shutdown_requested(str(tmp_path), 0) is False

    def test_exact_start_time_match(self, tmp_path):
        """Shutdown at exactly process start time → valid (>=)."""
        ts = int(time.time())
        (tmp_path / SHUTDOWN_FILE).write_text(str(ts))
        assert is_shutdown_requested(str(tmp_path), ts) is True

    def test_float_start_time(self, tmp_path):
        """Float start_time (from time.time()) is handled correctly."""
        ts = int(time.time())
        (tmp_path / SHUTDOWN_FILE).write_text(str(ts))
        assert is_shutdown_requested(str(tmp_path), float(ts) - 1.5) is True


# ---------------------------------------------------------------------------
# clear_shutdown tests
# ---------------------------------------------------------------------------


class TestClearShutdown:
    def test_removes_shutdown_file(self, tmp_path):
        (tmp_path / SHUTDOWN_FILE).write_text("123")
        clear_shutdown(str(tmp_path))
        assert not (tmp_path / SHUTDOWN_FILE).exists()

    def test_no_error_when_file_missing(self, tmp_path):
        """Clearing a non-existent file should not raise."""
        clear_shutdown(str(tmp_path))  # no error


# ---------------------------------------------------------------------------
# /shutdown skill handler tests
# ---------------------------------------------------------------------------


class TestShutdownSkillHandler:
    def test_handler_creates_shutdown_file(self, tmp_path):
        from types import SimpleNamespace
        from importlib import import_module

        handler_mod = import_module("skills.core.shutdown.handler")
        ctx = SimpleNamespace(koan_root=tmp_path)
        result = handler_mod.handle(ctx)

        assert (tmp_path / SHUTDOWN_FILE).exists()
        assert "Shutdown requested" in result

    def test_handler_returns_confirmation_message(self, tmp_path):
        from types import SimpleNamespace
        from importlib import import_module

        handler_mod = import_module("skills.core.shutdown.handler")
        ctx = SimpleNamespace(koan_root=tmp_path)
        result = handler_mod.handle(ctx)

        assert "agent loop" in result.lower()
        assert "bridge" in result.lower()


# ---------------------------------------------------------------------------
# Integration lifecycle tests
# ---------------------------------------------------------------------------


class TestShutdownLifecycle:
    def test_request_then_check(self, tmp_path):
        """Full lifecycle: request → check → clear."""
        start_time = int(time.time()) - 1
        request_shutdown(str(tmp_path))
        assert is_shutdown_requested(str(tmp_path), start_time) is True
        clear_shutdown(str(tmp_path))
        assert is_shutdown_requested(str(tmp_path), start_time) is False

    def test_stale_request_ignored_by_new_process(self, tmp_path):
        """Old shutdown file doesn't kill a newly started process."""
        request_shutdown(str(tmp_path))
        # Simulate new process starting later
        future_start = int(time.time()) + 10
        assert is_shutdown_requested(str(tmp_path), future_start) is False

    def test_multiple_processes_same_signal(self, tmp_path):
        """Both processes should see the same valid shutdown signal."""
        start_time = int(time.time()) - 1
        request_shutdown(str(tmp_path))
        # Both processes check independently
        assert is_shutdown_requested(str(tmp_path), start_time) is True
        assert is_shutdown_requested(str(tmp_path), start_time) is True

    def test_redelivered_shutdown_cleared_after_first_poll(self, tmp_path):
        """Simulate: bridge restarts → Telegram re-delivers /shutdown → cleared.

        The re-delivered /shutdown message creates a fresh .koan-shutdown
        file (timestamp > startup_time). After the first poll, awake.py
        clears it, so the shutdown check finds nothing.
        """
        startup_time = time.time()

        # Re-delivered /shutdown creates a fresh file (as the handler would)
        request_shutdown(str(tmp_path))

        # At this point, is_shutdown_requested would return True
        assert is_shutdown_requested(str(tmp_path), startup_time) is True

        # But awake.py clears it after the first poll
        request_shutdown(str(tmp_path))  # re-create (handler runs again)
        clear_shutdown(str(tmp_path))     # awake.py first-poll cleanup

        # Now the check returns False — process survives
        assert is_shutdown_requested(str(tmp_path), startup_time) is False

    def test_legitimate_shutdown_after_first_poll(self, tmp_path):
        """A real /shutdown sent AFTER first poll should still work."""
        startup_time = time.time()

        # First poll: re-delivered message cleared
        clear_shutdown(str(tmp_path))

        # Later: user sends a new /shutdown
        time.sleep(0.01)  # ensure distinct timestamp
        request_shutdown(str(tmp_path))

        # This one should be honored
        assert is_shutdown_requested(str(tmp_path), startup_time) is True


class TestSignalLockUsage:
    """Test that shutdown operations use signal_lock for cross-process safety."""

    def test_request_shutdown_uses_signal_lock(self, tmp_path):
        from unittest.mock import patch

        from app.shutdown_manager import request_shutdown

        with patch("app.utils.signal_lock") as mock_lock:
            request_shutdown(str(tmp_path))
            assert mock_lock.call_count == 1
            lock_path = str(mock_lock.call_args_list[0][0][0])
            assert lock_path.endswith(".koan-shutdown")

    def test_clear_shutdown_uses_signal_lock(self, tmp_path):
        from unittest.mock import patch

        from app.shutdown_manager import clear_shutdown

        with patch("app.utils.signal_lock") as mock_lock:
            clear_shutdown(str(tmp_path))
            assert mock_lock.call_count == 1
            lock_path = str(mock_lock.call_args_list[0][0][0])
            assert lock_path.endswith(".koan-shutdown")

    def test_lock_file_created_by_request_shutdown(self, tmp_path):
        from app.shutdown_manager import request_shutdown

        request_shutdown(str(tmp_path))
        assert (tmp_path / ".koan-shutdown").exists()
