"""Rolling burn-rate estimator for proactive quota management.

Maintains a circular buffer of recent run costs (percentage points of session
quota consumed) and computes a rolling burn rate plus an estimated
time-to-exhaustion. Persisted to ``instance/.burn-rate.json`` so it survives
restarts.

The buffer also tracks the last time a Telegram exhaustion warning fired so
the runtime can avoid notifying every iteration.
"""

from __future__ import annotations

import fcntl
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.utils import atomic_write

BURN_RATE_FILE = ".burn-rate.json"
MAX_SAMPLES = 20
MIN_SAMPLES_FOR_ESTIMATE = 5

# Single source of truth for autonomous-mode cost multipliers. Imported by
# usage_tracker.can_afford_run() so prediction and gating stay aligned.
MODE_MULTIPLIERS = {
    "review": 0.5,
    "implement": 1.0,
    "deep": 2.0,
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sample:
    """One observed run cost."""
    timestamp: datetime
    cost_pct: float


@dataclass
class BurnRateState:
    """Persisted state: rolling samples + last-warning timestamp."""
    samples: List[Sample]
    last_warned_at: Optional[datetime] = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _state_path(instance_dir: Path) -> Path:
    return Path(instance_dir) / BURN_RATE_FILE


def _read_locked(path: Path) -> str:
    """Read file contents under a shared (LOCK_SH) flock.

    Consistent with the project's atomic_write writer pattern so concurrent
    awake/run access cannot observe a partially-written file.
    """
    with open(path, "r", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return f.read()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _load_state(instance_dir: Path) -> BurnRateState:
    """Load burn-rate state, returning an empty state on any failure."""
    path = _state_path(instance_dir)
    if not path.exists():
        return BurnRateState(samples=[])
    try:
        raw = _read_locked(path)
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return BurnRateState(samples=[])

    samples: List[Sample] = []
    for entry in data.get("samples", []):
        ts = _parse_dt(entry.get("ts", ""))
        try:
            cost = float(entry.get("cost_pct"))
        except (TypeError, ValueError):
            continue
        if ts is None or not math.isfinite(cost) or cost < 0:
            continue
        samples.append(Sample(timestamp=ts, cost_pct=cost))

    samples.sort(key=lambda s: s.timestamp)
    samples = samples[-MAX_SAMPLES:]

    last_warned = _parse_dt(data.get("last_warned_at") or "")
    return BurnRateState(samples=samples, last_warned_at=last_warned)


def _save_state(instance_dir: Path, state: BurnRateState) -> None:
    path = _state_path(instance_dir)
    payload = {
        "samples": [
            {"ts": s.timestamp.isoformat(), "cost_pct": s.cost_pct}
            for s in state.samples
        ],
    }
    if state.last_warned_at is not None:
        payload["last_warned_at"] = state.last_warned_at.isoformat()
    try:
        atomic_write(path, json.dumps(payload, indent=2) + "\n")
    except OSError as exc:
        logger.warning("Could not write %s: %s", path, exc)


def record_run(instance_dir: Path, cost_pct: float,
               timestamp: Optional[datetime] = None) -> None:
    """Append a sample (and trim to MAX_SAMPLES).

    Args:
        instance_dir: Path to the instance directory.
        cost_pct: Percentage points of session quota consumed by the run.
            Negative values, NaN, and infinities are dropped.
        timestamp: Override for the sample timestamp (defaults to now UTC).
    """
    if not math.isfinite(cost_pct) or cost_pct < 0:
        return

    state = _load_state(Path(instance_dir))
    sample = Sample(timestamp=timestamp or _now_utc(), cost_pct=float(cost_pct))
    samples = state.samples + [sample]
    samples = samples[-MAX_SAMPLES:]
    _save_state(Path(instance_dir), BurnRateState(
        samples=samples,
        last_warned_at=state.last_warned_at,
    ))


class BurnRateSnapshot:
    """Read-once view of burn-rate state.

    Loads ``.burn-rate.json`` once at construction. All read methods operate
    on the cached state, eliminating redundant file I/O when multiple metrics
    are needed in the same call site (e.g. per-iteration warning checks in
    ``iteration_manager._maybe_warn_burn_rate``).
    """

    def __init__(self, instance_dir: Path):
        self._state = _load_state(Path(instance_dir))

    @property
    def samples(self) -> List[Sample]:
        """Rolling sample buffer (oldest → newest)."""
        return self._state.samples

    @property
    def last_warned_at(self) -> Optional[datetime]:
        """Timestamp of the most recent exhaustion warning, if any."""
        return self._state.last_warned_at

    def burn_rate_pct_per_minute(self) -> Optional[float]:
        """Rolling burn rate in % session quota per minute.

        Returns ``None`` if insufficient history (< 5 samples) or zero span.
        """
        samples = self._state.samples
        if len(samples) < MIN_SAMPLES_FOR_ESTIMATE:
            return None

        first, last = samples[0], samples[-1]
        span_minutes = (last.timestamp - first.timestamp).total_seconds() / 60.0
        if span_minutes <= 0:
            return None

        consumed = sum(s.cost_pct for s in samples)
        return consumed / span_minutes

    def time_to_exhaustion(self, session_pct: float,
                           mode: Optional[str] = None) -> Optional[float]:
        """Estimate minutes until session quota is exhausted.

        Args:
            session_pct: Current session usage (0-100).
            mode: Optional autonomous mode whose cost multiplier is applied.

        Returns:
            Minutes until exhaustion, or ``None`` when no estimate is possible.
        """
        rate = self.burn_rate_pct_per_minute()
        if rate is None or rate <= 0:
            return None

        if mode is not None:
            rate *= MODE_MULTIPLIERS.get(mode, 1.0)
            if rate <= 0:
                return None

        remaining = max(0.0, 100.0 - float(session_pct))
        if remaining <= 0:
            return 0.0
        return remaining / rate


# --- Convenience free functions (backward-compatible, single-use wrappers) ---

def get_samples(instance_dir: Path) -> List[Sample]:
    """Return the rolling sample buffer (oldest → newest)."""
    return BurnRateSnapshot(instance_dir).samples


def burn_rate_pct_per_minute(instance_dir: Path) -> Optional[float]:
    """Return rolling burn rate in % session quota per minute.

    Sums every sample's cost across the window and divides by the elapsed
    time between the oldest and newest sample. Including the first sample's
    cost avoids the 1/N under-count that happened when it was treated as a
    zero-cost "window start" marker.

    Returns:
        Burn rate in percentage points per minute, or ``None`` if there is
        not enough history (< 5 samples) or zero elapsed time.
    """
    return BurnRateSnapshot(instance_dir).burn_rate_pct_per_minute()


def time_to_exhaustion(instance_dir: Path, session_pct: float,
                       mode: Optional[str] = None) -> Optional[float]:
    """Estimate minutes until session quota is exhausted at current burn rate.

    Args:
        instance_dir: Instance directory.
        session_pct: Current session usage (0-100).
        mode: Optional autonomous mode whose cost multiplier (relative to
            ``implement``) is applied to the rolling burn rate. ``None``
            uses the observed rate as-is.

    Returns:
        Minutes until exhaustion, or ``None`` when no estimate is possible
        (insufficient history, zero rate, or quota already exhausted).
    """
    return BurnRateSnapshot(instance_dir).time_to_exhaustion(session_pct, mode)


def get_last_warned_at(instance_dir: Path) -> Optional[datetime]:
    """Return the timestamp of the most recent exhaustion warning, if any."""
    return BurnRateSnapshot(instance_dir).last_warned_at


def mark_warned(instance_dir: Path,
                timestamp: Optional[datetime] = None) -> None:
    """Record that an exhaustion warning has just been fired."""
    state = _load_state(Path(instance_dir))
    _save_state(Path(instance_dir), BurnRateState(
        samples=state.samples,
        last_warned_at=timestamp or _now_utc(),
    ))


def clear_warning(instance_dir: Path) -> None:
    """Clear the last-warned timestamp (e.g. after a quota reset)."""
    state = _load_state(Path(instance_dir))
    if state.last_warned_at is None:
        return
    _save_state(Path(instance_dir), BurnRateState(
        samples=state.samples,
        last_warned_at=None,
    ))
