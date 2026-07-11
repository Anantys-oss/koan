"""Tests for outcome-attributed experience entries.

Experience entries are structured memory records that capture the single most
valuable artifact the agent produces: 'for issue X the root cause was Y and
approach Z worked (or failed).'
"""

import json
import time
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Phase 1: append_memory_entry structured fields
# ---------------------------------------------------------------------------


class TestAppendMemoryEntryExperienceFields:
    """Verify append_memory_entry serializes structured experience fields."""

    def test_experience_fields_serialized(self, tmp_path):
        from app.memory_manager import MemoryManager

        mgr = MemoryManager(str(tmp_path))
        mgr.append_memory_entry(
            "experience", "my-toolkit", "Root cause was race condition",
            outcome="success",
            mission_kind="fix",
            root_cause="Race condition in session init",
            approach="Added sync.Mutex around session map",
            artifact="PR #42 (commit abc123)",
        )

        log_path = tmp_path / "memory" / "log.jsonl"
        lines = log_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["type"] == "experience"
        assert entry["outcome"] == "success"
        assert entry["mission_kind"] == "fix"
        assert "race condition" in entry["root_cause"].lower()
        assert "sync.Mutex" in entry["approach"]
        assert entry["artifact"] == "PR #42 (commit abc123)"

    def test_experience_fields_omitted_when_none(self, tmp_path):
        from app.memory_manager import MemoryManager

        mgr = MemoryManager(str(tmp_path))
        mgr.append_memory_entry("experience", "my-toolkit", "simple entry")

        log_path = tmp_path / "memory" / "log.jsonl"
        entry = json.loads(log_path.read_text().strip().split("\n")[0])
        assert "outcome" not in entry
        assert "mission_kind" not in entry
        assert "root_cause" not in entry
        assert "approach" not in entry
        assert "artifact" not in entry

    def test_module_level_wrapper_passes_fields(self, tmp_path):
        from app.memory_manager import append_memory_entry

        append_memory_entry(
            str(tmp_path), "experience", "my-toolkit", "entry via wrapper",
            outcome="failed", mission_kind="review",
        )
        log_path = tmp_path / "memory" / "log.jsonl"
        entry = json.loads(log_path.read_text().strip().split("\n")[0])
        assert entry["outcome"] == "failed"
        assert entry["mission_kind"] == "review"

    def test_root_cause_and_approach_capped(self, tmp_path):
        from app.memory_manager import append_memory_entry

        append_memory_entry(
            str(tmp_path), "experience", "p", "capped fields",
            root_cause="x" * 1000,
            approach="y" * 1000,
        )
        entry = json.loads(
            (tmp_path / "memory" / "log.jsonl").read_text().strip().split("\n")[0]
        )
        assert len(entry["root_cause"]) == 500
        assert len(entry["approach"]) == 500


# ---------------------------------------------------------------------------
# Phase 2: _classify_mission_kind and capture_experience gating
# ---------------------------------------------------------------------------


class TestClassifyMissionKind:

    def test_fix(self):
        from app.experience_capture import _classify_mission_kind
        assert _classify_mission_kind("/fix Fix race condition") == "fix"

    def test_implement(self):
        from app.experience_capture import _classify_mission_kind
        assert _classify_mission_kind("/implement Add dark mode") == "implement"

    def test_review(self):
        from app.experience_capture import _classify_mission_kind
        assert _classify_mission_kind("/review Check PR #42") == "review"

    def test_chat_returns_none(self):
        from app.experience_capture import _classify_mission_kind
        assert _classify_mission_kind("/chat What do you think?") is None

    def test_rebase_returns_none(self):
        from app.experience_capture import _classify_mission_kind
        assert _classify_mission_kind("/rebase PR #42") is None

    def test_empty_title(self):
        from app.experience_capture import _classify_mission_kind
        assert _classify_mission_kind("") is None
        assert _classify_mission_kind("   ") is None

    def test_ai_keyword_maps_to_implement(self):
        from app.experience_capture import _classify_mission_kind
        assert _classify_mission_kind("/ai Add new feature") == "implement"

    def test_freetext_fix_keyword(self):
        from app.experience_capture import _classify_mission_kind
        assert _classify_mission_kind("Fix the broken parser") == "fix"

    def test_freetext_implement_keyword(self):
        from app.experience_capture import _classify_mission_kind
        assert _classify_mission_kind("Implement user authentication") == "implement"


