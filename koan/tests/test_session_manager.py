"""Tests for session_manager.py — parallel session registry and lifecycle.

Mocks subprocess spawning (no real Claude calls per CLAUDE.md conventions).
"""

import json
import os
import subprocess
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from app.session_manager import (
    Session,
    SessionRegistry,
    SessionResult,
    get_max_parallel_sessions,
    kill_session,
    poll_sessions,
    recover_stale_sessions,
    spawn_session,
    _dict_to_session,
    SESSIONS_FILE,
)


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "missions.md").write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    return str(inst)


@pytest.fixture
def registry(instance_dir):
    """Create a SessionRegistry."""
    return SessionRegistry(instance_dir)


@pytest.fixture
def sample_session():
    """Create a sample session for testing."""
    return Session(
        id="abc123",
        mission_text="Fix the auth bug",
        project_name="myproject",
        project_path="/tmp/project",
        worktree_path="/tmp/project/.worktrees/abc123",
        branch_name="koan/session-abc123",
        pid=12345,
        status="running",
        started_at=time.time(),
        stdout_file="/tmp/stdout.txt",
        stderr_file="/tmp/stderr.txt",
    )


class TestSessionRegistry:
    def test_register_and_get(self, registry, sample_session):
        registry.register(sample_session)
        retrieved = registry.get(sample_session.id)
        assert retrieved is not None
        assert retrieved.id == sample_session.id
        assert retrieved.mission_text == sample_session.mission_text

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    def test_update(self, registry, sample_session):
        registry.register(sample_session)
        sample_session.status = "done"
        sample_session.exit_code = 0
        registry.update(sample_session)
        retrieved = registry.get(sample_session.id)
        assert retrieved.status == "done"
        assert retrieved.exit_code == 0

    def test_remove(self, registry, sample_session):
        registry.register(sample_session)
        registry.remove(sample_session.id)
        assert registry.get(sample_session.id) is None

    def test_get_all(self, registry):
        s1 = Session(id="s1", mission_text="m1", project_name="p", project_path="/p",
                     worktree_path="/w1", branch_name="b1", status="running")
        s2 = Session(id="s2", mission_text="m2", project_name="p", project_path="/p",
                     worktree_path="/w2", branch_name="b2", status="done")
        registry.register(s1)
        registry.register(s2)
        all_sessions = registry.get_all()
        assert len(all_sessions) == 2
        ids = {s.id for s in all_sessions}
        assert ids == {"s1", "s2"}

    def test_get_active(self, registry):
        s1 = Session(id="s1", mission_text="m1", project_name="p", project_path="/p",
                     worktree_path="/w1", branch_name="b1", status="running")
        s2 = Session(id="s2", mission_text="m2", project_name="p", project_path="/p",
                     worktree_path="/w2", branch_name="b2", status="done")
        registry.register(s1)
        registry.register(s2)
        active = registry.get_active()
        assert len(active) == 1
        assert active[0].id == "s1"

    def test_get_by_project(self, registry):
        s1 = Session(id="s1", mission_text="m1", project_name="proj-a", project_path="/a",
                     worktree_path="/w1", branch_name="b1", status="running")
        s2 = Session(id="s2", mission_text="m2", project_name="proj-b", project_path="/b",
                     worktree_path="/w2", branch_name="b2", status="running")
        registry.register(s1)
        registry.register(s2)
        proj_a = registry.get_by_project("proj-a")
        assert len(proj_a) == 1
        assert proj_a[0].id == "s1"

    def test_clear_completed(self, registry):
        s1 = Session(id="s1", mission_text="m1", project_name="p", project_path="/p",
                     worktree_path="/w1", branch_name="b1", status="running")
        s2 = Session(id="s2", mission_text="m2", project_name="p", project_path="/p",
                     worktree_path="/w2", branch_name="b2", status="done")
        s3 = Session(id="s3", mission_text="m3", project_name="p", project_path="/p",
                     worktree_path="/w3", branch_name="b3", status="failed")
        registry.register(s1)
        registry.register(s2)
        registry.register(s3)
        registry.clear_completed()
        all_sessions = registry.get_all()
        assert len(all_sessions) == 1
        assert all_sessions[0].id == "s1"

    def test_persistence(self, instance_dir):
        """Data persists across registry instances."""
        reg1 = SessionRegistry(instance_dir)
        s = Session(id="persist", mission_text="m", project_name="p", project_path="/p",
                    worktree_path="/w", branch_name="b", status="running")
        reg1.register(s)

        reg2 = SessionRegistry(instance_dir)
        retrieved = reg2.get("persist")
        assert retrieved is not None
        assert retrieved.id == "persist"

    def test_handles_corrupt_file(self, instance_dir):
        """Gracefully handles corrupt sessions.json."""
        path = Path(instance_dir) / SESSIONS_FILE
        path.write_text("not valid json{{{")
        reg = SessionRegistry(instance_dir)
        assert reg.get_all() == []

    def test_handles_missing_file(self, instance_dir):
        reg = SessionRegistry(instance_dir)
        assert reg.get_all() == []


