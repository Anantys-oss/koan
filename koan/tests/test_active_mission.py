"""Tests for provider-subprocess liveness signal (active_mission.py, #2086)."""

import json
import os
import time

import pytest

from app import active_mission as am
from app.signals import ACTIVE_FILE


@pytest.fixture
def koan_root(tmp_path):
    return tmp_path


def test_idle_when_no_signal(koan_root):
    state = am.get_execution_state(koan_root)
    assert state["state"] == "idle"
    assert state["pid"] is None
    assert am.read_active(koan_root) is None


def test_write_and_read_roundtrip(koan_root):
    am.write_active(koan_root, pid=os.getpid(), project="koan", run_num=3)
    record = am.read_active(koan_root)
    assert record["pid"] == os.getpid()
    assert record["project"] == "koan"
    assert record["run_num"] == 3
    assert record["started_at"] > 0


def test_clear_removes_signal(koan_root):
    am.write_active(koan_root, pid=os.getpid())
    am.clear_active(koan_root)
    assert am.read_active(koan_root) is None
    # Clearing an absent signal is a no-op, not an error.
    am.clear_active(koan_root)


def test_working_state_for_live_pid_with_recent_output(koan_root, tmp_path):
    stdout = tmp_path / "out.log"
    stdout.write_text("provider output")
    am.write_active(koan_root, pid=os.getpid(), stdout_file=str(stdout))
    state = am.get_execution_state(koan_root)
    assert state["state"] == "working"
    assert state["pid"] == os.getpid()
    assert state["last_output_age"] is not None


def test_working_state_when_output_unknown(koan_root):
    # No stdout_file recorded → output age unknown → still "working" for a live PID.
    am.write_active(koan_root, pid=os.getpid())
    state = am.get_execution_state(koan_root)
    assert state["state"] == "working"
    assert state["last_output_age"] is None


def test_stalled_state_for_live_pid_with_stale_output(koan_root, tmp_path):
    stdout = tmp_path / "out.log"
    stdout.write_text("old output")
    old = time.time() - (am.STALL_THRESHOLD_SECONDS + 60)
    os.utime(stdout, (old, old))
    am.write_active(koan_root, pid=os.getpid(), stdout_file=str(stdout))
    state = am.get_execution_state(koan_root)
    assert state["state"] == "stalled"


def test_zombie_state_for_dead_pid(koan_root):
    # PID 2**31-1 is effectively guaranteed not to exist.
    am.write_active(koan_root, pid=2_147_483_646)
    state = am.get_execution_state(koan_root)
    assert state["state"] == "zombie"


def test_corrupt_signal_reads_as_idle(koan_root):
    (koan_root / ACTIVE_FILE).write_text("{ not json")
    assert am.read_active(koan_root) is None
    assert am.get_execution_state(koan_root)["state"] == "idle"


def test_invalid_pid_is_not_alive(koan_root):
    am.write_active(koan_root, pid=0)
    assert am.get_execution_state(koan_root)["state"] == "zombie"


def test_record_is_valid_json(koan_root):
    am.write_active(koan_root, pid=os.getpid(), project="p")
    raw = (koan_root / ACTIVE_FILE).read_text()
    assert json.loads(raw)["project"] == "p"


def test_unreadable_stdout_classifies_as_stalled(koan_root, tmp_path):
    # A configured stdout file that vanished while the provider hangs must not
    # be masked as "working" — it biases pessimistically to "stalled".
    missing = tmp_path / "gone.log"
    am.write_active(koan_root, pid=os.getpid(), stdout_file=str(missing))
    state = am.get_execution_state(koan_root)
    assert state["state"] == "stalled"
    # ``inf`` must not leak into the serialized payload.
    assert state["last_output_age"] is None


def test_is_zombie_false_when_not_in_progress(koan_root):
    assert am.is_zombie(koan_root, in_progress=False) is False


def test_is_zombie_true_for_dead_recorded_pid(koan_root):
    am.write_active(koan_root, pid=2_147_483_646)
    assert am.is_zombie(koan_root, in_progress=True) is True


def test_is_zombie_false_while_provider_alive(koan_root):
    am.write_active(koan_root, pid=os.getpid())
    assert am.is_zombie(koan_root, in_progress=True) is False


def test_is_zombie_false_during_start_stop_window_with_fresh_heartbeat(koan_root):
    # In Progress, no provider signal, but the run loop heartbeat is fresh →
    # normal between-missions window, not a zombie (no flapping).
    (koan_root / ".koan-run-heartbeat").write_text(str(time.time()))
    assert am.is_zombie(koan_root, in_progress=True) is False


def test_is_zombie_true_when_run_loop_stale(koan_root):
    # The heartbeat age is the timestamp stored in the file, not its mtime.
    (koan_root / ".koan-run-heartbeat").write_text(str(time.time() - 1200))
    assert am.is_zombie(koan_root, in_progress=True) is True


def _register_running_session(koan_root, pid):
    """Register a running parallel session under koan_root/instance."""
    from app.session_manager import Session, SessionRegistry

    instance_dir = koan_root / "instance"
    instance_dir.mkdir(exist_ok=True)
    reg = SessionRegistry(str(instance_dir))
    reg.register(
        Session(
            id="s1",
            mission_text="m",
            project_name="koan",
            project_path="/p",
            worktree_path="/w",
            branch_name="koan/x",
            pid=pid,
            status="running",
        )
    )


def test_parallel_session_reported_as_working(koan_root):
    # No main-loop .koan-active signal, but a live parallel session is running.
    _register_running_session(koan_root, os.getpid())
    state = am.get_execution_state(koan_root)
    assert state["state"] == "working"
    assert state["sessions"] == 1


def test_parallel_session_prevents_false_zombie(koan_root):
    # In Progress with no main signal but a live parallel session → not a zombie
    # even when the run-loop heartbeat is stale.
    _register_running_session(koan_root, os.getpid())
    (koan_root / ".koan-run-heartbeat").write_text(str(time.time() - 1200))
    assert am.is_zombie(koan_root, in_progress=True) is False
