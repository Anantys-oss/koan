"""Tests for app.refactor_pr — the standalone /refactor PR runner."""

from unittest.mock import MagicMock, patch

import pytest

from app.refactor_pr import _build_refactor_comment, main, run_refactor
from app.refactor_step import RefactorResult


_PR_CONTEXT = {
    "title": "Add feature",
    "body": "",
    "branch": "koan/feature",
    "base": "main",
    "state": "OPEN",
    "head_owner": "sukria",
}


def _run(context=None, refactor_result=None, gh_ok=True):
    """Invoke run_refactor with the external boundaries mocked."""
    ctx = dict(_PR_CONTEXT)
    if context:
        ctx.update(context)
    result = refactor_result if refactor_result is not None else RefactorResult(
        committed=True, pushed=True,
        bullets=["Simplified X"], tests="passing (OK)",
    )
    notify = MagicMock()
    run_gh = MagicMock() if gh_ok else MagicMock(side_effect=RuntimeError("no comment"))

    with patch("app.refactor_pr.resolve_pr_location", return_value=("owner", "repo")), \
         patch("app.refactor_pr.fetch_pr_context", return_value=ctx), \
         patch("app.refactor_pr._find_remote_for_repo", return_value="origin"), \
         patch("app.refactor_pr._get_current_branch", return_value="main"), \
         patch("app.refactor_pr._checkout_pr_branch", return_value="origin"), \
         patch("app.refactor_pr._safe_checkout"), \
         patch("app.refactor_pr.run_refactor_pass", return_value=result) as mock_pass, \
         patch("app.refactor_pr.run_gh", run_gh):
        success, summary = run_refactor(
            "owner", "repo", "42", "/proj", context="", notify_fn=notify,
        )
    return success, summary, run_gh, mock_pass


class TestRunRefactor:
    def test_success_posts_comment(self):
        success, summary, run_gh, _ = _run()
        assert success is True
        # a PR comment was posted
        comment_calls = [c for c in run_gh.call_args_list if c.args[:2] == ("pr", "comment")]
        assert len(comment_calls) == 1
        body = comment_calls[0].kwargs.get("body") or comment_calls[0].args[-1]
        assert "Refactor" in body

    def test_merged_pr_short_circuits(self):
        success, summary, run_gh, mock_pass = _run(context={"state": "MERGED"})
        assert success is True
        assert "merged" in summary.lower()
        mock_pass.assert_not_called()

    def test_no_changes_posts_short_note(self):
        success, summary, run_gh, _ = _run(
            refactor_result=RefactorResult(committed=False),
        )
        assert success is True
        assert "no refactoring" in summary.lower()
        # the "no changes" note is still posted
        assert any(c.args[:2] == ("pr", "comment") for c in run_gh.call_args_list)

    def test_push_rejected_fails(self):
        success, summary, _, _ = _run(
            refactor_result=RefactorResult(committed=True, pushed=False),
        )
        assert success is False
        assert "push" in summary.lower()

    def test_missing_branch_fails(self):
        success, summary, _, mock_pass = _run(context={"branch": ""})
        assert success is False
        mock_pass.assert_not_called()

    def test_comment_failure_is_nonfatal(self):
        success, summary, _, _ = _run(gh_ok=False)
        assert success is True

    def test_context_forwarded_to_pass(self):
        notify = MagicMock()
        with patch("app.refactor_pr.resolve_pr_location", return_value=("owner", "repo")), \
             patch("app.refactor_pr.fetch_pr_context", return_value=dict(_PR_CONTEXT)), \
             patch("app.refactor_pr._find_remote_for_repo", return_value="origin"), \
             patch("app.refactor_pr._get_current_branch", return_value="main"), \
             patch("app.refactor_pr._checkout_pr_branch", return_value="origin"), \
             patch("app.refactor_pr._safe_checkout"), \
             patch("app.refactor_pr.run_gh"), \
             patch("app.refactor_pr.run_refactor_pass",
                   return_value=RefactorResult(committed=True, pushed=True)) as mock_pass:
            run_refactor("owner", "repo", "42", "/proj",
                         context="focus on the tests", notify_fn=notify)

        assert mock_pass.call_args.kwargs["context"] == "focus on the tests"


class TestBuildRefactorComment:
    def test_includes_bullets_and_tests(self):
        result = RefactorResult(
            committed=True, pushed=True,
            bullets=["a", "b"], tests="passing",
        )
        body = _build_refactor_comment("koan/x", result)
        assert "- a" in body and "- b" in body
        assert "passing" in body
        assert "Kōan" in body

    def test_falls_back_when_no_bullets(self):
        body = _build_refactor_comment("koan/x", RefactorResult(committed=True))
        assert "What changed" in body


class TestMain:
    def test_invalid_url_returns_1(self, capsys):
        rc = main(["not-a-url", "--project-path", "/proj"])
        assert rc == 1

    def test_valid_url_dispatches(self):
        with patch("app.refactor_pr.run_refactor",
                   return_value=(True, "done")) as mock_run:
            rc = main([
                "https://github.com/owner/repo/pull/42",
                "--project-path", "/proj",
                "--context", "focus on tests",
            ])
        assert rc == 0
        assert mock_run.call_args.kwargs["context"] == "focus on tests"