class TestDictToSession:
    def test_basic_conversion(self):
        d = {"id": "x", "mission_text": "m", "project_name": "p",
             "project_path": "/p", "worktree_path": "/w", "branch_name": "b"}
        s = _dict_to_session(d)
        assert s.id == "x"
        assert s.status == "pending"  # default

    def test_ignores_unknown_keys(self):
        d = {"id": "x", "mission_text": "m", "project_name": "p",
             "project_path": "/p", "worktree_path": "/w", "branch_name": "b",
             "unknown_field": "ignored"}
        s = _dict_to_session(d)
        assert s.id == "x"


class TestGetMaxParallelSessions:
    @patch("app.utils.load_config")
    def test_default(self, mock_config):
        mock_config.return_value = {}
        assert get_max_parallel_sessions() == 1

    @patch("app.utils.load_config")
    def test_configured(self, mock_config):
        mock_config.return_value = {"max_parallel_sessions": 3}
        assert get_max_parallel_sessions() == 3

    @patch("app.utils.load_config")
    def test_capped_at_max(self, mock_config):
        mock_config.return_value = {"max_parallel_sessions": 10}
        assert get_max_parallel_sessions() == 5

    @patch("app.utils.load_config")
    def test_minimum_one(self, mock_config):
        mock_config.return_value = {"max_parallel_sessions": 0}
        assert get_max_parallel_sessions() == 1

    @patch("app.utils.load_config", side_effect=ValueError("bad config"))
    def test_config_error_returns_default(self, mock_config, capsys):
        assert get_max_parallel_sessions() == 1
        assert "config read error" in capsys.readouterr().err


class TestPollSessions:
    def test_detects_completed(self, registry, sample_session):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock()

        # Create temp output files
        import tempfile
        fd, stdout = tempfile.mkstemp()
        os.write(fd, b"session output")
        os.close(fd)
        fd2, stderr = tempfile.mkstemp()
        os.write(fd2, b"")
        os.close(fd2)
        sample_session.stdout_file = stdout
        sample_session.stderr_file = stderr

        registry.register(sample_session)
        results = poll_sessions([sample_session], registry)

        assert len(results) == 1
        assert results[0].exit_code == 0
        assert results[0].session.status == "done"
        assert "session output" in results[0].stdout

        # Cleanup
        Path(stdout).unlink(missing_ok=True)
        Path(stderr).unlink(missing_ok=True)

    def test_still_running(self, registry, sample_session):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running
        sample_session._proc = mock_proc

        registry.register(sample_session)
        results = poll_sessions([sample_session], registry)
        assert len(results) == 0

    def test_failed_session(self, registry, sample_session):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock()
        sample_session.stdout_file = ""
        sample_session.stderr_file = ""

        registry.register(sample_session)
        results = poll_sessions([sample_session], registry)

        assert len(results) == 1
        assert results[0].exit_code == 1
        assert results[0].session.status == "failed"

    def test_no_proc_skipped(self, registry, sample_session):
        """Sessions without _proc attribute are skipped."""
        registry.register(sample_session)
        results = poll_sessions([sample_session], registry)
        assert len(results) == 0

    def test_cleanup_error_is_logged_but_result_is_returned(
        self, registry, sample_session, tmp_path, capsys,
    ):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock(side_effect=RuntimeError("cleanup boom"))

        stdout = tmp_path / "stdout.txt"
        stderr = tmp_path / "stderr.txt"
        stdout.write_text("ok")
        stderr.write_text("warn")
        sample_session.stdout_file = str(stdout)
        sample_session.stderr_file = str(stderr)

        registry.register(sample_session)
        results = poll_sessions([sample_session], registry)

        assert len(results) == 1
        assert results[0].stdout == "ok"
        assert results[0].stderr == "warn"
        assert "cleanup error" in capsys.readouterr().err


