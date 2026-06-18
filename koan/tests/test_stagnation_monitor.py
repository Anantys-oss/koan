"""Tests for stagnation_monitor — hash logic, escalation, config integration, retry tracking."""

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import app.stagnation_monitor as stagnation_monitor
from app.stagnation_monitor import (
    StagnationMonitor,
    _mission_key,
    _prune_tracker,
    _tail_hash,
    classify_stagnation,
    clear_retry_count,
    get_crash_count,
    get_retry_count,
    get_retry_info,
    get_total_attempts,
    increment_crash_count,
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


class TestStagnationOffByOne:
    """Regression test: abort_after_cycles counts duplicates, not total samples.

    With abort_after_cycles=3, the monitor should need 3 consecutive
    *duplicate* hashes (i.e. 3 comparisons where current == previous)
    before aborting. The first observation of a hash is a baseline, not
    a duplicate. Total samples to abort = abort_after_cycles + 1.

    Bug: _consecutive was initialised to 1 on a new hash, so the first
    observation was counted as a duplicate. This caused abort after only
    abort_after_cycles total samples (abort_after_cycles - 1 actual
    duplicates), killing missions one sample early.
    """

    def test_no_abort_after_abort_after_cycles_total_samples(self, tmp_path):
        """abort_after_cycles=3 must NOT abort after only 3 total samples."""
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        aborts = []
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: aborts.append(True),
            check_interval_seconds=1,
            abort_after_cycles=3,
        )
        # 3 samples with the same hash: 1 baseline + 2 duplicates.
        # With abort_after_cycles=3, this should NOT be enough.
        monitor._sample_once()  # baseline
        monitor._sample_once()  # 1st duplicate
        monitor._sample_once()  # 2nd duplicate
        assert not monitor.stagnated, (
            "abort_after_cycles=3 should require 3 duplicates, not 2"
        )
        assert aborts == []

    def test_aborts_after_abort_after_cycles_duplicates(self, tmp_path):
        """abort_after_cycles=3 must abort after 3 actual duplicates (4 total)."""
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        aborts = []
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: aborts.append(True),
            check_interval_seconds=1,
            abort_after_cycles=3,
        )
        monitor._sample_once()  # baseline
        monitor._sample_once()  # 1st duplicate
        monitor._sample_once()  # 2nd duplicate
        monitor._sample_once()  # 3rd duplicate → abort
        assert monitor.stagnated is True
        assert aborts == [True]


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
        monitor._sample_once()  # sample 1 → baseline (consecutive=0)
        monitor._sample_once()  # sample 2 → 1st duplicate → warn fires
        assert warns == [1]
        assert not monitor.stagnated
        assert aborts == []
        monitor._sample_once()  # sample 3 → 2nd duplicate
        assert not monitor.stagnated
        monitor._sample_once()  # sample 4 → 3rd duplicate → abort fires
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
        monitor._sample_once()  # baseline (consecutive=0)
        monitor._sample_once()  # 1st duplicate → warn
        monitor._sample_once()  # 2nd duplicate → no additional warn
        monitor._sample_once()  # 3rd duplicate → no additional warn
        assert warns == [1]

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
        monitor._sample_once()  # baseline
        monitor._sample_once()  # 1st duplicate → warn (blows up)
        monitor._sample_once()  # 2nd duplicate
        monitor._sample_once()  # 3rd duplicate → abort
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


