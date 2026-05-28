"""Tests for app.subprocess_runner — kill, watchdog, liveness primitives."""

import signal
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.subprocess_runner import (
    LivenessWatchdog,
    ProcessWatchdog,
    force_kill_process_group,
    kill_process_group,
)


# ── kill_process_group ──────────────────────────────────────────────────

class TestKillProcessGroup:
    def test_none_proc_is_noop(self):
        kill_process_group(None)

    def test_already_exited_is_noop(self):
        proc = MagicMock()
        proc.poll.return_value = 0
        kill_process_group(proc)
        proc.wait.assert_not_called()

    def test_sigterm_then_exits(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 42
        with patch("app.subprocess_runner.os.getpgid", return_value=100), \
             patch("app.subprocess_runner.os.killpg") as killpg:
            kill_process_group(proc)
        killpg.assert_called_once_with(100, signal.SIGTERM)
        proc.wait.assert_called_once_with(timeout=3)

    def test_escalates_to_sigkill_on_timeout(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 42
        proc.wait.side_effect = [
            subprocess.TimeoutExpired("cmd", 3),
            None,
        ]
        calls = []
        with patch("app.subprocess_runner.os.getpgid", return_value=100), \
             patch("app.subprocess_runner.os.killpg",
                   side_effect=lambda pgid, sig: calls.append(sig)):
            kill_process_group(proc)
        assert calls == [signal.SIGTERM, signal.SIGKILL]

    def test_swallows_process_lookup_error(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 42
        with patch("app.subprocess_runner.os.getpgid",
                   side_effect=ProcessLookupError):
            kill_process_group(proc)

    def test_unkillable_process_logged(self, capsys):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 42
        proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 3)
        with patch("app.subprocess_runner.os.getpgid", return_value=100), \
             patch("app.subprocess_runner.os.killpg"):
            kill_process_group(proc)
        assert "did not exit after SIGKILL" in capsys.readouterr().err


# ── force_kill_process_group ────────────────────────────────────────────

class TestForceKillProcessGroup:
    def test_none_proc_is_noop(self):
        force_kill_process_group(None)

    def test_sigkill_directly(self):
        proc = MagicMock()
        proc.pid = 42
        with patch("app.subprocess_runner.os.getpgid", return_value=100), \
             patch("app.subprocess_runner.os.killpg") as killpg:
            force_kill_process_group(proc)
        killpg.assert_called_once_with(100, signal.SIGKILL)

    def test_falls_back_to_proc_kill(self):
        proc = MagicMock()
        proc.pid = 42
        with patch("app.subprocess_runner.os.getpgid",
                   side_effect=OSError("gone")):
            force_kill_process_group(proc)
        proc.kill.assert_called_once()


# ── ProcessWatchdog ─────────────────────────────────────────────────────

class TestProcessWatchdog:
    def test_fires_after_timeout(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 99
        fired_event = threading.Event()

        with patch("app.subprocess_runner.os.getpgid", return_value=99), \
             patch("app.subprocess_runner.os.killpg"):
            wd = ProcessWatchdog(
                proc, 0.1,
                on_timeout=fired_event.set,
            ).start()
            fired_event.wait(timeout=2)
            wd.cancel()

        assert wd.fired is True

    def test_cancel_prevents_fire(self):
        proc = MagicMock()
        proc.poll.return_value = None
        callback = MagicMock()

        wd = ProcessWatchdog(proc, 0.5, on_timeout=callback).start()
        wd.cancel()
        time.sleep(0.7)

        assert wd.fired is False
        callback.assert_not_called()

    def test_mark_completed_blocks_fire(self):
        proc = MagicMock()
        proc.poll.return_value = None
        callback = MagicMock()

        with patch("app.subprocess_runner.threading.Timer") as TimerMock:
            captured = {}

            def factory(timeout, fn):
                captured["fn"] = fn
                return MagicMock()

            TimerMock.side_effect = factory
            wd = ProcessWatchdog(proc, 10, on_timeout=callback).start()
            wd.mark_completed()
            captured["fn"]()

        assert wd.fired is False
        callback.assert_not_called()

    def test_graceful_false_uses_force_kill(self):
        proc = MagicMock()
        proc.pid = 42
        fired = threading.Event()

        with patch("app.subprocess_runner.os.getpgid", return_value=100), \
             patch("app.subprocess_runner.os.killpg",
                   side_effect=lambda *a: fired.set()) as killpg:
            wd = ProcessWatchdog(proc, 0.1, graceful=False).start()
            fired.wait(timeout=2)
            wd.cancel()

        killpg.assert_called_with(100, signal.SIGKILL)


# ── LivenessWatchdog ────────────────────────────────────────────────────

class TestLivenessWatchdog:
    def test_fires_without_heartbeat(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 99
        fired_event = threading.Event()

        with patch("app.subprocess_runner.os.getpgid", return_value=99), \
             patch("app.subprocess_runner.os.killpg"):
            lw = LivenessWatchdog(
                proc, 0.1,
                on_timeout=fired_event.set,
            ).start()
            fired_event.wait(timeout=2)
            lw.cancel()

        assert lw.fired is True

    def test_heartbeat_resets_countdown(self):
        proc = MagicMock()
        proc.poll.return_value = None
        callback = MagicMock()

        lw = LivenessWatchdog(proc, 0.3, on_timeout=callback).start()
        for _ in range(5):
            time.sleep(0.1)
            lw.heartbeat()
        lw.cancel()

        assert lw.fired is False
        callback.assert_not_called()

    def test_cancel_prevents_fire(self):
        proc = MagicMock()
        proc.poll.return_value = None
        callback = MagicMock()

        lw = LivenessWatchdog(proc, 0.2, on_timeout=callback).start()
        lw.cancel()
        time.sleep(0.4)

        assert lw.fired is False
        callback.assert_not_called()

    def test_heartbeat_after_fire_is_noop(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 99
        fired = threading.Event()

        with patch("app.subprocess_runner.os.getpgid", return_value=99), \
             patch("app.subprocess_runner.os.killpg"):
            lw = LivenessWatchdog(proc, 0.05, on_timeout=fired.set).start()
            fired.wait(timeout=2)
            lw.heartbeat()
            lw.cancel()

        assert lw.fired is True
