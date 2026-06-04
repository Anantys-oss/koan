"""Tests for the /explain skill runner."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.core.explain.explain_runner import (
    _build_explain_prompt,
    _is_transient_error,
    _run_claude_explain_with_retry,
    _EXPLAIN_TAG,
    main,
    run_explain,
)


SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "core" / "explain"


class TestBuildExplainPrompt:
    """Test prompt building for PR explanations."""

    def _make_context(self, **overrides):
        ctx = {
            "title": "Fix off-by-one in pagination",
            "author": "alice",
            "branch": "fix/pagination",
            "base": "main",
            "body": "Fixes the pagination bug where page 2 shows page 1 results.",
            "diff": "--- a/api.py\n+++ b/api.py\n@@ -10 +10 @@\n-offset = page\n+offset = (page - 1) * size",
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
        }
        ctx.update(overrides)
        return ctx

    def test_prompt_contains_pr_metadata(self):
        context = self._make_context()
        prompt = _build_explain_prompt(context, skill_dir=SKILL_DIR)

        assert "Fix off-by-one in pagination" in prompt
        assert "alice" in prompt
        assert "fix/pagination" in prompt
        assert "main" in prompt

    def test_prompt_contains_diff(self):
        context = self._make_context()
        prompt = _build_explain_prompt(context, skill_dir=SKILL_DIR)

        assert "offset = (page - 1) * size" in prompt

    def test_prompt_contains_body(self):
        context = self._make_context()
        prompt = _build_explain_prompt(context, skill_dir=SKILL_DIR)

        assert "pagination bug" in prompt

    def test_prompt_with_custom_skill_dir(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "explain.md").write_text(
            "Custom prompt: {TITLE} by {AUTHOR}\nDiff:\n{DIFF}"
        )

        context = self._make_context()
        prompt = _build_explain_prompt(context, skill_dir=tmp_path)

        assert "Custom prompt:" in prompt
        assert "Fix off-by-one in pagination" in prompt
        assert "alice" in prompt

    @patch("app.skill_memory.build_memory_block_for_skill")
    def test_project_memory_injected(self, mock_memory):
        mock_memory.return_value = "\n## Project Learnings\n- Use pagination util\n"
        context = self._make_context()

        prompt = _build_explain_prompt(
            context, skill_dir=SKILL_DIR, project_path="/some/project",
        )

        mock_memory.assert_called_once()
        assert "Project Learnings" in prompt


class TestIsTransientError:
    """Test transient error detection."""

    def test_rate_limit(self):
        assert _is_transient_error("CLI invocation failed: exit=1 | stderr=rate_limit_exceeded")

    def test_timeout(self):
        assert _is_transient_error("CLI invocation timed out after 600s")

    def test_connection_error(self):
        assert _is_transient_error("Connection refused ECONNRESET")

    def test_server_error_503(self):
        assert _is_transient_error("exit=1 | stderr=503 Service Unavailable")

    def test_overloaded(self):
        assert _is_transient_error("API is overloaded")

    def test_non_transient(self):
        assert not _is_transient_error("invalid model name")

    def test_non_transient_syntax(self):
        assert not _is_transient_error("exit=1 | stderr=SyntaxError in prompt")

    def test_empty_error(self):
        assert not _is_transient_error("")


class TestRetryLogic:
    """Test retry-with-backoff for Claude CLI failures."""

    @patch("skills.core.explain.explain_runner.time.sleep")
    @patch("skills.core.explain.explain_runner._run_claude_explain")
    def test_retry_on_transient_error(self, mock_claude, mock_sleep):
        mock_claude.side_effect = [
            ("", "rate limit exceeded"),
            ("Explanation text", ""),
        ]

        output, error = _run_claude_explain_with_retry("prompt", "/path")

        assert output == "Explanation text"
        assert error == ""
        assert mock_claude.call_count == 2
        mock_sleep.assert_called_once()

    @patch("skills.core.explain.explain_runner.time.sleep")
    @patch("skills.core.explain.explain_runner._run_claude_explain")
    def test_no_retry_on_non_transient(self, mock_claude, mock_sleep):
        mock_claude.return_value = ("", "invalid model name foo")

        output, error = _run_claude_explain_with_retry("prompt", "/path")

        assert error == "invalid model name foo"
        assert mock_claude.call_count == 1
        mock_sleep.assert_not_called()

    @patch("skills.core.explain.explain_runner.time.sleep")
    @patch("skills.core.explain.explain_runner._run_claude_explain")
    def test_retry_exhausted(self, mock_claude, mock_sleep):
        mock_claude.return_value = ("", "timeout after 600s")

        output, error = _run_claude_explain_with_retry("prompt", "/path")

        assert "timeout" in error
        assert mock_claude.call_count == 2

    @patch("skills.core.explain.explain_runner._run_claude_explain")
    def test_success_no_retry(self, mock_claude):
        mock_claude.return_value = ("Explanation", "")

        output, error = _run_claude_explain_with_retry("prompt", "/path")

        assert output == "Explanation"
        assert mock_claude.call_count == 1


class TestRunExplain:
    """Test the main run_explain orchestration."""

    @patch("skills.core.explain.explain_runner._post_explanation_comment")
    @patch("app.rebase_pr.fetch_pr_context")
    @patch("app.claude_step.resolve_pr_location")
    @patch("skills.core.explain.explain_runner._run_claude_explain")
    def test_success(self, mock_claude, mock_resolve, mock_fetch, mock_post):
        mock_resolve.return_value = ("owner", "repo")
        mock_fetch.return_value = {
            "title": "Fix bug",
            "author": "bob",
            "branch": "fix/bug",
            "base": "main",
            "body": "Fixes a bug.",
            "diff": "+fixed line",
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
        }
        mock_claude.return_value = ("Great explanation of the fix.", "")
        mock_post.return_value = (True, "")

        notify = MagicMock()
        success, summary = run_explain(
            "owner", "repo", "42", "/path",
            notify_fn=notify,
            skill_dir=SKILL_DIR,
        )

        assert success is True
        assert "Great explanation" in summary
        assert "#42" in summary
        notify.assert_called_once()
        mock_post.assert_called_once_with("owner", "repo", "42", "Great explanation of the fix.")

    @patch("skills.core.explain.explain_runner._post_explanation_comment")
    @patch("app.rebase_pr.fetch_pr_context")
    @patch("app.claude_step.resolve_pr_location")
    @patch("skills.core.explain.explain_runner._run_claude_explain")
    def test_success_comment_post_failure(self, mock_claude, mock_resolve, mock_fetch, mock_post):
        """Explanation succeeds even when PR comment posting fails."""
        mock_resolve.return_value = ("owner", "repo")
        mock_fetch.return_value = {
            "title": "Fix", "author": "a", "branch": "b", "base": "main",
            "body": "", "diff": "+change",
            "review_comments": "", "reviews": "", "issue_comments": "",
        }
        mock_claude.return_value = ("Explanation text", "")
        mock_post.return_value = (False, "403 Forbidden")

        success, summary = run_explain(
            "owner", "repo", "42", "/path",
            notify_fn=MagicMock(),
            skill_dir=SKILL_DIR,
        )

        assert success is True
        assert "comment post failed" in summary

    @patch("app.rebase_pr.fetch_pr_context")
    @patch("app.claude_step.resolve_pr_location")
    def test_empty_diff(self, mock_resolve, mock_fetch):
        mock_resolve.return_value = ("owner", "repo")
        mock_fetch.return_value = {
            "title": "Empty PR",
            "author": "bob",
            "branch": "empty",
            "base": "main",
            "body": "",
            "diff": "",
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
        }

        success, summary = run_explain(
            "owner", "repo", "99", "/path",
            notify_fn=MagicMock(),
        )

        assert success is False
        assert "no diff" in summary.lower()

    @patch("app.claude_step.resolve_pr_location")
    def test_pr_not_found(self, mock_resolve):
        mock_resolve.side_effect = RuntimeError("PR not found on any remote")

        success, summary = run_explain(
            "owner", "repo", "999", "/path",
            notify_fn=MagicMock(),
        )

        assert success is False
        assert "not found" in summary.lower()

    @patch("skills.core.explain.explain_runner.time.sleep")
    @patch("app.rebase_pr.fetch_pr_context")
    @patch("app.claude_step.resolve_pr_location")
    @patch("skills.core.explain.explain_runner._run_claude_explain")
    def test_claude_error_with_retry(self, mock_claude, mock_resolve, mock_fetch, mock_sleep):
        """Non-transient errors fail without retry."""
        mock_resolve.return_value = ("owner", "repo")
        mock_fetch.return_value = {
            "title": "Fix",
            "author": "a",
            "branch": "b",
            "base": "main",
            "body": "",
            "diff": "+change",
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
        }
        mock_claude.return_value = ("", "invalid model")

        success, summary = run_explain(
            "owner", "repo", "42", "/path",
            notify_fn=MagicMock(),
            skill_dir=SKILL_DIR,
        )

        assert success is False
        assert "failed" in summary.lower()
        assert mock_claude.call_count == 1
        mock_sleep.assert_not_called()

    @patch("skills.core.explain.explain_runner._post_explanation_comment")
    @patch("skills.core.explain.explain_runner.time.sleep")
    @patch("app.rebase_pr.fetch_pr_context")
    @patch("app.claude_step.resolve_pr_location")
    @patch("skills.core.explain.explain_runner._run_claude_explain")
    def test_transient_error_retries_then_succeeds(
        self, mock_claude, mock_resolve, mock_fetch, mock_sleep, mock_post,
    ):
        mock_resolve.return_value = ("owner", "repo")
        mock_fetch.return_value = {
            "title": "Fix", "author": "a", "branch": "b", "base": "main",
            "body": "", "diff": "+change",
            "review_comments": "", "reviews": "", "issue_comments": "",
        }
        mock_claude.side_effect = [
            ("", "rate limit exceeded"),
            ("Explanation after retry", ""),
        ]
        mock_post.return_value = (True, "")

        success, summary = run_explain(
            "owner", "repo", "42", "/path",
            notify_fn=MagicMock(),
            skill_dir=SKILL_DIR,
        )

        assert success is True
        assert "Explanation after retry" in summary
        assert mock_claude.call_count == 2
        mock_sleep.assert_called_once()


class TestPostExplanationComment:
    """Test PR comment posting."""

    @patch("app.github.run_gh")
    @patch("app.github.find_bot_comment")
    def test_posts_new_comment(self, mock_find, mock_gh):
        from skills.core.explain.explain_runner import _post_explanation_comment

        mock_find.return_value = None

        ok, err = _post_explanation_comment("owner", "repo", "42", "Explanation text")

        assert ok is True
        mock_gh.assert_called_once()
        body_arg = str(mock_gh.call_args)
        assert _EXPLAIN_TAG in body_arg

    @patch("app.github.run_gh")
    @patch("app.github.find_bot_comment")
    def test_updates_existing_comment(self, mock_find, mock_gh):
        from skills.core.explain.explain_runner import _post_explanation_comment

        mock_find.return_value = {"id": 123, "body": "old", "user": "bot"}

        ok, err = _post_explanation_comment("owner", "repo", "42", "Updated explanation")

        assert ok is True
        assert "PATCH" in str(mock_gh.call_args)

    @patch("app.github.run_gh")
    @patch("app.github.find_bot_comment")
    def test_post_failure_returns_error(self, mock_find, mock_gh):
        from skills.core.explain.explain_runner import _post_explanation_comment

        mock_find.return_value = None
        mock_gh.side_effect = RuntimeError("403 Forbidden")

        ok, err = _post_explanation_comment("owner", "repo", "42", "text")

        assert ok is False
        assert "403" in err


class TestMain:
    """Test the CLI entry point."""

    @patch("skills.core.explain.explain_runner.run_explain")
    def test_main_success(self, mock_run):
        mock_run.return_value = (True, "Explanation done.")
        exit_code = main([
            "https://github.com/owner/repo/pull/42",
            "--project-path", "/path/to/project",
        ])
        assert exit_code == 0

    @patch("skills.core.explain.explain_runner.run_explain")
    def test_main_failure(self, mock_run):
        mock_run.return_value = (False, "Failed.")
        exit_code = main([
            "https://github.com/owner/repo/pull/42",
            "--project-path", "/path/to/project",
        ])
        assert exit_code == 1

    def test_main_invalid_url(self):
        exit_code = main([
            "not-a-url",
            "--project-path", "/path",
        ])
        assert exit_code == 1


class TestErrorHandling:
    """Test error handling for edge cases that caused silent failures."""

    @patch("skills.core.explain.explain_runner._post_explanation_comment")
    @patch("app.rebase_pr.fetch_pr_context")
    @patch("app.claude_step.resolve_pr_location")
    def test_notify_failure_does_not_crash(self, mock_resolve, mock_fetch, mock_post):
        mock_resolve.return_value = ("owner", "repo")
        mock_fetch.return_value = {
            "title": "Fix", "author": "a", "branch": "b", "base": "main",
            "body": "", "diff": "+change",
            "review_comments": "", "reviews": "", "issue_comments": "",
        }
        mock_post.return_value = (True, "")
        notify = MagicMock(side_effect=ConnectionError("telegram down"))

        with patch("skills.core.explain.explain_runner._run_claude_explain") as mock_claude:
            mock_claude.return_value = ("Explanation", "")
            success, summary = run_explain(
                "owner", "repo", "42", "/path",
                notify_fn=notify,
                skill_dir=SKILL_DIR,
            )

        assert success is True
        assert "Explanation" in summary

    @patch("app.rebase_pr.fetch_pr_context")
    @patch("app.claude_step.resolve_pr_location")
    def test_prompt_build_failure_returns_error(self, mock_resolve, mock_fetch):
        mock_resolve.return_value = ("owner", "repo")
        mock_fetch.return_value = {
            "title": "Fix", "author": "a", "branch": "b", "base": "main",
            "body": "", "diff": "+change",
            "review_comments": "", "reviews": "", "issue_comments": "",
        }

        with patch(
            "skills.core.explain.explain_runner._build_explain_prompt",
            side_effect=KeyError("missing_key"),
        ):
            success, summary = run_explain(
                "owner", "repo", "42", "/path",
                notify_fn=MagicMock(),
                skill_dir=SKILL_DIR,
            )

        assert success is False
        assert "prompt" in summary.lower()

    @patch("skills.core.explain.explain_runner.run_explain")
    def test_main_catches_unhandled_exception(self, mock_run):
        mock_run.side_effect = TypeError("unexpected error")

        exit_code = main([
            "https://github.com/owner/repo/pull/42",
            "--project-path", "/path",
        ])

        assert exit_code == 1

    @patch("app.rebase_pr.fetch_pr_context")
    @patch("app.claude_step.resolve_pr_location")
    @patch("skills.core.explain.explain_runner._run_claude_explain")
    def test_oserror_in_claude_cli_returns_error(self, mock_claude, mock_resolve, mock_fetch):
        mock_resolve.return_value = ("owner", "repo")
        mock_fetch.return_value = {
            "title": "Fix", "author": "a", "branch": "b", "base": "main",
            "body": "", "diff": "+change",
            "review_comments": "", "reviews": "", "issue_comments": "",
        }
        mock_claude.return_value = ("", "file not found")

        success, summary = run_explain(
            "owner", "repo", "42", "/path",
            notify_fn=MagicMock(),
            skill_dir=SKILL_DIR,
        )

        assert success is False
        assert "failed" in summary.lower()


class TestSkillDispatchIntegration:
    """Test that /explain is properly wired in skill_dispatch."""

    def test_explain_in_canonical_runners(self):
        from app.skill_dispatch import _CANONICAL_RUNNERS
        assert "explain" in _CANONICAL_RUNNERS

    def test_explain_alias_xp(self):
        from app.skill_dispatch import _COMMAND_ALIASES
        assert _COMMAND_ALIASES.get("xp") == "explain"

    def test_build_explain_command(self):
        from app.skill_dispatch import build_skill_command

        cmd = build_skill_command(
            command="explain",
            args="https://github.com/owner/repo/pull/42",
            project_name="myproject",
            project_path="/path/to/project",
            koan_root="/koan",
            instance_dir="/koan/instance",
        )

        assert cmd is not None
        assert "https://github.com/owner/repo/pull/42" in cmd
        assert "--project-path" in cmd
        assert "/path/to/project" in cmd
        assert "--project-name" in cmd
        assert "myproject" in cmd

    def test_build_explain_no_url_returns_none(self):
        from app.skill_dispatch import build_skill_command

        cmd = build_skill_command(
            command="explain",
            args="no url here",
            project_name="myproject",
            project_path="/path",
            koan_root="/koan",
            instance_dir="/koan/instance",
        )

        assert cmd is None

    def test_validate_explain_requires_pr_url(self):
        from app.skill_dispatch import validate_skill_args

        error = validate_skill_args("explain", "no url")
        assert error is not None
        assert "PR URL" in error

    def test_validate_explain_with_pr_url(self):
        from app.skill_dispatch import validate_skill_args

        error = validate_skill_args(
            "explain", "https://github.com/owner/repo/pull/42"
        )
        assert error is None

    def test_is_skill_mission(self):
        from app.skill_dispatch import is_skill_mission

        assert is_skill_mission("/explain https://github.com/o/r/pull/1")
        assert is_skill_mission("[project:koan] /explain https://github.com/o/r/pull/1")

    def test_parse_skill_mission(self):
        from app.skill_dispatch import parse_skill_mission

        project, cmd, args = parse_skill_mission(
            "[project:koan] /explain https://github.com/o/r/pull/1"
        )
        assert project == "koan"
        assert cmd == "explain"
        assert "https://github.com" in args


class TestMissionDedup:
    """Test that /explain and /xp are covered by mission dedup regex."""

    def test_explain_dedup(self):
        from app.missions import _extract_mission_signature

        sig = _extract_mission_signature(
            "/explain https://github.com/owner/repo/pull/42"
        )
        assert sig == "explain:https://github.com/owner/repo/pull/42"

    def test_xp_alias_dedup(self):
        from app.missions import _extract_mission_signature

        sig = _extract_mission_signature(
            "/xp https://github.com/owner/repo/pull/42"
        )
        assert sig == "xp:https://github.com/owner/repo/pull/42"
