"""Tests for bridge crash recovery wrapper in awake.py."""

import sys
from unittest.mock import patch, call

import pytest

from app.awake import (
    main,
    MAX_BRIDGE_CRASHES,
    BRIDGE_BACKOFF_MULTIPLIER,
    MAX_BRIDGE_BACKOFF,
)


class TestBridgeCrashRecovery:
    """Verify the main() wrapper restarts _bridge_loop on crashes."""

    def test_normal_exit_no_restart(self):
        with patch("app.awake._bridge_loop") as mock_loop:
            main()
        mock_loop.assert_called_once()

    def test_keyboard_interrupt_exits_cleanly(self):
        with patch("app.awake._bridge_loop", side_effect=KeyboardInterrupt):
            main()

    def test_system_exit_propagates(self):
        with patch("app.awake._bridge_loop", side_effect=SystemExit(0)):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_system_exit_nonzero_propagates(self):
        with patch("app.awake._bridge_loop", side_effect=SystemExit(1)):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_single_crash_restarts(self):
        call_count = 0

        def crash_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient failure")

        with patch("app.awake._bridge_loop", side_effect=crash_once):
            with patch("time.sleep") as mock_sleep:
                main()

        assert call_count == 2
        mock_sleep.assert_called_once_with(BRIDGE_BACKOFF_MULTIPLIER * 1)

    def test_max_crashes_exits(self):
        with patch(
            "app.awake._bridge_loop",
            side_effect=RuntimeError("persistent failure"),
        ):
            with patch("time.sleep"):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 1

    def test_crash_count_matches_max(self):
        call_count = 0

        def always_crash():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("boom")

        with patch("app.awake._bridge_loop", side_effect=always_crash):
            with patch("time.sleep"):
                with pytest.raises(SystemExit):
                    main()

        assert call_count == MAX_BRIDGE_CRASHES

    def test_backoff_increases_linearly(self):
        call_count = 0
        target_crashes = 3

        def crash_n_times():
            nonlocal call_count
            call_count += 1
            if call_count <= target_crashes:
                raise RuntimeError("boom")

        with patch("app.awake._bridge_loop", side_effect=crash_n_times):
            with patch("time.sleep") as mock_sleep:
                main()

        expected = [
            call(BRIDGE_BACKOFF_MULTIPLIER * i) for i in range(1, target_crashes + 1)
        ]
        assert mock_sleep.call_args_list == expected

    def test_backoff_capped_at_max(self):
        crash_count_needed = (MAX_BRIDGE_BACKOFF // BRIDGE_BACKOFF_MULTIPLIER) + 2
        call_count = 0

        def crash_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count <= crash_count_needed:
                raise RuntimeError("boom")

        with patch("app.awake._bridge_loop", side_effect=crash_then_succeed):
            with patch("time.sleep") as mock_sleep:
                if crash_count_needed < MAX_BRIDGE_CRASHES:
                    main()
                else:
                    with pytest.raises(SystemExit):
                        main()

        last_sleep = mock_sleep.call_args_list[-1][0][0]
        assert last_sleep <= MAX_BRIDGE_BACKOFF

    def test_crash_logged_to_stderr(self, capsys):
        call_count = 0

        def crash_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("test error msg")

        with patch("app.awake._bridge_loop", side_effect=crash_once):
            with patch("time.sleep"):
                main()

        stderr = capsys.readouterr().err
        assert "Unexpected crash (1/" in stderr
        assert "test error msg" in stderr
        assert "Restarting in" in stderr

    def test_max_crash_logged_to_stderr(self, capsys):
        with patch(
            "app.awake._bridge_loop",
            side_effect=RuntimeError("fatal"),
        ):
            with patch("time.sleep"):
                with pytest.raises(SystemExit):
                    main()

        stderr = capsys.readouterr().err
        assert "Too many crashes" in stderr