class TestTailHashEdgeCases:
    """Cover remaining _tail_hash branches — OSError on read, binary content."""

    def test_returns_none_when_file_unreadable_during_read(self, tmp_path):
        """OSError during open/read returns None (lines 84-85)."""
        import os
        if os.getuid() == 0:
            pytest.skip("chmod(000) has no effect when running as root")
        f = tmp_path / "out.log"
        _make_stdout(f, 60)
        # Make file unreadable — getsize succeeds but open fails.
        f.chmod(0o000)
        try:
            assert _tail_hash(str(f), 50) is None
        finally:
            f.chmod(0o644)

    def test_hash_stable_with_binary_content(self, tmp_path):
        """Binary content (non-UTF-8) still hashes deterministically."""
        f = tmp_path / "out.log"
        f.write_bytes(b"\x80\xff" * 300 + b"\n" * 60)
        h1 = _tail_hash(str(f), 50)
        h2 = _tail_hash(str(f), 50)
        assert h1 is not None
        assert h1 == h2

    def test_long_jsonl_lines_captured_fully(self, tmp_path):
        """Regression: lines * 200 was too small for long JSONL lines.

        With 50 sample_lines and ~1 KiB per line, the old 10 KiB window
        only captured ~10 lines, making the hash unstable as the file
        grew. The 4 KiB-per-line multiplier guarantees the full tail is
        read so stagnation is detected correctly.
        """
        f = tmp_path / "out.log"
        # 60 lines of ~1200 bytes each (structured JSONL-like content)
        lines = [json.dumps({"iteration": i, "data": "x" * 1000}) for i in range(60)]
        f.write_text("\n".join(lines) + "\n")

        # Must return a stable hash over the last 50 lines.
        h1 = _tail_hash(str(f), 50)
        h2 = _tail_hash(str(f), 50)
        assert h1 is not None
        assert h1 == h2

        # Append one identical line — tail should still be captured fully
        # and hash should change because the line window shifts.
        with open(f, "a") as fh:
            fh.write(json.dumps({"iteration": 99, "data": "x" * 1000}) + "\n")
        h3 = _tail_hash(str(f), 50)
        assert h3 != h1


class TestSampleOnceNoneHash:
    """Cover _sample_once reset when hash returns None (lines 175-177)."""

    def test_none_hash_resets_consecutive_counter(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        aborts = []
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: aborts.append(True),
            check_interval_seconds=1,
            abort_after_cycles=3,
        )
        # Build up consecutive identical hashes.
        monitor._sample_once()  # baseline (consecutive=0)
        monitor._sample_once()  # 1st duplicate (consecutive=1)

        # Truncate the file so _tail_hash returns None.
        f.write_text("tiny")
        monitor._sample_once()  # None → reset to 0
        assert monitor._consecutive == 0

        # Restore output; count must start over from 0.
        _make_stdout(f, 60)
        monitor._sample_once()  # baseline (consecutive=0)
        monitor._sample_once()  # 1st duplicate (consecutive=1)
        assert not monitor.stagnated  # would need 3 duplicates

    def test_none_hash_does_not_count_toward_stagnation(self, tmp_path):
        """Consecutive None returns must never trigger abort."""
        f = tmp_path / "stdout.log"
        f.write_text("tiny")  # below _DEFAULT_MIN_BYTES

        aborts = []
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: aborts.append(True),
            check_interval_seconds=1,
            abort_after_cycles=2,
        )
        for _ in range(10):
            monitor._sample_once()
        assert not monitor.stagnated
        assert aborts == []


class TestAbortCallbackException:
    """Cover on_abort exception handling (lines 204-207)."""

    def test_abort_exception_does_not_prevent_stagnated_flag(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        def _bad_abort():
            raise RuntimeError("kill failed")

        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=_bad_abort,
            check_interval_seconds=1,
            abort_after_cycles=2,
        )
        monitor._sample_once()  # baseline
        monitor._sample_once()  # 1st duplicate
        monitor._sample_once()  # 2nd duplicate → abort
        assert monitor.stagnated is True


class TestStagnationResetAndRestagnation:
    """Cover the warned-reset path when fresh output appears mid-stagnation."""

    def test_fresh_output_resets_warn_flag(self, tmp_path):
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
        monitor._sample_once()  # baseline (consecutive=0)
        monitor._sample_once()  # 1st duplicate → warn fires
        assert warns == [1]

        # Fresh output resets.
        with open(f, "a") as fh:
            fh.write("new progress that changes the tail hash\n")
        monitor._sample_once()  # new baseline (consecutive=0), warned=False

        # Stagnate again — warn fires a second time.
        monitor._sample_once()  # 1st duplicate → warn fires
        assert warns == [1, 1]


# ---------------------------------------------------------------------------
# Retry Tracker Tests
# ---------------------------------------------------------------------------


