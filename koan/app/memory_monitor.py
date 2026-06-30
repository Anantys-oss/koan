"""Process memory watchdog (#2232).

Samples current RSS each agent-loop iteration. After RSS stays above a
configurable threshold for N consecutive samples, the caller restarts the
process (via RESTART_EXIT_CODE re-exec) to reclaim memory back to baseline.
Optional tracemalloc mode captures top allocation sites for diagnosis.
"""
from __future__ import annotations

import sys


def read_rss_mb(pid: int | None = None) -> float:
    """Resident set size in MB for a process (current process by default).

    Prefers /proc/<pid>/status VmRSS (current RSS, decreases when freed).
    Falls back to resource.ru_maxrss (peak, not current) only for the current
    process when /proc is unavailable (e.g. non-Linux). ru_maxrss units are
    platform-dependent: KB on Linux, bytes on macOS/BSD — scaled accordingly.
    Returns 0.0 if neither source is readable.
    """
    target = "self" if pid is None else str(pid)
    try:
        with open(f"/proc/{target}/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    if pid is not None:
        # Cannot use ru_maxrss for another process; report unknown.
        return 0.0
    try:
        import resource
        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # ru_maxrss is bytes on macOS/BSD, kilobytes on Linux.
        if sys.platform == "darwin" or "bsd" in sys.platform:
            return maxrss / (1024.0 * 1024.0)
        return maxrss / 1024.0
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
        min_runs_before_restart: int = 1,
    ) -> None:
        self.threshold_mb = int(threshold_mb)
        self.sustained_samples = max(1, int(sustained_samples))
        self.min_runs_before_restart = int(min_runs_before_restart)
        self.tracemalloc_enabled = bool(tracemalloc_enabled)
        self.tracemalloc_error: str | None = None
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
            # Record the failure so callers can surface "diagnostics broken"
            # distinctly from "diagnostics intentionally off".
            self.tracemalloc_error = str(exc)
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


def _read_run_pid(koan_root) -> int | None:
    """Resolve the agent-loop ('run') process PID from its pid file."""
    try:
        from app.signals import pid_file
        from pathlib import Path
        pid_path = Path(koan_root) / pid_file("run")
        return int(pid_path.read_text().strip())
    except (OSError, ValueError, ImportError):
        return None


def get_memory_status(koan_root=None) -> dict:
    """Lightweight memory snapshot for observability endpoints.

    Reports the *agent loop's* RSS (the watchdog's subject), not the caller's.
    The dashboard runs in a separate process, so it resolves the 'run' PID and
    reads that process's RSS. Falls back to the current process only when the
    run PID cannot be resolved (e.g. agent loop not running).
    """
    if koan_root is None:
        try:
            from app.utils import KOAN_ROOT
            koan_root = KOAN_ROOT
        except Exception as exc:  # pragma: no cover - defensive
            print(f"get_memory_status: KOAN_ROOT import failed: {exc}", file=sys.stderr)
            koan_root = None

    run_pid = _read_run_pid(koan_root) if koan_root is not None else None
    if run_pid is not None:
        rss = read_rss_mb(run_pid)
        source = "agent_loop"
        if rss <= 0:
            # PID stale or /proc unreadable; fall back to this process.
            rss = read_rss_mb()
            source = "self"
    else:
        rss = read_rss_mb()
        source = "self"

    config_error = False
    try:
        from app.config import get_memory_monitor_config
        conf = get_memory_monitor_config()
        threshold = conf.get("threshold_mb", 0)
        enabled = bool(conf.get("enabled", False))
    except Exception as exc:  # pragma: no cover - defensive
        print(f"get_memory_status: config read failed: {exc}", file=sys.stderr)
        # Don't fabricate a plausible "disabled" state; flag the failure so
        # consumers can distinguish it from an intentionally-off watchdog.
        threshold, enabled, config_error = None, None, True
    status = {
        "rss_mb": round(rss, 1),
        "threshold_mb": threshold,
        "watchdog_enabled": enabled,
        "source": source,
    }
    if config_error:
        status["config_error"] = True
    return status
