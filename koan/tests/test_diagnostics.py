"""Tests for koan/diagnostics/ — diagnostic check runner and check modules."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from diagnostics import CheckResult, FixResult, discover_checks, fix_all, run_all


# ---------------------------------------------------------------------------
# Runner framework
# ---------------------------------------------------------------------------

class TestCheckResult:
    def test_basic(self):
        r = CheckResult(name="test", severity="ok", message="all good")
        assert r.name == "test"
        assert r.severity == "ok"
        assert r.message == "all good"
        assert r.hint == ""

    def test_with_hint(self):
        r = CheckResult(name="test", severity="error", message="bad", hint="fix it")
        assert r.hint == "fix it"


class TestDiscoverChecks:
    def test_finds_check_modules(self):
        modules = discover_checks()
        assert isinstance(modules, list)
        assert "config_check" in modules
        assert "environment_check" in modules
        assert "instance_check" in modules
        assert "process_check" in modules
        assert "project_check" in modules
        assert "connectivity_check" in modules

    def test_sorted(self):
        modules = discover_checks()
        assert modules == sorted(modules)


class TestRunAll:
    def test_returns_results(self, tmp_path):
        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)

        with patch("diagnostics.discover_checks", return_value=["config_check"]), \
             patch("diagnostics.importlib") as mock_importlib:
            mock_module = MagicMock()
            mock_module.run.return_value = [
                CheckResult("test", "ok", "good")
            ]
            mock_importlib.import_module.return_value = mock_module

            results = run_all(str(tmp_path), instance_dir)
            assert len(results) == 1
            assert results[0][0] == "config_check"
            assert results[0][1][0].severity == "ok"

    def test_handles_crashing_module(self, tmp_path):
        with patch("diagnostics.discover_checks", return_value=["bad_check"]), \
             patch("diagnostics.importlib") as mock_importlib:
            mock_module = MagicMock()
            mock_module.run.side_effect = RuntimeError("boom")
            mock_importlib.import_module.return_value = mock_module

            results = run_all(str(tmp_path), str(tmp_path))
            assert len(results) == 1
            assert results[0][1][0].severity == "error"
            assert "crashed" in results[0][1][0].message

    def test_passes_full_flag(self, tmp_path):
        with patch("diagnostics.discover_checks", return_value=["conn"]), \
             patch("diagnostics.importlib") as mock_importlib:
            mock_module = MagicMock()

            def run_fn(koan_root, instance_dir, full=False):
                return [CheckResult("test", "ok", f"full={full}")]
            mock_module.run = run_fn
            mock_importlib.import_module.return_value = mock_module

            results = run_all(str(tmp_path), str(tmp_path), full=True)
            assert "full=True" in results[0][1][0].message


# ---------------------------------------------------------------------------
# config_check
# ---------------------------------------------------------------------------

class TestConfigCheck:
    def test_missing_config_yaml(self, tmp_path):
        from diagnostics.config_check import run

        results = run(str(tmp_path), str(tmp_path))
        names = [r.name for r in results]
        assert "config_yaml" in names
        config_result = [r for r in results if r.name == "config_yaml"][0]
        assert config_result.severity == "error"

    def test_valid_config_yaml(self, tmp_path):
        from diagnostics.config_check import run

        (tmp_path / "config.yaml").write_text("interval_seconds: 60\n")
        with patch("app.utils.load_config", return_value={"interval_seconds": 60}):
            results = run(str(tmp_path), str(tmp_path))
            config_results = [r for r in results if r.name == "config_yaml"]
            assert any(r.severity == "ok" for r in config_results)

    def test_missing_projects_yaml(self, tmp_path):
        from diagnostics.config_check import run

        (tmp_path / "config.yaml").write_text("interval_seconds: 60\n")
        with patch("app.utils.load_config", return_value={}):
            results = run(str(tmp_path), str(tmp_path))
            proj_results = [r for r in results if "projects" in r.name]
            assert any(r.severity == "warn" for r in proj_results)

    def test_missing_soul_md(self, tmp_path):
        from diagnostics.config_check import run

        (tmp_path / "config.yaml").write_text("interval_seconds: 60\n")
        with patch("app.utils.load_config", return_value={}):
            results = run(str(tmp_path), str(tmp_path))
            soul_results = [r for r in results if r.name == "soul_md"]
            assert any(r.severity == "warn" for r in soul_results)

    def test_soul_md_exists(self, tmp_path):
        from diagnostics.config_check import run

        (tmp_path / "config.yaml").write_text("interval_seconds: 60\n")
        (tmp_path / "soul.md").write_text("I am a helpful agent.")
        with patch("app.utils.load_config", return_value={}):
            results = run(str(tmp_path), str(tmp_path))
            soul_results = [r for r in results if r.name == "soul_md"]
            assert any(r.severity == "ok" for r in soul_results)


# ---------------------------------------------------------------------------
# environment_check
# ---------------------------------------------------------------------------

class TestEnvironmentCheck:
    def test_python_version_ok(self, tmp_path):
        from diagnostics.environment_check import run

        results = run(str(tmp_path), str(tmp_path))
        py_results = [r for r in results if r.name == "python_version"]
        assert len(py_results) == 1
        assert py_results[0].severity == "ok"

    def test_binary_git_found(self, tmp_path):
        from diagnostics.environment_check import run

        results = run(str(tmp_path), str(tmp_path))
        git_results = [r for r in results if r.name == "binary_git"]
        assert len(git_results) == 1
        assert git_results[0].severity == "ok"

    def test_binary_missing(self, tmp_path):
        from diagnostics.environment_check import run

        with patch("shutil.which", return_value=None):
            results = run(str(tmp_path), str(tmp_path))
            binary_results = [r for r in results if r.name.startswith("binary_")]
            assert all(r.severity in ("error", "warn") for r in binary_results)

    def test_package_check(self, tmp_path):
        from diagnostics.environment_check import run

        results = run(str(tmp_path), str(tmp_path))
        pkg_results = [r for r in results if r.name.startswith("package_")]
        assert len(pkg_results) >= 1


# ---------------------------------------------------------------------------
# instance_check
# ---------------------------------------------------------------------------

class TestInstanceCheck:
    def test_missing_instance_dir(self, tmp_path):
        from diagnostics.instance_check import run

        results = run(str(tmp_path), str(tmp_path / "nonexistent"))
        assert len(results) == 1
        assert results[0].severity == "error"
        assert "not found" in results[0].message

    def test_valid_instance(self, tmp_path):
        from diagnostics.instance_check import run

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        (instance / "memory").mkdir()
        (instance / "journal").mkdir()

        results = run(str(tmp_path), str(instance))
        ok_results = [r for r in results if r.severity == "ok"]
        assert len(ok_results) >= 3

    def test_missions_structural_issues(self, tmp_path):
        from diagnostics.instance_check import run

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        (instance / "memory").mkdir()
        (instance / "journal").mkdir()

        results = run(str(tmp_path), str(instance))
        missions_results = [r for r in results if r.name == "missions_md"]
        assert any(r.severity == "warn" for r in missions_results)

    def test_in_progress_missions_reported(self, tmp_path):
        from diagnostics.instance_check import run

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n- Fix bug\n\n## Done\n"
        )
        (instance / "memory").mkdir()
        (instance / "journal").mkdir()

        results = run(str(tmp_path), str(instance))
        stale = [r for r in results if r.name == "stale_missions"]
        assert len(stale) == 1
        assert stale[0].severity == "warn"
        assert "1 mission" in stale[0].message


# ---------------------------------------------------------------------------
# process_check
# ---------------------------------------------------------------------------

class TestProcessCheck:
    def test_no_processes_running(self, tmp_path):
        from diagnostics.process_check import run

        with patch("app.pid_manager.check_pidfile", return_value=None), \
             patch("app.pause_manager.is_paused", return_value=False), \
             patch("app.health_check.check_heartbeat", return_value=True):
            results = run(str(tmp_path), str(tmp_path))
            proc_results = [r for r in results if r.name.startswith("process_")]
            assert all(r.severity == "warn" for r in proc_results)

    def test_processes_running(self, tmp_path):
        from diagnostics.process_check import run

        with patch("app.pid_manager.check_pidfile", return_value=12345), \
             patch("app.pause_manager.is_paused", return_value=False), \
             patch("app.health_check.check_heartbeat", return_value=True):
            results = run(str(tmp_path), str(tmp_path))
            proc_results = [r for r in results if r.name.startswith("process_")]
            assert all(r.severity == "ok" for r in proc_results)

    def test_stale_pid_file(self, tmp_path):
        from diagnostics.process_check import run

        (tmp_path / ".koan-pid-run").write_text("99999")

        with patch("app.pid_manager.check_pidfile", return_value=None), \
             patch("app.pause_manager.is_paused", return_value=False), \
             patch("app.health_check.check_heartbeat", return_value=True):
            results = run(str(tmp_path), str(tmp_path))
            run_result = [r for r in results if r.name == "process_run"][0]
            assert run_result.severity == "warn"
            assert "Stale" in run_result.message

    def test_paused_state(self, tmp_path):
        from diagnostics.process_check import run

        mock_state = SimpleNamespace(reason="manual")
        with patch("app.pid_manager.check_pidfile", return_value=None), \
             patch("app.pause_manager.is_paused", return_value=True), \
             patch("app.pause_manager.get_pause_state", return_value=mock_state), \
             patch("app.health_check.check_heartbeat", return_value=True):
            results = run(str(tmp_path), str(tmp_path))
            pause_result = [r for r in results if r.name == "pause_state"][0]
            assert pause_result.severity == "warn"
            assert "paused" in pause_result.message

    def test_disk_space(self, tmp_path):
        from diagnostics.process_check import run

        with patch("app.pid_manager.check_pidfile", return_value=None), \
             patch("app.pause_manager.is_paused", return_value=False), \
             patch("app.health_check.check_heartbeat", return_value=True):
            results = run(str(tmp_path), str(tmp_path))
            disk_result = [r for r in results if r.name == "disk_space"]
            assert len(disk_result) == 1


# ---------------------------------------------------------------------------
# project_check
# ---------------------------------------------------------------------------

class TestProjectCheck:
    def test_no_projects_yaml(self, tmp_path):
        from diagnostics.project_check import run

        with patch("app.projects_config.load_projects_config", return_value=None):
            results = run(str(tmp_path), str(tmp_path))
            assert len(results) == 1
            assert results[0].severity == "warn"

    def test_project_path_missing(self, tmp_path):
        from diagnostics.project_check import run

        config = {"projects": {"myproj": {"path": str(tmp_path / "nonexistent")}}}
        with patch("app.projects_config.load_projects_config", return_value=config), \
             patch("app.projects_config.get_projects_from_config",
                   return_value=[("myproj", str(tmp_path / "nonexistent"))]):
            results = run(str(tmp_path), str(tmp_path))
            assert any(r.severity == "error" and "missing" in r.message for r in results)

    def test_project_not_git_repo(self, tmp_path):
        from diagnostics.project_check import run

        proj_dir = tmp_path / "myproj"
        proj_dir.mkdir()

        config = {"projects": {"myproj": {"path": str(proj_dir)}}}
        with patch("app.projects_config.load_projects_config", return_value=config), \
             patch("app.projects_config.get_projects_from_config",
                   return_value=[("myproj", str(proj_dir))]):
            results = run(str(tmp_path), str(tmp_path))
            assert any(r.severity == "error" and "not a git repo" in r.message for r in results)

    def test_project_valid(self, tmp_path):
        from diagnostics.project_check import run

        proj_dir = tmp_path / "myproj"
        proj_dir.mkdir()
        (proj_dir / ".git").mkdir()

        config = {"projects": {"myproj": {"path": str(proj_dir)}}}
        with patch("app.projects_config.load_projects_config", return_value=config), \
             patch("app.projects_config.get_projects_from_config",
                   return_value=[("myproj", str(proj_dir))]), \
             patch("subprocess.run", return_value=SimpleNamespace(stdout="", returncode=0)):
            results = run(str(tmp_path), str(tmp_path))
            proj_results = [r for r in results if r.name == "project_myproj"]
            assert any(r.severity == "ok" for r in proj_results)

    def test_full_flag_checks_remote(self, tmp_path):
        from diagnostics.project_check import run

        proj_dir = tmp_path / "myproj"
        proj_dir.mkdir()
        (proj_dir / ".git").mkdir()

        config = {"projects": {"myproj": {"path": str(proj_dir)}}}
        with patch("app.projects_config.load_projects_config", return_value=config), \
             patch("app.projects_config.get_projects_from_config",
                   return_value=[("myproj", str(proj_dir))]), \
             patch("subprocess.run", return_value=SimpleNamespace(stdout="", returncode=0)):
            results = run(str(tmp_path), str(tmp_path), full=True)
            remote_results = [r for r in results if "remote" in r.name]
            assert len(remote_results) == 1


# ---------------------------------------------------------------------------
# connectivity_check
# ---------------------------------------------------------------------------

class TestConnectivityCheck:
    def test_skips_when_not_full(self, tmp_path):
        from diagnostics.connectivity_check import run

        results = run(str(tmp_path), str(tmp_path), full=False)
        assert results == []

    def test_telegram_no_token(self, tmp_path, monkeypatch):
        from diagnostics.connectivity_check import run

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        results = run(str(tmp_path), str(tmp_path), full=True)
        tg_results = [r for r in results if r.name == "telegram_api"]
        assert len(tg_results) == 1
        assert tg_results[0].severity == "warn"

    def test_github_cli_authenticated(self, tmp_path, monkeypatch):
        from diagnostics.connectivity_check import run

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stderr="")):
            results = run(str(tmp_path), str(tmp_path), full=True)
            gh_results = [r for r in results if r.name == "github_cli"]
            assert any(r.severity == "ok" for r in gh_results)

    def test_github_cli_not_authenticated(self, tmp_path, monkeypatch):
        from diagnostics.connectivity_check import run

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with patch("subprocess.run", return_value=SimpleNamespace(
            returncode=1, stderr="not logged in"
        )):
            results = run(str(tmp_path), str(tmp_path), full=True)
            gh_results = [r for r in results if r.name == "github_cli"]
            assert any(r.severity == "warn" for r in gh_results)


# ---------------------------------------------------------------------------
# Doctor skill handler
# ---------------------------------------------------------------------------

class TestDoctorHandler:
    def _make_ctx(self, tmp_path, args=""):
        return SimpleNamespace(
            koan_root=tmp_path,
            instance_dir=tmp_path,
            args=args,
            send_message=MagicMock(),
        )

    def test_basic_output(self, tmp_path):
        from skills.core.doctor.handler import handle

        ctx = self._make_ctx(tmp_path)
        with patch("diagnostics.run_all", return_value=[
            ("config_check", [
                CheckResult("config_yaml", "ok", "config.yaml is valid"),
            ]),
            ("environment_check", [
                CheckResult("python_version", "ok", "Python 3.12"),
            ]),
        ]):
            result = handle(ctx)
            assert "Doctor" in result
            assert "2 checks" in result
            assert "2 passed" in result
            assert "✅" in result

    def test_warnings_and_errors(self, tmp_path):
        from skills.core.doctor.handler import handle

        ctx = self._make_ctx(tmp_path)
        with patch("diagnostics.run_all", return_value=[
            ("config_check", [
                CheckResult("config_yaml", "error", "not found", "create it"),
                CheckResult("soul_md", "warn", "missing", "add it"),
            ]),
        ]):
            result = handle(ctx)
            assert "1 error" in result
            assert "1 warning" in result
            assert "❌" in result
            assert "⚠️" in result
            assert "↳" in result

    def test_full_flag_passed(self, tmp_path):
        from skills.core.doctor.handler import handle

        ctx = self._make_ctx(tmp_path, args="--full")
        with patch("diagnostics.run_all") as mock_run:
            mock_run.return_value = []
            handle(ctx)
            mock_run.assert_called_once_with(
                str(tmp_path), str(tmp_path), full=True
            )

    def test_no_full_hint_shown(self, tmp_path):
        from skills.core.doctor.handler import handle

        ctx = self._make_ctx(tmp_path)
        with patch("diagnostics.run_all", return_value=[]):
            result = handle(ctx)
            assert "--full" in result

    def test_long_output_splits(self, tmp_path):
        from skills.core.doctor.handler import handle

        ctx = self._make_ctx(tmp_path)
        many_checks = [
            CheckResult(f"check_{i}", "warn", f"Warning message number {i} " * 10, "Fix it")
            for i in range(50)
        ]
        with patch("diagnostics.run_all", return_value=[
            ("config_check", many_checks),
        ]):
            result = handle(ctx)
            assert isinstance(result, str)

    def test_fix_flag_triggers_fix_all(self, tmp_path):
        from skills.core.doctor.handler import handle

        ctx = self._make_ctx(tmp_path, args="--fix")
        with patch("diagnostics.run_all", return_value=[]), \
             patch("diagnostics.fix_all") as mock_fix:
            mock_fix.return_value = [
                ("process_check", [
                    FixResult("process_run", True, "Removed stale PID file"),
                ]),
            ]
            result = handle(ctx)
            mock_fix.assert_called_once()
            assert "Repairs" in result
            assert "Removed stale PID" in result

    def test_fixable_hint_shown_when_not_fixing(self, tmp_path):
        from skills.core.doctor.handler import handle

        ctx = self._make_ctx(tmp_path)
        with patch("diagnostics.run_all", return_value=[
            ("process_check", [
                CheckResult("process_run", "warn", "Stale PID", "remove it", fixable=True),
            ]),
        ]):
            result = handle(ctx)
            assert "--fix" in result
            assert "auto-repair 1 issue" in result

    def test_no_fixable_hint_when_all_ok(self, tmp_path):
        from skills.core.doctor.handler import handle

        ctx = self._make_ctx(tmp_path)
        with patch("diagnostics.run_all", return_value=[
            ("config_check", [
                CheckResult("config_yaml", "ok", "all good"),
            ]),
        ]):
            result = handle(ctx)
            assert "--fix" not in result or "--full" in result

    def test_fix_and_full_together(self, tmp_path):
        from skills.core.doctor.handler import handle

        ctx = self._make_ctx(tmp_path, args="--fix --full")
        with patch("diagnostics.run_all") as mock_run, \
             patch("diagnostics.fix_all", return_value=[]):
            mock_run.return_value = []
            handle(ctx)
            mock_run.assert_called_once_with(
                str(tmp_path), str(tmp_path), full=True
            )


# ---------------------------------------------------------------------------
# FixResult
# ---------------------------------------------------------------------------

class TestFixResult:
    def test_basic(self):
        r = FixResult(name="test", success=True, message="fixed it")
        assert r.name == "test"
        assert r.success is True
        assert r.message == "fixed it"

    def test_failure(self):
        r = FixResult(name="test", success=False, message="could not fix")
        assert r.success is False


class TestCheckResultFixable:
    def test_default_not_fixable(self):
        r = CheckResult(name="test", severity="warn", message="issue")
        assert r.fixable is False

    def test_fixable_flag(self):
        r = CheckResult(name="test", severity="warn", message="issue", fixable=True)
        assert r.fixable is True


# ---------------------------------------------------------------------------
# fix_all framework
# ---------------------------------------------------------------------------

class TestFixAll:
    def test_runs_fix_functions(self, tmp_path):
        with patch("diagnostics.discover_checks", return_value=["process_check"]), \
             patch("diagnostics.importlib") as mock_importlib:
            mock_module = MagicMock()
            mock_module.fix.return_value = [
                FixResult("process_run", True, "Removed stale PID"),
            ]
            mock_importlib.import_module.return_value = mock_module

            results = fix_all(str(tmp_path), str(tmp_path))
            assert len(results) == 1
            assert results[0][0] == "process_check"
            assert results[0][1][0].success is True

    def test_skips_modules_without_fix(self, tmp_path):
        with patch("diagnostics.discover_checks", return_value=["config_check"]), \
             patch("diagnostics.importlib") as mock_importlib:
            mock_module = MagicMock(spec=["run"])  # no fix attribute
            mock_importlib.import_module.return_value = mock_module

            results = fix_all(str(tmp_path), str(tmp_path))
            assert len(results) == 0

    def test_handles_crashing_fix(self, tmp_path):
        with patch("diagnostics.discover_checks", return_value=["bad_check"]), \
             patch("diagnostics.importlib") as mock_importlib:
            mock_module = MagicMock()
            mock_module.fix.side_effect = RuntimeError("boom")
            mock_importlib.import_module.return_value = mock_module

            results = fix_all(str(tmp_path), str(tmp_path))
            assert len(results) == 1
            assert results[0][1][0].success is False
            assert "crashed" in results[0][1][0].message

    def test_empty_fix_results_excluded(self, tmp_path):
        with patch("diagnostics.discover_checks", return_value=["process_check"]), \
             patch("diagnostics.importlib") as mock_importlib:
            mock_module = MagicMock()
            mock_module.fix.return_value = []
            mock_importlib.import_module.return_value = mock_module

            results = fix_all(str(tmp_path), str(tmp_path))
            assert len(results) == 0


# ---------------------------------------------------------------------------
# process_check fix
# ---------------------------------------------------------------------------

class TestProcessCheckFix:
    def test_removes_stale_pid_file(self, tmp_path):
        from diagnostics.process_check import fix

        (tmp_path / ".koan-pid-run").write_text("99999")
        with patch("app.pid_manager.check_pidfile", return_value=None):
            results = fix(str(tmp_path), str(tmp_path))
            assert len(results) >= 1
            assert results[0].success is True
            assert "Removed stale PID" in results[0].message
            assert not (tmp_path / ".koan-pid-run").exists()

    def test_skips_live_process(self, tmp_path):
        from diagnostics.process_check import fix

        (tmp_path / ".koan-pid-run").write_text("12345")
        with patch("app.pid_manager.check_pidfile", return_value=12345):
            results = fix(str(tmp_path), str(tmp_path))
            assert len(results) == 0
            assert (tmp_path / ".koan-pid-run").exists()

    def test_no_pid_files_noop(self, tmp_path):
        from diagnostics.process_check import fix

        with patch("app.pid_manager.check_pidfile", return_value=None):
            results = fix(str(tmp_path), str(tmp_path))
            assert len(results) == 0

    def test_multiple_stale_pids(self, tmp_path):
        from diagnostics.process_check import fix

        (tmp_path / ".koan-pid-run").write_text("99999")
        (tmp_path / ".koan-pid-awake").write_text("99998")
        with patch("app.pid_manager.check_pidfile", return_value=None):
            results = fix(str(tmp_path), str(tmp_path))
            assert len(results) == 2
            assert all(r.success for r in results)

    def test_unlink_error_reported(self, tmp_path):
        from diagnostics.process_check import fix

        pid_file = tmp_path / ".koan-pid-run"
        pid_file.write_text("99999")
        with patch("app.pid_manager.check_pidfile", return_value=None), \
             patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            results = fix(str(tmp_path), str(tmp_path))
            assert len(results) >= 1
            assert results[0].success is False
            assert "denied" in results[0].message


# ---------------------------------------------------------------------------
# instance_check fix
# ---------------------------------------------------------------------------

class TestInstanceCheckFix:
    def test_creates_missing_directories(self, tmp_path):
        from diagnostics.instance_check import fix

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )

        results = fix(str(tmp_path), str(instance))
        dir_results = [r for r in results if r.name.endswith("_dir")]
        assert len(dir_results) == 2
        assert all(r.success for r in dir_results)
        assert (instance / "memory").is_dir()
        assert (instance / "journal").is_dir()

    def test_skips_existing_directories(self, tmp_path):
        from diagnostics.instance_check import fix

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "memory").mkdir()
        (instance / "journal").mkdir()
        (instance / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )

        results = fix(str(tmp_path), str(instance))
        dir_results = [r for r in results if r.name.endswith("_dir")]
        assert len(dir_results) == 0

    def test_fixes_missions_structural_issues(self, tmp_path):
        from diagnostics.instance_check import fix

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "memory").mkdir()
        (instance / "journal").mkdir()
        (instance / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## Pending\n- dup task\n\n## In Progress\n\n## Done\n"
        )

        with patch("app.utils.atomic_write") as mock_write:
            results = fix(str(tmp_path), str(instance))
            missions_results = [r for r in results if r.name == "missions_md"]
            assert len(missions_results) == 1
            assert missions_results[0].success is True
            mock_write.assert_called_once()

    def test_recovers_stale_missions(self, tmp_path):
        from diagnostics.instance_check import fix

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "memory").mkdir()
        (instance / "journal").mkdir()
        (instance / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n- Fix bug\n\n## Done\n"
        )

        with patch("app.recover.recover_missions", return_value=(1, [])):
            results = fix(str(tmp_path), str(instance))
            stale_results = [r for r in results if r.name == "stale_missions"]
            assert len(stale_results) == 1
            assert stale_results[0].success is True
            assert "1 moved to Pending" in stale_results[0].message

    def test_no_instance_dir_noop(self, tmp_path):
        from diagnostics.instance_check import fix

        results = fix(str(tmp_path), str(tmp_path / "nonexistent"))
        assert len(results) == 0