class TestMissionKey:
    def test_deterministic(self):
        a = _mission_key("fix the bug")
        b = _mission_key("fix the bug")
        assert a == b

    def test_different_titles_produce_different_keys(self):
        a = _mission_key("fix the bug")
        b = _mission_key("add a feature")
        assert a != b

    def test_returns_hex_string(self):
        key = _mission_key("hello")
        assert len(key) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in key)

    def test_golden_hash_unchanged(self):
        """Regression guard (S2): the key is sha256 of the canonical-clean title.

        If this value drifts, every existing stagnation/crash tracker entry is
        orphaned (a live mission silently resets its retry counter). The
        canonical key for "fix the bug" is just the text itself.
        """
        assert _mission_key("fix the bug") == (
            "280fd7e3571b7c850d6fe771aee96db0ccdeef47910414e3a4a93bb06ea9f7c2"
        )

    def test_delegates_to_canonical_mission_key(self):
        """_mission_key must hash exactly missions.canonical_mission_key output."""
        import hashlib
        from app.missions import canonical_mission_key
        title = "- do thing [project:foo] [r:3] ⏳(2026-01-01T00:00) [complexity:large]"
        expected = hashlib.sha256(
            canonical_mission_key(title).encode("utf-8", errors="replace")
        ).hexdigest()
        assert _mission_key(title) == expected

    def test_strips_queued_timestamp(self):
        base = _mission_key("fix the bug [project:foo]")
        with_ts = _mission_key("fix the bug [project:foo] ⏳(2026-01-01T12:00)")
        assert base == with_ts

    def test_strips_started_timestamp(self):
        base = _mission_key("fix the bug [project:foo]")
        with_ts = _mission_key("fix the bug [project:foo] ⏳(2026-01-01T12:00) ▶(2026-01-01T12:05)")
        assert base == with_ts

    def test_strips_recovery_counter(self):
        base = _mission_key("fix the bug [project:foo]")
        with_r = _mission_key("fix the bug [project:foo] [r:2]")
        assert base == with_r

    def test_strips_complexity_tag(self):
        base = _mission_key("fix the bug [project:foo]")
        with_c = _mission_key("fix the bug [project:foo] [complexity:large]")
        assert base == with_c

    def test_stable_across_requeue_cycle(self):
        """Key must match across all lifecycle states of the same mission."""
        clean = _mission_key("fix the bug [project:foo]")
        in_progress = _mission_key(
            "- fix the bug [project:foo] [r:1] ⏳(2026-01-01T12:00) ▶(2026-01-01T12:05)"
        )
        requeued = _mission_key("- fix the bug [project:foo] [r:1]")
        assert clean == in_progress == requeued


