"""Tests for stagnation_monitor — hash logic, escalation, config integration."""

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.stagnation_monitor import (
    StagnationMonitor,
    _tail_hash,
    classify_stagnation,
    get_retry_info,
    increment_retry_count,
)


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

    def test_typed_stagnation_tag(self):
        from app.missions import fail_mission
        content = "## Pending\n\n- /fix https://github.com/x/y/issues/2\n\n## Failed\n\n"
        updated = fail_mission(content, "/fix https://github.com/x/y/issues/2",
                               cause_tag="stagnation:tool_loop")
        assert "[stagnation:tool_loop]" in updated


class TestClassifyStagnation:
    """Tests for classify_stagnation() — one per pattern type + unknown."""

    def test_tool_loop_detected(self, tmp_path):
        """Repeated tool names in >= 5 lines → tool_loop."""
        f = tmp_path / "stdout.log"
        lines = []
        # Add enough filler to pass min-bytes threshold
        for i in range(20):
            lines.append(f"filler line {i:04d} .............")
        # 6 lines with Bash tool name
        for i in range(6):
            lines.append(f"Calling Bash tool: ls -la iteration {i}")
        f.write_text("\n".join(lines) + "\n")
        pattern, excerpt = classify_stagnation(str(f))
        assert pattern == "tool_loop"
        assert "Bash" in excerpt

    def test_infinite_retry_detected(self, tmp_path):
        """Error keywords in >= 3 lines → infinite_retry."""
        f = tmp_path / "stdout.log"
        lines = []
        for i in range(20):
            lines.append(f"filler line {i:04d} .............")
        lines.append("Error: connection refused to database")
        lines.append("Exception raised in handler")
        lines.append("Traceback (most recent call last):")
        f.write_text("\n".join(lines) + "\n")
        pattern, excerpt = classify_stagnation(str(f))
        assert pattern == "infinite_retry"

    def test_interactive_wait_detected(self, tmp_path):
        """Stdin prompt in output → interactive_wait."""
        f = tmp_path / "stdout.log"
        lines = []
        for i in range(30):
            lines.append(f"filler line {i:04d} .............")
        lines.append("Do you want to continue? [y/n]")
        f.write_text("\n".join(lines) + "\n")
        pattern, excerpt = classify_stagnation(str(f))
        assert pattern == "interactive_wait"
        assert "[y/n]" in excerpt

    def test_quota_mid_session_detected(self, tmp_path):
        """Quota exhaustion markers → quota_mid_session."""
        f = tmp_path / "stdout.log"
        lines = []
        for i in range(30):
            lines.append(f"filler line {i:04d} .............")
        lines.append('{"error": "rate_limit exceeded, please try again later"}')
        f.write_text("\n".join(lines) + "\n")
        pattern, excerpt = classify_stagnation(str(f))
        assert pattern == "quota_mid_session"

    def test_silent_for_missing_file(self, tmp_path):
        """Missing stdout file → silent."""
        pattern, excerpt = classify_stagnation(str(tmp_path / "nope.log"))
        assert pattern == "silent"
        assert excerpt == ""

    def test_silent_for_tiny_file(self, tmp_path):
        """File below min-bytes threshold → silent."""
        f = tmp_path / "stdout.log"
        f.write_text("tiny\n")
        pattern, excerpt = classify_stagnation(str(f))
        assert pattern == "silent"

    def test_unknown_fallback(self, tmp_path):
        """Normal output with no patterns → unknown."""
        f = tmp_path / "stdout.log"
        lines = []
        for i in range(40):
            lines.append(f"normal progress output line {i:04d} with some padding text here")
        f.write_text("\n".join(lines) + "\n")
        pattern, excerpt = classify_stagnation(str(f))
        assert pattern == "unknown"
        assert len(excerpt) <= 200

    def test_excerpt_capped_at_200_chars(self, tmp_path):
        """Excerpt must never exceed 200 characters."""
        f = tmp_path / "stdout.log"
        lines = []
        for i in range(40):
            lines.append("x" * 300)
        f.write_text("\n".join(lines) + "\n")
        _, excerpt = classify_stagnation(str(f))
        assert len(excerpt) <= 200

    def test_tool_loop_takes_priority_over_errors(self, tmp_path):
        """tool_loop is checked before infinite_retry — first match wins."""
        f = tmp_path / "stdout.log"
        lines = []
        for i in range(20):
            lines.append(f"filler line {i:04d} .............")
        # 5 tool references + 3 error lines
        for i in range(5):
            lines.append(f"Read tool call {i}: reading file.py")
        lines.append("Error: something went wrong")
        lines.append("Exception in handler")
        lines.append("Traceback occurred")
        f.write_text("\n".join(lines) + "\n")
        pattern, _ = classify_stagnation(str(f))
        assert pattern == "tool_loop"


class TestMonitorCapturesPattern:
    """StagnationMonitor populates pattern_type/pattern_excerpt on abort."""

    def test_pattern_set_on_stagnation(self, tmp_path):
        f = tmp_path / "stdout.log"
        # Write tool-loop content
        lines = []
        for i in range(30):
            lines.append(f"filler {i:04d} .............")
        for i in range(6):
            lines.append(f"Calling Bash tool iteration {i}")
        f.write_text("\n".join(lines) + "\n")

        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: None,
            check_interval_seconds=1,
            abort_after_cycles=2,
        )
        monitor._sample_once()
        monitor._sample_once()
        assert monitor.stagnated
        assert monitor.pattern_type == "tool_loop"
        assert "Bash" in monitor.pattern_excerpt

    def test_pattern_defaults_on_no_stagnation(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: None,
            check_interval_seconds=1,
            abort_after_cycles=5,
        )
        # Only one sample — not stagnated
        monitor._sample_once()
        assert not monitor.stagnated
        assert monitor.pattern_type == ""
        assert monitor.pattern_excerpt == ""


class TestRetryTrackerWithPattern:
    """Retry tracker stores and retrieves pattern classification."""

    def test_increment_stores_pattern(self, tmp_path):
        instance = str(tmp_path)
        increment_retry_count(
            instance, "test mission",
            pattern_type="tool_loop", pattern_excerpt="Bash Bash Bash",
        )
        info = get_retry_info(instance, "test mission")
        assert info["count"] == 1
        assert info["pattern_type"] == "tool_loop"
        assert info["sample_lines"] == "Bash Bash Bash"

    def test_backward_compat_with_int_format(self, tmp_path):
        """Old tracker format (bare int) still works."""
        from app.stagnation_monitor import _mission_key, _retry_tracker_path
        instance = str(tmp_path)
        path = _retry_tracker_path(instance)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = _mission_key("old mission")
        path.write_text(json.dumps({key: 3}))

        info = get_retry_info(instance, "old mission")
        assert info["count"] == 3
        assert info["pattern_type"] == ""

    def test_increment_preserves_latest_pattern(self, tmp_path):
        instance = str(tmp_path)
        increment_retry_count(
            instance, "flaky", pattern_type="tool_loop", pattern_excerpt="Read x5",
        )
        increment_retry_count(
            instance, "flaky", pattern_type="infinite_retry", pattern_excerpt="Error x3",
        )
        info = get_retry_info(instance, "flaky")
        assert info["count"] == 2
        assert info["pattern_type"] == "infinite_retry"
