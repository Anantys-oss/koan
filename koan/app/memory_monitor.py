"""Process memory watchdog (#2232).

Samples current RSS each agent-loop iteration. After RSS stays above a
configurable threshold for N consecutive samples, the caller restarts the
process (via RESTART_EXIT_CODE re-exec) to reclaim memory back to baseline.
Optional tracemalloc mode captures top allocation sites for diagnosis.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_STATUS_FILENAME = ".memory-watchdog-status.json"


def read_rss_mb(pid: int | None = None) -> float:
    """Resident set size in MB for a process (current process by default).

    Prefers /proc/<pid>/status VmRSS (current RSS, decreases when freed).
    Falls back to resource.ru_maxrss (peak, not current) only for the current
    process when /proc is unavailable (e.g. non-Linux). ru_maxrss units are
    platform-dependent: KB on Linux, bytes on macOS/BSD — scaled accordingly.
    Returns 0.0 if neither source is readable (treat 0.0 as "unknown", not
    "this process uses no memory" — a real RSS is always positive).
    """
    target = "self" if pid is None else str(pid)
    try:
        with open(f"/proc/{target}/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError) as exc:
        if pid is not None:
            # Surface why a foreign-process read failed so a stale/unreadable
            # PID is diagnosable instead of silently degrading to self RSS.
            print(
                f"[memory_monitor] read_rss_mb: /proc/{target}/status "
                f"unreadable: {exc}",
                file=sys.stderr,
            )
        elif Path("/proc").exists():
            # Log the self-read failure before falling back to ru_maxrss:
            # that fallback silently switches from current RSS to *peak* RSS
            # (a monotonically non-decreasing metric), so make the switch
            # observable instead of degrading the watchdog without a trace.
            # Gated on /proc existing so non-Linux (where absence is the
            # documented normal fallback path) doesn't spam stderr each sample.
            print(
                f"[memory_monitor] read_rss_mb: /proc/self/status unreadable "
                f"({exc}); falling back to ru_maxrss (peak, not current RSS)",
                file=sys.stderr,
            )
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
        self._rss_read_failed = False
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
        if rss <= 0:
            # 0.0 means "unknown" (read failed), not "no memory". Treating it as
            # below-threshold would silently reset _over_count and render the
            # watchdog inert. Leave the counter unchanged and log once so a
            # mid-session RSS-read failure degrades loudly instead of disabling
            # protection.
            if not self._rss_read_failed:
                print(
                    "[memory_monitor] sample: RSS read returned 0.0 (unknown); "
                    "leaving overage counter unchanged",
                    file=sys.stderr,
                )
                self._rss_read_failed = True
            return self._over_count >= self.sustained_samples
        self._rss_read_failed = False
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


def _status_path(koan_root) -> Path:
    return Path(koan_root, "instance", _STATUS_FILENAME)


def write_watchdog_status(koan_root, *, enabled, threshold_mb, reason="") -> None:
    """Persist the agent loop's *runtime* watchdog decision (#2232).

    _build_memory_monitor may disable the watchdog at startup (unreadable
    baseline, or threshold <= baseline) even when config says enabled. Writing
    the actual decision here lets observability report runtime state instead of
    raw config, so the dashboard can't falsely advertise protection.
    """
    try:
        path = _status_path(koan_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "enabled": bool(enabled),
                    "threshold_mb": threshold_mb,
                    "reason": reason,
                }
            ),
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - defensive
        print(f"[memory_monitor] write_watchdog_status failed: {exc}", file=sys.stderr)


def read_watchdog_status(koan_root) -> dict | None:
    """Read the persisted runtime watchdog decision, or None if unavailable."""
    try:
        data = json.loads(_status_path(koan_root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _read_run_pid(koan_root) -> int | None:
    """Resolve the agent-loop ('run') process PID from its pid file."""
    try:
        from app.signals import pid_file
        from pathlib import Path
        pid_path = Path(koan_root) / pid_file("run")
        return int(pid_path.read_text().strip())
    except ImportError as exc:
        # app.signals is a required module; a broken import is a real fault,
        # not the routine "run isn't active" case — surface it.
        print(f"[memory_monitor] _read_run_pid: app.signals import failed: {exc}",
              file=sys.stderr)
        return None
    except (OSError, ValueError):
        # pid file missing/empty/stale — expected when the agent loop is down.
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
    # Prefer the agent loop's persisted runtime decision over raw config: the
    # watchdog can be disabled at startup even when config says enabled, and the
    # dashboard must reflect what the loop is actually doing.
    runtime = read_watchdog_status(koan_root) if koan_root is not None else None
    if runtime is not None:
        threshold = runtime.get("threshold_mb")
        enabled = bool(runtime.get("enabled", False))
    else:
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
