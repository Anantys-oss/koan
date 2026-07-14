"""Lifecycle wiring for universal page-cache reclaim (#2374)."""
from unittest import mock

import pytest
from app import run


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


def test_sleep_between_runs_calls_idle_reclaim(monkeypatch):
    monkeypatch.setattr(run, "check_pending_missions", lambda inst: False)
    monkeypatch.setattr(run, "interruptible_sleep", lambda *a, **k: "timeout")
    monkeypatch.setattr(run, "set_status", lambda *a, **k: None)
    called = mock.Mock()
    monkeypatch.setattr("app.page_cache.maybe_reclaim_page_cache_idle", called)
    run._sleep_between_runs("/root", "inst", interval=60)
    called.assert_called_once()


def test_pending_missions_skip_idle_reclaim(monkeypatch):
    monkeypatch.setattr(run, "check_pending_missions", lambda inst: True)
    monkeypatch.setattr(run, "set_status", lambda *a, **k: None)
    called = mock.Mock()
    monkeypatch.setattr("app.page_cache.maybe_reclaim_page_cache_idle", called)
    run._sleep_between_runs("/root", "inst", interval=60)
    called.assert_not_called()
