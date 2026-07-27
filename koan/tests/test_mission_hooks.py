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


# --- US1: executor behavior against real shell (gate on) ---------------------


class TestExecutor:
    def test_commands_run_in_order(self, tmp_path):
        out = tmp_path / "order.txt"
        _write_config(
            tmp_path,
            "review:\n  pre_hooks:\n"
            f"    - 'echo one >> {out}'\n"
            f"    - 'echo two >> {out}'\n",
        )
        with patch.object(mh, "hooks_enabled", return_value=True):
            mh.run_pre_hooks(str(tmp_path), "proj", "review")
        assert out.read_text().split() == ["one", "two"]

    def test_nonzero_command_does_not_abort_remaining(self, tmp_path):
        flag = tmp_path / "after.flag"
        _write_config(
            tmp_path,
            "review:\n  pre_hooks:\n"
            "    - 'false'\n"
            f"    - 'touch {flag}'\n",
        )
        with patch.object(mh, "hooks_enabled", return_value=True):
            mh.run_pre_hooks(str(tmp_path), "proj", "review")
        assert flag.exists()  # second command ran despite the first failing

    def test_timeout_is_logged_and_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mh, "MISSION_HOOK_TIMEOUT", 1)
        _write_config(tmp_path, "review:\n  pre_hooks:\n    - 'sleep 5'\n")
        with patch.object(mh, "hooks_enabled", return_value=True), \
             patch.object(mh, "_log") as log:
            mh.run_pre_hooks(str(tmp_path), "proj", "review")  # must not raise
        assert any("timed out" in str(c) for c in log.call_args_list)

    def test_post_hook_sees_status_and_type_env(self, tmp_path):
        out = tmp_path / "env.txt"
        _write_config(
            tmp_path,
            "review:\n  post_hooks:\n"
            f'    - \'echo "$KOAN_MISSION_STATUS $KOAN_MISSION_TYPE" > {out}\'\n',
        )
        with patch.object(mh, "hooks_enabled", return_value=True):
            mh.run_post_hooks(str(tmp_path), "proj", "review", success=False)
        assert out.read_text().strip() == "failure review"

    def test_pre_hook_has_no_status_env(self, tmp_path):
        out = tmp_path / "env.txt"
        _write_config(
            tmp_path,
            "review:\n  pre_hooks:\n"
            f'    - \'echo "[${{KOAN_MISSION_STATUS:-unset}}]" > {out}\'\n',
        )
        with patch.object(mh, "hooks_enabled", return_value=True):
            mh.run_pre_hooks(str(tmp_path), "proj", "review")
        assert out.read_text().strip() == "[unset]"

    def test_runs_in_project_cwd(self, tmp_path):
        out = tmp_path / "cwd.txt"
        _write_config(
            tmp_path, f"review:\n  pre_hooks:\n    - 'pwd > {out}'\n")
        with patch.object(mh, "hooks_enabled", return_value=True):
            mh.run_pre_hooks(str(tmp_path), "proj", "review")
        # realpath to tolerate /tmp -> /private/tmp symlinking on macOS.
        import os
        assert os.path.realpath(out.read_text().strip()) == os.path.realpath(str(tmp_path))


# --- US2: precedence at the execution layer ----------------------------------


class TestPrecedenceExecution:
    def _run(self, tmp_path, config, mtype, phase, success=True):
        out = tmp_path / "ran.txt"
        out.unlink(missing_ok=True)  # isolate each invocation
        # Rewrite each hook command to append its own tag to a shared file, so
        # we can assert exactly which resolved list executed.
        _write_config(tmp_path, config.replace("OUT", str(out)))
        with patch.object(mh, "hooks_enabled", return_value=True):
            if phase == "pre":
                mh.run_pre_hooks(str(tmp_path), "proj", mtype)
            else:
                mh.run_post_hooks(str(tmp_path), "proj", mtype, success)
        return out.read_text().split() if out.exists() else []

    def test_unlisted_type_inherits_default(self, tmp_path):
        cfg = "default:\n  pre_hooks:\n    - 'echo D >> OUT'\n"
        assert self._run(tmp_path, cfg, "plan", "pre") == ["D"]

    def test_type_replaces_default_no_duplication(self, tmp_path):
        cfg = (
            "default:\n  pre_hooks:\n    - 'echo D >> OUT'\n"
            "review:\n  pre_hooks:\n    - 'echo R >> OUT'\n"
        )
        # Only the review list runs — default is NOT also appended.
        assert self._run(tmp_path, cfg, "review", "pre") == ["R"]

    def test_per_phase_independent_precedence(self, tmp_path):
        cfg = (
            "default:\n  pre_hooks:\n    - 'echo DP >> OUT'\n"
            "  post_hooks:\n    - 'echo DPOST >> OUT'\n"
            "review:\n  pre_hooks:\n    - 'echo RP >> OUT'\n"
        )
        # pre overridden by review; post falls back to default.
        assert self._run(tmp_path, cfg, "review", "pre") == ["RP"]
        assert self._run(tmp_path, cfg, "review", "post") == ["DPOST"]