class TestRetryTracker:
    """Cover get_retry_count, increment_retry_count, clear_retry_count."""

    def test_get_returns_zero_for_unknown_mission(self, tmp_path):
        assert get_retry_count(str(tmp_path), "never-seen") == 0

    def test_increment_returns_new_count(self, tmp_path):
        d = str(tmp_path)
        assert increment_retry_count(d, "mission A") == 1
        assert increment_retry_count(d, "mission A") == 2
        assert increment_retry_count(d, "mission A") == 3

    def test_get_reads_persisted_count(self, tmp_path):
        d = str(tmp_path)
        increment_retry_count(d, "mission B")
        increment_retry_count(d, "mission B")
        assert get_retry_count(d, "mission B") == 2

    def test_clear_removes_counter(self, tmp_path):
        d = str(tmp_path)
        increment_retry_count(d, "mission C")
        increment_retry_count(d, "mission C")
        clear_retry_count(d, "mission C")
        assert get_retry_count(d, "mission C") == 0

    def test_clear_noop_for_unknown_mission(self, tmp_path):
        """Clearing a non-existent key must not raise."""
        clear_retry_count(str(tmp_path), "ghost mission")

    def test_independent_missions_do_not_interfere(self, tmp_path):
        d = str(tmp_path)
        increment_retry_count(d, "alpha")
        increment_retry_count(d, "alpha")
        increment_retry_count(d, "beta")
        assert get_retry_count(d, "alpha") == 2
        assert get_retry_count(d, "beta") == 1
        clear_retry_count(d, "alpha")
        assert get_retry_count(d, "alpha") == 0
        assert get_retry_count(d, "beta") == 1

    def test_handles_corrupt_json_file(self, tmp_path):
        """Corrupt tracker file should be treated as empty."""
        tracker = tmp_path / ".mission-retries.json"
        tracker.write_text("not valid json {{{")
        assert get_retry_count(str(tmp_path), "mission X") == 0
        # Increment should overwrite the corrupt file.
        assert increment_retry_count(str(tmp_path), "mission X") == 1

    def test_handles_non_dict_json(self, tmp_path):
        """JSON that is a list (not a dict) should be treated as empty."""
        tracker = tmp_path / ".mission-retries.json"
        tracker.write_text("[1, 2, 3]")
        assert get_retry_count(str(tmp_path), "any") == 0

    def test_handles_non_integer_value(self, tmp_path):
        """A stored value that isn't an int should default to 0."""
        key = _mission_key("broken")
        tracker = tmp_path / ".mission-retries.json"
        tracker.write_text(json.dumps({key: "not-a-number"}))
        assert get_retry_count(str(tmp_path), "broken") == 0

    def test_increment_handles_non_integer_stored_value(self, tmp_path):
        """increment on a corrupt stored value should treat it as 0 and return 1."""
        key = _mission_key("corrupt-entry")
        tracker = tmp_path / ".mission-retries.json"
        tracker.write_text(json.dumps({key: [1, 2]}))
        assert increment_retry_count(str(tmp_path), "corrupt-entry") == 1

    def test_save_handles_oserror(self, tmp_path):
        """OSError during save is logged to stderr, not raised."""
        d = str(tmp_path)
        with patch("app.utils.atomic_write", side_effect=OSError("disk full")):
            # Should not raise — the OSError is caught and printed to stderr.
            increment_retry_count(d, "test mission")

    def test_clear_with_clear_total_false_preserves_total_attempts(self, tmp_path):
        """clear_retry_count(clear_total=False) resets stagnation count but keeps total_attempts and crash_count."""
        d = str(tmp_path)
        increment_retry_count(d, "mission X")
        increment_retry_count(d, "mission X")
        increment_crash_count(d, "mission X")
        clear_retry_count(d, "mission X", clear_total=False)
        assert get_retry_count(d, "mission X") == 0
        assert get_total_attempts(d, "mission X") == 3  # 2 stagnation + 1 crash
        assert get_crash_count(d, "mission X") == 1

    def test_clear_with_clear_total_true_resets_everything(self, tmp_path):
        """clear_retry_count() (default) removes the entry entirely including total_attempts."""
        d = str(tmp_path)
        increment_retry_count(d, "mission Y")
        increment_crash_count(d, "mission Y")
        clear_retry_count(d, "mission Y")
        assert get_retry_count(d, "mission Y") == 0
        assert get_total_attempts(d, "mission Y") == 0
        assert get_crash_count(d, "mission Y") == 0

    def test_increment_retry_does_not_change_crash_count(self, tmp_path):
        """increment_retry_count (stagnation) must not affect crash_count."""
        d = str(tmp_path)
        increment_retry_count(d, "stagnation mission")
        increment_retry_count(d, "stagnation mission")
        assert get_crash_count(d, "stagnation mission") == 0
        assert get_retry_count(d, "stagnation mission") == 2