class TestCaptureExperienceGating:

    def test_success_captures_for_fix_mission(self, tmp_path):
        from app.experience_capture import capture_experience

        with patch("app.experience_capture.append_memory_entry") as mock_append:
            capture_experience(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                mission_title="/fix Fix race condition",
                exit_code=0,
                outcome="success",
                root_cause="Race condition",
                approach="Added mutex",
                artifact="PR #42",
                duration_minutes=30,
            )
        mock_append.assert_called_once()

    def test_failed_always_captures(self, tmp_path):
        from app.experience_capture import capture_experience
        from app.mission_verifier import VerifyResult

        with patch("app.experience_capture.append_memory_entry") as mock_append:
            capture_experience(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                mission_title="/fix Fix connection leak",
                exit_code=1,
                outcome="failed",
                verify_result=VerifyResult(passed=False, summary="tests failed"),
                root_cause="Pool not releasing",
                approach="Tried finally block",
                duration_minutes=45,
            )
        mock_append.assert_called_once()
        _, kwargs = mock_append.call_args
        assert kwargs["outcome"] == "failed"

    def test_skips_non_significant_missions(self, tmp_path):
        from app.experience_capture import capture_experience

        with patch("app.experience_capture.append_memory_entry") as mock_append:
            capture_experience(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                mission_title="/rebase PR #42",
                exit_code=0,
                outcome="success",
                duration_minutes=2,
            )
        mock_append.assert_not_called()

    def test_skips_chat_missions(self, tmp_path):
        from app.experience_capture import capture_experience

        with patch("app.experience_capture.append_memory_entry") as mock_append:
            capture_experience(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                mission_title="/chat What do you think?",
                exit_code=0,
                outcome="success",
                duration_minutes=30,
            )
        mock_append.assert_not_called()

    def test_short_success_fix_skipped(self, tmp_path):
        """Short fix missions with no journal substance are skipped."""
        from app.experience_capture import capture_experience

        with patch("app.experience_capture.append_memory_entry") as mock_append:
            capture_experience(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                mission_title="/fix typo in README",
                exit_code=0,
                outcome="success",
                duration_minutes=1,
            )
        mock_append.assert_not_called()

    def test_capture_never_raises(self, tmp_path):
        """capture_experience must never raise, even on bad input."""
        from app.experience_capture import capture_experience

        with patch(
            "app.experience_capture.append_memory_entry",
            side_effect=RuntimeError("disk full"),
        ):
            # Must not raise
            capture_experience(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                mission_title="/fix Important bug",
                exit_code=0,
                outcome="success",
                duration_minutes=30,
            )

    def test_verify_result_passed_folded_into_content(self, tmp_path):
        from app.experience_capture import capture_experience
        from app.mission_verifier import VerifyResult

        with patch("app.experience_capture.append_memory_entry") as mock_append:
            capture_experience(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                mission_title="/fix Fix race condition",
                exit_code=0,
                outcome="success",
                verify_result=VerifyResult(passed=True, summary="all checks passed"),
                duration_minutes=30,
            )
        mock_append.assert_called_once()
        content_arg = mock_append.call_args.args[3]
        assert "verified" in content_arg
        assert "all checks passed" in content_arg

    def test_verify_result_none_no_verify_line(self, tmp_path):
        from app.experience_capture import capture_experience

        with patch("app.experience_capture.append_memory_entry") as mock_append:
            capture_experience(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                mission_title="/fix Fix race condition",
                exit_code=0,
                outcome="success",
                verify_result=None,
                duration_minutes=30,
            )
        mock_append.assert_called_once()
        content_arg = mock_append.call_args.args[3]
        assert "verified" not in content_arg.lower()


# ---------------------------------------------------------------------------
# Phase 3: run_post_mission capture (success + failure)
# ---------------------------------------------------------------------------


