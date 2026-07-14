"""Universal kernel page-cache reclaim (#2374).

Billing on Railway (and most container platforms) tracks the cgroup's
``memory.current``, which counts reclaimable page cache (``file``) — not just
process RSS (``anon``). Missions do heavy file I/O and the kernel keeps those
clean pages warm with no memory pressure, so the billed baseline ratchets up.
``/sys/fs/cgroup/memory.reclaim`` is read-only on Railway, but unprivileged
``posix_fadvise(POSIX_FADV_DONTNEED)`` drops clean pages. This module is the
single reclaim primitive; it is wired at the daemon's lifecycle choke points
(post-mission ``finally`` + idle tick) so no future feature has to opt in.
"""
from __future__ import annotations

import contextlib
import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.config import get_page_cache_reclaim_config
from app.memory_monitor import read_cgroup_memory_stat
from app.run_log import log_safe as _log
from app.utils import get_known_projects, koan_tmp_dir

# Files checked between deadline re-checks inside one directory's file list.
_BUDGET_CHECK_EVERY = 256
# Suppress the health log when the reclaimed delta is below this (MB).
_LOG_MIN_DELTA_MB = 3.0

_last_idle_reclaim_ts: float = 0.0


@dataclass
class ReclaimStats:
    supported: bool = True
    files: int = 0
    errors: int = 0
    elapsed_s: float = 0.0
    budget_hit: bool = False
    file_mb_before: float | None = None
    file_mb_after: float | None = None

    @property
    def delta_mb(self) -> float | None:
        if self.file_mb_before is None or self.file_mb_after is None:
            return None
        return round(self.file_mb_before - self.file_mb_after, 1)


def _reset_idle_throttle() -> None:
    """Test seam: clear the module-level idle throttle."""
    global _last_idle_reclaim_ts
    _last_idle_reclaim_ts = 0.0


def default_reclaim_roots() -> list[Path]:
    """The known heavy page-cache contributors, resolved centrally (DRY anchor).

    Every configured project workdir, ``KOAN_ROOT/instance/`` (logs + SQLite),
    the venv, and the per-uid scratch dir. Non-existent roots are dropped;
    operator ``extra_roots`` are appended. ``KOAN_ROOT`` is read from the
    environment (not the import-time constant) so it stays correct if it changed.

    Ordering matters: the small, high-value roots (``instance/``, venv, scratch)
    are emitted **before** the large project workdirs so a truncated sweep (time
    budget hit) still reclaims them rather than spending the whole budget walking
    one big ``node_modules``/``.git`` tree.
    """
    # Priority roots first — small + high-value, so they always get their turn
    # within the time budget before the large project trees.
    priority: list[Path] = []
    koan_root = os.environ.get("KOAN_ROOT", "")
    if koan_root:
        priority.append(Path(koan_root) / "instance")
    # Venv: sys.prefix points at the active interpreter's env.
    priority.append(Path(sys.prefix))
    with contextlib.suppress(OSError):
        priority.append(Path(koan_tmp_dir()))
    project_roots = [Path(path) for _name, path in get_known_projects()]
    cfg = get_page_cache_reclaim_config()
    extra = [Path(str(e)) for e in cfg.get("extra_roots", [])]
    roots = priority + project_roots + extra
    # De-dupe on resolved path; keep only existing dirs. Preserve the priority
    # ordering above (do NOT re-sort).
    seen: set[str] = set()
    resolved: list[Path] = []
    for r in roots:
        try:
            rp = r.resolve()
        except OSError:
            continue
        key = str(rp)
        if key in seen or not rp.is_dir():
            continue
        seen.add(key)
        resolved.append(rp)
    # Drop roots nested under another kept root (e.g. the venv living inside a
    # project workdir) so os.walk doesn't fadvise the same files twice and burn
    # the time budget. Order-independent: a root is dropped when any of its
    # parents is itself a kept root, which preserves the priority ordering above.
    kept = {str(p) for p in resolved}
    out: list[Path] = []
    for rp in resolved:
        if any(str(anc) in kept for anc in rp.parents):
            continue
        out.append(rp)
    return out


