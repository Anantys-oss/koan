"""Tests for bridge_watchdog — health detection + tier escalation.

The watchdog acts on three observable inputs:
  * the bridge PID (read from instance/.koan-pid-awake)
  * the heartbeat mtime
  * git HEAD vs the bridge's recorded SHA

Tests mock the three external effects (request_restart, os.kill,
pid_manager.start_awake) and assert the right tier fires in each scenario.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.bridge_watchdog import (
    BRIDGE_HEARTBEAT_STALE_S,
    HEAL_CIRCUIT_BREAKER_LIMIT,
    HEAL_TIER_COOLDOWN_S,
    check_and_heal_bridge,
    write_bridge_version_stamp,
)
from app.signals import (
    BRIDGE_HEAL_STATE_FILE,
    BRIDGE_VERSION_FILE,
    HEARTBEAT_FILE,
    pid_file,
)


# ---------------------------------------------------------------------------
# Helpers — build a koan_root tmp dir in a known state.
# ---------------------------------------------------------------------------


def _set_bridge_pid(koan_root: Path, pid: int) -> None:
    (koan_root / pid_file("awake")).write_text(str(pid))


def _set_heartbeat(koan_root: Path, age_seconds: float) -> None:
    path = koan_root / HEARTBEAT_FILE
    path.write_text(str(time.time() - age_seconds))
    mtime = time.time() - age_seconds
    import os
    os.utime(path, (mtime, mtime))


def _set_bridge_sha(koan_root: Path, sha: str) -> None:
    (koan_root / BRIDGE_VERSION_FILE).write_text(sha)


@pytest.fixture
def healthy_root(tmp_path):
    """A koan_root where everything is fine: alive pid, fresh heartbeat, SHA match."""
    _set_bridge_pid(tmp_path, 99999999)  # nonsense pid; we'll mock _is_process_alive
    _set_heartbeat(tmp_path, age_seconds=2.0)
    _set_bridge_sha(tmp_path, "abc1234")
    return tmp_path


@pytest.fixture(autouse=True)
def stub_external_effects():
    """Default: process always alive, git HEAD matches whatever bridge SHA is set,
    request_restart / SIGTERM / start_awake all succeed without side-effects.

    Helpers like ``request_restart`` and ``start_awake`` are imported
    lazily inside the watchdog so they're patched at their source
    modules, not on ``app.bridge_watchdog``.
    """
    with (
        patch("app.bridge_watchdog._is_process_alive", return_value=True) as mock_alive,
        patch("app.bridge_watchdog._read_git_head", return_value="abc1234") as mock_head,
        patch("app.bridge_watchdog.os.kill") as mock_kill,
        patch("app.restart_manager.request_restart") as mock_req_restart,
        patch("app.pid_manager.start_awake", return_value=(True, "ok")) as mock_start,
    ):
        yield {
            "is_alive": mock_alive,
            "head": mock_head,
            "kill": mock_kill,
            "request_restart": mock_req_restart,
            "start_awake": mock_start,
        }


# ---------------------------------------------------------------------------
# write_bridge_version_stamp
# ---------------------------------------------------------------------------


class TestWriteBridgeVersionStamp:
    def test_records_git_head(self, tmp_path):
        with patch("app.bridge_watchdog._read_git_head", return_value="deadbeef"):
            write_bridge_version_stamp(tmp_path)
        assert (tmp_path / BRIDGE_VERSION_FILE).read_text() == "deadbeef"

    def test_records_unknown_when_git_unavailable(self, tmp_path):
        with patch("app.bridge_watchdog._read_git_head", return_value=None):
            write_bridge_version_stamp(tmp_path)
        assert (tmp_path / BRIDGE_VERSION_FILE).read_text() == "unknown"


# ---------------------------------------------------------------------------
# Healthy bridge — no action
# ---------------------------------------------------------------------------


class TestHealthy:
    def test_no_action_when_everything_fresh(self, healthy_root, stub_external_effects):
        msg = check_and_heal_bridge(healthy_root)
        assert msg is None
        stub_external_effects["request_restart"].assert_not_called()
        stub_external_effects["kill"].assert_not_called()
        stub_external_effects["start_awake"].assert_not_called()

    def test_resets_heal_state_when_healthy(self, healthy_root):
        """A prior heal cycle leaves state on disk; once healthy, the watchdog
        wipes it so a future incident starts at tier 1, not somewhere mid-flight."""
        state_path = healthy_root / BRIDGE_HEAL_STATE_FILE
        state_path.write_text(json.dumps({
            "last_action_ts": time.time() - 1000,
            "last_tier": 2,
            "consecutive_failures": 1,
        }))
        msg = check_and_heal_bridge(healthy_root)
        assert msg is None
        data = json.loads(state_path.read_text())
        assert data["last_tier"] == 0
        assert data["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# Tier 1 — SHA mismatch triggers cooperative restart
# ---------------------------------------------------------------------------


class TestTier1:
    def test_sha_mismatch_triggers_cooperative_restart(
        self, healthy_root, stub_external_effects
    ):
        stub_external_effects["head"].return_value = "newsha999"  # disk drifted
        msg = check_and_heal_bridge(healthy_root)
        assert msg is not None
        assert "tier 1" in msg
        stub_external_effects["request_restart"].assert_called_once_with(str(healthy_root))
        stub_external_effects["kill"].assert_not_called()

    def test_stale_heartbeat_triggers_cooperative_restart(
        self, healthy_root, stub_external_effects
    ):
        _set_heartbeat(healthy_root, age_seconds=BRIDGE_HEARTBEAT_STALE_S + 10)
        msg = check_and_heal_bridge(healthy_root)
        assert msg is not None
        assert "tier 1" in msg
        stub_external_effects["request_restart"].assert_called_once()

    def test_unknown_bridge_sha_does_not_trigger(
        self, healthy_root, stub_external_effects
    ):
        """If the bridge's stamp is missing (pre-upgrade incarnation), we
        can't reliably compare SHAs — skip the check rather than flapping."""
        (healthy_root / BRIDGE_VERSION_FILE).unlink()
        msg = check_and_heal_bridge(healthy_root)
        assert msg is None