class TestRunPostMissionCapture:

    def test_captures_on_success(self, tmp_path):
        from app.mission_runner import run_post_mission

        with patch("app.mission_runner._record_session_outcome"), \
             patch("app.mission_runner.check_auto_merge", return_value=None), \
             patch("app.mission_runner.trigger_reflection", return_value=False), \
             patch("app.mission_runner._run_quality_pipeline", return_value={}), \
             patch("app.mission_runner._run_lint_gate", return_value=None), \
             patch("app.mission_runner.archive_pending", return_value=False), \
             patch("app.quota_handler.handle_quota_exhaustion", return_value=None), \
             patch("app.mission_runner.update_usage", return_value=True), \
             patch("app.mission_runner.check_security_review", return_value=True), \
             patch("app.experience_capture.capture_experience") as mock_capture:
            run_post_mission(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                project_path=str(tmp_path),
                run_num=1,
                exit_code=0,
                stdout_file=str(tmp_path / "stdout"),
                stderr_file=str(tmp_path / "stderr"),
                mission_title="/fix Fix race condition in session init",
                start_time=int(time.time()) - 1800,
            )
        mock_capture.assert_called_once()
        _, kwargs = mock_capture.call_args
        assert kwargs["exit_code"] == 0
        assert kwargs["outcome"] == "success"

    def test_captures_on_failure(self, tmp_path):
        from app.mission_runner import run_post_mission

        with patch("app.mission_runner._record_session_outcome"), \
             patch("app.quota_handler.handle_quota_exhaustion", return_value=None), \
             patch("app.mission_runner.update_usage", return_value=True), \
             patch("app.mission_runner.archive_pending", return_value=False), \
             patch("app.experience_capture.capture_experience") as mock_capture:
            run_post_mission(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                project_path=str(tmp_path),
                run_num=1,
                exit_code=1,
                stdout_file=str(tmp_path / "stdout"),
                stderr_file=str(tmp_path / "stderr"),
                mission_title="/fix Fix database connection leak",
                start_time=int(time.time()) - 2700,
            )
        mock_capture.assert_called_once()
        _, kwargs = mock_capture.call_args
        assert kwargs["exit_code"] == 1
        assert kwargs["outcome"] == "failed"
        assert kwargs["verify_result"] is None


# ---------------------------------------------------------------------------
# Phase 4: _finalize_mission stagnation capture
# ---------------------------------------------------------------------------


class TestStagnationCapture:

    def test_stagnation_cap_captures_reverted(self, tmp_path):
        from app.run import _finalize_mission, _last_mission_stagnated

        _last_mission_stagnated.set()

        with patch("app.run.get_retry_count", return_value=3), \
             patch("app.run.get_total_attempts", return_value=3), \
             patch("app.run._update_mission_in_file"), \
             patch("app.run.record_execution"), \
             patch("app.experience_capture.capture_experience") as mock_capture:
            _finalize_mission(
                instance=str(tmp_path),
                mission_title="/fix Fix infinite loop in parser",
                project_name="my-toolkit",
                exit_code=1,
            )
        mock_capture.assert_called_once()
        _, kwargs = mock_capture.call_args
        assert kwargs["outcome"] == "reverted"

        _last_mission_stagnated.clear()


# ---------------------------------------------------------------------------
# Phase 5: run_ci_fix_loop capture
# ---------------------------------------------------------------------------


