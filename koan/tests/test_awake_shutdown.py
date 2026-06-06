"""Tests for awake.py shutdown signal integration.

Specifically tests the fix for the bug where Telegram re-delivers the
/shutdown message on bridge restart, causing the bridge to immediately
shut down again (infinite shutdown loop).

Root cause: Telegram's getUpdates with no offset re-delivers old messages.
The /shutdown handler creates a fresh .koan-shutdown file each time.
Fix: clear the shutdown file after the first poll (same pattern as restart).
"""

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.shutdown_manager import (
    request_shutdown,
    is_shutdown_requested,
    clear_shutdown,
    SHUTDOWN_FILE,
)


class TestShutdownCommandRouting:
    """Tests for /shutdown command routing in handle_command."""

    @patch("app.command_handlers._dispatch_skill")
    def test_shutdown_routes_to_skill(self, mock_dispatch):
        """/shutdown should be dispatched via the skill system."""
        from app.command_handlers import handle_command
        handle_command("/shutdown")
        mock_dispatch.assert_called_once()

    @patch("app.command_handlers._dispatch_skill")
    def test_shutdown_is_not_a_core_command(self, mock_dispatch):
        """/shutdown goes through skill dispatch, not hardcoded handlers."""
        from app.command_handlers import CORE_COMMANDS
        assert "shutdown" not in CORE_COMMANDS


class TestShutdownRedeliveryFix:
    """Tests for the Telegram message re-delivery bug fix.

    Scenario:
    1. User sends /shutdown → awake.py exits
    2. User restarts awake.py
    3. Telegram re-delivers /shutdown message (offset not persisted)
    4. Handler creates fresh .koan-shutdown file
    5. After first poll, awake.py clears the file
    6. Shutdown check finds nothing → process survives
    """

    def test_clear_after_first_poll_neutralizes_redelivery(self, tmp_path):
        """First-poll clear prevents re-delivered /shutdown from killing the bridge."""
        startup_time = time.time()

        # Simulate: re-delivered /shutdown during first poll creates the file
        request_shutdown(str(tmp_path))
        assert (tmp_path / SHUTDOWN_FILE).exists()

        # After first poll: awake.py clears stale signal files
        clear_shutdown(str(tmp_path))
        assert not (tmp_path / SHUTDOWN_FILE).exists()

        # Shutdown check: no file → False → process lives
        assert is_shutdown_requested(str(tmp_path), startup_time) is False

    def test_new_shutdown_after_first_poll_still_works(self, tmp_path):
        """Legitimate /shutdown sent after first poll should be honored."""
        startup_time = time.time()

        # First poll: clear any stale files
        clear_shutdown(str(tmp_path))

        # User sends a fresh /shutdown command later
        time.sleep(0.01)
        request_shutdown(str(tmp_path))

        # This should be honored
        assert is_shutdown_requested(str(tmp_path), startup_time) is True

    def test_stale_shutdown_from_previous_session_cleaned_at_startup(self, tmp_path):
        """A .koan-shutdown file from a previous session is cleaned up."""
        # Previous session: shutdown was requested
        old_time = int(time.time()) - 100
        (tmp_path / SHUTDOWN_FILE).write_text(str(old_time))

        # New session starts
        startup_time = time.time()

        # Staleness check correctly identifies it as old
        assert is_shutdown_requested(str(tmp_path), startup_time) is False

        # File was auto-cleaned
        assert not (tmp_path / SHUTDOWN_FILE).exists()

    def test_first_poll_clear_idempotent(self, tmp_path):
        """Clearing when no file exists should not error."""
        clear_shutdown(str(tmp_path))  # no file
        clear_shutdown(str(tmp_path))  # still no file, no error

    def test_redelivery_race_condition(self, tmp_path):
        """Even if handler runs during first poll, the clear wins.

        Timeline:
        1. startup_time recorded
        2. First poll: /shutdown re-delivered → handler creates file
        3. First poll ends: clear_shutdown() removes file
        4. shutdown check: no file → False
        """
        startup_time = time.time()

        # Step 2: handler creates file during first poll
        request_shutdown(str(tmp_path))
        file_exists_during_poll = (tmp_path / SHUTDOWN_FILE).exists()
        assert file_exists_during_poll

        # Step 3: first poll cleanup
        clear_shutdown(str(tmp_path))

        # Step 4: shutdown check — should find nothing
        assert is_shutdown_requested(str(tmp_path), startup_time) is False

    def test_run_py_not_affected_by_bridge_clear(self, tmp_path):
        """run.py's shutdown check is independent — if file exists, it acts.

        The bridge clears the file after first poll, but run.py checks
        at its own pace. If run.py reads before the bridge clears, it
        correctly sees the valid shutdown.
        """
        start_time = time.time() - 1
        request_shutdown(str(tmp_path))

        # run.py checks and sees a valid shutdown
        assert is_shutdown_requested(str(tmp_path), start_time) is True

        # run.py clears on exit
        clear_shutdown(str(tmp_path))

        # Bridge also tries to clear — no error
        clear_shutdown(str(tmp_path))


class TestAwakeMainLoopShutdownCheck:
    """Tests verifying the awake.py main loop integration."""

    def test_shutdown_check_in_main_uses_startup_time(self):
        """Verify is_shutdown_requested is called with startup_time."""
        # This is a structural test — the actual integration is tested
        # by reading the source and verifying the call pattern.
        import inspect
        from app import awake
        source = inspect.getsource(awake._bridge_loop)

        # The shutdown check must use startup_time (not 0 or time.time())
        assert "is_shutdown_requested(str(KOAN_ROOT), startup_time)" in source

    def test_first_poll_clears_shutdown(self):
        """Verify that first_poll block clears shutdown file."""
        import inspect
        from app import awake
        source = inspect.getsource(awake._bridge_loop)

        # The first_poll block must clear both restart and shutdown
        assert "clear_shutdown" in source
        assert "clear_restart" in source
        # Both should be in the first_poll block
        first_poll_idx = source.index("if first_poll:")
        first_poll_end = source.index("first_poll = False")
        first_poll_block = source[first_poll_idx:first_poll_end]
        assert "clear_shutdown" in first_poll_block
        assert "clear_restart" in first_poll_block