# ---------------------------------------------------------------------------
# Cooldown — don't keep retrying within a tier's grace window
# ---------------------------------------------------------------------------


class TestCooldown:
    def test_no_second_action_during_cooldown(
        self, healthy_root, stub_external_effects
    ):
        stub_external_effects["head"].return_value = "newsha999"
        # First call triggers tier 1.
        check_and_heal_bridge(healthy_root)
        stub_external_effects["request_restart"].reset_mock()

        # Second call within the cooldown window — no new action.
        msg = check_and_heal_bridge(healthy_root)
        assert msg is None
        stub_external_effects["request_restart"].assert_not_called()


# ---------------------------------------------------------------------------
# Tier 2 — SIGTERM after tier 1 didn't take
# ---------------------------------------------------------------------------


class TestTier2:
    def _force_post_cooldown_state(self, koan_root: Path, last_tier: int):
        """Drop a heal-state file as if the previous tier already ran and
        the cooldown has elapsed."""
        (koan_root / BRIDGE_HEAL_STATE_FILE).write_text(json.dumps({
            "last_action_ts": time.time() - (HEAL_TIER_COOLDOWN_S + 5),
            "last_tier": last_tier,
            "consecutive_failures": 0,
        }))

    def test_escalates_to_sigterm_after_tier1(
        self, healthy_root, stub_external_effects
    ):
        stub_external_effects["head"].return_value = "newsha999"
        _set_bridge_pid(healthy_root, 42424)
        self._force_post_cooldown_state(healthy_root, last_tier=1)

        msg = check_and_heal_bridge(healthy_root)
        assert msg is not None
        assert "tier 2" in msg
        stub_external_effects["kill"].assert_called_once()
        sent_pid, sent_signal = stub_external_effects["kill"].call_args[0]
        assert sent_pid == 42424
        # SIGTERM = 15 on POSIX
        import signal as _s
        assert sent_signal == _s.SIGTERM
        # request_restart should NOT fire again (tier 1 already happened).
        stub_external_effects["request_restart"].assert_not_called()


# ---------------------------------------------------------------------------
# Tier 3 — SIGKILL + relaunch
# ---------------------------------------------------------------------------


class TestTier3:
    def test_kills_then_relaunches_after_sigterm(
        self, healthy_root, stub_external_effects
    ):
        stub_external_effects["head"].return_value = "newsha999"
        _set_bridge_pid(healthy_root, 4242)
        (healthy_root / BRIDGE_HEAL_STATE_FILE).write_text(json.dumps({
            "last_action_ts": time.time() - (HEAL_TIER_COOLDOWN_S + 5),
            "last_tier": 2,
            "consecutive_failures": 0,
        }))
        # _is_process_alive: True first (still alive after SIGTERM), then dies
        # after one polling iteration.
        liveness = iter([True, True, False])
        stub_external_effects["is_alive"].side_effect = lambda *_args, **_kw: next(
            liveness, False
        )

        with patch("app.bridge_watchdog.time.sleep"):  # don't actually sleep
            msg = check_and_heal_bridge(healthy_root)

        assert msg is not None
        assert "tier 3" in msg
        stub_external_effects["start_awake"].assert_called_once_with(healthy_root)

    def test_no_pid_skips_straight_to_relaunch(
        self, healthy_root, stub_external_effects
    ):
        """If the PID file is gone entirely we have no process to signal —
        go straight to tier 3 cold start."""
        (healthy_root / pid_file("awake")).unlink()
        msg = check_and_heal_bridge(healthy_root)
        assert msg is not None
        assert "tier 3" in msg
        stub_external_effects["start_awake"].assert_called_once_with(healthy_root)
        # No SIGKILL since there's no PID to signal.
        stub_external_effects["kill"].assert_not_called()


# ---------------------------------------------------------------------------
# Circuit breaker — stop trying after N failed tier-3 cycles
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_emits_alert_and_stops_acting_after_limit(
        self, healthy_root, stub_external_effects
    ):
        stub_external_effects["head"].return_value = "newsha999"
        # State already at the breaker limit, with cooldown elapsed so the
        # watchdog is willing to act again — except for the breaker.
        from app.bridge_watchdog import POST_HEAL_QUIET_S
        (healthy_root / BRIDGE_HEAL_STATE_FILE).write_text(json.dumps({
            "last_action_ts": time.time() - (POST_HEAL_QUIET_S + 5),
            "last_tier": 3,
            "consecutive_failures": HEAL_CIRCUIT_BREAKER_LIMIT,
        }))

        msg = check_and_heal_bridge(healthy_root)
        assert msg is not None
        assert "circuit-broken" in msg
        stub_external_effects["request_restart"].assert_not_called()
        stub_external_effects["kill"].assert_not_called()
        stub_external_effects["start_awake"].assert_not_called()
