"""Tests for app.refactor_step — the reusable refactor pass."""

from unittest.mock import MagicMock, patch

import pytest

from app.refactor_step import (
    RefactorResult,
    _parse_summary_bullets,
    run_internal_refactor_pass,
    run_refactor_pass,
)


_OUTPUT_WITH_SUMMARY = (
    "Did some work.\n"
    "COMMIT_SUBJECT: refactor: simplify auth\n"
    "===REFACTOR_SUMMARY===\n"
    "- Extracted a shared helper\n"
    "- Removed dead code\n"
    "===END===\n"
)


def _step(committed=True, output=_OUTPUT_WITH_SUMMARY):
    return MagicMock(committed=committed, output=output)


def _patch_common():
    """Patch the lazily-imported dependencies of run_refactor_pass."""
    return [
        patch("app.claude_step._get_current_branch", return_value="koan/feature"),
        patch("app.projects_config.resolve_base_branch", return_value="main"),
        patch("app.prompts.load_prompt_or_skill", return_value="PROMPT"),
        patch("app.commit_conventions.get_project_commit_guidance", return_value=""),
        patch("app.utils.project_name_for_path", return_value="repo"),
    ]


class TestParseSummaryBullets:
    def test_extracts_bullets(self):
        assert _parse_summary_bullets(_OUTPUT_WITH_SUMMARY) == [
            "Extracted a shared helper",
            "Removed dead code",
        ]

    def test_no_block_returns_empty(self):
        assert _parse_summary_bullets("no markers here") == []

    def test_empty_output(self):
        assert _parse_summary_bullets("") == []


class TestRunRefactorPass:
    def test_commits_parses_and_pushes(self):
        patches = _patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("app.claude_step.run_claude_step", return_value=_step()), \
             patch("app.pr_review.detect_test_command", return_value=None), \
             patch("app.git_utils.ordered_remotes", return_value=["origin"]), \
             patch("app.claude_step._run_git") as mock_git:
            result = run_refactor_pass("/proj")

        assert result.committed is True
        assert result.pushed is True
        assert result.bullets == ["Extracted a shared helper", "Removed dead code"]
        mock_git.assert_called_once()
        # plain push, never force
        pushed_args = mock_git.call_args.args[0]
        assert "--force" not in pushed_args
        assert "--force-with-lease" not in pushed_args

    def test_no_changes_is_noop(self):
        patches = _patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("app.claude_step.run_claude_step", return_value=_step(committed=False)), \
             patch("app.git_utils.ordered_remotes", return_value=["origin"]), \
             patch("app.claude_step._run_git") as mock_git:
            result = run_refactor_pass("/proj")

        assert result.committed is False
        assert result.pushed is False
        mock_git.assert_not_called()

    def test_push_false_skips_push(self):
        patches = _patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("app.claude_step.run_claude_step", return_value=_step()), \
             patch("app.pr_review.detect_test_command", return_value=None), \
             patch("app.claude_step._run_git") as mock_git:
            result = run_refactor_pass("/proj", push=False, run_tests=False)

        assert result.committed is True
        assert result.pushed is False
        mock_git.assert_not_called()

    def test_run_tests_false_skips_detection(self):
        patches = _patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("app.claude_step.run_claude_step", return_value=_step()), \
             patch("app.pr_review.detect_test_command") as mock_detect, \
             patch("app.git_utils.ordered_remotes", return_value=["origin"]), \
             patch("app.claude_step._run_git"):
            run_refactor_pass("/proj", run_tests=False)

        mock_detect.assert_not_called()

    def test_tests_fixed_after_one_attempt(self):
        patches = _patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("app.claude_step.run_claude_step", return_value=_step()) as mock_step, \
             patch("app.pr_review.detect_test_command", return_value="make test"), \
             patch("app.claude_step.run_project_tests",
                   side_effect=[{"passed": False, "output": "boom", "details": "1 failed"},
                                {"passed": True, "details": "ok"}]), \
             patch("app.git_utils.ordered_remotes", return_value=["origin"]), \
             patch("app.claude_step._run_git"):
            result = run_refactor_pass("/proj")

        assert result.tests == "fixed and passing"
        # one refactor step + one fix step
        assert mock_step.call_count == 2

    def test_uses_convention_subject_when_guidance_present(self):
        with patch("app.claude_step._get_current_branch", return_value="koan/x"), \
             patch("app.projects_config.resolve_base_branch", return_value="main"), \
             patch("app.prompts.load_prompt_or_skill", return_value="PROMPT"), \
             patch("app.commit_conventions.get_project_commit_guidance",
                   return_value="Use conventional commits"), \
             patch("app.utils.project_name_for_path", return_value="repo"), \
             patch("app.claude_step.run_claude_step", return_value=_step()) as mock_step, \
             patch("app.pr_review.detect_test_command", return_value=None), \
             patch("app.git_utils.ordered_remotes", return_value=["origin"]), \
             patch("app.claude_step._run_git"):
            run_refactor_pass("/proj")

        assert mock_step.call_args.kwargs["use_convention_subject"] is True

    def test_push_failure_reported(self):
        patches = _patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("app.claude_step.run_claude_step", return_value=_step()), \
             patch("app.pr_review.detect_test_command", return_value=None), \
             patch("app.git_utils.ordered_remotes", return_value=["origin", "upstream"]), \
             patch("app.claude_step._run_git", side_effect=RuntimeError("rejected")):
            result = run_refactor_pass("/proj")

        assert result.committed is True
        assert result.pushed is False


class TestRunInternalRefactorPass:
    def test_swallows_exceptions(self):
        notify = MagicMock()
        with patch("app.refactor_step.run_refactor_pass",
                   side_effect=RuntimeError("kaboom")):
            result = run_internal_refactor_pass("/proj", notify_fn=notify)

        assert isinstance(result, RefactorResult)
        assert result.committed is False

    def test_notifies_on_commit(self):
        notify = MagicMock()
        with patch("app.refactor_step.run_refactor_pass",
                   return_value=RefactorResult(committed=True, pushed=True)):
            run_internal_refactor_pass("/proj", notify_fn=notify)

        assert notify.called
        assert "Refactor pass" in notify.call_args.args[0]