class TestTotalAttempts:
    """Tests for the cross-system total_attempts counter."""

    def test_get_total_returns_zero_for_unknown_mission(self, tmp_path):
        assert get_total_attempts(str(tmp_path), "unseen") == 0

    def test_increment_retry_also_increments_total(self, tmp_path):
        d = str(tmp_path)
        increment_retry_count(d, "mission A")
        increment_retry_count(d, "mission A")
        assert get_total_attempts(d, "mission A") == 2

    def test_increment_total_without_stagnation(self, tmp_path):
        """increment_crash_count increments total_attempts without touching stagnation count."""
        d = str(tmp_path)
        increment_crash_count(d, "crash mission")
        increment_crash_count(d, "crash mission")
        assert get_total_attempts(d, "crash mission") == 2
        assert get_retry_count(d, "crash mission") == 0

    def test_combined_total_across_stagnation_and_crash(self, tmp_path):
        """total_attempts accumulates across both stagnation and crash-recovery increments."""
        d = str(tmp_path)
        increment_retry_count(d, "flaky mission")   # stagnation
        increment_crash_count(d, "flaky mission")   # crash-recovery
        increment_retry_count(d, "flaky mission")   # stagnation again
        assert get_retry_count(d, "flaky mission") == 2
        assert get_total_attempts(d, "flaky mission") == 3

    def test_total_survives_stagnation_counter_clear(self, tmp_path):
        """Clearing the stagnation counter with clear_total=False leaves total_attempts intact."""
        d = str(tmp_path)
        increment_retry_count(d, "sticky mission")  # stagnation (total = 1)
        increment_crash_count(d, "sticky mission")  # crash (total = 2)
        clear_retry_count(d, "sticky mission", clear_total=False)
        assert get_retry_count(d, "sticky mission") == 0
        assert get_total_attempts(d, "sticky mission") == 2

    def test_total_reset_on_success_clear(self, tmp_path):
        """Full clear (default) wipes total_attempts — used after genuine success."""
        d = str(tmp_path)
        increment_retry_count(d, "mission Z")
        increment_crash_count(d, "mission Z")
        clear_retry_count(d, "mission Z")  # clear_total=True by default
        assert get_total_attempts(d, "mission Z") == 0

    def test_get_retry_info_includes_total_attempts(self, tmp_path):
        """get_retry_info() includes total_attempts in its return dict."""
        d = str(tmp_path)
        increment_retry_count(d, "info mission")
        increment_crash_count(d, "info mission")
        info = get_retry_info(d, "info mission")
        assert info["count"] == 1
        assert info["total_attempts"] == 2

    def test_increment_total_handles_fresh_entry(self, tmp_path):
        """increment_crash_count creates a new entry when mission has no prior tracker data."""
        d = str(tmp_path)
        result = increment_crash_count(d, "brand new mission")
        assert result == 1
        assert get_total_attempts(d, "brand new mission") == 1
        assert get_retry_count(d, "brand new mission") == 0
        assert get_crash_count(d, "brand new mission") == 1

    def test_key_stable_across_lifecycle_markers_for_total(self, tmp_path):
        """total_attempts key is stable regardless of [r:N] tags or timestamps."""
        d = str(tmp_path)
        increment_crash_count(d, "- Fix the tests [r:1]")
        assert get_total_attempts(d, "- Fix the tests") == 1
        assert get_total_attempts(d, "- Fix the tests [r:2]") == 1


class TestCrashCount:
    """Tests for crash_count tracking — separate from stagnation requeue count."""

    def test_get_crash_count_returns_zero_for_unknown(self, tmp_path):
        assert get_crash_count(str(tmp_path), "unseen mission") == 0

    def test_increment_crash_count_increments_both_crash_and_total(self, tmp_path):
        d = str(tmp_path)
        result = increment_crash_count(d, "crash mission")
        assert result == 1
        assert get_crash_count(d, "crash mission") == 1
        assert get_total_attempts(d, "crash mission") == 1

    def test_increment_crash_count_does_not_change_stagnation_count(self, tmp_path):
        d = str(tmp_path)
        increment_crash_count(d, "crash mission")
        increment_crash_count(d, "crash mission")
        assert get_retry_count(d, "crash mission") == 0
        assert get_crash_count(d, "crash mission") == 2

    def test_crash_count_key_stable_across_lifecycle_markers(self, tmp_path):
        d = str(tmp_path)
        increment_crash_count(d, "- Fix the bug [r:1]")
        assert get_crash_count(d, "- Fix the bug") == 1
        assert get_crash_count(d, "- Fix the bug [r:2]") == 1

    def test_clear_full_also_removes_crash_count(self, tmp_path):
        d = str(tmp_path)
        increment_crash_count(d, "mission A")
        clear_retry_count(d, "mission A")
        assert get_crash_count(d, "mission A") == 0

    def test_clear_preserve_total_also_preserves_crash_count(self, tmp_path):
        d = str(tmp_path)
        increment_crash_count(d, "mission B")
        increment_crash_count(d, "mission B")
        clear_retry_count(d, "mission B", clear_total=False)
        # crash_count should be preserved
        assert get_crash_count(d, "mission B") == 2
        assert get_total_attempts(d, "mission B") == 2