class TestKillSession:
    def test_kills_process_and_updates_registry(self, registry, sample_session):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 99999
        mock_proc.wait.return_value = None
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock()

        registry.register(sample_session)

        with patch("os.getpgid", return_value=99999), \
             patch("os.killpg"), \
             patch("app.session_manager.remove_worktree"):
            kill_session(sample_session, registry)

        assert sample_session.status == "failed"
        retrieved = registry.get(sample_session.id)
        assert retrieved.status == "failed"

    def test_handles_already_dead_process(self, registry, sample_session):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # Already finished
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock()

        registry.register(sample_session)

        with patch("app.session_manager.remove_worktree"):
            kill_session(sample_session, registry)

        assert sample_session.status == "failed"

    def test_kills_by_pid_when_proc_reference_missing(self, registry, sample_session):
        sample_session.pid = 12345
        registry.register(sample_session)

        with (
            patch("os.kill") as mock_kill,
            patch("app.session_manager.remove_worktree"),
        ):
            kill_session(sample_session, registry)

        mock_kill.assert_called_once()
        assert mock_kill.call_args.args[0] == 12345

    def test_timeout_escalates_to_sigkill(self, registry, sample_session):
        import signal

        mock_proc = MagicMock()
        mock_proc.pid = 22222
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="agent", timeout=5),
            None,
        ]
        sample_session._proc = mock_proc
        registry.register(sample_session)

        with (
            patch("os.getpgid", return_value=22222),
            patch("os.killpg") as mock_killpg,
            patch("app.session_manager.remove_worktree"),
        ):
            kill_session(sample_session, registry)

        assert mock_killpg.call_args_list[0].args == (22222, signal.SIGTERM)
        assert mock_killpg.call_args_list[1].args == (22222, signal.SIGKILL)


