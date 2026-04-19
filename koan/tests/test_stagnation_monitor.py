"""Tests for stagnation_monitor — hash logic, escalation, config integration."""

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.stagnation_monitor import StagnationMonitor, _tail_hash


def _make_stdout(path: Path, lines: int, prefix: str = "line") -> None:
    """Write *lines* sample lines to *path* — enough bytes to clear the min floor."""
    # 16 bytes of filler per line keeps total above _DEFAULT_MIN_BYTES (512).
    content = "\n".join(f"{prefix} {i:04d} ............." for i in range(lines))
    path.write_text(content + "\n")


class TestTailHash:
    def test_returns_none_for_missing_file(self, tmp_path):
        assert _tail_hash(str(tmp_path / "does-not-exist"), 50) is None

    def test_returns_none_for_tiny_output(self, tmp_path):
        f = tmp_path / "tiny.log"
        f.write_text("hi\n")
        assert _tail_hash(str(f), 50) is None

    def test_deterministic_for_identical_input(self, tmp_path):
        f = tmp_path / "out.log"
        _make_stdout(f, 60)
        a = _tail_hash(str(f), 50)
        b = _tail_hash(str(f), 50)
        assert a is not None and a == b

    def test_changes_when_new_content_appended(self, tmp_path):
        f = tmp_path / "out.log"
        _make_stdout(f, 60)
        before = _tail_hash(str(f), 50)
        with open(f, "a") as fh:
            fh.write("brand new progress line that shifts the tail\n")
        after = _tail_hash(str(f), 50)
        assert before != after

    def test_only_last_N_lines_matter(self, tmp_path):
        """Edits above the sample window must not change the hash."""
        f = tmp_path / "out.log"
        _make_stdout(f, 200)
        baseline = _tail_hash(str(f), 10)
        # Rewrite the first 50 lines with different content but keep the tail.
        content = f.read_text().splitlines()
        head = ["MUTATED " + l for l in content[:50]]
        f.write_text("\n".join(head + content[50:]) + "\n")
        after = _tail_hash(str(f), 10)
        assert baseline == after


class TestStagnationMonitorBehavior:
    def test_aborts_after_k_identical_samples(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)  # file frozen — hash will be identical every sample

        aborts = []
        warns = []
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: aborts.append(True),
            on_warn=lambda count: warns.append(count),
            check_interval_seconds=1,
            abort_after_cycles=3,
        )
        # Drive the sampler synchronously to avoid timing flakiness.
        monitor._sample_once()  # sample 1 → consecutive=1
        monitor._sample_once()  # sample 2 → consecutive=2 → warn fires
        assert warns == [2]
        assert not monitor.stagnated
        assert aborts == []
        monitor._sample_once()  # sample 3 → consecutive=3 → abort fires
        assert monitor.stagnated is True
        assert aborts == [True]

    def test_does_not_abort_when_output_keeps_changing(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        aborts = []
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: aborts.append(True),
            check_interval_seconds=1,
            abort_after_cycles=3,
        )
        for i in range(5):
            # Append a unique line each cycle so the tail hash shifts.
            with open(f, "a") as fh:
                fh.write(f"progress {i} — new content line that changes tail\n")
            monitor._sample_once()
        assert not monitor.stagnated
        assert aborts == []

    def test_abort_callback_invoked_once_even_with_more_samples(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        aborts = []
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: aborts.append(True),
            check_interval_seconds=1,
            abort_after_cycles=2,
        )
        for _ in range(6):
            monitor._sample_once()
        assert aborts == [True]  # exactly one abort

    def test_warn_callback_fires_only_once_per_stagnation_window(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        warns = []
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: None,
            on_warn=lambda n: warns.append(n),
            check_interval_seconds=1,
            abort_after_cycles=5,
        )
        monitor._sample_once()
        monitor._sample_once()  # consecutive=2 → warn
        monitor._sample_once()  # consecutive=3 → no additional warn
        monitor._sample_once()  # consecutive=4 → no additional warn
        assert warns == [2]

    def test_callback_exception_does_not_kill_monitor(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        def _bad_warn(_n):
            raise RuntimeError("boom")

        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: None,
            on_warn=_bad_warn,
            check_interval_seconds=1,
            abort_after_cycles=3,
        )
        # Should not raise even though warn callback blows up.
        monitor._sample_once()
        monitor._sample_once()
        monitor._sample_once()
        assert monitor.stagnated is True

    def test_rejects_abort_after_cycles_below_two(self, tmp_path):
        with pytest.raises(ValueError):
            StagnationMonitor(
                stdout_file=str(tmp_path / "f.log"),
                on_abort=lambda: None,
                abort_after_cycles=1,
            )

    def test_daemon_thread_starts_and_stops_cleanly(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: None,
            check_interval_seconds=1,
            abort_after_cycles=3,
        )
        monitor.start()
        assert monitor._thread is not None
        assert monitor._thread.is_alive()
        monitor.stop(timeout=2.0)
        assert not monitor._thread.is_alive()

    def test_start_is_idempotent(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: None,
        )
        monitor.start()
        first = monitor._thread
        monitor.start()  # second call: must not spawn a new thread
        assert monitor._thread is first
        monitor.stop(timeout=2.0)