class TestClassifyStagnationEdgeCases:
    """Cover edge cases in classify_stagnation: OSError on read, line trimming, empty decode."""

    def test_oserror_on_file_read_returns_silent(self, tmp_path):
        """OSError during open (not getsize) → silent. Covers L149-150."""
        f = tmp_path / "stdout.log"
        # Write enough content to pass the min-bytes threshold
        _make_stdout(f, 60)
        # Make file unreadable after getsize succeeds
        with patch("builtins.open", side_effect=OSError("permission denied")):
            pattern, excerpt = classify_stagnation(str(f))
        assert pattern == "silent"
        assert excerpt == ""

    def test_lines_trimmed_to_tail_lines(self, tmp_path):
        """When file has more lines than tail_lines, only tail is kept. Covers L154-155."""
        f = tmp_path / "stdout.log"
        # Write 200 lines — well above default tail_lines (100)
        lines = [f"line {i:04d} with padding text here please" for i in range(200)]
        # Put a tool_loop signature only in the first 50 lines (should be trimmed away)
        for i in range(10):
            lines[i] = f"Calling Bash tool: ls iteration {i}"
        # Tail 100 lines should have no tool pattern → unknown
        f.write_text("\n".join(lines) + "\n")
        pattern, _ = classify_stagnation(str(f), tail_lines=100)
        # The Bash lines are in lines 0-9, trimmed away — should not be tool_loop
        assert pattern != "tool_loop"

    def test_empty_lines_after_decode_returns_silent(self, tmp_path):
        """File truncated between getsize and read → empty splitlines → silent. Covers L157-158."""
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)
        # Simulate TOCTOU: file passes size check but read returns empty
        real_open = open

        def mock_open_empty(path, mode="r", **kw):
            fh = real_open(path, mode, **kw)
            fh.read = lambda *a, **k: b""
            fh.seek = lambda *a, **k: None
            return fh

        with patch("builtins.open", side_effect=mock_open_empty):
            pattern, excerpt = classify_stagnation(str(f))
        assert pattern == "silent"


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
        monitor._sample_once()  # baseline
        monitor._sample_once()  # 1st duplicate
        monitor._sample_once()  # 2nd duplicate → abort
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


class TestClassifyExceptionInSampleOnce:
    """When classification raises during _sample_once, monitor still sets stagnated=True."""

    def test_classify_exception_sets_unknown(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: None,
            check_interval_seconds=1,
            abort_after_cycles=2,
        )
        monitor._sample_once()  # baseline (consecutive=0)
        monitor._sample_once()  # 1st duplicate (consecutive=1)
        assert not monitor.stagnated

        # Patch _classify_from_bytes to raise on the abort path — this
        # 2nd duplicate is the one that crosses abort_after_cycles=2.
        with patch(
            "app.stagnation_monitor._classify_from_bytes",
            side_effect=RuntimeError("disk error"),
        ):
            monitor._sample_once()  # 2nd duplicate (consecutive=2) → abort path

        assert monitor.stagnated
        assert monitor.pattern_type == "unknown"
        assert monitor.pattern_excerpt == ""


