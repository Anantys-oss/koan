"""Tests for app.mission_hooks — gate + best-effort shell executor."""

from unittest.mock import patch

import pytest

import app.mission_hooks as mh


@pytest.fixture(autouse=True)
def _reset_skip_flag():
    """The one-shot skip diagnostic uses a module global; reset per test."""
    mh._skip_logged = False
    yield
    mh._skip_logged = False


def _write_config(repo, text):
    koan = repo / ".koan"
    koan.mkdir(exist_ok=True)
    (koan / "config.yaml").write_text(text)


# --- hooks_enabled resolution ------------------------------------------------


class TestHooksEnabled:
    def test_global_disabled_by_default(self):
        with patch("app.projects_config.get_project_mission_hooks", return_value=None), \
             patch("app.config.is_mission_hooks_enabled", return_value=False):
            assert mh.hooks_enabled("proj") is False

    def test_global_enabled(self):
        with patch("app.projects_config.get_project_mission_hooks", return_value=None), \
             patch("app.config.is_mission_hooks_enabled", return_value=True):
            assert mh.hooks_enabled("proj") is True

    def test_project_override_true_beats_global_false(self):
        with patch("app.projects_config.get_project_mission_hooks", return_value=True), \
             patch("app.config.is_mission_hooks_enabled", return_value=False):
            assert mh.hooks_enabled("proj") is True

    def test_project_override_false_beats_global_true(self):
        with patch("app.projects_config.get_project_mission_hooks", return_value=False), \
             patch("app.config.is_mission_hooks_enabled", return_value=True):
            assert mh.hooks_enabled("proj") is False

    def test_config_error_is_failsafe_false(self):
        with patch(
            "app.projects_config.get_project_mission_hooks",
            side_effect=RuntimeError("boom"),
        ):
            assert mh.hooks_enabled("proj") is False


# --- no-op when enabled but no commands configured ---------------------------


def test_no_commands_runs_nothing(tmp_path):
    with patch.object(mh, "hooks_enabled", return_value=True), \
         patch.object(mh, "_run_commands") as run:
        mh.run_pre_hooks(str(tmp_path), "proj", "review")
    run.assert_not_called()


# --- US3: operator safety gate (default off) ---------------------------------


class TestGateOff:
    def _disabled(self):
        return (
            patch("app.projects_config.get_project_mission_hooks", return_value=None),
            patch("app.config.is_mission_hooks_enabled", return_value=False),
        )

    def test_gate_off_runs_no_subprocess(self, tmp_path):
        _write_config(tmp_path, "review:\n  pre_hooks:\n    - 'touch nope.flag'\n")
        p1, p2 = self._disabled()
        with p1, p2, patch.object(mh.subprocess, "run") as run:
            mh.run_pre_hooks(str(tmp_path), "proj", "review")
            mh.run_post_hooks(str(tmp_path), "proj", "review", success=True)
        run.assert_not_called()
        assert not (tmp_path / "nope.flag").exists()

    def test_gate_off_logs_skip_once(self, tmp_path):
        _write_config(tmp_path, "review:\n  pre_hooks:\n    - 'echo hi'\n")
        p1, p2 = self._disabled()
        with p1, p2, patch.object(mh, "_log") as log:
            mh.run_pre_hooks(str(tmp_path), "proj", "review")
            mh.run_pre_hooks(str(tmp_path), "proj", "review")
        skips = [c for c in log.call_args_list if "skipped (not enabled)" in str(c)]
        assert len(skips) == 1

    def test_project_override_false_gates_off(self, tmp_path):
        _write_config(tmp_path, "review:\n  pre_hooks:\n    - 'touch nope.flag'\n")
        with patch("app.projects_config.get_project_mission_hooks", return_value=False), \
             patch("app.config.is_mission_hooks_enabled", return_value=True), \
             patch.object(mh.subprocess, "run") as run:
            mh.run_pre_hooks(str(tmp_path), "proj", "review")
        run.assert_not_called()