def reclaim_page_cache(
    paths: Iterable[Path], *, budget_s: float = 10.0
) -> ReclaimStats:
    """Drop clean page-cache for every regular file under ``paths``.

    ``os.open(O_RDONLY)`` + ``posix_fadvise(DONTNEED)`` + close each file.
    Per-file ``OSError`` is swallowed (files vanish mid-walk). Symlinks and
    special files are lstat-skipped so traversal never escapes the roots.
    Honors a soft time budget so it can never stall the loop. No-op (with a
    debug log) where ``os.posix_fadvise`` is unavailable (macOS).
    """
    stats = ReclaimStats()
    fadvise = getattr(os, "posix_fadvise", None)
    dontneed = getattr(os, "POSIX_FADV_DONTNEED", None)
    if fadvise is None or dontneed is None:
        stats.supported = False
        _log("debug", "Page cache reclaim: posix_fadvise unavailable — skipping")
        return stats

    before = read_cgroup_memory_stat()
    stats.file_mb_before = before.get("file_mb") if before else None

    start = time.monotonic()
    deadline = start + max(0.0, budget_s)
    for root in paths:
        if time.monotonic() >= deadline:
            stats.budget_hit = True
            break
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            if time.monotonic() >= deadline:
                stats.budget_hit = True
                break
            for idx, name in enumerate(filenames):
                if idx % _BUDGET_CHECK_EVERY == 0 and time.monotonic() >= deadline:
                    stats.budget_hit = True
                    break
                fp = os.path.join(dirpath, name)
                try:
                    st = os.lstat(fp)
                    if not stat.S_ISREG(st.st_mode):
                        continue  # skip symlinks, fifos, sockets, devices
                    fd = os.open(fp, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    try:
                        fadvise(fd, 0, 0, dontneed)
                        stats.files += 1
                    finally:
                        os.close(fd)
                except OSError:
                    stats.errors += 1
                    continue
            if stats.budget_hit:
                break
        if stats.budget_hit:
            break

    after = read_cgroup_memory_stat()
    stats.file_mb_after = after.get("file_mb") if after else None
    stats.elapsed_s = round(time.monotonic() - start, 2)
    return stats


def _errors_notable(stats: ReclaimStats) -> bool:
    """True when errors dominate — systemic denial, not the odd vanished file.

    A handful of ``OSError``s while files churn under the walk is normal; errors
    that equal or outnumber the files we actually reclaimed (in the limit, every
    ``os.open`` denied by permissions/SELinux → ``files == 0``) means the sweep
    did no real work and the operator needs to see it.
    """
    return stats.errors > 0 and stats.errors >= max(1, stats.files)


def _format_stats(stats: ReclaimStats) -> str:
    files_k = f"{stats.files / 1000:.0f}k" if stats.files >= 1000 else str(stats.files)
    if stats.file_mb_before is not None and stats.file_mb_after is not None:
        head = f"file {stats.file_mb_before:.0f}→{stats.file_mb_after:.0f} MB"
    else:
        head = "cgroup stats unavailable"
    parts = [f"{stats.elapsed_s}s", f"{files_k} files"]
    if stats.errors:
        parts.append(f"{stats.errors} errors")
    if stats.budget_hit:
        parts.append("budget hit — raise time_budget_s or trim extra_roots")
    return f"Page cache reclaim: {head} ({', '.join(parts)})"


def run_reclaim(reason: str, *, budget_s: float | None = None) -> ReclaimStats:
    """Reclaim over ``default_reclaim_roots()``, log the outcome once at health level.

    ``reason`` is a short tag ('post-mission' / 'idle') used only in the debug
    trail. Logs at ``health`` when the reclaimed delta is meaningful (to avoid
    per-mission noise) OR when the sweep was truncated (``budget_hit``) or did no
    real work (errors dominating) — a small-delta success is the only case
    suppressed, so systemic failures never hide behind silence.
    """
    cfg = get_page_cache_reclaim_config()
    if not cfg.get("enabled", True):
        return ReclaimStats(supported=True)
    budget = cfg.get("time_budget_s", 10) if budget_s is None else budget_s
    stats = reclaim_page_cache(default_reclaim_roots(), budget_s=budget)
    if not stats.supported:
        return stats
    delta = stats.delta_mb
    meaningful = delta is None or delta >= _LOG_MIN_DELTA_MB
    if meaningful or stats.budget_hit or _errors_notable(stats):
        _log("health", _format_stats(stats))
    return stats


def maybe_reclaim_page_cache_idle(now: float | None = None) -> ReclaimStats | None:
    """Throttled idle reclaim: fire at most once per ``idle_interval_s``.

    Called from every idle-sleep site; the module-level throttle makes wiring
    it at more than one site idempotent. ``idle_interval_s == 0`` disables the
    idle tick (the post-mission hook still runs).
    """
    global _last_idle_reclaim_ts
    cfg = get_page_cache_reclaim_config()
    if not cfg.get("enabled", True):
        return None
    interval = cfg.get("idle_interval_s", 900)
    if interval <= 0:
        return None
    ts = time.monotonic() if now is None else now
    if _last_idle_reclaim_ts and (ts - _last_idle_reclaim_ts) < interval:
        return None
    _last_idle_reclaim_ts = ts
    return run_reclaim("idle")