class TestSingleReadOnAbort:
    """_sample_once() must read the stdout file only once, even when stagnation fires.

    Before the fix, _tail_hash() and classify_stagnation() each open the file
    independently — two reads of a potentially multi-megabyte file, seeing
    slightly different snapshots because the subprocess keeps writing.

    After the fix, a single _read_tail() call covers both the hash window and
    the classification window.
    """

    def test_file_opened_once_on_abort_sample(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: None,
            abort_after_cycles=2,
        )
        monitor._sample_once()  # baseline
        monitor._sample_once()  # 1st duplicate

        real_read_tail = stagnation_monitor._read_tail
        call_count = []

        def counting_read_tail(stdout_file, lines):
            call_count.append(lines)
            return real_read_tail(stdout_file, lines)

        with patch("app.stagnation_monitor._read_tail", side_effect=counting_read_tail):
            monitor._sample_once()  # 2nd duplicate → abort

        assert monitor.stagnated
        assert len(call_count) == 1, (
            f"Expected 1 _read_tail call on abort sample, got {len(call_count)}"
        )

    def test_file_opened_once_on_non_abort_sample(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: None,
            abort_after_cycles=3,
        )

        real_read_tail = stagnation_monitor._read_tail
        call_count = []

        def counting_read_tail(stdout_file, lines):
            call_count.append(lines)
            return real_read_tail(stdout_file, lines)

        with patch("app.stagnation_monitor._read_tail", side_effect=counting_read_tail):
            monitor._sample_once()  # baseline
            monitor._sample_once()  # 1st duplicate (warn)
            monitor._sample_once()  # 2nd duplicate (no abort yet)

        assert not monitor.stagnated
        assert len(call_count) == 3, (
            f"Expected 1 _read_tail call per sample (3 samples), got {len(call_count)}"
        )


class TestMonitorLoopIntegration:
    """Cover _loop method (L278-280) — starts, samples, and stops on stagnation."""

    def test_loop_stops_on_stagnation(self, tmp_path):
        f = tmp_path / "stdout.log"
        _make_stdout(f, 60)

        aborted = threading.Event()
        monitor = StagnationMonitor(
            stdout_file=str(f),
            on_abort=lambda: aborted.set(),
            check_interval_seconds=0.05,
            abort_after_cycles=2,
        )
        monitor.start()
        # Wait for stagnation (output never changes)
        aborted.wait(timeout=5.0)
        monitor.stop(timeout=2.0)
        assert monitor.stagnated
        assert aborted.is_set()


class TestGetRetryInfoFallback:
    """Cover get_retry_info fallback for non-int, non-dict stored values. Covers L412."""

    def test_unexpected_type_returns_zero_defaults(self, tmp_path):
        """A stored value that is neither int nor dict → zero defaults."""
        from app.stagnation_monitor import _retry_tracker_path
        instance = str(tmp_path)
        path = _retry_tracker_path(instance)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = _mission_key("weird mission")
        # Store a string value (not int or dict)
        path.write_text(json.dumps({key: "not-a-valid-entry"}))

        info = get_retry_info(instance, "weird mission")
        assert info["count"] == 0
        assert info["pattern_type"] == ""
        assert info["sample_lines"] == ""

    def test_list_value_returns_zero_defaults(self, tmp_path):
        """A stored list value → zero defaults."""
        from app.stagnation_monitor import _retry_tracker_path
        instance = str(tmp_path)
        path = _retry_tracker_path(instance)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = _mission_key("list mission")
        path.write_text(json.dumps({key: [1, 2, 3]}))

        info = get_retry_info(instance, "list mission")
        assert info["count"] == 0
        assert info["pattern_type"] == ""
        assert info["sample_lines"] == ""


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


