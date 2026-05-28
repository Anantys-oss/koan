"""Tests for hooks.py — hook registry and discovery."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.hooks import HookRegistry, fire_hook, init_hooks, reset_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the global registry before and after each test."""
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def hooks_dir(tmp_path):
    """Create an empty hooks directory."""
    d = tmp_path / "hooks"
    d.mkdir()
    return d


def _write_hook(hooks_dir: Path, name: str, code: str) -> Path:
    """Write a hook module to the hooks directory."""
    path = hooks_dir / f"{name}.py"
    path.write_text(code)
    return path


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------


class TestHookDiscovery:
    def test_empty_dir(self, hooks_dir):
        registry = HookRegistry(hooks_dir)
        assert not registry.has_hooks("post_mission")

    def test_nonexistent_dir(self, tmp_path):
        registry = HookRegistry(tmp_path / "nonexistent")
        assert not registry.has_hooks("post_mission")

    def test_discovers_valid_hook(self, hooks_dir):
        _write_hook(hooks_dir, "my_hook", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        assert registry.has_hooks("post_mission")

    def test_discovers_multiple_events(self, hooks_dir):
        _write_hook(hooks_dir, "multi", (
            "def on_pre(ctx): pass\n"
            "def on_post(ctx): pass\n"
            "HOOKS = {'pre_mission': on_pre, 'post_mission': on_post}\n"
        ))
        registry = HookRegistry(hooks_dir)
        assert registry.has_hooks("pre_mission")
        assert registry.has_hooks("post_mission")

    def test_discovers_multiple_modules(self, hooks_dir):
        _write_hook(hooks_dir, "hook_a", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        _write_hook(hooks_dir, "hook_b", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        assert registry.has_hooks("post_mission")
        # Both handlers should be registered
        assert len(registry._handlers["post_mission"]) == 2

    def test_skips_underscore_files(self, hooks_dir):
        _write_hook(hooks_dir, "__init__", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        _write_hook(hooks_dir, "_private", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        assert not registry.has_hooks("post_mission")

    def test_skips_non_py_files(self, hooks_dir):
        (hooks_dir / "readme.md").write_text("# hooks")
        (hooks_dir / "data.json").write_text("{}")
        registry = HookRegistry(hooks_dir)
        assert not registry.has_hooks("post_mission")

    def test_skips_module_without_hooks_dict(self, hooks_dir):
        _write_hook(hooks_dir, "no_hooks", "x = 42\n")
        registry = HookRegistry(hooks_dir)
        assert not registry.has_hooks("post_mission")

    def test_skips_module_with_non_dict_hooks(self, hooks_dir):
        _write_hook(hooks_dir, "bad_hooks", "HOOKS = 'not a dict'\n")
        registry = HookRegistry(hooks_dir)
        assert not registry.has_hooks("post_mission")

    def test_skips_non_callable_values(self, hooks_dir):
        _write_hook(hooks_dir, "bad_vals", (
            "HOOKS = {'post_mission': 'not callable'}\n"
        ))
        registry = HookRegistry(hooks_dir)
        assert not registry.has_hooks("post_mission")

    def test_syntax_error_skipped(self, hooks_dir, capsys):
        _write_hook(hooks_dir, "broken", "def f(\n")
        registry = HookRegistry(hooks_dir)
        assert not registry.has_hooks("post_mission")
        captured = capsys.readouterr()
        assert "[hooks] Failed to load broken.py" in captured.err

    def test_import_error_skipped(self, hooks_dir, capsys):
        _write_hook(hooks_dir, "bad_import", "import nonexistent_module_xyz\n")
        registry = HookRegistry(hooks_dir)
        assert not registry.has_hooks("post_mission")
        captured = capsys.readouterr()
        assert "[hooks] Failed to load bad_import.py" in captured.err

    def test_valid_hooks_loaded_despite_broken_module(self, hooks_dir, capsys):
        _write_hook(hooks_dir, "a_broken", "def f(\n")
        _write_hook(hooks_dir, "b_valid", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        assert registry.has_hooks("post_mission")
        captured = capsys.readouterr()
        assert "[hooks] Failed to load a_broken.py" in captured.err


# ---------------------------------------------------------------------------
# Fire tests
# ---------------------------------------------------------------------------


class TestHookFire:
    def test_fire_no_hooks(self, hooks_dir):
        registry = HookRegistry(hooks_dir)
        # Should not raise
        registry.fire("post_mission", project_name="test")

    def test_fire_calls_handler(self, hooks_dir):
        _write_hook(hooks_dir, "tracker", (
            "calls = []\n"
            "def handler(ctx): calls.append(ctx)\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        registry.fire("post_mission", project_name="myproj", exit_code=0)
        # Verify the handler was called by importing the module's state
        mod_name = "koan_hook_tracker"
        assert mod_name in sys.modules
        assert len(sys.modules[mod_name].calls) == 1
        assert sys.modules[mod_name].calls[0]["project_name"] == "myproj"
        assert sys.modules[mod_name].calls[0]["exit_code"] == 0

    def test_fire_multiple_handlers(self, hooks_dir):
        _write_hook(hooks_dir, "hook_a", (
            "count = 0\n"
            "def handler(ctx):\n"
            "    global count\n"
            "    count += 1\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        _write_hook(hooks_dir, "hook_b", (
            "count = 0\n"
            "def handler(ctx):\n"
            "    global count\n"
            "    count += 1\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        registry.fire("post_mission")
        assert sys.modules["koan_hook_hook_a"].count == 1
        assert sys.modules["koan_hook_hook_b"].count == 1

    def test_fire_handler_error_logged(self, hooks_dir, capsys):
        _write_hook(hooks_dir, "crasher", (
            "def handler(ctx): raise ValueError('boom')\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        # Should not raise
        failures = registry.fire("post_mission")
        captured = capsys.readouterr()
        assert "[hooks] Error in post_mission handler koan_hook_crasher.handler" in captured.err
        assert "boom" in captured.err
        assert failures == {"koan_hook_crasher.handler": "boom"}

    def test_fire_error_doesnt_block_other_hooks(self, hooks_dir, capsys):
        _write_hook(hooks_dir, "hook_a_crash", (
            "def handler(ctx): raise RuntimeError('fail')\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        _write_hook(hooks_dir, "hook_b_ok", (
            "called = False\n"
            "def handler(ctx):\n"
            "    global called\n"
            "    called = True\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        failures = registry.fire("post_mission")
        assert sys.modules["koan_hook_hook_b_ok"].called is True
        captured = capsys.readouterr()
        assert "fail" in captured.err
        assert failures == {"koan_hook_hook_a_crash.handler": "fail"}

    def test_fire_returns_empty_dict_on_success(self, hooks_dir):
        _write_hook(hooks_dir, "ok_hook", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        failures = registry.fire("post_mission")
        assert failures == {}

    def test_fire_returns_empty_dict_no_handlers(self, hooks_dir):
        registry = HookRegistry(hooks_dir)
        failures = registry.fire("post_mission")
        assert failures == {}

    def test_fire_returns_multiple_failures(self, hooks_dir, capsys):
        _write_hook(hooks_dir, "hook_x", (
            "def explode(ctx): raise TypeError('type err')\n"
            "HOOKS = {'test_event': explode}\n"
        ))
        _write_hook(hooks_dir, "hook_y", (
            "def kaboom(ctx): raise KeyError('key err')\n"
            "HOOKS = {'test_event': kaboom}\n"
        ))
        registry = HookRegistry(hooks_dir)
        failures = registry.fire("test_event")
        assert len(failures) == 2
        assert "koan_hook_hook_x.explode" in failures
        assert "koan_hook_hook_y.kaboom" in failures

    def test_fire_unknown_event(self, hooks_dir):
        _write_hook(hooks_dir, "hook", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        # Should not raise
        registry.fire("unknown_event")

    def test_has_hooks_false_for_unregistered(self, hooks_dir):
        _write_hook(hooks_dir, "hook", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        assert not registry.has_hooks("pre_mission")

    def test_context_passed_as_dict(self, hooks_dir):
        _write_hook(hooks_dir, "ctx_check", (
            "received = None\n"
            "def handler(ctx):\n"
            "    global received\n"
            "    received = ctx\n"
            "HOOKS = {'test_event': handler}\n"
        ))
        registry = HookRegistry(hooks_dir)
        registry.fire("test_event", a=1, b="two")
        mod = sys.modules["koan_hook_ctx_check"]
        assert mod.received == {"a": 1, "b": "two"}


# ---------------------------------------------------------------------------
# Module-level convenience function tests
# ---------------------------------------------------------------------------


class TestFireHookConvenience:
    def test_fire_hook_noop_without_init(self):
        # Should not raise when registry is None
        result = fire_hook("post_mission", project_name="test")
        assert result == {}

    def test_fire_hook_after_init(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "tracker", (
            "calls = []\n"
            "def handler(ctx): calls.append(ctx)\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        # init_hooks expects instance_dir, hooks_dir = instance_dir/hooks
        init_hooks(str(tmp_path))
        result = fire_hook("post_mission", project_name="proj")
        mod = sys.modules["koan_hook_tracker"]
        assert len(mod.calls) == 1
        assert result == {}


class TestInitHooks:
    def test_creates_hooks_dir_if_missing(self, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        hooks_dir = instance / "hooks"
        assert not hooks_dir.exists()
        init_hooks(str(instance))
        assert hooks_dir.is_dir()

    def test_reinitializes_on_second_call(self, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        init_hooks(str(instance))
        from app.hooks import get_registry
        r1 = get_registry()
        init_hooks(str(instance))
        r2 = get_registry()
        assert r1 is not r2


# ---------------------------------------------------------------------------
# Integration: post-mission hook fires from run_post_mission
# ---------------------------------------------------------------------------


class TestPostMissionHookIntegration:
    """Verify fire_hook('post_mission', ...) is called from run_post_mission."""

    @patch("app.mission_runner.update_usage", return_value=False)
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner._record_session_outcome")
    @patch("app.mission_runner._read_pending_content", return_value="")
    @patch("app.mission_runner._read_stdout_summary", return_value="")
    def test_post_mission_hook_called(
        self, mock_summary, mock_pending, mock_outcome,
        mock_merge, mock_reflect, mock_archive,
        mock_quota, mock_usage, tmp_path,
    ):
        from app.hooks import init_hooks, get_registry
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "tracker", (
            "calls = []\n"
            "def handler(ctx): calls.append(ctx)\n"
            "HOOKS = {'post_mission': handler}\n"
        ))

        # Patch fire_hook to use our custom registry
        init_hooks(str(tmp_path))

        from app.mission_runner import run_post_mission
        result = run_post_mission(
            instance_dir=str(tmp_path),
            project_name="testproj",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=0,
            stdout_file="/dev/null",
            stderr_file="/dev/null",
            mission_title="Test mission",
            start_time=0,
        )

        mod = sys.modules.get("koan_hook_tracker")
        assert mod is not None
        assert len(mod.calls) == 1
        ctx = mod.calls[0]
        assert ctx["project_name"] == "testproj"
        assert ctx["mission_title"] == "Test mission"
        assert ctx["exit_code"] == 0
        assert "result" in ctx
        assert "duration_minutes" in ctx

    @patch("app.mission_runner.update_usage", return_value=False)
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.mission_runner._record_session_outcome")
    @patch("app.mission_runner._read_pending_content", return_value="")
    @patch("app.mission_runner._read_stdout_summary", return_value="")
    def test_post_mission_hook_fires_on_failure(
        self, mock_summary, mock_pending, mock_outcome,
        mock_archive, mock_quota, mock_usage, tmp_path,
    ):
        from app.hooks import init_hooks
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "fail_tracker", (
            "calls = []\n"
            "def handler(ctx): calls.append(ctx)\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        init_hooks(str(tmp_path))

        from app.mission_runner import run_post_mission
        run_post_mission(
            instance_dir=str(tmp_path),
            project_name="testproj",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=1,
            stdout_file="/dev/null",
            stderr_file="/dev/null",
            start_time=0,
        )

        mod = sys.modules.get("koan_hook_fail_tracker")
        assert mod is not None
        assert len(mod.calls) == 1
        assert mod.calls[0]["exit_code"] == 1

    @patch("app.mission_runner.update_usage", return_value=False)
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner._record_session_outcome")
    @patch("app.mission_runner._read_pending_content", return_value="")
    @patch("app.mission_runner._read_stdout_summary", return_value="")
    def test_pipeline_records_fail_on_hook_error(
        self, mock_summary, mock_pending, mock_outcome,
        mock_merge, mock_reflect, mock_archive,
        mock_quota, mock_usage, tmp_path,
    ):
        """When a post_mission hook raises, pipeline tracker records 'fail'."""
        from app.hooks import init_hooks
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "crasher", (
            "def handler(ctx): raise RuntimeError('hook exploded')\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        init_hooks(str(tmp_path))

        from app.mission_runner import run_post_mission
        result = run_post_mission(
            instance_dir=str(tmp_path),
            project_name="testproj",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=0,
            stdout_file="/dev/null",
            stderr_file="/dev/null",
            mission_title="Test mission",
            start_time=0,
        )

        steps = result.get("pipeline_steps", {})
        assert "hooks" in steps
        assert steps["hooks"]["status"] == "fail"
        assert "handler" in steps["hooks"]["detail"]

    @patch("app.mission_runner.update_usage", return_value=False)
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner._record_session_outcome")
    @patch("app.mission_runner._read_pending_content", return_value="")
    @patch("app.mission_runner._read_stdout_summary", return_value="")
    def test_pipeline_records_success_when_hooks_pass(
        self, mock_summary, mock_pending, mock_outcome,
        mock_merge, mock_reflect, mock_archive,
        mock_quota, mock_usage, tmp_path,
    ):
        """When all hooks succeed, pipeline tracker records 'success'."""
        from app.hooks import init_hooks
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "ok_hook", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        init_hooks(str(tmp_path))

        from app.mission_runner import run_post_mission
        result = run_post_mission(
            instance_dir=str(tmp_path),
            project_name="testproj",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=0,
            stdout_file="/dev/null",
            stderr_file="/dev/null",
            mission_title="Test mission",
            start_time=0,
        )

        steps = result.get("pipeline_steps", {})
        assert "hooks" in steps
        assert steps["hooks"]["status"] == "success"


# ---------------------------------------------------------------------------
# Integration: session and pre-mission hook wiring
# ---------------------------------------------------------------------------


class TestSessionHookWiring:
    """Verify session_start hook is wired in startup_manager."""

    def test_session_start_hook_in_source(self):
        """Verify the session_start fire_hook call exists in startup_manager source."""
        import inspect
        from app import startup_manager
        source = inspect.getsource(startup_manager)
        assert 'fire_hook, init_hooks' in source
        assert '"session_start"' in source


class TestPreMissionHookWiring:
    """Verify pre_mission hook fires before Claude execution in _run_iteration."""

    def test_fire_hook_called_with_pre_mission(self):
        """Verify the pre_mission fire_hook call exists in run.py source."""
        # Static verification: ensure fire_hook("pre_mission", ...) is in the source
        import inspect
        from app import run
        source = inspect.getsource(run)
        assert 'fire_hook(\n            "pre_mission"' in source or \
               'fire_hook("pre_mission"' in source


class TestSessionEndHookWiring:
    """Verify session_end hook fires in main_loop finally block."""

    def test_fire_hook_called_with_session_end(self):
        """Verify the session_end fire_hook call exists in run.py source."""
        import inspect
        from app import run
        source = inspect.getsource(run)
        assert 'fire_hook("session_end"' in source


# ---------------------------------------------------------------------------
# Automation rule execution tests
# ---------------------------------------------------------------------------


import yaml as _yaml


def _write_rules(instance_dir, rules_data):
    """Write automation_rules.yaml into instance_dir."""
    path = Path(instance_dir) / "automation_rules.yaml"
    path.write_text(_yaml.dump(rules_data))


class TestAutomationRuleExecution:
    """Tests for automation rule execution in HookRegistry.fire()."""

    def _make_registry(self, tmp_path):
        """Create a HookRegistry with empty hooks dir and given instance_dir."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (tmp_path / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        return HookRegistry(hooks_dir, instance_dir=str(tmp_path))

    def test_read_automation_rules_empty_when_absent(self, tmp_path):
        from app.hooks import read_automation_rules
        rules = read_automation_rules(str(tmp_path))
        assert rules == []

    def test_read_automation_rules_returns_rules_when_present(self, tmp_path):
        from app.hooks import read_automation_rules
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "post_mission", "action": "notify", "enabled": True, "created": ""},
        ])
        rules = read_automation_rules(str(tmp_path))
        assert len(rules) == 1
        assert rules[0].id == "r1"

    def test_rule_fires_on_matching_event(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "post_mission", "action": "notify",
             "params": {"message": "hello"}, "enabled": True, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        registry.fire("post_mission")
        outbox = (tmp_path / "outbox.md").read_text()
        assert "hello" in outbox

    def test_rule_not_fired_on_non_matching_event(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "pre_mission", "action": "notify",
             "params": {"message": "should_not_appear"}, "enabled": True, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        registry.fire("post_mission")
        outbox_path = tmp_path / "outbox.md"
        assert not outbox_path.exists() or "should_not_appear" not in outbox_path.read_text()

    def test_disabled_rule_is_skipped(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "post_mission", "action": "notify",
             "params": {"message": "disabled"}, "enabled": False, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        registry.fire("post_mission")
        outbox_path = tmp_path / "outbox.md"
        assert not outbox_path.exists() or "disabled" not in outbox_path.read_text()

    def test_loop_guard_skips_after_max_fires(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "post_mission", "action": "notify",
             "params": {"message": "tick"}, "enabled": True, "created": ""},
        ])
        # Set max_fires_per_minute=2 via config
        import yaml
        (tmp_path / "config.yaml").write_text(
            yaml.dump({"automation_rules": {"max_fires_per_minute": 2}})
        )
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        from app.hooks import HookRegistry
        from unittest.mock import patch
        registry = HookRegistry(hooks_dir, instance_dir=str(tmp_path))
        # Patch load_config to return our config
        config = {"automation_rules": {"max_fires_per_minute": 2}}
        with patch("app.hooks.HookRegistry._loop_guard") as mock_guard:
            # First 2 calls: not guarded; 3rd: guarded
            mock_guard.side_effect = [False, False, True]
            registry.fire("post_mission")
            registry.fire("post_mission")
            registry.fire("post_mission")
        # After 2 fires the 3rd should be skipped; outbox should have exactly 2 entries
        outbox_text = (tmp_path / "outbox.md").read_text()
        assert outbox_text.count("tick") == 2

    def test_notify_action_appends_to_outbox(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "post_mission", "action": "notify",
             "params": {"message": "mission done"}, "enabled": True, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        registry.fire("post_mission")
        outbox = (tmp_path / "outbox.md").read_text()
        assert "mission done" in outbox

    def test_create_mission_appends_to_pending(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "post_mission", "action": "create_mission",
             "params": {"text": "Follow-up task"}, "enabled": True, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        registry.fire("post_mission")
        missions = (tmp_path / "missions.md").read_text()
        assert "Follow-up task" in missions

    def test_pause_action_writes_koan_pause(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "pre_mission", "action": "pause",
             "enabled": True, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        # koan-pause lives in parent of instance_dir (KOAN_ROOT)
        pause_file = tmp_path.parent / ".koan-pause"
        assert not pause_file.exists()
        registry.fire("pre_mission")
        assert pause_file.exists()

    def test_resume_action_removes_koan_pause(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "session_start", "action": "resume",
             "enabled": True, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        pause_file = tmp_path.parent / ".koan-pause"
        pause_file.write_text("test\n")
        assert pause_file.exists()
        registry.fire("session_start")
        assert not pause_file.exists()

    def test_resume_action_idempotent_when_not_paused(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "session_start", "action": "resume",
             "enabled": True, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        # Should not raise even if pause file doesn't exist
        registry.fire("session_start")

    def test_auto_merge_skipped_when_project_path_absent(self, tmp_path, capsys):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "post_mission", "action": "auto_merge",
             "enabled": True, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        # No project_path in ctx
        registry.fire("post_mission")
        captured = capsys.readouterr()
        assert "auto_merge action skipped" in captured.err

    def test_auto_merge_fires_when_project_path_present(self, tmp_path):
        _write_rules(str(tmp_path), [
            {"id": "r1", "event": "post_mission", "action": "auto_merge",
             "enabled": True, "created": ""},
        ])
        registry = self._make_registry(tmp_path)
        from unittest.mock import patch
        with patch("app.git_auto_merge.auto_merge_branch") as mock_merge:
            registry.fire(
                "post_mission",
                project_path=str(tmp_path),
                project_name="myproj",
                branch="koan/test-branch",
            )
            mock_merge.assert_called_once_with(
                str(tmp_path), "myproj", str(tmp_path), "koan/test-branch"
            )

    def test_exception_in_rule_does_not_block_subsequent_rules(self, tmp_path):
        _write_rules(str(tmp_path), [
            # First rule: bad action that will raise internally
            {"id": "r_bad", "event": "post_mission", "action": "notify",
             "params": {}, "enabled": True, "created": ""},
            # Second rule: creates a mission — should still run
            {"id": "r_good", "event": "post_mission", "action": "create_mission",
             "params": {"text": "second rule ran"}, "enabled": True, "created": ""},
        ])
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (tmp_path / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        from app.hooks import HookRegistry
        registry = HookRegistry(hooks_dir, instance_dir=str(tmp_path))
        # Patch _execute_rule to raise on the first call but not second
        original = registry._execute_rule
        call_count = [0]
        def patched_execute(rule, ctx):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first rule exploded")
            original(rule, ctx)
        registry._execute_rule = patched_execute
        registry.fire("post_mission")
        missions = (tmp_path / "missions.md").read_text()
        assert "second rule ran" in missions

    def test_no_registry_when_instance_dir_absent(self, tmp_path):
        """Rules are not evaluated when instance_dir is not provided."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        registry = HookRegistry(hooks_dir)  # No instance_dir
        # Should not try to load rules and not raise
        registry.fire("post_mission")


class TestAutomationRuleJournal:
    """Verify _execute_rule writes a [automation_rule]-tagged journal entry."""

    def test_journal_entry_written_on_rule_fire(self, tmp_path):
        from app.hooks import HookRegistry
        from datetime import datetime, timezone
        import re

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (tmp_path / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        _write_rules(str(tmp_path), [
            {"id": "j1", "event": "post_mission", "action": "notify",
             "params": {"message": "journal test"}, "enabled": True, "created": ""},
        ])
        registry = HookRegistry(hooks_dir, instance_dir=str(tmp_path))
        registry.fire("post_mission")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        journal_file = tmp_path / "journal" / today / "automation.md"
        assert journal_file.exists(), f"Expected journal file at {journal_file}"
        content = journal_file.read_text()
        assert "[automation_rule]" in content
        assert "j1" in content
        assert "post_mission" in content
        assert "notify" in content

    def test_journal_dir_created_if_absent(self, tmp_path):
        from app.hooks import HookRegistry
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (tmp_path / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        _write_rules(str(tmp_path), [
            {"id": "j2", "event": "post_mission", "action": "notify",
             "params": {"message": "create dir"}, "enabled": True, "created": ""},
        ])
        registry = HookRegistry(hooks_dir, instance_dir=str(tmp_path))
        # Ensure journal dir doesn't exist yet
        assert not (tmp_path / "journal").exists()
        registry.fire("post_mission")
        assert (tmp_path / "journal").is_dir()
