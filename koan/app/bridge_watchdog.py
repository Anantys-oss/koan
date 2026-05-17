"""Self-healing watchdog for the Telegram bridge process.

The bridge (``awake.py``) is a long-running process with no wrapper, so
a frozen ``sys.modules`` after an ``/update`` leaves it serving stale
imports until someone with shell access kicks it.  The runner, by
contrast, has a wrapper that relaunches it on every exit — so it is
*always* fresh code.  This module gives the runner the responsibility
of watching the bridge and recovering it when something goes wrong.

Two failure modes are detected:

1.  **Stale modules** — the process is alive and the heartbeat is fresh,
    but ``sys.modules`` has not been reloaded after a code update.
    Caught by stamping the bridge's git HEAD at startup and comparing
    against the on-disk HEAD on each runner iteration.

2.  **Hung / dead bridge** — the heartbeat goes stale or the PID file
    is gone / pointing at a dead PID.

Recovery is escalated through four tiers so we never go straight from
"unhealthy" to ``SIGKILL``:

    Tier 1  request_restart() — let the bridge re-exec itself
    Tier 2  SIGTERM the bridge PID
    Tier 3  SIGKILL + pid_manager.start_awake() — last resort
    Tier 4  circuit-broken — stop trying, write a loud alert

State persists between calls in ``instance/.koan-bridge-heal-state``
so each runner iteration knows what's already been attempted.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal as _signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.signals import (
    BRIDGE_HEAL_STATE_FILE,
    BRIDGE_VERSION_FILE,
    HEARTBEAT_FILE,
    pid_file,
)

_log = logging.getLogger(__name__)

# --- Tunables --------------------------------------------------------------

# Bridge heartbeat is written every poll cycle (~3 s).  90 s of silence
# is enough to be confident the bridge is hung, not just briefly busy.
BRIDGE_HEARTBEAT_STALE_S: float = 90.0

# Once we trigger a tier, wait this long before evaluating the next one.
# Tier 1 (cooperative re-exec) typically completes in <5 s, but we give
# the slow path (process startup, banner, first poll) some breathing room.
HEAL_TIER_COOLDOWN_S: float = 45.0

# Grace period after SIGTERM before escalating to SIGKILL.
SIGTERM_GRACE_S: float = 5.0

# Cooldown after a successful heal cycle (any tier) before the watchdog
# is willing to act again.  Prevents log spam and false positives during
# a legitimate restart that's still in progress.
POST_HEAL_QUIET_S: float = 60.0

# After this many consecutive tier-3 escalations without recovery, the
# watchdog gives up and just alerts the operator.
HEAL_CIRCUIT_BREAKER_LIMIT: int = 3


# --- Bridge-side: stamp the code version ----------------------------------


def write_bridge_version_stamp(koan_root: Path) -> None:
    """Record the git HEAD SHA of the bridge's working tree.

    Called by ``awake.py`` once at startup.  The runner reads this file
    on every iteration and compares it against the on-disk HEAD to
    detect "bridge is alive but running pre-update code".  If git is
    unavailable, falls back to the literal string ``"unknown"`` so the
    runner can still tell "the stamp exists" from "the stamp is missing".
    """
    from app.utils import atomic_write

    sha = _read_git_head(koan_root) or "unknown"
    atomic_write(koan_root / BRIDGE_VERSION_FILE, sha)


# --- Runner-side: detection + escalation ----------------------------------


@dataclass
class _HealState:
    last_action_ts: float = 0.0
    last_tier: int = 0
    consecutive_failures: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "last_action_ts": self.last_action_ts,
                "last_tier": self.last_tier,
                "consecutive_failures": self.consecutive_failures,
            }
        )

    @classmethod
    def from_path(cls, path: Path) -> "_HealState":
        try:
            data = json.loads(path.read_text())
        except (FileNotFoundError, ValueError, OSError):
            return cls()
        return cls(
            last_action_ts=float(data.get("last_action_ts", 0.0)),
            last_tier=int(data.get("last_tier", 0)),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
        )


@dataclass
class _BridgeStatus:
    pid: Optional[int]
    process_alive: bool
    heartbeat_age: float  # math.inf if no heartbeat file
    bridge_sha: Optional[str]  # None if no stamp written yet
    disk_sha: Optional[str]  # None if git is unavailable

    @property
    def sha_mismatch(self) -> bool:
        # Don't fire on missing data — only act on a real divergence
        # between two known SHAs.  "unknown" vs a real sha is also a
        # mismatch, since the bridge boot failed to record its version.
        if self.bridge_sha is None or self.disk_sha is None:
            return False
        return self.bridge_sha != self.disk_sha

    @property
    def heartbeat_stale(self) -> bool:
        return self.heartbeat_age > BRIDGE_HEARTBEAT_STALE_S

    @property
    def needs_cold_start(self) -> bool:
        # No bridge running at all — runner must bring one up.
        return self.pid is None or not self.process_alive

    @property
    def unhealthy(self) -> bool:
        return self.needs_cold_start or self.sha_mismatch or self.heartbeat_stale

    def summary(self) -> str:
        bits = [
            f"pid={self.pid}",
            f"alive={self.process_alive}",
            f"heartbeat_age={self.heartbeat_age:.1f}s",
            f"bridge_sha={(self.bridge_sha or 'missing')[:8]}",
            f"disk_sha={(self.disk_sha or 'missing')[:8]}",
        ]
        return " ".join(bits)


def check_and_heal_bridge(koan_root: Path) -> Optional[str]:
    """Inspect bridge health and escalate recovery one tier per call.

    Intended to be called once per runner main-loop iteration.  Returns
    a one-line human-readable message describing the action taken when
    something happened, or ``None`` when the bridge is healthy (or we
    are in a cooldown window).  Callers should forward the message to
    the outbox and journal so the operator finds out.
    """
    status = _probe_bridge(koan_root)
    state_path = koan_root / BRIDGE_HEAL_STATE_FILE
    state = _HealState.from_path(state_path)
    now = time.time()

    if not status.unhealthy:
        # Healthy: reset state if we'd been actively healing.
        if state.last_tier != 0 or state.consecutive_failures != 0:
            _save_state(state_path, _HealState())
        return None

    # Don't keep retrying inside the cooldown of a previous tier.
    if state.last_action_ts and (now - state.last_action_ts) < HEAL_TIER_COOLDOWN_S:
        return None

    # Circuit breaker: stop trying after N back-to-back failures.
    if state.consecutive_failures >= HEAL_CIRCUIT_BREAKER_LIMIT:
        # Only re-emit the alert occasionally, not every iteration.
        if (now - state.last_action_ts) < POST_HEAL_QUIET_S:
            return None
        state.last_action_ts = now
        _save_state(state_path, state)
        return (
            f"⚠️ Bridge self-heal circuit-broken after "
            f"{state.consecutive_failures} attempts. Manual intervention "
            f"required. status: {status.summary()}"
        )

    next_tier = _next_tier(state, status)
    action_msg = _execute_tier(next_tier, status, koan_root)

    state.last_action_ts = now
    state.last_tier = next_tier
    if next_tier == 3:
        # Tier 3 is the last automated recourse — count it as a failure
        # of the cooperative path so the breaker can trip.
        state.consecutive_failures += 1
    _save_state(state_path, state)

    return f"Bridge self-heal tier {next_tier}: {action_msg}. status: {status.summary()}"


# --- Internals -------------------------------------------------------------


def _probe_bridge(koan_root: Path) -> _BridgeStatus:
    pid = _read_bridge_pid(koan_root)
    process_alive = pid is not None and _is_process_alive(pid)
    heartbeat_age = _heartbeat_age(koan_root)
    bridge_sha = _read_text(koan_root / BRIDGE_VERSION_FILE)
    disk_sha = _read_git_head(koan_root)
    return _BridgeStatus(
        pid=pid,
        process_alive=process_alive,
        heartbeat_age=heartbeat_age,
        bridge_sha=bridge_sha,
        disk_sha=disk_sha,
    )


def _next_tier(state: _HealState, status: _BridgeStatus) -> int:
    """Pick the next escalation tier based on what's been tried.

    If the bridge is fully missing we skip the cooperative tier — there
    is no process to receive a restart signal, so we go straight to
    relaunch (tier 3).
    """
    if status.needs_cold_start:
        return 3
    # Otherwise step up one tier at a time.
    return min(state.last_tier + 1, 3)


def _execute_tier(tier: int, status: _BridgeStatus, koan_root: Path) -> str:
    if tier == 1:
        return _tier1_cooperative(koan_root)
    if tier == 2:
        return _tier2_sigterm(status.pid)
    if tier == 3:
        return _tier3_kill_and_relaunch(status.pid, koan_root)
    # Shouldn't happen — _next_tier caps at 3.
    return f"unknown tier {tier}, no action"


def _tier1_cooperative(koan_root: Path) -> str:
    """Ask the bridge to re-exec via the standard restart channel.

    The runner is always on fresh code (wrapper relaunches it on every
    exit), so its ``request_restart`` writes the *new* triple-marker
    format and is not subject to the old race.
    """
    from app.restart_manager import request_restart

    request_restart(str(koan_root))
    return "cooperative restart requested via request_restart()"


def _tier2_sigterm(pid: Optional[int]) -> str:
    if pid is None:
        return "tier 2 skipped: no bridge pid"
    try:
        os.kill(pid, _signal.SIGTERM)
    except ProcessLookupError:
        return f"tier 2: pid {pid} already gone"
    except OSError as e:
        return f"tier 2: SIGTERM {pid} failed ({e})"
    return f"SIGTERM sent to bridge pid {pid}"


def _tier3_kill_and_relaunch(pid: Optional[int], koan_root: Path) -> str:
    parts = []
    # Step 1: make sure the bridge is dead.
    if pid is not None and _is_process_alive(pid):
        # Wait briefly in case tier 2's SIGTERM is still landing.
        deadline = time.monotonic() + SIGTERM_GRACE_S
        while time.monotonic() < deadline and _is_process_alive(pid):
            time.sleep(0.25)
        if _is_process_alive(pid):
            try:
                os.kill(pid, _signal.SIGKILL)
                parts.append(f"SIGKILL sent to bridge pid {pid}")
            except (OSError, ProcessLookupError) as e:
                parts.append(f"SIGKILL {pid} failed ({e})")
        else:
            parts.append(f"bridge pid {pid} exited after SIGTERM")
    else:
        parts.append("no live bridge pid")

    # Step 2: launch a fresh bridge.  pid_manager.start_awake handles
    # log rotation, detached subprocess, PID-file verification.
    try:
        from app.pid_manager import start_awake

        ok, msg = start_awake(koan_root)
    except Exception as e:  # pragma: no cover - defensive
        parts.append(f"start_awake raised: {e}")
        return "; ".join(parts)

    parts.append(f"relaunch: ok={ok} ({msg})")
    return "; ".join(parts)


def _read_bridge_pid(koan_root: Path) -> Optional[int]:
    path = koan_root / pid_file("awake")
    try:
        text = path.read_text().strip()
        return int(text) if text else None
    except (FileNotFoundError, ValueError, OSError):
        return None


def _is_process_alive(pid: int) -> bool:
    """Cheap liveness check (signal 0).  Mirrors ``pid_manager._is_process_alive``
    but without the zombie/proc lookup — for the watchdog's purposes a
    zombie is "not actually running" and we'll catch it on the next
    heartbeat check anyway."""
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _heartbeat_age(koan_root: Path) -> float:
    path = koan_root / HEARTBEAT_FILE
    try:
        mtime = path.stat().st_mtime
    except (FileNotFoundError, OSError):
        return float("inf")
    return max(0.0, time.time() - mtime)


def _read_text(path: Path) -> Optional[str]:
    try:
        value = path.read_text().strip()
    except (FileNotFoundError, OSError):
        return None
    return value or None


def _read_git_head(koan_root: Path) -> Optional[str]:
    """Resolve the current HEAD SHA of the Kōan working tree.

    Bounded by a 2 s timeout so a stuck git invocation cannot block the
    main loop.  Returns ``None`` on any failure (no git, detached repo,
    timeout) — callers treat ``None`` as "can't compare, skip the
    sha-mismatch check this round".
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(koan_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def _save_state(path: Path, state: _HealState) -> None:
    from app.utils import atomic_write

    with contextlib.suppress(OSError):
        atomic_write(path, state.to_json())
