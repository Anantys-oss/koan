"""Tests for error-path logging — verifies silent suppressions were replaced with log calls."""

from pathlib import Path
from unittest.mock import MagicMock, patch


class TestSkillDispatchErrorLogging:
    """skill_dispatch._get_skills_dir_mtime logs OSError instead of suppressing."""

    @patch("app.skill_dispatch._log_skill")
    def test_temp_file_cleanup_failure_logged(self, mock_log):
        from app.skill_dispatch import cleanup_skill_temp_files

        skill_cmd = ["--context-file", "/nonexistent/koan-test-file"]
        with patch("os.unlink", side_effect=OSError("no such file")):
            cleanup_skill_temp_files(skill_cmd)
        mock_log.assert_called_once()
        assert "Temp skill file cleanup failed" in mock_log.call_args[0][1]


class TestMissionRunnerErrorLogging:
    """mission_runner silent except:pass sites now log errors."""

    @patch("app.mission_runner._log_runner")
    def test_timeout_alert_state_read_failure_logged(self, mock_log, tmp_path):
        from app.mission_runner import _check_pipeline_timeout_rate

        instance_dir = str(tmp_path)
        # Write corrupt state file so JSON parse fails
        state_file = tmp_path / ".pipeline-timeout-alert.json"
        state_file.write_text("{invalid json")

        # Write enough outcomes to trigger the threshold check
        outcomes = [{"pipeline_timed_out": True}] * 10

        with patch("app.session_tracker.load_outcomes", return_value=outcomes):
            with patch("app.utils.append_to_outbox"):
                with patch("app.utils.atomic_write"):
                    _check_pipeline_timeout_rate(instance_dir)

        logged_msgs = [c[0][1] for c in mock_log.call_args_list]
        assert any("Timeout alert state read failed" in m for m in logged_msgs)

    @patch("app.mission_runner._log_runner")
    def test_timeout_alert_state_write_failure_logged(self, mock_log, tmp_path):
        """Verify the write failure path in _check_pipeline_timeout_rate."""
        from app.mission_runner import _check_pipeline_timeout_rate

        instance_dir = str(tmp_path)
        outcomes = [{"pipeline_timed_out": True}] * 10

        with patch("app.session_tracker.load_outcomes", return_value=outcomes):
            with patch("app.utils.append_to_outbox"):
                with patch(
                    "app.utils.atomic_write", side_effect=OSError("read-only fs")
                ):
                    _check_pipeline_timeout_rate(instance_dir)

        logged_msgs = [c[0][1] for c in mock_log.call_args_list]
        assert any("Timeout alert state write failed" in m for m in logged_msgs)


class TestCliExecErrorLogging:
    """cli_exec silent suppressions now log errors."""

    @patch("app.cli_exec._log_cli")
    def test_prompt_file_cleanup_failure_logged(self, mock_log):
        from app.cli_exec import _cleanup_prompt_file

        with patch("app.cli_exec.os.unlink", side_effect=OSError("busy")):
            _cleanup_prompt_file("/tmp/fake-prompt-file")

        mock_log.assert_called_once()
        assert "Prompt file cleanup failed" in mock_log.call_args[0][1]


class TestRunErrorLogging:
    """run.py silent suppressions now log errors."""

    @patch("app.run.log")
    def test_cleanup_temp_failure_logged(self, mock_log):
        from app.run import _cleanup_temp

        with patch("app.run.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.unlink.side_effect = OSError("permission denied")
            MockPath.return_value = mock_path_instance
            _cleanup_temp("/tmp/fake-stdout", "/tmp/fake-stderr")

        error_calls = [c for c in mock_log.call_args_list if c[0][0] == "error"]
        assert len(error_calls) == 2
        assert "Temp file cleanup failed" in error_calls[0][0][1]


class TestIterationManagerErrorLogging:
    """iteration_manager silent suppressions now log errors."""

    @patch("app.iteration_manager._log_iteration")
    def test_diagnostic_type_detection_failure_logged(self, mock_log):
        from app.iteration_manager import _select_diagnostic_type

        with patch(
            "app.mission_metrics.compute_project_trend",
            side_effect=ValueError("bad data"),
        ):
            result = _select_diagnostic_type("/tmp/instance", "test-project")

        assert result == "audit"
        mock_log.assert_called_once()
        assert "Diagnostic type detection failed" in mock_log.call_args[0][1]
