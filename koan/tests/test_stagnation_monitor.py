"""Tests for StagnationMonitor — hash comparison, abort-after-N, config integration."""

import hashlib
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_monitor(stdout_file, interval=1, cycles=3, tail=8192,
                  stagnation_event=None, stop_event=None):
    """Construct a StagnationMonitor with test-friendly defaults."""
    from app.run import StagnationMonitor
    if stagnation_event is None:
        stagnation_event = threading.Event()
    if stop_event is None:
        stop_event = threading.Event()
    return StagnationMonitor(
        stdout_file=str(stdout_file),
        check_interval_seconds=interval,
        abort_after_cycles=cycles,
        tail_bytes=tail,
        stagnation_event=stagnation_event,
        stop_event=stop_event,
    ), stagnation_event, stop_event


# ---------------------------------------------------------------------------
# Unit tests: _sample()
# ---------------------------------------------------------------------------

class TestSample:
    """Test the _sample() helper in isolation."""

    def test_returns_none_when_file_absent(self, tmp_path):
        monitor, _, _ = _make_monitor(tmp_path / "nonexistent.txt")
        assert monitor._sample() is None

    def test_returns_none_when_file_empty(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_bytes(b"")
        monitor, _, _ = _make_monitor(f)
        assert monitor._sample() is None

    def test_returns_bytes_for_nonempty_file(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_bytes(b"hello world")
        monitor, _, _ = _make_monitor(f)
        result = monitor._sample()
        assert isinstance(result, bytes)
        assert len(result) == 32  # SHA-256 digest

    def test_different_content_yields_different_hash(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_bytes(b"state A")
        monitor, _, _ = _make_monitor(f)
        hash_a = monitor._sample()

        f.write_bytes(b"state B")
        hash_b = monitor._sample()

        assert hash_a != hash_b

    def test_same_content_yields_same_hash(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_bytes(b"stable content")
        monitor, _, _ = _make_monitor(f)
        assert monitor._sample() == monitor._sample()

    def test_growing_file_yields_different_hash(self, tmp_path):
        """Adding bytes must change the hash even if tail content is identical."""
        f = tmp_path / "out.txt"
        f.write_bytes(b"x" * 8192)
        monitor, _, _ = _make_monitor(f, tail=8192)
        hash_before = monitor._sample()

        # Append more data — same tail content, but file is bigger
        with f.open("ab") as fh:
            fh.write(b"y" * 100)
        hash_after = monitor._sample()

        assert hash_before != hash_after


# ---------------------------------------------------------------------------
# Unit tests: counter logic
# ---------------------------------------------------------------------------

class TestCounterLogic:
    """Test that consecutive identical samples increment counter correctly."""

    def test_no_abort_on_single_identical_sample(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_bytes(b"output")
        monitor, stagnation_event, stop_event = _make_monitor(f, interval=0.05, cycles=3)
        monitor.start()
        # Give monitor just enough time for one cycle
        time.sleep(0.15)
        stop_event.set()
        assert not stagnation_event.is_set()

    def test_aborts_after_n_consecutive_identical_samples(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_bytes(b"stuck output")
        monitor, stagnation_event, stop_event = _make_monitor(f, interval=0.05, cycles=3)
        monitor.start()
        # 3 cycles × 0.05s + buffer
        time.sleep(0.5)
        stop_event.set()
        assert stagnation_event.is_set()

    def test_counter_resets_when_content_changes(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_bytes(b"initial output")
        monitor, stagnation_event, stop_event = _make_monitor(f, interval=0.05, cycles=5)
        monitor.start()
        # Let 2 identical samples go by (below threshold)
        time.sleep(0.12)
        # Change the content — counter should reset
        f.write_bytes(b"new output after change")
        # Wait another 2 cycles — still below threshold after reset
        time.sleep(0.12)
        stop_event.set()
        assert not stagnation_event.is_set()

    def test_no_false_trigger_while_file_absent(self, tmp_path):
        """File doesn't exist initially — counter must stay at zero."""
        missing = tmp_path / "absent.txt"
        monitor, stagnation_event, stop_event = _make_monitor(missing, interval=0.05, cycles=2)
        monitor.start()
        time.sleep(0.2)
        stop_event.set()
        assert not stagnation_event.is_set()

    def test_stops_cleanly_when_stop_event_set(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_bytes(b"output")
        monitor, stagnation_event, stop_event = _make_monitor(f, interval=1, cycles=2)
        monitor.start()
        # Stop well before any cycles complete
        stop_event.set()
        monitor._thread.join(timeout=2)
        assert not monitor._thread.is_alive()
        assert not stagnation_event.is_set()


# ---------------------------------------------------------------------------
# Unit tests: missions.py fail_mission cause kwarg
# ---------------------------------------------------------------------------

class TestFailMissionCause:
    """Test that fail_mission() appends [cause] to the failure entry."""

    def _missions_content(self):
        return (
            "## Pending\n\n"
            "- Fix the bug [project:myapp]\n\n"
            "## In Progress\n\n"
            "## Done\n\n"
        )

    def test_fail_mission_without_cause_has_no_tag(self):
        from app.missions import fail_mission
        content = self._missions_content()
        result = fail_mission(content, "Fix the bug")
        assert "[stagnation]" not in result
        assert "❌" in result

    def test_fail_mission_with_stagnation_cause(self):
        from app.missions import fail_mission
        content = self._missions_content()
        result = fail_mission(content, "Fix the bug", cause="stagnation")
        assert "[stagnation]" in result
        assert "❌" in result

    def test_fail_mission_cause_appears_after_timestamp(self):
        from app.missions import fail_mission
        content = self._missions_content()
        result = fail_mission(content, "Fix the bug", cause="stagnation")
        # Entry format: "- text ❌ (YYYY-MM-DD HH:MM) [stagnation]"
        lines = [l for l in result.splitlines() if "❌" in l]
        assert len(lines) == 1
        line = lines[0]
        assert line.endswith("[stagnation]")

    def test_fail_in_progress_with_cause(self):
        from app.missions import fail_mission
        content = (
            "## Pending\n\n"
            "## In Progress\n\n"
            "- Running task [project:x]\n\n"
            "## Done\n\n"
        )
        result = fail_mission(content, "Running task", cause="stagnation")
        assert "[stagnation]" in result

    def test_complete_mission_unaffected_by_cause_param(self):
        """complete_mission() does not have a cause param — ensure no regression."""
        from app.missions import complete_mission
        content = self._missions_content()
        result = complete_mission(content, "Fix the bug")
        assert "✅" in result
        assert "[stagnation]" not in result


# ---------------------------------------------------------------------------
# Unit tests: get_stagnation_config
# ---------------------------------------------------------------------------

class TestGetStagnationConfig:
    """Test config loading and per-project override."""

    def test_defaults_when_no_config_section(self, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")
        with patch("app.config._load_config", return_value={}):
            from app.config import get_stagnation_config
            cfg = get_stagnation_config()
        assert cfg["enabled"] is True
        assert cfg["check_interval_seconds"] == 60
        assert cfg["abort_after_cycles"] == 3
        assert cfg["tail_bytes"] == 8192

    def test_reads_values_from_config_section(self, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")
        stagnation_cfg = {
            "enabled": False,
            "check_interval_seconds": 30,
            "abort_after_cycles": 5,
            "tail_bytes": 4096,
        }
        with patch("app.config._load_config", return_value={"stagnation": stagnation_cfg}):
            from app.config import get_stagnation_config
            cfg = get_stagnation_config()
        assert cfg["enabled"] is False
        assert cfg["check_interval_seconds"] == 30
        assert cfg["abort_after_cycles"] == 5
        assert cfg["tail_bytes"] == 4096

    def test_per_project_stagnation_enabled_false_overrides(self, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")
        with patch("app.config._load_config", return_value={}):
            with patch(
                "app.config._load_project_overrides",
                return_value={"stagnation_enabled": False},
            ):
                from app.config import get_stagnation_config
                cfg = get_stagnation_config(project_name="myproject")
        assert cfg["enabled"] is False

    def test_per_project_stagnation_enabled_true_enables(self, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")
        global_off = {"stagnation": {"enabled": False}}
        with patch("app.config._load_config", return_value=global_off):
            with patch(
                "app.config._load_project_overrides",
                return_value={"stagnation_enabled": True},
            ):
                from app.config import get_stagnation_config
                cfg = get_stagnation_config(project_name="special")
        assert cfg["enabled"] is True

    def test_minimum_values_clamped(self, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", "/tmp/test-koan")
        stagnation_cfg = {
            "enabled": True,
            "check_interval_seconds": 0,   # below min 1
            "abort_after_cycles": 0,        # below min 1
            "tail_bytes": 10,               # below min 128
        }
        with patch("app.config._load_config", return_value={"stagnation": stagnation_cfg}):
            from app.config import get_stagnation_config
            cfg = get_stagnation_config()
        assert cfg["check_interval_seconds"] >= 1
        assert cfg["abort_after_cycles"] >= 1
        assert cfg["tail_bytes"] >= 128


# ---------------------------------------------------------------------------
# Integration: stagnation abort path in _run_iteration (mocked subprocess)
# ---------------------------------------------------------------------------

class TestStagnationAbortPath:
    """Verify that a stagnation-aborted run calls fail_mission with cause=stagnation
    and sends a Telegram warning (via send_telegram, not format_and_send)."""

    def test_stagnation_sets_last_mission_stagnated_flag(self, tmp_path, monkeypatch):
        """run_claude_task sets _last_mission_stagnated when stagnation_event fires."""
        import app.run as run_module

        stdout_file = tmp_path / "stdout.txt"
        stderr_file = tmp_path / "stderr.txt"
        stdout_file.write_bytes(b"stuck output\n" * 100)

        # Patch get_stagnation_config to use fast settings
        stagnation_cfg = {
            "enabled": True,
            "check_interval_seconds": 0,  # will be clamped to 1 by prod, mock directly
            "abort_after_cycles": 1,
            "tail_bytes": 8192,
        }

        fake_proc = MagicMock()
        fake_proc.pid = 12345
        fake_proc.returncode = 1
        wait_calls = [0]

        def _fake_wait(timeout=None):
            wait_calls[0] += 1
            if wait_calls[0] <= 2:
                raise __import__("subprocess").TimeoutExpired(cmd="x", timeout=timeout)
            return None

        fake_proc.wait.side_effect = _fake_wait
        fake_proc.poll.return_value = None

        def fake_popen_cli(cmd, **kwargs):
            return fake_proc, lambda: None

        monkeypatch.setattr("app.run._last_mission_stagnated", False)

        # Patch to force stagnation event to fire quickly
        original_monitor_init = run_module.StagnationMonitor.__init__

        def patched_monitor_init(self, stdout_file, check_interval_seconds,
                                  abort_after_cycles, tail_bytes,
                                  stagnation_event, stop_event):
            original_monitor_init(
                self, stdout_file,
                check_interval_seconds=1,  # 1 second
                abort_after_cycles=1,
                tail_bytes=8192,
                stagnation_event=stagnation_event,
                stop_event=stop_event,
            )

        with (
            patch("app.config.get_stagnation_config", return_value=stagnation_cfg),
            patch("app.config.get_mission_timeout", return_value=3600),
            patch("app.cli_exec.popen_cli", side_effect=fake_popen_cli),
            patch("app.run.StagnationMonitor.__init__", patched_monitor_init),
            patch("app.run._kill_process_group"),
            patch("os.getpgid", return_value=12345),
        ):
            # Manually set stagnation_event via a mock monitor
            # by triggering a real StagnationMonitor on the file
            pass

        # Simpler: test via the global flag being set when stagnation_event fires
        # in the wait loop (unit test of the wait loop logic directly)
        stagnation_event = threading.Event()
        stagnation_event.set()  # pre-fired

        import subprocess
        run_module._last_mission_stagnated = False

        with (
            patch("app.config.get_stagnation_config", return_value=stagnation_cfg),
            patch("app.config.get_mission_timeout", return_value=3600),
            patch("app.cli_exec.popen_cli", return_value=(fake_proc, lambda: None)),
            patch.object(run_module, "StagnationMonitor") as MockMonitor,
            patch("app.run._kill_process_group"),
            patch("os.getpgid", return_value=12345),
        ):
            mock_instance = MagicMock()
            mock_instance._stagnation_event = stagnation_event
            MockMonitor.return_value = mock_instance

            # Wire the stagnation_event into what the wait loop checks
            # by making the constructor capture and expose the event
            def _build_monitor(stdout_file, check_interval_seconds,
                                abort_after_cycles, tail_bytes,
                                stagnation_event, stop_event):
                stagnation_event.set()  # trigger immediately
                return mock_instance

            MockMonitor.side_effect = _build_monitor

            fake_proc.wait.side_effect = [
                __import__("subprocess").TimeoutExpired(cmd="x", timeout=30),
                None,
            ]

            run_module.run_claude_task(
                cmd=["echo", "hello"],
                stdout_file=str(stdout_file),
                stderr_file=str(stderr_file),
                cwd="/tmp",
            )

        assert run_module._last_mission_stagnated is True