class TestRetryTrackerConcurrency:
    """Concurrent increment_retry_count must not lose updates."""

    def test_concurrent_increments_no_lost_updates(self, tmp_path):
        """N threads incrementing the same mission must all be counted."""
        import threading

        n = 30
        barrier = threading.Barrier(n)

        def _inc():
            barrier.wait()
            increment_retry_count(str(tmp_path), "shared mission")

        threads = [threading.Thread(target=_inc) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert get_retry_count(str(tmp_path), "shared mission") == n


class TestMigrationTrackerCache:
    """_migrate_tracker_filename caches after first check per instance_dir."""

    @pytest.fixture(autouse=True)
    def _clear_migration_cache(self):
        yield
        from app.stagnation_monitor import _migration_checked
        _migration_checked.clear()

    def test_migration_stat_only_once(self, tmp_path):
        """After first call, subsequent calls skip the filesystem check."""
        from app.stagnation_monitor import (
            _RETRY_TRACKER_OLD_FILENAME,
            _migrate_tracker_filename,
            _migration_checked,
        )

        instance = str(tmp_path)
        _migration_checked.discard(instance)

        old_path = tmp_path / _RETRY_TRACKER_OLD_FILENAME

        _migrate_tracker_filename(instance)
        assert instance in _migration_checked

        old_path.write_text('{"late": 1}')
        _migrate_tracker_filename(instance)
        _migrate_tracker_filename(instance)
        assert old_path.exists()

    def test_migration_performs_rename_then_caches(self, tmp_path):
        """When old file exists, rename happens once, then cache prevents re-check."""
        from app.stagnation_monitor import (
            _RETRY_TRACKER_FILENAME,
            _RETRY_TRACKER_OLD_FILENAME,
            _migrate_tracker_filename,
            _migration_checked,
        )

        instance = str(tmp_path)
        _migration_checked.discard(instance)

        old_path = tmp_path / _RETRY_TRACKER_OLD_FILENAME
        old_path.write_text('{"k": 1}')

        _migrate_tracker_filename(instance)
        new_path = tmp_path / _RETRY_TRACKER_FILENAME
        assert new_path.exists()
        assert not old_path.exists()
        assert instance in _migration_checked

    def test_different_instance_dirs_checked_independently(self, tmp_path):
        """Each instance_dir gets its own cache entry."""
        from app.stagnation_monitor import _migrate_tracker_filename, _migration_checked

        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        _migration_checked.discard(dir_a)
        _migration_checked.discard(dir_b)

        _migrate_tracker_filename(dir_a)
        assert dir_a in _migration_checked
        assert dir_b not in _migration_checked

        _migrate_tracker_filename(dir_b)
        assert dir_b in _migration_checked


class TestPruneTracker:
    def test_removes_old_entries(self):
        old_ts = time.time() - 31 * 86400
        data = {
            "hash_old": {"count": 2, "updated_at": old_ts},
            "hash_new": {"count": 1, "updated_at": time.time()},
        }
        removed = _prune_tracker(data, max_age_days=30)
        assert removed == 1
        assert "hash_old" not in data
        assert "hash_new" in data

    def test_prunes_legacy_entries_without_updated_at(self):
        data = {
            "hash_legacy": {"count": 3, "pattern_type": "loop"},
        }
        removed = _prune_tracker(data, max_age_days=30)
        assert removed == 1
        assert "hash_legacy" not in data

    def test_prunes_legacy_scalar_entries(self):
        data = {
            "hash_int": 3,
            "hash_dict": {"count": 1, "updated_at": time.time()},
        }
        removed = _prune_tracker(data, max_age_days=30)
        assert removed == 1
        assert "hash_int" not in data
        assert "hash_dict" in data

    def test_empty_data_noop(self):
        assert _prune_tracker({}) == 0

    def test_increment_sets_updated_at(self, tmp_path):
        increment_retry_count(str(tmp_path), "test mission")
        tracker_path = tmp_path / ".mission-retries.json"
        data = json.loads(tracker_path.read_text())
        key = _mission_key("test mission")
        assert "updated_at" in data[key]
        assert data[key]["updated_at"] > 0

    def test_increment_crash_sets_updated_at(self, tmp_path):
        increment_crash_count(str(tmp_path), "test mission")
        tracker_path = tmp_path / ".mission-retries.json"
        data = json.loads(tracker_path.read_text())
        key = _mission_key("test mission")
        assert "updated_at" in data[key]

    def test_clear_partial_preserves_updated_at(self, tmp_path):
        d = str(tmp_path)
        increment_crash_count(d, "test mission")
        clear_retry_count(d, "test mission", clear_total=False)
        tracker_path = tmp_path / ".mission-retries.json"
        data = json.loads(tracker_path.read_text())
        key = _mission_key("test mission")
        assert "updated_at" in data[key]
        assert data[key]["updated_at"] > 0
