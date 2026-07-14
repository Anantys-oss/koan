"""Lifecycle wiring for universal page-cache reclaim (#2374).

The idle hook lives INSIDE loop_manager.interruptible_sleep — the one sleep
primitive every idle path shares (between-runs, contemplative, and the whole
_IDLE_WAIT_CONFIG family: focus_wait, passive_wait, ...). Wiring it at
individual call-sites is the regression these tests guard against: focus_wait
originally shipped with no reclaim at all.
"""
import os
from unittest import mock

import pytest
from app import run
from app import loop_manager


def test_post_mission_reclaim_fires_even_when_body_raises(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(
        "app.page_cache.run_reclaim",
        lambda reason, **k: called.setdefault("reason", reason),
    )
    # Force the subprocess-launch path to raise so we exercise the finally,
    # not the happy path. popen_cli is late-imported from app.cli_exec.
    monkeypatch.setattr(
        "app.cli_exec.popen_cli", mock.Mock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr("app.utils.create_mission_tmp_dir", lambda tag="": str(tmp_path))
    monkeypatch.setattr("app.utils.cleanup_mission_tmp_dir", lambda p: None)
    monkeypatch.setattr(run, "_start_stagnation_monitor", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        run.run_claude_task(
            cmd=["true"], cwd=str(tmp_path),
            stdout_file=str(tmp_path / "o"), stderr_file=str(tmp_path / "e"),
            project_name="proj", run_num=1,
        )
    assert called.get("reason") == "post-mission"


def _mk_dirs(tmp_path):
    koan_root = str(tmp_path / "root")
    instance = str(tmp_path / "instance")
    os.makedirs(koan_root, exist_ok=True)
    os.makedirs(instance, exist_ok=True)
    return koan_root, instance


def test_interruptible_sleep_calls_idle_reclaim(monkeypatch, tmp_path):
    """Any idle path sleeping through the shared primitive reclaims."""
    koan_root, instance = _mk_dirs(tmp_path)
    called = mock.Mock()
    monkeypatch.setattr("app.page_cache.maybe_reclaim_page_cache_idle", called)
    result = loop_manager.interruptible_sleep(
        interval=1, koan_root=koan_root, instance_dir=instance, check_interval=1,
    )
    assert result == "timeout"
    called.assert_called()


def test_interruptible_sleep_reclaim_error_is_nonfatal(monkeypatch, tmp_path):
    """A reclaim failure must never break the sleep loop."""
    koan_root, instance = _mk_dirs(tmp_path)
    monkeypatch.setattr(
        "app.page_cache.maybe_reclaim_page_cache_idle",
        mock.Mock(side_effect=RuntimeError("boom")),
    )
    result = loop_manager.interruptible_sleep(
        interval=1, koan_root=koan_root, instance_dir=instance, check_interval=1,
    )
    assert result == "timeout"


def test_pending_mission_wake_skips_idle_reclaim(monkeypatch, tmp_path):
    """Waking for a mission is not idle — no reclaim before handing back."""
    koan_root, instance = _mk_dirs(tmp_path)
    monkeypatch.setattr(loop_manager, "check_pending_missions", lambda inst: True)
    called = mock.Mock()
    monkeypatch.setattr("app.page_cache.maybe_reclaim_page_cache_idle", called)
    result = loop_manager.interruptible_sleep(
        interval=60, koan_root=koan_root, instance_dir=instance, check_interval=1,
    )
    assert result == "mission"
    called.assert_not_called()


def test_focus_wait_reclaims_via_shared_primitive(monkeypatch, tmp_path):
    """The focus_wait/_IDLE_WAIT_CONFIG family sleeps via the REAL
    interruptible_sleep, so it inherits the idle reclaim with no wiring of
    its own (#2374 regression: focus_wait shipped with no reclaim)."""
    called = mock.Mock()
    monkeypatch.setattr("app.page_cache.maybe_reclaim_page_cache_idle", called)
    monkeypatch.setattr(run, "set_status", lambda *a, **k: None)
    plan = {
        "action": "focus_wait", "project_name": "koan",
        "project_path": str(tmp_path), "autonomous_mode": "implement",
        "available_pct": 50, "display_lines": [], "mission_title": "",
        "focus_area": "", "decision_reason": "", "recurring_injected": [],
        "focus_remaining": "1h",
    }
    monkeypatch.setattr(run, "plan_iteration", lambda *a, **k: plan)
    instance = str(tmp_path / "instance")
    os.makedirs(instance, exist_ok=True)
    run._run_iteration(
        koan_root=str(tmp_path), instance=instance,
        projects=[("koan", str(tmp_path))],
        count=0, max_runs=10, interval=1, git_sync_interval=5,
    )
    called.assert_called()