class TestSpawnSessionProviderThreading:
    """spawn_session must execute under the role's CLI provider (cli: section)."""

    @patch("app.session_manager.inject_worktree_claude_md")
    @patch("app.session_manager.create_worktree")
    def test_role_provider_threaded_into_popen_cli(
        self, mock_create_wt, mock_inject, registry, tmp_path,
    ):
        """cli.mission: codex under a global claude → popen_cli gets the codex
        provider, and build_mission_command gets the same provider_override."""
        import app.provider as provider

        wt = MagicMock()
        wt.session_id = "test-prov"
        wt.path = str(tmp_path / "worktree")
        wt.branch = "koan/session-test-prov"
        mock_create_wt.return_value = wt

        full = {
            "cli_provider": "claude",  # global
            "cli": {"default": {"mission": "codex"}},
            "skip_permissions": False,
        }
        captured = {}

        def fake_build(*args, **kwargs):
            captured["build_provider"] = kwargs.get("provider_override")
            return (["codex", "-p", "x"], [])

        opened = []
        real_open = open

        def tracking_open(path, mode="r", **kwargs):
            f = real_open(path, mode, **kwargs)
            opened.append(f)
            return f

        def fake_popen(cmd, provider=None, **kwargs):
            captured["popen_provider"] = provider
            return MagicMock(pid=4321), MagicMock()

        with patch("app.config._load_config", return_value=full), \
             patch("app.config._load_project_overrides", return_value={}), \
             patch("app.utils.load_config", return_value=full), \
             patch("app.mission_runner.build_mission_command", side_effect=fake_build), \
             patch("builtins.open", side_effect=tracking_open), \
             patch("app.cli_exec.popen_cli", side_effect=fake_popen):
            provider.reset_provider()
            spawn_session(
                mission_text="do it",
                project_name="p",
                project_path=str(tmp_path),
                instance_dir=registry.instance_dir,
                registry=registry,
                autonomous_mode="implement",
            )

        for f in opened:
            f.close()

        # popen_cli received a provider, it is the codex role provider, and it
        # is the SAME instance passed to build_mission_command (not the global
        # claude singleton).
        assert captured["popen_provider"] is not None
        assert captured["popen_provider"].name == "codex"
        assert captured["popen_provider"] is captured["build_provider"]


