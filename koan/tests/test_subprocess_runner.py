"""Tests for app.subprocess_runner — kill, watchdog, liveness primitives."""

import contextlib
import itertools
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.subprocess_runner import (
    LivenessWatchdog,
    ProcessWatchdog,
    force_kill_process_group,
    kill_orphaned_process_group,
    kill_process_group,
    kill_process_group_by_pid,
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


# ── kill_process_group_by_pid ───────────────────────────────────────────

class TestKillProcessGroupByPid:
    def test_nonpositive_pid_is_noop(self):
        with patch("app.subprocess_runner.os.getpgid") as getpgid:
            kill_process_group_by_pid(0)
            kill_process_group_by_pid(-1)
        getpgid.assert_not_called()

    def test_dead_pid_is_noop(self):
        with patch("app.subprocess_runner.os.getpgid",
                   side_effect=ProcessLookupError), \
             patch("app.subprocess_runner.os.killpg") as killpg:
            kill_process_group_by_pid(4242)
        killpg.assert_not_called()

    def test_refuses_a_pid_that_does_not_lead_its_group(self):
        """Guards against a stale pid file making us signal our own group."""
        with patch("app.subprocess_runner.os.getpgid", return_value=99), \
             patch("app.subprocess_runner.os.killpg") as killpg:
            kill_process_group_by_pid(4242)
        killpg.assert_not_called()

    def test_sigterm_only_when_the_group_exits(self):
        """Liveness is the *group* emptying, not merely the leader dying."""
        signals = []

        def fake_killpg(pgid, sig):
            if sig == 0:
                raise ProcessLookupError  # probe: nothing left in the group
            signals.append(sig)

        with patch("app.subprocess_runner.os.getpgid", return_value=4242), \
             patch("app.subprocess_runner.os.killpg", side_effect=fake_killpg):
            kill_process_group_by_pid(4242)
        assert signals == [signal.SIGTERM]

    def test_escalates_to_sigkill_when_the_group_survives(self):
        signals = []

        def fake_killpg(pgid, sig):
            if sig != 0:
                signals.append(sig)  # probes never raise: the group holds on

        with patch("app.subprocess_runner.os.getpgid", return_value=4242), \
             patch("app.subprocess_runner.os.killpg", side_effect=fake_killpg), \
             patch("app.subprocess_runner.time.sleep", return_value=None), \
             patch("app.subprocess_runner.time.monotonic",
                   side_effect=itertools.count(0, 1).__next__):
            kill_process_group_by_pid(4242, graceful_timeout=1, force_timeout=1)
        assert signals == [signal.SIGTERM, signal.SIGKILL]

    def test_kills_a_real_detached_group(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        try:
            kill_process_group_by_pid(proc.pid, graceful_timeout=3)
            assert proc.wait(timeout=5) is not None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


class TestKillOrphanedProcessGroup:
    """The lever for a group whose leader has already been reaped."""

    def test_refuses_our_own_group(self):
        with patch("app.subprocess_runner.os.killpg") as killpg:
            kill_orphaned_process_group(os.getpgrp())
        killpg.assert_not_called()

    def test_refuses_init_and_unset_group_ids(self):
        """A pgid of 0 or 1 means the capture failed, not that init must die."""
        with patch("app.subprocess_runner.os.killpg") as killpg:
            for pgid in (-1, 0, 1):
                kill_orphaned_process_group(pgid)
        killpg.assert_not_called()

    def test_kills_a_group_whose_leader_is_already_gone(self):
        """The case kill_process_group() cannot reach: leader reaped, child alive."""
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "child.pid"
            script = (
                "import os, sys, time\n"
                "if os.fork() == 0:\n"
                "    with open(sys.argv[1], 'w') as fh:\n"
                "        fh.write(str(os.getpid()))\n"
                "    time.sleep(60)\n"
                "    os._exit(0)\n"
                "os._exit(0)\n"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", script, str(pid_path)], start_new_session=True,
            )
            pgid = os.getpgid(proc.pid)
            proc.wait(timeout=10)
            deadline = time.time() + 10
            child_pid = 0
            while time.time() < deadline and not child_pid:
                try:
                    child_pid = int(pid_path.read_text().strip() or 0)
                except (OSError, ValueError):
                    time.sleep(0.05)
            assert child_pid, "child never reported its pid"
            try:
                kill_orphaned_process_group(pgid, graceful_timeout=3)
                deadline = time.time() + 5
                while time.time() < deadline:
                    try:
                        os.kill(child_pid, 0)
                    except OSError:
                        break
                    time.sleep(0.05)
                with pytest.raises(OSError):
                    os.kill(child_pid, 0)
            finally:
                with contextlib.suppress(OSError):
                    os.kill(child_pid, signal.SIGKILL)
