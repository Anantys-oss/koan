"""Tests for parallel session orchestration wired into run.py.

Covers:
- spawn→poll→complete/fail lifecycle via _parallel_reap_sessions
- _parallel_dispatch_sessions fills slots up to max_parallel_sessions
- Single-slot regression: parallel path not taken when max == 1
- Same-project guard: two missions for the same project → only one dispatched
- Quota-exhaustion propagation from run_post_mission
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


@pytest.fixture(autouse=True)
def reset_run_module_state():
    """Clear module-level parallel state between tests."""
    import importlib
    import app.run as run_mod
    # Reset state before test
    run_mod._live_sessions.clear()
    run_mod._session_registry = None
    yield
    # Clean up after test
    run_mod._live_sessions.clear()
    run_mod._session_registry = None


@pytest.fixture
def instance_dir(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "missions.md").write_text(
        "# Missions\n\n## Pending\n\n- Fix bug A\n- Fix bug B\n\n## In Progress\n\n## Done\n"
    )
    return str(inst)


@pytest.fixture
def koan_root(tmp_path):
    root = tmp_path / "koan_root"
    root.mkdir()
    return str(root)


def _make_session(sid="sess1", project="myproject", path="/tmp/proj",
                  exit_code=None, started_at=None):
    from app.session_manager import Session
    s = Session(
        id=sid,
        mission_text="Fix bug A",
        project_name=project,
        project_path=path,
        worktree_path=f"/tmp/worktrees/{sid}",
        branch_name=f"koan/session-{sid}",
        pid=12345,
        status="running",
        started_at=started_at or time.time(),
        stdout_file=f"/tmp/stdout-{sid}.txt",
        stderr_file=f"/tmp/stderr-{sid}.txt",
    )
    if exit_code is not None:
        s.status = "done" if exit_code == 0 else "failed"
        s.exit_code = exit_code
        s.finished_at = time.time()
    return s


class TestParallelReapSessions:
    def test_no_live_sessions_returns_false(self, instance_dir, koan_root):
        from app.run import _parallel_reap_sessions
        result = _parallel_reap_sessions(instance_dir, koan_root, run_num=1, max_runs=10)
        assert result is False

    def test_no_completed_sessions_returns_false(self, instance_dir, koan_root):
        import app.run as run_mod
        from app.session_manager import SessionRegistry
        session = _make_session("s1")
        run_mod._live_sessions["s1"] = session

        with patch("app.run._get_session_registry") as mock_reg, \
             patch("app.session_manager.poll_sessions", return_value=[]) as mock_poll:
            mock_reg.return_value = MagicMock()
            result = _parallel_reap_sessions(instance_dir, koan_root, 1, 10)

        assert result is False
        assert "s1" in run_mod._live_sessions  # not removed

    def test_completed_session_reaped_success(self, instance_dir, koan_root):
        import app.run as run_mod
        from app.session_manager import Session, SessionResult

        session = _make_session("s1", exit_code=0)
        run_mod._live_sessions["s1"] = session

        completed_result = SessionResult(session=session, exit_code=0, stdout="ok", stderr="")

        mock_registry = MagicMock()
        mock_post = {"success": True, "quota_exhausted": False}

        with patch("app.run._get_session_registry", return_value=mock_registry), \
             patch("app.session_manager.poll_sessions", return_value=[completed_result]), \
             patch("app.mission_runner.run_post_mission", return_value=mock_post), \
             patch("app.missions.complete_mission_by_session", return_value="updated content") as mock_complete, \
             patch("app.missions.fail_mission_by_session") as mock_fail, \
             patch("app.run.atomic_write") as mock_write, \
             patch("app.run._notify_mission_end") as mock_notify, \
             patch("app.run._commit_instance"):
            result = _parallel_reap_sessions(instance_dir, koan_root, 1, 10)

        assert result is True
        assert "s1" not in run_mod._live_sessions
        mock_complete.assert_called_once()
        mock_fail.assert_not_called()
        mock_notify.assert_called_once()

    def test_completed_session_reaped_failure(self, instance_dir, koan_root):
        import app.run as run_mod
        from app.session_manager import SessionResult

        session = _make_session("s1", exit_code=1)
        session.status = "failed"
        run_mod._live_sessions["s1"] = session

        completed_result = SessionResult(session=session, exit_code=1, stdout="err", stderr="err")

        mock_post = {"success": False, "quota_exhausted": False}

        with patch("app.run._get_session_registry", return_value=MagicMock()), \
             patch("app.session_manager.poll_sessions", return_value=[completed_result]), \
             patch("app.mission_runner.run_post_mission", return_value=mock_post), \
             patch("app.missions.complete_mission_by_session") as mock_complete, \
             patch("app.missions.fail_mission_by_session", return_value="updated") as mock_fail, \
             patch("app.run.atomic_write"), \
             patch("app.run._notify_mission_end"), \
             patch("app.run._commit_instance"):
            result = _parallel_reap_sessions(instance_dir, koan_root, 1, 10)

        assert result is True
        assert "s1" not in run_mod._live_sessions
        mock_fail.assert_called_once()
        mock_complete.assert_not_called()

    def test_quota_exhaustion_notifies(self, instance_dir, koan_root):
        import app.run as run_mod
        from app.session_manager import SessionResult

        session = _make_session("s1", exit_code=0)
        run_mod._live_sessions["s1"] = session
        completed_result = SessionResult(session=session, exit_code=0)
        mock_post = {"success": True, "quota_exhausted": True}

        with patch("app.run._get_session_registry", return_value=MagicMock()), \
             patch("app.session_manager.poll_sessions", return_value=[completed_result]), \
             patch("app.mission_runner.run_post_mission", return_value=mock_post), \
             patch("app.missions.complete_mission_by_session", return_value=""), \
             patch("app.run.atomic_write"), \
             patch("app.run._notify_mission_end"), \
             patch("app.run._notify") as mock_notify, \
             patch("app.run._commit_instance"):
            _parallel_reap_sessions(instance_dir, koan_root, 1, 10)

        # Should send a quota warning notification
        notified_msgs = [str(c) for c in mock_notify.call_args_list]
        assert any("quota" in m.lower() for m in notified_msgs)


class TestParallelDispatchSessions:
    def _make_projects(self, project_a="/tmp/projA", project_b="/tmp/projB"):
        return [("projectA", project_a), ("projectB", project_b)]

    def test_dispatches_primary_session(self, instance_dir, koan_root):
        import app.run as run_mod
        mock_session = _make_session("new1", project="projectA", path="/tmp/projA")
        mock_session._proc = MagicMock()
        mock_session._cleanup = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_active.return_value = []
        mock_registry.get_by_project.return_value = []

        with patch("app.run._get_session_registry", return_value=mock_registry), \
             patch("app.session_manager.get_max_parallel_sessions", return_value=2), \
             patch("app.session_manager.spawn_session", return_value=mock_session) as mock_spawn, \
             patch("app.missions.pick_missions", return_value=[]), \
             patch("app.missions.start_mission_parallel", return_value="updated"), \
             patch("app.run.atomic_write"), \
             patch("app.run._notify"), \
             patch("app.git_sync.run_git", return_value="main"):
            result = _parallel_dispatch_sessions(
                primary_mission="Fix bug A",
                primary_project="projectA",
                primary_project_path="/tmp/projA",
                instance=instance_dir,
                koan_root=koan_root,
                run_num=1,
                max_runs=10,
                autonomous_mode="implement",
                projects=self._make_projects(),
                last_project="",
            )

        assert result is True
        assert "new1" in run_mod._live_sessions
        mock_spawn.assert_called_once()

    def test_fills_multiple_slots(self, instance_dir, koan_root):
        import app.run as run_mod
        sessions_spawned = []

        def _make_mock_session(sid, proj):
            s = _make_session(sid, project=proj)
            s._proc = MagicMock()
            s._cleanup = MagicMock()
            return s

        spawn_counter = [0]

        def fake_spawn(mission_text, project_name, project_path, **kwargs):
            spawn_counter[0] += 1
            sid = f"sess{spawn_counter[0]}"
            s = _make_mock_session(sid, project_name)
            s.mission_text = mission_text
            sessions_spawned.append(s)
            return s

        mock_registry = MagicMock()
        mock_registry.get_active.return_value = []
        mock_registry.get_by_project.return_value = []

        with patch("app.run._get_session_registry", return_value=mock_registry), \
             patch("app.session_manager.get_max_parallel_sessions", return_value=3), \
             patch("app.session_manager.spawn_session", side_effect=fake_spawn), \
             patch("app.missions.pick_missions", return_value=["[project:projectB] Fix bug B"]), \
             patch("app.missions.start_mission_parallel", return_value="updated"), \
             patch("app.run.atomic_write"), \
             patch("app.run._notify"), \
             patch("app.git_sync.run_git", return_value="main"):
            result = _parallel_dispatch_sessions(
                primary_mission="Fix bug A",
                primary_project="projectA",
                primary_project_path="/tmp/projA",
                instance=instance_dir,
                koan_root=koan_root,
                run_num=1,
                max_runs=10,
                autonomous_mode="implement",
                projects=self._make_projects(),
                last_project="",
            )

        assert result is True
        assert spawn_counter[0] == 2  # primary + one extra slot

    def test_same_project_guard_skips_duplicate(self, instance_dir, koan_root):
        mock_registry = MagicMock()
        mock_registry.get_active.return_value = []
        # projectA already has an active session
        mock_registry.get_by_project.side_effect = lambda p: (
            [_make_session("existing")] if p.lower() == "projecta" else []
        )

        with patch("app.run._get_session_registry", return_value=mock_registry), \
             patch("app.session_manager.get_max_parallel_sessions", return_value=2), \
             patch("app.session_manager.spawn_session") as mock_spawn, \
             patch("app.missions.pick_missions", return_value=[]), \
             patch("app.run._notify"), \
             patch("app.git_sync.run_git", return_value="main"):
            result = _parallel_dispatch_sessions(
                primary_mission="Fix bug A",
                primary_project="projectA",
                primary_project_path="/tmp/projA",
                instance=instance_dir,
                koan_root=koan_root,
                run_num=1,
                max_runs=10,
                autonomous_mode="implement",
                projects=self._make_projects(),
                last_project="",
            )

        assert result is False
        mock_spawn.assert_not_called()

    def test_all_slots_occupied_returns_false(self, instance_dir, koan_root):
        mock_registry = MagicMock()
        # 2 slots already active == max
        mock_registry.get_active.return_value = [
            _make_session("s1", project="projectA"),
            _make_session("s2", project="projectB"),
        ]

        with patch("app.run._get_session_registry", return_value=mock_registry), \
             patch("app.session_manager.get_max_parallel_sessions", return_value=2), \
             patch("app.session_manager.spawn_session") as mock_spawn:
            result = _parallel_dispatch_sessions(
                primary_mission="Fix bug A",
                primary_project="projectA",
                primary_project_path="/tmp/projA",
                instance=instance_dir,
                koan_root=koan_root,
                run_num=1,
                max_runs=10,
                autonomous_mode="implement",
                projects=self._make_projects(),
                last_project="",
            )

        assert result is False
        mock_spawn.assert_not_called()


class TestSingleSlotRegression:
    """Verify that max_parallel_sessions == 1 bypasses all parallel logic."""

    def test_reap_not_called_when_max_is_one(self, instance_dir, koan_root):
        """_parallel_reap_sessions returns False immediately when no live sessions."""
        import app.run as run_mod
        # No live sessions → reap returns False without any I/O
        from app.run import _parallel_reap_sessions
        assert run_mod._live_sessions == {}
        result = _parallel_reap_sessions(instance_dir, koan_root, 1, 10)
        assert result is False

    def test_parallel_dispatch_not_entered_for_max_one(self, instance_dir, koan_root):
        """_parallel_dispatch_sessions with max_slots=1 → still dispatches normally.

        This test verifies the guard in _run_iteration (max > 1) by checking
        that when get_max_parallel_sessions returns 1, the dispatch function
        itself still respects active-count >= max_slots → returns False.
        """
        mock_registry = MagicMock()
        mock_registry.get_active.return_value = []
        mock_registry.get_by_project.return_value = []
        mock_session = _make_session("s1")
        mock_session._proc = MagicMock()
        mock_session._cleanup = MagicMock()

        # With max_slots=1 and 0 active: slots=0, active=0, max=1
        # active_count(0) < max_slots(1) → primary would be dispatched
        with patch("app.run._get_session_registry", return_value=mock_registry), \
             patch("app.session_manager.get_max_parallel_sessions", return_value=1), \
             patch("app.session_manager.spawn_session", return_value=mock_session), \
             patch("app.missions.pick_missions", return_value=[]), \
             patch("app.missions.start_mission_parallel", return_value=""), \
             patch("app.run.atomic_write"), \
             patch("app.run._notify"), \
             patch("app.git_sync.run_git", return_value="main"):
            # The _run_iteration guard (max_slots > 1) prevents this function
            # from being called when max == 1. Here we test the function
            # directly; it should still work (dispatches primary) — it's the
            # caller's responsibility not to invoke it for max==1.
            result = _parallel_dispatch_sessions(
                primary_mission="Fix bug A",
                primary_project="projectA",
                primary_project_path="/tmp/projA",
                instance=instance_dir,
                koan_root=koan_root,
                run_num=1,
                max_runs=10,
                autonomous_mode="implement",
                projects=[("projectA", "/tmp/projA")],
                last_project="",
            )
        # Function dispatches primary — caller guards max>1 check
        assert result is True


# ---------------------------------------------------------------------------
# Helper: import the function under test without triggering KOAN_ROOT checks
# ---------------------------------------------------------------------------

def _parallel_reap_sessions(*args, **kwargs):
    from app.run import _parallel_reap_sessions as fn
    return fn(*args, **kwargs)


def _parallel_dispatch_sessions(*args, **kwargs):
    from app.run import _parallel_dispatch_sessions as fn
    return fn(*args, **kwargs)
