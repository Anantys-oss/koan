"""Tests for app.mission_executor — skill dispatch, retry, and git-head helpers.

Focus on the lower-mock-surface helpers and the cli_skill translation
branches of _handle_skill_dispatch. The heavy _run_iteration paths are
already exercised by test_run.py.
"""

import contextlib
import os
import subprocess
from unittest.mock import patch

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")


# ---------------------------------------------------------------------------
# _get_git_head
# ---------------------------------------------------------------------------

class TestGetGitHead:

    def test_returns_sha_on_success(self):
        from app.mission_executor import _get_git_head

        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="abc123\n", stderr="",
        )
        with patch("app.mission_executor.subprocess.run", return_value=completed):
            assert _get_git_head("/tmp/proj") == "abc123"

    def test_returns_empty_on_nonzero_returncode(self):
        from app.mission_executor import _get_git_head

        completed = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="fatal\n", stderr="",
        )
        with patch("app.mission_executor.subprocess.run", return_value=completed):
            assert _get_git_head("/tmp/proj") == ""

    def test_returns_empty_on_subprocess_error(self):
        from app.mission_executor import _get_git_head

        with patch("app.mission_executor.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("git", 5)):
            assert _get_git_head("/tmp/proj") == ""

    def test_returns_empty_on_os_error(self):
        from app.mission_executor import _get_git_head

        with patch("app.mission_executor.subprocess.run",
                   side_effect=OSError("no git")):
            assert _get_git_head("/tmp/proj") == ""


# ---------------------------------------------------------------------------
# _maybe_retry_mission
# ---------------------------------------------------------------------------

class TestMaybeRetryMission:

    @pytest.fixture(autouse=True)
    def _reset_state(self):
        import app.run as _run
        _run._last_mission_timed_out = False
        _run._last_mission_aborted = False
        _run._last_mission_stagnated.clear()
        yield
        _run._last_mission_timed_out = False
        _run._last_mission_aborted = False
        _run._last_mission_stagnated.clear()

    def _call(self, **overrides):
        from app.mission_executor import _maybe_retry_mission
        kwargs = dict(
            claude_exit=1,
            stdout_file="/tmp/nonexistent-out",
            stderr_file="/tmp/nonexistent-err",
            cmd=["claude"],
            project_path="/tmp/proj",
            pre_head="head1",
            instance="/tmp/inst",
            project_name="koan",
            run_num=1,
            has_mission=True,
            provider_name="claude",
        )
        kwargs.update(overrides)
        return _maybe_retry_mission(**kwargs)

    def test_skips_on_watchdog_timeout(self):
        import app.run as _run
        _run._last_mission_timed_out = True
        with patch("app.run.log"):
            exit_code, out, err = self._call()
        assert exit_code == 1

    def test_skips_on_user_abort(self):
        import app.run as _run
        _run._last_mission_aborted = True
        with patch("app.run.log"):
            exit_code, _, _ = self._call()
        assert exit_code == 1

    def test_skips_on_stagnation(self):
        import app.run as _run
        _run._last_mission_stagnated.set()
        with patch("app.run.log"):
            exit_code, _, _ = self._call()
        assert exit_code == 1

    def test_skips_non_retryable(self, tmp_path):
        from app.cli_errors import ErrorCategory
        out = tmp_path / "out"
        out.write_text("some output")
        err = tmp_path / "err"
        err.write_text("")
        with patch("app.run.log"), \
             patch("app.cli_errors.classify_cli_error",
                   return_value=ErrorCategory.TERMINAL):
            exit_code, _, _ = self._call(
                stdout_file=str(out), stderr_file=str(err),
            )
        assert exit_code == 1

    def test_skips_autonomous_run(self, tmp_path):
        from app.cli_errors import ErrorCategory
        out = tmp_path / "out"
        out.write_text("transient")
        err = tmp_path / "err"
        err.write_text("")
        with patch("app.run.log"), \
             patch("app.cli_errors.classify_cli_error",
                   return_value=ErrorCategory.RETRYABLE):
            exit_code, _, _ = self._call(
                stdout_file=str(out), stderr_file=str(err),
                has_mission=False,
            )
        assert exit_code == 1

    def test_skips_when_commits_produced(self, tmp_path):
        from app.cli_errors import ErrorCategory
        out = tmp_path / "out"
        out.write_text("transient")
        err = tmp_path / "err"
        err.write_text("")
        with patch("app.run.log"), \
             patch("app.cli_errors.classify_cli_error",
                   return_value=ErrorCategory.RETRYABLE), \
             patch("app.run._get_git_head", return_value="head2"):
            exit_code, _, _ = self._call(
                stdout_file=str(out), stderr_file=str(err),
                pre_head="head1",
            )
        assert exit_code == 1

    def test_retries_on_transient_error(self, tmp_path):
        from app.cli_errors import ErrorCategory
        out = tmp_path / "out"
        out.write_text("transient")
        err = tmp_path / "err"
        err.write_text("")
        with patch("app.run.log"), \
             patch("app.mission_executor.time.sleep"), \
             patch("app.cli_errors.classify_cli_error",
                   return_value=ErrorCategory.RETRYABLE), \
             patch("app.run._get_git_head", return_value="head1"), \
             patch("app.run.run_claude_task", return_value=0) as mock_run:
            exit_code, _, _ = self._call(
                stdout_file=str(out), stderr_file=str(err),
                pre_head="head1",
            )
        assert exit_code == 0
        mock_run.assert_called_once()

    def test_read_failure_falls_back_to_empty(self, tmp_path):
        """Missing stdout/stderr files yield empty text passed to classifier."""
        from app.cli_errors import ErrorCategory
        captured = {}

        def _capture(exit_code, stdout, stderr, provider_name=""):
            captured["stdout"] = stdout
            captured["stderr"] = stderr
            return ErrorCategory.TERMINAL

        with patch("app.run.log"), \
             patch("app.cli_errors.classify_cli_error", side_effect=_capture):
            self._call(
                stdout_file=str(tmp_path / "missing-out"),
                stderr_file=str(tmp_path / "missing-err"),
            )
        assert captured["stdout"] == ""
        assert captured["stderr"] == ""


# ---------------------------------------------------------------------------
# _handle_skill_dispatch — cli_skill translation branches (no skill_cmd match)
# ---------------------------------------------------------------------------

class TestHandleSkillDispatchTranslation:

    def _call(self, title, instance):
        from app.mission_executor import _handle_skill_dispatch
        return _handle_skill_dispatch(
            mission_title=title,
            project_name="koan",
            project_path="/tmp/proj",
            koan_root="/tmp/koan",
            instance=instance,
            run_num=1,
            max_runs=10,
            autonomous_mode="implement",
            interval=60,
        )

    def test_combo_skill_expands(self, tmp_path):
        with patch("app.skill_dispatch.dispatch_skill_mission", return_value=None), \
             patch("app.skill_dispatch.is_skill_mission", return_value=True), \
             patch("app.skill_dispatch.expand_combo_skill", return_value=True), \
             patch("app.run.log"), \
             patch("app.run._notify"), \
             patch("app.run._finalize_mission") as mock_fin, \
             patch("app.run._commit_instance"):
            handled, title = self._call("/rr https://x/pull/1", str(tmp_path))
        assert handled is True
        mock_fin.assert_called_once()

    def test_passthrough_command_returns_stripped(self, tmp_path):
        with patch("app.skill_dispatch.dispatch_skill_mission", return_value=None), \
             patch("app.skill_dispatch.is_skill_mission", return_value=True), \
             patch("app.skill_dispatch.expand_combo_skill", return_value=False), \
             patch("app.skill_dispatch.strip_passthrough_command",
                   return_value="do the thing"), \
             patch("app.run.log"):
            handled, title = self._call("/gh_request do the thing", str(tmp_path))
        assert handled is False
        assert title == "do the thing"

    def test_cli_skill_translation_returns_translated(self, tmp_path):
        with patch("app.skill_dispatch.dispatch_skill_mission", return_value=None), \
             patch("app.skill_dispatch.is_skill_mission", return_value=True), \
             patch("app.skill_dispatch.expand_combo_skill", return_value=False), \
             patch("app.skill_dispatch.strip_passthrough_command", return_value=None), \
             patch("app.skill_dispatch.translate_cli_skill_mission",
                   return_value="/provider-slash translated"), \
             patch("app.run.log"):
            handled, title = self._call("/myskill arg", str(tmp_path))
        assert handled is False
        assert title == "/provider-slash translated"

    def test_unknown_command_with_arg_error(self, tmp_path):
        with patch("app.skill_dispatch.dispatch_skill_mission", return_value=None), \
             patch("app.skill_dispatch.is_skill_mission", return_value=True), \
             patch("app.skill_dispatch.expand_combo_skill", return_value=False), \
             patch("app.skill_dispatch.strip_passthrough_command", return_value=None), \
             patch("app.skill_dispatch.translate_cli_skill_mission", return_value=None), \
             patch("app.skill_dispatch.parse_skill_mission",
                   return_value=("", "review", "")), \
             patch("app.skill_dispatch.validate_skill_args",
                   return_value="missing PR url"), \
             patch("app.run.log"), \
             patch("app.run._notify") as mock_notify, \
             patch("app.run._finalize_mission"), \
             patch("app.run._commit_instance"):
            handled, _ = self._call("/review", str(tmp_path))
        assert handled is True
        assert any("missing PR url" in str(c) for c in mock_notify.call_args_list)

    def test_unknown_command_without_arg_error(self, tmp_path):
        with patch("app.skill_dispatch.dispatch_skill_mission", return_value=None), \
             patch("app.skill_dispatch.is_skill_mission", return_value=True), \
             patch("app.skill_dispatch.expand_combo_skill", return_value=False), \
             patch("app.skill_dispatch.strip_passthrough_command", return_value=None), \
             patch("app.skill_dispatch.translate_cli_skill_mission", return_value=None), \
             patch("app.skill_dispatch.parse_skill_mission",
                   return_value=("", "bogus", "")), \
             patch("app.skill_dispatch.validate_skill_args", return_value=None), \
             patch("app.run.log"), \
             patch("app.run._notify") as mock_notify, \
             patch("app.run._finalize_mission") as mock_fin, \
             patch("app.run._commit_instance"):
            handled, _ = self._call("/bogus", str(tmp_path))
        assert handled is True
        mock_fin.assert_called_once()
        assert any("Unknown skill command" in str(c) for c in mock_notify.call_args_list)

    def test_non_skill_mission_falls_through(self, tmp_path):
        """No skill_cmd and not a /command — proceed to Claude unchanged."""
        with patch("app.skill_dispatch.dispatch_skill_mission", return_value=None), \
             patch("app.skill_dispatch.is_skill_mission", return_value=False), \
             patch("app.run.log"):
            handled, title = self._call("just a normal mission", str(tmp_path))
        assert handled is False
        assert title == "just a normal mission"


# ---------------------------------------------------------------------------
# _handle_skill_dispatch — matched skill_cmd path (core-integrity + stagnation)
# ---------------------------------------------------------------------------

class TestHandleSkillDispatchMatched:

    def _call(self, instance):
        from app.mission_executor import _handle_skill_dispatch
        return _handle_skill_dispatch(
            mission_title="/review https://x/pull/1",
            project_name="koan",
            project_path="/tmp/proj",
            koan_root="/tmp/koan",
            instance=instance,
            run_num=1,
            max_runs=10,
            autonomous_mode="implement",
            interval=60,
        )

    def test_matched_skill_recovers_core_files_and_stagnation_requeue(self, tmp_path):
        """A matched skill that fails core-integrity and was stagnated:
        recovers some files, marks others unrecoverable (forcing exit 1),
        and skips debug escalation because the mission was requeued."""
        import app.run as _run

        skill_result = {"exit_code": 0, "stdout": "done", "stderr": "",
                        "quota_exhausted": False, "quota_info": None}

        _run._last_mission_stagnated.set()
        try:
            # Use ExitStack rather than a comma-chained `with` so the 20
            # patches don't exceed Python 3.11's 20-statically-nested-block
            # compile limit (SyntaxError: too many statically nested blocks).
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch(
                    "app.skill_dispatch.dispatch_skill_mission",
                    return_value=["claude", "/review"]))
                stack.enter_context(patch(
                    "app.skill_dispatch.cleanup_skill_temp_files"))
                stack.enter_context(patch(
                    "app.loop_manager.create_pending_file",
                    side_effect=RuntimeError("boom")))
                stack.enter_context(patch(
                    "app.core_files.snapshot_core_files", return_value=set()))
                stack.enter_context(patch(
                    "app.core_files.check_core_files", return_value=["warn"]))
                stack.enter_context(patch(
                    "app.core_files.recover_project_files",
                    return_value=(["a.py"], ["b.py"])))
                stack.enter_context(patch(
                    "app.core_files.log_integrity_warnings"))
                stack.enter_context(patch(
                    "app.stagnation_monitor.get_retry_count", return_value=2))
                stack.enter_context(patch(
                    "app.run._run_skill_mission", return_value=skill_result))
                stack.enter_context(patch(
                    "app.run._provider_identity",
                    return_value=("claude", "Claude")))
                stack.enter_context(patch(
                    "app.run._classify_and_handle_cli_error",
                    return_value=False))
                stack.enter_context(patch(
                    "app.run._probe_exit0_quota", return_value=False))
                stack.enter_context(patch("app.run._notify_mission_end"))
                stack.enter_context(patch("app.run._finalize_mission"))
                stack.enter_context(patch("app.run._commit_instance"))
                stack.enter_context(patch("app.run._sleep_between_runs"))
                stack.enter_context(patch("app.run.set_status"))
                stack.enter_context(patch("app.run._notify"))
                stack.enter_context(patch("app.run.log"))
                mock_esc = stack.enter_context(patch(
                    "app.mission_executor._maybe_escalate_to_debug"))
                handled, _ = self._call(str(tmp_path))
        finally:
            _run._last_mission_stagnated.clear()

        assert handled is True
        # Stagnation requeued (retry_count > 0) → escalation skipped
        mock_esc.assert_not_called()

    def test_matched_skill_swallows_run_exception(self, tmp_path):
        """An exception inside the skill runner is caught; dispatch still
        finalizes and reports handled."""
        with patch("app.skill_dispatch.dispatch_skill_mission",
                   return_value=["claude", "/review"]), \
             patch("app.skill_dispatch.cleanup_skill_temp_files"), \
             patch("app.loop_manager.create_pending_file"), \
             patch("app.core_files.snapshot_core_files", return_value=set()), \
             patch("app.core_files.check_core_files", return_value=[]), \
             patch("app.run._run_skill_mission",
                   side_effect=RuntimeError("kaboom")), \
             patch("app.run._provider_identity",
                   return_value=("claude", "Claude")), \
             patch("app.run._classify_and_handle_cli_error", return_value=False), \
             patch("app.run._probe_exit0_quota", return_value=False), \
             patch("app.run._notify_mission_end"), \
             patch("app.run._finalize_mission") as mock_fin, \
             patch("app.run._commit_instance"), \
             patch("app.run._sleep_between_runs"), \
             patch("app.run.set_status"), \
             patch("app.run._notify"), \
             patch("app.run.log") as mock_log, \
             patch("app.mission_executor._maybe_escalate_to_debug"):
            handled, _ = self._call(str(tmp_path))

        assert handled is True
        # exit_code stays 1 (default) since the runner blew up → finalize called
        mock_fin.assert_called_once()
        assert any("exception" in str(c).lower() for c in mock_log.call_args_list)


# ---------------------------------------------------------------------------
# _maybe_escalate_to_debug — duplicate-insert path
# ---------------------------------------------------------------------------

class TestEscalateDuplicate:

    def test_duplicate_insert_returns_false(self, tmp_path, monkeypatch):
        from app.mission_executor import _maybe_escalate_to_debug

        (tmp_path / "missions.md").write_text("## Pending\n\n## In Progress\n\n## Done\n")
        monkeypatch.setattr("app.config.is_debug_on_fix_failure", lambda: True)
        with patch("app.utils.insert_pending_mission", return_value=False), \
             patch("app.run.log") as mock_log:
            result = _maybe_escalate_to_debug(
                mission_title="/fix https://github.com/org/repo/issues/42",
                exit_code=1,
                instance=str(tmp_path),
            )
        assert result is False
        assert any("duplicate" in str(c).lower() for c in mock_log.call_args_list)