class TestStagnationConfig:
    def test_defaults_when_no_config(self):
        from app.config import get_stagnation_config
        with patch("app.config._load_config", return_value={}):
            cfg = get_stagnation_config()
        assert cfg["enabled"] is True
        assert cfg["check_interval_seconds"] == 60
        assert cfg["abort_after_cycles"] == 3
        assert cfg["sample_lines"] == 50

    def test_yaml_overrides_apply(self):
        from app.config import get_stagnation_config
        with patch("app.config._load_config", return_value={
            "stagnation": {
                "check_interval_seconds": 30,
                "abort_after_cycles": 5,
                "sample_lines": 10,
            },
        }):
            cfg = get_stagnation_config()
        assert cfg["check_interval_seconds"] == 30
        assert cfg["abort_after_cycles"] == 5
        assert cfg["sample_lines"] == 10
        assert cfg["enabled"] is True  # default preserved

    def test_project_override_disables(self):
        from app.config import get_stagnation_config
        with patch("app.config._load_config", return_value={
            "stagnation": {"enabled": True},
        }), patch("app.config._load_project_overrides", return_value={
            "stagnation": {"enabled": False},
        }):
            cfg = get_stagnation_config("flaky_repo")
        assert cfg["enabled"] is False

    def test_project_shortcut_false_disables(self):
        """Per-project ``stagnation: false`` must disable the monitor."""
        from app.config import get_stagnation_config
        with patch("app.config._load_config", return_value={}), \
             patch("app.config._load_project_overrides", return_value={
                 "stagnation": False,
             }):
            cfg = get_stagnation_config("flaky_repo")
        assert cfg["enabled"] is False

    def test_clamps_invalid_abort_threshold_to_two(self):
        from app.config import get_stagnation_config
        with patch("app.config._load_config", return_value={
            "stagnation": {"abort_after_cycles": 1},
        }):
            cfg = get_stagnation_config()
        # Floor is 2 — must never produce a same-sample abort.
        assert cfg["abort_after_cycles"] == 2


class TestFailMissionCauseTag:
    def test_cause_tag_appears_after_timestamp(self):
        from app.missions import fail_mission
        content = "## Pending\n\n- /fix https://github.com/x/y/issues/1\n\n## Failed\n\n"
        updated = fail_mission(content, "/fix https://github.com/x/y/issues/1",
                               cause_tag="stagnation")
        assert "[stagnation]" in updated
        assert "\u274c" in updated  # ❌ marker still present

    def test_no_tag_when_cause_empty(self):
        from app.missions import fail_mission
        content = "## Pending\n\n- /fix issue 1\n\n## Failed\n\n"
        updated = fail_mission(content, "/fix issue 1")
        assert "[stagnation]" not in updated
        assert "\u274c" in updated