class TestCiFixCapture:

    def test_ci_fix_success_captures(self, tmp_path):
        from app.claude_step import run_ci_fix_loop

        mock_step_result = type("StepResult", (), {"quota_exhausted": False})()

        def mock_step_runner(**kwargs):
            return mock_step_result, False, 1

        def mock_push(branch, project_path):
            pass

        def mock_recheck(branch, repo):
            return "success", "run-123", "CI passed after fix"

        def mock_prompt_builder(logs, diff):
            return "fix the CI failure"

        with patch("app.claude_step._force_push"), \
             patch("app.claude_step._run_git", return_value=""), \
             patch("app.claude_step.truncate_diff", return_value=""), \
             patch("app.experience_capture.capture_experience") as mock_capture:
            run_ci_fix_loop(
                branch="koan/test",
                base="main",
                full_repo="owner/repo",
                project_path=str(tmp_path),
                ci_logs="CI failed: test error",
                actions_log=[],
                max_attempts=2,
                use_polling=False,
                prompt_builder=mock_prompt_builder,
                step_runner=mock_step_runner,
                push_fn=mock_push,
                recheck_fn=mock_recheck,
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
            )
        mock_capture.assert_called_once()
        _, kwargs = mock_capture.call_args
        assert kwargs["outcome"] == "success"
        assert kwargs["mission_kind"] == "fix"
        assert kwargs["exit_code"] == 0

    def test_ci_fix_no_capture_without_instance_dir(self, tmp_path):
        from app.claude_step import run_ci_fix_loop

        mock_step_result = type("StepResult", (), {"quota_exhausted": False})()

        def mock_step_runner(**kwargs):
            return mock_step_result, False, 1

        with patch("app.claude_step._force_push"), \
             patch("app.claude_step._run_git", return_value=""), \
             patch("app.claude_step.truncate_diff", return_value=""), \
             patch("app.experience_capture.capture_experience") as mock_capture:
            run_ci_fix_loop(
                branch="koan/test",
                base="main",
                full_repo="owner/repo",
                project_path=str(tmp_path),
                ci_logs="CI failed",
                actions_log=[],
                max_attempts=1,
                prompt_builder=lambda l, d: "fix",
                step_runner=mock_step_runner,
                push_fn=lambda b, p: None,
                recheck_fn=lambda b, r: ("success", "run-1", "passed"),
            )
        mock_capture.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 6: End-to-end retry test
# ---------------------------------------------------------------------------


class TestFailedThenSucceededRetry:

    def test_failed_then_succeeded_produces_two_entries(self, tmp_path):
        from app.mission_runner import run_post_mission
        from app.mission_verifier import VerifyResult

        captured = []

        # --- Attempt 1: failure ---
        with patch("app.mission_runner._record_session_outcome"), \
             patch("app.quota_handler.handle_quota_exhaustion", return_value=None), \
             patch("app.mission_runner.update_usage", return_value=True), \
             patch("app.mission_runner.archive_pending", return_value=False), \
             patch("app.experience_capture.capture_experience",
                   side_effect=lambda **kw: captured.append(kw)):
            run_post_mission(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                project_path=str(tmp_path),
                run_num=1,
                exit_code=1,
                stdout_file=str(tmp_path / "stdout"),
                stderr_file=str(tmp_path / "stderr"),
                mission_title="/fix Fix race condition in session init",
                start_time=int(time.time()) - 2700,
            )

        # --- Attempt 2: success ---
        with patch("app.mission_runner._record_session_outcome"), \
             patch("app.mission_runner.check_auto_merge", return_value=None), \
             patch("app.mission_runner.trigger_reflection", return_value=False), \
             patch("app.mission_runner._run_quality_pipeline", return_value={}), \
             patch("app.mission_runner._run_lint_gate", return_value=None), \
             patch("app.mission_runner.archive_pending", return_value=False), \
             patch("app.quota_handler.handle_quota_exhaustion", return_value=None), \
             patch("app.mission_runner.update_usage", return_value=True), \
             patch("app.mission_runner.check_security_review", return_value=True), \
             patch("app.mission_runner._run_mission_verification",
                   return_value=VerifyResult(passed=True, summary="all checks passed")), \
             patch("app.experience_capture.capture_experience",
                   side_effect=lambda **kw: captured.append(kw)):
            run_post_mission(
                instance_dir=str(tmp_path),
                project_name="my-toolkit",
                project_path=str(tmp_path),
                run_num=2,
                exit_code=0,
                stdout_file=str(tmp_path / "stdout"),
                stderr_file=str(tmp_path / "stderr"),
                mission_title="/fix Fix race condition in session init",
                start_time=int(time.time()) - 1800,
            )

        assert len(captured) == 2
        assert captured[0]["outcome"] == "failed"
        assert captured[0]["exit_code"] == 1
        assert captured[1]["outcome"] == "success"
        assert captured[1]["exit_code"] == 0
