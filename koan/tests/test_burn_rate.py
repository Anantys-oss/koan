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
        # 4 cost samples consumed (skipping the first) over 4 minutes → 1.0/min
        assert burn_rate.burn_rate_pct_per_minute(instance_dir) == pytest.approx(1.0)

    def test_variable_rate(self, instance_dir):
        # Five samples: costs 2, 4, 2, 4, 8 over 10 minutes
        _record_series(
            instance_dir,
            [(0, 2.0), (3, 4.0), (5, 2.0), (8, 4.0), (10, 8.0)],
        )
        # Consumed (excluding first) = 4+2+4+8 = 18 over 10 min = 1.8/min
        assert burn_rate.burn_rate_pct_per_minute(instance_dir) == pytest.approx(1.8)


class TestTimeToExhaustion:
    def test_no_history(self, instance_dir):
        assert burn_rate.time_to_exhaustion(instance_dir, 50.0) is None

    def test_basic_estimate(self, instance_dir):
        # 1%/min, 60% remaining → 60 min
        _record_series(instance_dir, [(i, 1.0) for i in range(5)])
        tte = burn_rate.time_to_exhaustion(instance_dir, session_pct=40.0)
        assert tte == pytest.approx(60.0)

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
