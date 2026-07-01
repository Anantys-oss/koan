import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.memory_monitor import MemoryMonitor, read_rss_mb, get_memory_status

_HAS_PROC = Path("/proc/self/status").exists()


def test_read_rss_mb_positive():
    assert read_rss_mb() > 0  # this process has a real RSS


def test_sample_triggers_after_sustained_overage():
    m = MemoryMonitor(threshold_mb=1, sustained_samples=3)
    # threshold 1 MB is always exceeded by the test process
    assert m.sample() is False  # 1/3
    assert m.sample() is False  # 2/3
    assert m.sample() is True   # 3/3 -> restart signal
    assert m.last_rss_mb > 0


def test_counter_resets_when_below_threshold():
    m = MemoryMonitor(threshold_mb=10**9, sustained_samples=2)
    assert m.sample() is False  # never over a 1 PB threshold
    assert m._over_count == 0


def test_zero_threshold_never_triggers():
    m = MemoryMonitor(threshold_mb=0, sustained_samples=1)
    assert m.sample() is False


def test_top_allocations_empty_when_disabled():
    m = MemoryMonitor(threshold_mb=1, sustained_samples=1, tracemalloc_enabled=False)
    assert m.tracemalloc_enabled is False
    assert m.top_allocations() == []


def test_top_allocations_returns_data_when_enabled():
    m = MemoryMonitor(threshold_mb=1, sustained_samples=1, tracemalloc_enabled=True)
    data = [bytearray(50_000) for _ in range(20)]  # force allocations
    lines = m.top_allocations(limit=5)
    assert isinstance(lines, list) and len(lines) <= 5
    del data


def test_get_memory_status_shape():
    status = get_memory_status()
    assert "rss_mb" in status
    assert status["rss_mb"] > 0


@pytest.mark.skipif(
    not _HAS_PROC, reason="foreign-PID RSS resolution needs /proc (Linux-only)"
)
def test_get_memory_status_resolves_run_pid():
    """When the run PID resolves, status reports that process's RSS."""
    with patch("app.memory_monitor._read_run_pid", lambda _root: os.getpid()):
        status = get_memory_status("/tmp/whatever")
    assert status["source"] == "agent_loop"
    assert status["rss_mb"] > 0


def test_get_memory_status_falls_back_to_self_when_no_run_pid():
    """No run PID -> read this process's RSS, source 'self'."""
    with patch("app.memory_monitor._read_run_pid", lambda _root: None):
        status = get_memory_status("/tmp/whatever")
    assert status["source"] == "self"
    assert status["rss_mb"] > 0


def test_get_memory_status_flags_config_error():
    """A config-read failure surfaces config_error rather than a fake disabled state."""
    def boom():
        raise RuntimeError("config exploded")

    with patch("app.config.get_memory_monitor_config", boom):
        status = get_memory_status()
    assert status.get("config_error") is True
    assert status["watchdog_enabled"] is None
    assert status["threshold_mb"] is None


def test_tracemalloc_error_recorded_on_start_failure():
    """A failed tracemalloc.start() is recorded, not silently swallowed."""
    with patch("tracemalloc.start", side_effect=RuntimeError("no tracing")), \
            patch("tracemalloc.is_tracing", lambda: False):
        m = MemoryMonitor(threshold_mb=1, sustained_samples=1, tracemalloc_enabled=True)
    assert m.tracemalloc_enabled is False
    assert m.tracemalloc_error is not None


def test_top_allocations_reflects_large_allocations():
    m = MemoryMonitor(threshold_mb=1, sustained_samples=1, tracemalloc_enabled=True)
    blob = [bytearray(100_000) for _ in range(50)]  # ~5 MB after start()
    lines = m.top_allocations(limit=10)
    assert lines, "tracemalloc should report allocation sites"
    assert all(isinstance(x, str) for x in lines)
    del blob
