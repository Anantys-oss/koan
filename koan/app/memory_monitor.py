"""Process memory watchdog (#2232).

Samples current RSS each agent-loop iteration. After RSS stays above a
configurable threshold for N consecutive samples, the caller restarts the
process (via RESTART_EXIT_CODE re-exec) to reclaim memory back to baseline.
Optional tracemalloc mode captures top allocation sites for diagnosis.
"""
from __future__ import annotations

import sys


def read_rss_mb() -> float:
    """Current resident set size in MB.

    Prefers /proc/self/status VmRSS (current RSS, decreases when freed).
    Falls back to resource.ru_maxrss (peak; KB on Linux) when /proc is
    unavailable (e.g. non-Linux). Returns 0.0 if neither is readable.
    """
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception as exc:  # pragma: no cover - platform dependent
        print(f"[memory_monitor] read_rss_mb fallback failed: {exc}", file=sys.stderr)
        return 0.0


class MemoryMonitor:
    """Tracks RSS overage and signals when a restart is warranted."""

    def __init__(
        self,
        threshold_mb: int,
        sustained_samples: int,
        tracemalloc_enabled: bool = False,
        tracemalloc_frames: int = 10,
    ) -> None:
        self.threshold_mb = int(threshold_mb)
        self.sustained_samples = max(1, int(sustained_samples))
        self.tracemalloc_enabled = bool(tracemalloc_enabled)
        self._tracemalloc_frames = int(tracemalloc_frames)
        self._over_count = 0
        self._last_rss_mb = 0.0
        if self.tracemalloc_enabled:
            self._start_tracemalloc()

    def _start_tracemalloc(self) -> None:
        try:
            import tracemalloc
            if not tracemalloc.is_tracing():
                tracemalloc.start(self._tracemalloc_frames)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[memory_monitor] tracemalloc start failed: {exc}", file=sys.stderr)
            self.tracemalloc_enabled = False

    @property
    def last_rss_mb(self) -> float:
        return self._last_rss_mb

    def reset(self) -> None:
        self._over_count = 0

    def sample(self) -> bool:
        """Record current RSS; return True if a restart is warranted."""
        rss = read_rss_mb()
        self._last_rss_mb = rss
        if self.threshold_mb > 0 and rss >= self.threshold_mb:
            self._over_count += 1
        else:
            self._over_count = 0
        return self._over_count >= self.sustained_samples

    def top_allocations(self, limit: int = 10) -> list[str]:
        """Human-readable top allocation sites (empty unless tracemalloc on)."""
        if not self.tracemalloc_enabled:
            return []
        try:
            import tracemalloc
            if not tracemalloc.is_tracing():
                return []
            snapshot = tracemalloc.take_snapshot()
            stats = snapshot.statistics("lineno")[: max(1, limit)]
            return [
                f"{s.traceback[0]}: {s.size / 1024 / 1024:.1f} MiB ({s.count} blocks)"
                for s in stats
            ]
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[memory_monitor] top_allocations failed: {exc}", file=sys.stderr)
            return []


def get_memory_status() -> dict:
    """Lightweight memory snapshot for observability endpoints."""
    rss = read_rss_mb()
    try:
        from app.config import get_memory_monitor_config
        conf = get_memory_monitor_config()
        threshold = conf.get("threshold_mb", 0)
        enabled = conf.get("enabled", False)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"get_memory_status: config read failed: {exc}", file=sys.stderr)
        threshold, enabled = 0, False
    return {
        "rss_mb": round(rss, 1),
        "threshold_mb": threshold,
        "watchdog_enabled": bool(enabled),
    }