class TestSpawnSessionFileHandleLeak:
    """Verify file handles are closed when spawn_session fails."""

    @patch("app.session_manager.inject_worktree_claude_md")
    @patch("app.session_manager.create_worktree")
    def test_out_f_closed_when_popen_raises(self, mock_create_wt, mock_inject, registry, tmp_path):
        """out_f and err_f are both closed when popen_cli() raises."""
        wt = MagicMock()
        wt.session_id = "test-leak"
        wt.path = str(tmp_path / "worktree")
        wt.branch = "koan/session-test-leak"
        mock_create_wt.return_value = wt

        opened_files = []
        real_open = open

        def tracking_open(path, mode="r", **kwargs):
            f = real_open(path, mode, **kwargs)
            opened_files.append(f)
            return f

        with patch("app.mission_runner.build_mission_command", return_value=(["echo"], [])), \
             patch("builtins.open", side_effect=tracking_open), \
             patch("app.cli_exec.popen_cli", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                spawn_session(
                    mission_text="test",
                    project_name="p",
                    project_path=str(tmp_path),
                    instance_dir=registry.instance_dir,
                    registry=registry,
                )

        # Both file handles that were opened should be closed
        assert len(opened_files) == 2
        assert all(f.closed for f in opened_files), "leaked file handle(s)"

    @patch("app.session_manager.inject_worktree_claude_md")
    @patch("app.session_manager.create_worktree")
    def test_out_f_closed_when_stderr_open_raises(self, mock_create_wt, mock_inject, registry, tmp_path):
        """out_f is closed when the second open() (stderr) raises."""
        wt = MagicMock()
        wt.session_id = "test-leak2"
        wt.path = str(tmp_path / "worktree")
        wt.branch = "koan/session-test-leak2"
        mock_create_wt.return_value = wt

        opened_files = []
        real_open = open
        call_count = 0

        def open_fail_second(path, mode="r", **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("disk full")
            f = real_open(path, mode, **kwargs)
            opened_files.append(f)
            return f

        with patch("app.mission_runner.build_mission_command", return_value=(["echo"], [])), \
             patch("builtins.open", side_effect=open_fail_second):
            with pytest.raises(OSError, match="disk full"):
                spawn_session(
                    mission_text="test",
                    project_name="p",
                    project_path=str(tmp_path),
                    instance_dir=registry.instance_dir,
                    registry=registry,
                )

        # The first file handle (out_f) must be closed despite second open failing
        assert len(opened_files) == 1
        assert opened_files[0].closed, "out_f leaked when stderr open() raised"


class TestSpawnSessionTmpdir:
    """Sessions run with a private per-session TMPDIR, reaped at cleanup."""

    @pytest.fixture(autouse=True)
    def _isolated_tmp(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "_koan_tmp_dir_cache", None)
        monkeypatch.setenv("KOAN_TMP_DIR", str(tmp_path / "ktmp"))

    @patch("app.session_manager.inject_worktree_claude_md")
    @patch("app.session_manager.create_worktree")
    def test_env_tmpdir_set_and_reaped_on_cleanup(self, mock_create_wt, mock_inject, registry, tmp_path):
        from app.utils import koan_tmp_dir

        wt = MagicMock()
        wt.session_id = "tmpdir123456"
        wt.path = str(tmp_path / "worktree")
        wt.branch = "koan/session-tmpdir123456"
        mock_create_wt.return_value = wt

        captured = {}

        def fake_popen(cmd, provider=None, **kwargs):
            captured["env"] = kwargs.get("env")
            return MagicMock(pid=4321), MagicMock()

        with patch("app.mission_runner.build_mission_command", return_value=(["echo"], [])), \
             patch("app.cli_exec.popen_cli", side_effect=fake_popen):
            session = spawn_session(
                mission_text="test",
                project_name="p",
                project_path=str(tmp_path),
                instance_dir=registry.instance_dir,
                registry=registry,
            )

        env = captured["env"]
        assert env is not None
        session_tmp = Path(env["TMPDIR"])
        assert str(session_tmp.parent) == koan_tmp_dir()
        assert session_tmp.name == f"mission-{os.getpid()}-tmpdir123456"
        assert session_tmp.is_dir()

        (session_tmp / "left-behind").write_text("scratch")
        session._cleanup()
        assert not session_tmp.exists()

    @patch("app.session_manager.inject_worktree_claude_md")
    @patch("app.session_manager.create_worktree")
    def test_tmpdir_reaped_when_popen_raises(self, mock_create_wt, mock_inject, registry, tmp_path):
        from app.utils import koan_tmp_dir

        wt = MagicMock()
        wt.session_id = "tmpdirboom99"
        wt.path = str(tmp_path / "worktree")
        wt.branch = "koan/session-tmpdirboom99"
        mock_create_wt.return_value = wt

        with patch("app.mission_runner.build_mission_command", return_value=(["echo"], [])), \
             patch("app.cli_exec.popen_cli", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                spawn_session(
                    mission_text="test",
                    project_name="p",
                    project_path=str(tmp_path),
                    instance_dir=registry.instance_dir,
                    registry=registry,
                )

        leftovers = list(Path(koan_tmp_dir()).glob("mission-*"))
        assert leftovers == [], f"session TMPDIR leaked: {leftovers}"


class TestRecoverStaleSessions:
    def test_marks_dead_sessions_as_failed(self, registry):
        s = Session(id="dead", mission_text="m", project_name="p",
                    project_path="/tmp/fake", worktree_path="/tmp/fake/wt",
                    branch_name="b", status="running", pid=999999)
        registry.register(s)

        with patch("os.kill", side_effect=ProcessLookupError), \
             patch("app.session_manager.remove_worktree"):
            recover_stale_sessions(registry)

        retrieved = registry.get("dead")
        assert retrieved.status == "failed"

    def test_leaves_alive_sessions(self, registry):
        s = Session(id="alive", mission_text="m", project_name="p",
                    project_path="/tmp/fake", worktree_path="/tmp/fake/wt",
                    branch_name="b", status="running", pid=os.getpid())
        registry.register(s)

        recover_stale_sessions(registry)

        retrieved = registry.get("alive")
        assert retrieved.status == "running"

    def test_handles_zero_pid(self, registry):
        s = Session(id="nopid", mission_text="m", project_name="p",
                    project_path="/tmp/fake", worktree_path="/tmp/fake/wt",
                    branch_name="b", status="running", pid=0)
        registry.register(s)

        recover_stale_sessions(registry)

        retrieved = registry.get("nopid")
        assert retrieved.status == "failed"

    def test_permission_error_leaves_session_running(self, registry):
        s = Session(id="permission", mission_text="m", project_name="p",
                    project_path="/tmp/fake", worktree_path="/tmp/fake/wt",
                    branch_name="b", status="running", pid=123)
        registry.register(s)

        with (
            patch("app.session_manager.prune_worktrees"),
            patch("os.kill", side_effect=PermissionError),
        ):
            recover_stale_sessions(registry)

        retrieved = registry.get("permission")
        assert retrieved.status == "running"


# ---------------------------------------------------------------------------
# Mission containment: parallel sessions are the THIRD spawn path.
#
# The first cut of mission containment wired run_claude_task and
# _run_skill_mission and missed this one, which
# was then confirmed live on a fleet host: a parallel /implement logged
# "[parallel] Spawned bbf2bd38511f" while `systemctl list-units 'koan-mission-*'`
# listed nothing, and its Gradle daemon sat at pid=97020 ppid=1 rss=822MB in
# koan's own SSH login scope (session-68.scope) — already out of the mission's
# process group, so os.killpg could never have reached it.
# ---------------------------------------------------------------------------

class _ScopeSpawnHarness:
    """Spawn a session with the worktree/provider/CLI boundaries stubbed out."""

    @staticmethod
    def spawn(registry, tmp_path, monkeypatch, session_id="scopesess1234",
              cmd=None, **spawn_kwargs):
        # Keep the scope registry (and `make stop`'s view of it) inside the
        # test's own tree rather than the worker's ambient KOAN_ROOT.
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        wt = MagicMock()
        wt.session_id = session_id
        wt.path = str(tmp_path / "worktree")
        wt.branch = f"koan/session-{session_id}"

        captured = {}

        def fake_popen(argv, provider=None, **kwargs):
            captured["argv"] = list(argv)
            captured["kwargs"] = dict(kwargs)
            proc = MagicMock()
            proc.pid = 4321
            proc.poll.return_value = None
            return proc, MagicMock()

        with patch("app.session_manager.create_worktree", return_value=wt), \
             patch("app.session_manager.inject_worktree_claude_md"), \
             patch("app.mission_runner.build_mission_command",
                   return_value=(cmd or ["echo", "hi"], [])), \
             patch("app.cli_exec.popen_cli", side_effect=fake_popen):
            session = spawn_session(
                mission_text="test",
                project_name="p",
                project_path=str(tmp_path),
                instance_dir=registry.instance_dir,
                registry=registry,
                **spawn_kwargs,
            )
        return session, captured


class TestSpawnSessionMissionScope:
    """spawn_session must go through mission_scope, not bare popen_cli."""

    def test_cli_is_started_through_launch_scoped(self, registry, tmp_path, monkeypatch):
        from app.mission_scope import ScopedProcess

        session, captured = _ScopeSpawnHarness.spawn(registry, tmp_path, monkeypatch)

        # The ScopedProcess is what proves the route: only launch_scoped builds
        # one, and only it passes `launcher` down to popen_cli so the wrapping
        # lands after the prompt rewrite.
        assert isinstance(session._scoped, ScopedProcess)
        assert session._scoped.proc is session._proc
        assert "launcher" in captured["kwargs"]
        session._cleanup()

    def test_start_new_session_still_set_exactly_once(self, registry, tmp_path, monkeypatch):
        """launch_scoped owns the flag now — it must not be passed twice."""
        session, captured = _ScopeSpawnHarness.spawn(registry, tmp_path, monkeypatch)

        assert captured["kwargs"]["start_new_session"] is True
        session._cleanup()

    def test_the_mission_argv_is_never_rewritten(self, registry, tmp_path, monkeypatch):
        session, captured = _ScopeSpawnHarness.spawn(
            registry, tmp_path, monkeypatch, cmd=["echo", "contained"],
        )

        assert captured["argv"] == ["echo", "contained"]
        session._cleanup()

    def test_the_scope_is_registered_under_koan_root(self, registry, tmp_path, monkeypatch):
        """`make stop` reaches a live parallel session through this record."""
        from app.signals import MISSION_SCOPES_DIR

        session, _ = _ScopeSpawnHarness.spawn(registry, tmp_path, monkeypatch)

        entries = list((tmp_path / MISSION_SCOPES_DIR).iterdir())
        assert len(entries) == 1
        record = json.loads(entries[0].read_text())
        assert record["pid"] == 4321
        session._cleanup()

    def test_disabled_reproduces_the_unscoped_spawn(self, registry, tmp_path, monkeypatch):
        """mission_limits.enabled: false must behave exactly as before mission scopes."""
        from app import mission_scope

        with patch("app.config.get_mission_limits_config",
                   return_value={"enabled": False}), \
             patch.object(mission_scope, "_probe_systemd_run") as probe:
            session, captured = _ScopeSpawnHarness.spawn(registry, tmp_path, monkeypatch)

        probe.assert_not_called()
        assert session._scoped.mode == "session"
        assert session._scoped.unit == ""
        assert captured["kwargs"]["launcher"] == []
        assert captured["kwargs"]["start_new_session"] is True
        session._cleanup()

    def test_fallback_when_systemd_run_is_absent_still_spawns(
        self, registry, tmp_path, monkeypatch,
    ):
        """The conftest fixture reports no usable systemd-run — the macOS case."""
        session, captured = _ScopeSpawnHarness.spawn(registry, tmp_path, monkeypatch)

        assert session._scoped.mode == "session"
        assert captured["kwargs"]["launcher"] == []
        assert session.pid == 4321
        session._cleanup()

    def test_spawn_failure_still_closes_the_file_handles(self, registry, tmp_path, monkeypatch):
        """The scope wrapping must not defeat the existing leak guard."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        wt = MagicMock()
        wt.session_id = "scopeboom1234"
        wt.path = str(tmp_path / "worktree")
        wt.branch = "koan/session-scopeboom1234"

        opened = []
        real_open = open

        def tracking_open(path, mode="r", **kwargs):
            f = real_open(path, mode, **kwargs)
            opened.append(f)
            return f

        with patch("app.session_manager.create_worktree", return_value=wt), \
             patch("app.session_manager.inject_worktree_claude_md"), \
             patch("app.mission_runner.build_mission_command", return_value=(["echo"], [])), \
             patch("builtins.open", side_effect=tracking_open), \
             patch("app.cli_exec.popen_cli", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                spawn_session(
                    mission_text="test",
                    project_name="p",
                    project_path=str(tmp_path),
                    instance_dir=registry.instance_dir,
                    registry=registry,
                )

        assert len(opened) == 2
        assert all(f.closed for f in opened), "leaked file handle(s)"


class TestParallelSessionScopeTeardown:
    """Both exit paths must drop the boundary — success included."""

    def test_poll_sessions_tears_the_scope_down_on_success(
        self, registry, sample_session, tmp_path,
    ):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock()
        sample_session._scoped = MagicMock()
        sample_session.stdout_file = ""
        sample_session.stderr_file = ""

        registry.register(sample_session)
        results = poll_sessions([sample_session], registry)

        assert len(results) == 1
        assert results[0].session.status == "done"
        sample_session._scoped.teardown.assert_called_once_with()

    def test_poll_sessions_tears_down_before_the_tmpdir_is_reaped(
        self, registry, sample_session,
    ):
        """Order matters: a leaked daemon must not still be writing into the
        scratch dir _cleanup() is about to remove."""
        order = []
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock(side_effect=lambda: order.append("cleanup"))
        scoped = MagicMock()
        scoped.teardown.side_effect = lambda **kw: order.append("teardown")
        sample_session._scoped = scoped
        sample_session.stdout_file = ""
        sample_session.stderr_file = ""

        registry.register(sample_session)
        poll_sessions([sample_session], registry)

        assert order == ["teardown", "cleanup"]

    def test_a_teardown_failure_still_collects_the_other_sessions(
        self, registry, sample_session, capsys,
    ):
        def _running(session_id):
            s = Session(id=session_id, mission_text="m", project_name="p",
                        project_path="/tmp/fake", worktree_path="/tmp/fake/wt",
                        branch_name="b", status="running")
            s._proc = MagicMock(**{"poll.return_value": 0})
            s._cleanup = MagicMock()
            return s

        first = _running("boom")
        first._scoped = MagicMock()
        first._scoped.teardown.side_effect = RuntimeError("systemctl exploded")
        second = _running("fine")
        second._scoped = MagicMock()
        registry.register(first)
        registry.register(second)

        results = poll_sessions([first, second], registry)

        assert {r.session.id for r in results} == {"boom", "fine"}
        second._scoped.teardown.assert_called_once()
        assert "scope teardown error" in capsys.readouterr().err

    def test_sessions_without_a_scope_are_unaffected(self, registry, sample_session):
        """Registry-restored sessions carry no transient _scoped attribute."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock()
        sample_session.stdout_file = ""
        sample_session.stderr_file = ""

        registry.register(sample_session)
        results = poll_sessions([sample_session], registry)

        assert len(results) == 1

    def test_kill_session_tears_the_scope_down(self, registry, sample_session):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 99999
        mock_proc.wait.return_value = None
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock()
        sample_session._scoped = MagicMock()

        registry.register(sample_session)

        with patch("os.getpgid", return_value=99999), \
             patch("os.killpg"), \
             patch("app.session_manager.remove_worktree"):
            kill_session(sample_session, registry)

        # Our own SIGKILL must not be reported back as a memory-cap hit.
        sample_session._scoped.teardown.assert_called_once_with(
            koan_initiated_kill=True,
        )

    def test_kill_session_keeps_the_process_group_kill(self, registry, sample_session):
        """The scope teardown is an addition to killpg, never a replacement."""
        import signal as _signal

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 33333
        mock_proc.wait.return_value = None
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock()
        sample_session._scoped = MagicMock()

        registry.register(sample_session)

        with patch("os.getpgid", return_value=33333), \
             patch("os.killpg") as mock_killpg, \
             patch("app.session_manager.remove_worktree"):
            kill_session(sample_session, registry)

        assert mock_killpg.call_args_list[0].args == (33333, _signal.SIGTERM)
        sample_session._scoped.teardown.assert_called_once()

    def test_kill_session_survives_a_teardown_failure(
        self, registry, sample_session, capsys,
    ):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        sample_session._proc = mock_proc
        sample_session._cleanup = MagicMock()
        sample_session._scoped = MagicMock()
        sample_session._scoped.teardown.side_effect = RuntimeError("no manager")

        registry.register(sample_session)

        with patch("app.session_manager.remove_worktree"):
            kill_session(sample_session, registry)

        assert sample_session.status == "failed"
        assert registry.get(sample_session.id).status == "failed"
        assert "scope teardown error" in capsys.readouterr().err

    def test_fallback_teardown_kills_the_mission_process_group(
        self, registry, tmp_path, monkeypatch,
    ):
        """With no systemd-run the boundary IS the process group, and killing
        it must still happen on the abort path."""
        from app import mission_scope

        session, _ = _ScopeSpawnHarness.spawn(
            registry, tmp_path, monkeypatch, session_id="fallbackkill",
        )
        assert session._scoped.mode == "session"
        session._scoped._pgid = 4321

        with patch.object(mission_scope, "kill_process_group") as killer, \
             patch.object(mission_scope, "kill_orphaned_process_group") as orphan_killer, \
             patch.object(mission_scope, "_systemctl") as systemctl, \
             patch("app.session_manager.remove_worktree"), \
             patch("os.getpgid", return_value=4321), \
             patch("os.killpg"):
            kill_session(session, registry)

        killer.assert_called_once_with(session._proc)
        orphan_killer.assert_called_once_with(4321)
        systemctl.assert_not_called()
