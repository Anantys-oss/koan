"""Tests for the rolling burn-rate estimator."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import burn_rate


@pytest.fixture
def instance_dir(tmp_path: Path) -> Path:
    return tmp_path


def _record_series(instance_dir: Path, samples):
    """Record a series of (offset_minutes, cost_pct) samples from a base time."""
    base = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    for offset_min, cost in samples:
        burn_rate.record_run(
            instance_dir, cost_pct=cost,
            timestamp=base + timedelta(minutes=offset_min),
        )
    return base


class TestSampleBuffer:
    def test_record_run_persists_sample(self, instance_dir):
        burn_rate.record_run(instance_dir, cost_pct=4.5)
        samples = burn_rate.get_samples(instance_dir)
        assert len(samples) == 1
        assert samples[0].cost_pct == pytest.approx(4.5)

    def test_buffer_caps_at_max_samples(self, instance_dir):
        for i in range(burn_rate.MAX_SAMPLES + 10):
            burn_rate.record_run(instance_dir, cost_pct=1.0)
        samples = burn_rate.get_samples(instance_dir)
        assert len(samples) == burn_rate.MAX_SAMPLES

    def test_buffer_keeps_newest_samples(self, instance_dir):
        # Older samples first, then newer
        base = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
        for i in range(burn_rate.MAX_SAMPLES + 5):
            burn_rate.record_run(
                instance_dir, cost_pct=float(i),
                timestamp=base + timedelta(minutes=i),
            )
        samples = burn_rate.get_samples(instance_dir)
        # The first sample retained should be cost_pct=5 (we dropped 0..4)
        assert samples[0].cost_pct == pytest.approx(5.0)
        assert samples[-1].cost_pct == pytest.approx(
            float(burn_rate.MAX_SAMPLES + 4)
        )

    def test_invalid_values_are_dropped(self, instance_dir):
        burn_rate.record_run(instance_dir, cost_pct=-1.0)
        burn_rate.record_run(instance_dir, cost_pct=float("nan"))
        burn_rate.record_run(instance_dir, cost_pct=float("inf"))
        assert burn_rate.get_samples(instance_dir) == []

    def test_invalid_values_log_warning(self, instance_dir, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="app.burn_rate"):
            burn_rate.record_run(instance_dir, cost_pct=-1.0)
            burn_rate.record_run(instance_dir, cost_pct=float("nan"))
            burn_rate.record_run(instance_dir, cost_pct=float("inf"))
        assert len(caplog.records) == 3
        assert all("Dropped invalid" in r.message for r in caplog.records)

    def test_persistence_across_calls(self, instance_dir):
        burn_rate.record_run(instance_dir, cost_pct=2.0)
        burn_rate.record_run(instance_dir, cost_pct=3.0)
        samples = burn_rate.get_samples(instance_dir)
        assert [s.cost_pct for s in samples] == [2.0, 3.0]

    def test_corrupt_state_file_recovers_empty(self, instance_dir):
        (instance_dir / burn_rate.BURN_RATE_FILE).write_text("not json")
        assert burn_rate.get_samples(instance_dir) == []
        # Recording after corruption rebuilds cleanly
        burn_rate.record_run(instance_dir, cost_pct=1.0)
        assert len(burn_rate.get_samples(instance_dir)) == 1

    def test_corrupt_state_writes_outbox_alert(self, instance_dir):
        """Corruption triggers a WARNING outbox message."""
        outbox = instance_dir / "outbox.md"
        (instance_dir / burn_rate.BURN_RATE_FILE).write_text("{bad json")
        burn_rate.get_samples(instance_dir)
        assert outbox.exists()
        content = outbox.read_text()
        assert "priority:warning" in content
        assert "corrupted" in content.lower()

    def test_corrupt_state_resets_file(self, instance_dir):
        """Corruption overwrites the file so the alert fires only once."""
        outbox = instance_dir / "outbox.md"
        (instance_dir / burn_rate.BURN_RATE_FILE).write_text("not json")
        burn_rate.get_samples(instance_dir)  # first load — triggers alert
        first_alert = outbox.read_text()

        # Second load reads the now-valid empty state — no new alert
        burn_rate.get_samples(instance_dir)
        assert outbox.read_text() == first_alert

    def test_corrupt_state_does_not_break_on_outbox_failure(self, instance_dir):
        """Alert failure must not prevent _load_state from returning."""
        (instance_dir / burn_rate.BURN_RATE_FILE).write_text("corrupt")
        # Make outbox dir unwritable to force append_to_outbox to fail
        outbox = instance_dir / "outbox.md"
        outbox.touch()
        outbox.chmod(0o000)
        try:
            # Must still return empty state without raising
            assert burn_rate.get_samples(instance_dir) == []
        finally:
            outbox.chmod(0o644)


class TestBurnRateEstimate:
    def test_no_history_returns_none(self, instance_dir):
        assert burn_rate.burn_rate_pct_per_minute(instance_dir) is None

    def test_insufficient_history_returns_none(self, instance_dir):
        _record_series(instance_dir, [(0, 1.0), (1, 1.0), (2, 1.0)])
        assert burn_rate.burn_rate_pct_per_minute(instance_dir) is None

    def test_zero_span_returns_none(self, instance_dir):
        # 5 samples all at the same timestamp
        base = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
        for _ in range(burn_rate.MIN_SAMPLES_FOR_ESTIMATE):
            burn_rate.record_run(instance_dir, cost_pct=1.0, timestamp=base)
        assert burn_rate.burn_rate_pct_per_minute(instance_dir) is None

    def test_constant_rate(self, instance_dir):
        # 5 samples, 1% each, spaced 1 minute apart
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        # Total cost 5 over 4 minutes → 1.25/min
        assert burn_rate.burn_rate_pct_per_minute(instance_dir) == pytest.approx(1.25)

    def test_variable_rate(self, instance_dir):
        # Five samples: costs 2, 4, 2, 4, 8 over 10 minutes
        _record_series(
            instance_dir,
            [(0, 2.0), (3, 4.0), (5, 2.0), (8, 4.0), (10, 8.0)],
        )
        # Total cost = 2+4+2+4+8 = 20 over 10 min = 2.0/min
        assert burn_rate.burn_rate_pct_per_minute(instance_dir) == pytest.approx(2.0)


class TestTimeToExhaustion:
    def test_no_history(self, instance_dir):
        assert burn_rate.time_to_exhaustion(instance_dir, 50.0) is None

    def test_basic_estimate(self, instance_dir):
        # 1.25%/min (5/4), 60% remaining → 48 min
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        tte = burn_rate.time_to_exhaustion(instance_dir, session_pct=40.0)
        assert tte == pytest.approx(48.0)

    def test_zero_remaining(self, instance_dir):
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        assert burn_rate.time_to_exhaustion(instance_dir, session_pct=100.0) == 0.0

    def test_mode_multiplier_makes_deep_faster(self, instance_dir):
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        implement = burn_rate.time_to_exhaustion(instance_dir, 50.0, mode="implement")
        deep = burn_rate.time_to_exhaustion(instance_dir, 50.0, mode="deep")
        review = burn_rate.time_to_exhaustion(instance_dir, 50.0, mode="review")
        assert deep < implement < review
        assert deep == pytest.approx(implement / 2.0)
        assert review == pytest.approx(implement * 2.0)


class TestWarningTracking:
    def test_mark_and_get(self, instance_dir):
        assert burn_rate.get_last_warned_at(instance_dir) is None
        burn_rate.mark_warned(instance_dir)
        ts = burn_rate.get_last_warned_at(instance_dir)
        assert ts is not None
        assert isinstance(ts, datetime)

    def test_mark_preserves_samples(self, instance_dir):
        burn_rate.record_run(instance_dir, cost_pct=2.0)
        burn_rate.mark_warned(instance_dir)
        samples = burn_rate.get_samples(instance_dir)
        assert len(samples) == 1
        assert samples[0].cost_pct == pytest.approx(2.0)

    def test_clear_warning(self, instance_dir):
        burn_rate.mark_warned(instance_dir)
        burn_rate.clear_warning(instance_dir)
        assert burn_rate.get_last_warned_at(instance_dir) is None


class TestBurnRateSnapshot:
    """Tests for the read-once BurnRateSnapshot class."""

    def test_snapshot_loads_samples_once(self, instance_dir):
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        snapshot = burn_rate.BurnRateSnapshot(instance_dir)
        assert len(snapshot.samples) == 5
        assert snapshot.samples[0].cost_pct == pytest.approx(1.0)

    def test_snapshot_burn_rate(self, instance_dir):
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        snapshot = burn_rate.BurnRateSnapshot(instance_dir)
        # Same result as the free function
        assert snapshot.burn_rate_pct_per_minute() == pytest.approx(1.25)

    def test_snapshot_time_to_exhaustion(self, instance_dir):
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        snapshot = burn_rate.BurnRateSnapshot(instance_dir)
        tte = snapshot.time_to_exhaustion(session_pct=40.0)
        assert tte == pytest.approx(48.0)

    def test_snapshot_time_to_exhaustion_with_mode(self, instance_dir):
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        snapshot = burn_rate.BurnRateSnapshot(instance_dir)
        deep = snapshot.time_to_exhaustion(50.0, mode="deep")
        impl = snapshot.time_to_exhaustion(50.0, mode="implement")
        assert deep == pytest.approx(impl / 2.0)

    def test_snapshot_last_warned_at(self, instance_dir):
        snapshot = burn_rate.BurnRateSnapshot(instance_dir)
        assert snapshot.last_warned_at is None

        burn_rate.mark_warned(instance_dir)
        snapshot = burn_rate.BurnRateSnapshot(instance_dir)
        assert snapshot.last_warned_at is not None

    def test_snapshot_is_frozen(self, instance_dir):
        """Snapshot is not affected by writes after construction."""
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        snapshot = burn_rate.BurnRateSnapshot(instance_dir)
        assert len(snapshot.samples) == 5

        # Write more samples after snapshot was taken
        burn_rate.record_run(instance_dir, cost_pct=99.0)
        # Snapshot still sees the old state
        assert len(snapshot.samples) == 5

    def test_snapshot_empty_state(self, instance_dir):
        snapshot = burn_rate.BurnRateSnapshot(instance_dir)
        assert snapshot.samples == []
        assert snapshot.last_warned_at is None
        assert snapshot.burn_rate_pct_per_minute() is None
        assert snapshot.time_to_exhaustion(50.0) is None


class TestTOCTOURace:
    """Verify that concurrent mutations don't silently lose data."""

    def test_concurrent_record_run_preserves_all_samples(self, instance_dir):
        """Concurrent record_run calls must not silently drop samples.

        Before the fix, record_run() did load -> modify -> save without
        holding a lock across the cycle.  Two concurrent writers could both
        read the same state, each append their sample, and the second write
        would silently overwrite the first's sample.
        """
        import threading

        n_writers = 10
        barrier = threading.Barrier(n_writers, timeout=5)
        errors = []

        base = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)

        def writer(idx):
            try:
                barrier.wait()
                burn_rate.record_run(
                    instance_dir,
                    cost_pct=float(idx + 1),  # 1.0 .. 10.0
                    timestamp=base + timedelta(minutes=idx),
                )
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=writer, args=(i,))
            for i in range(n_writers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        samples = burn_rate.get_samples(instance_dir)
        assert len(samples) == n_writers, (
            f"Expected {n_writers} samples but got {len(samples)} — "
            f"TOCTOU race lost {n_writers - len(samples)} samples"
        )

    def test_concurrent_mark_warned_preserves_samples(self, instance_dir):
        """mark_warned() must not clobber samples written concurrently."""
        import threading

        # Seed with samples
        base = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
        for i in range(5):
            burn_rate.record_run(
                instance_dir, cost_pct=float(i + 1),
                timestamp=base + timedelta(minutes=i),
            )

        barrier = threading.Barrier(2, timeout=5)
        errors = []

        def do_record():
            try:
                barrier.wait()
                burn_rate.record_run(
                    instance_dir, cost_pct=99.0,
                    timestamp=base + timedelta(minutes=99),
                )
            except Exception as e:
                errors.append(str(e))

        def do_warn():
            try:
                barrier.wait()
                burn_rate.mark_warned(instance_dir)
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=do_record)
        t2 = threading.Thread(target=do_warn)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors
        samples = burn_rate.get_samples(instance_dir)
        assert len(samples) == 6, (
            f"Expected 6 samples but got {len(samples)} — "
            f"concurrent mark_warned lost samples"
        )
        assert burn_rate.get_last_warned_at(instance_dir) is not None

    def test_lock_file_created(self, instance_dir):
        """_mutate_state creates a lock file next to the state file."""
        burn_rate.record_run(instance_dir, cost_pct=1.0)
        lock_path = instance_dir / burn_rate.LOCK_FILE
        assert lock_path.exists()


class TestStateFile:
    def test_file_layout(self, instance_dir):
        burn_rate.record_run(instance_dir, cost_pct=2.5)
        burn_rate.mark_warned(instance_dir)
        data = json.loads(
            (instance_dir / burn_rate.BURN_RATE_FILE).read_text()
        )
        assert "samples" in data
        assert "last_warned_at" in data
        assert data["samples"][0]["cost_pct"] == pytest.approx(2.5)
