"""Tests for fix_runner.py — the fix execution pipeline."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from skills.core.fix.fix_runner import (
    run_fix,
    _build_issue_body,
    _build_branch_section,
    _execute_fix,
    _build_prompt,
    _submit_fix_pr,
    _get_existing_koan_branch,
    _extract_skip_diagnose,
    main,
)
from app.issue_tracker.types import IssueContent, IssueRef
from app.issue_tracker import UnresolvedJiraProjectError

# Shared helpers imported via app.pr_submit
from app.pr_submit import (
    get_current_branch,
    get_commit_subjects,
    get_fork_owner,
    guess_project_name,
    resolve_submit_target,
)


_FIX_MODULE = "skills.core.fix.fix_runner"
_DIAG_MODULE = "skills.core.fix.fix_diagnose"
_PR_MODULE = "app.pr_submit"

_MOCK_DIAGNOSTIC = {
    "confidence": "HIGH", "hypothesis": "Test hypothesis",
    "code_paths": "", "analysis": "", "raw": "",
}



def _github_issue(
    title="Bug title", body="Bug body", comments=None,
    state="open", key="42", repo="o/r",
):
    """Build an IssueContent as the tracker's fetch_issue would return it."""
    ref = IssueRef(
        provider="github",
        url="https://github.com/o/r/issues/42",
        key=key,
        repo=repo,
    )
    return IssueContent(
        ref=ref, title=title, body=body,
        comments=comments or [], state=state,
    )


# ---------------------------------------------------------------------------
# _build_issue_body
# ---------------------------------------------------------------------------

class TestBuildIssueBody:
    def test_body_only(self):
        result = _build_issue_body("Bug description", [])
        assert result == "Bug description"

    def test_body_with_comments(self):
        comments = [
            {"body": "I can reproduce this on v2.1", "author": "user1"},
            {"body": "Same issue here with screenshots", "author": "user2"},
        ]
        result = _build_issue_body("Bug description", comments)
        assert "Bug description" in result
        assert "user1" in result
        assert "I can reproduce this" in result

    def test_skips_bot_comments(self):
        comments = [
            {"body": "This is an automated message from CI", "author": "github-actions[bot]"},
        ]
        result = _build_issue_body("Bug", comments)
        assert "[bot]" not in result

    def test_skips_short_comments(self):
        comments = [
            {"body": "+1", "author": "user1"},
            {"body": "me too", "author": "user2"},
        ]
        result = _build_issue_body("Bug", comments)
        # Short comments (< 20 chars) are filtered
        assert "user1" not in result
        assert "user2" not in result

    def test_empty_body(self):
        result = _build_issue_body("", [])
        assert result == ""

    def test_none_body_equivalent(self):
        result = _build_issue_body("", [{"body": "This is a useful comment with detail", "author": "user1"}])
        assert "user1" in result


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_with_skill_dir(self):
        skill_dir = Path(__file__).resolve().parent.parent / "skills" / "core" / "fix"
        prompt = _build_prompt(
            issue_url="https://github.com/o/r/issues/1",
            issue_title="Bug title",
            issue_body="Bug description",
            context="backend only",
            skill_dir=skill_dir,
            branch_prefix="koan.atoomic/",
            issue_number="1",
        )
        assert "Bug title" in prompt
        assert "Bug description" in prompt
        assert "backend only" in prompt
        assert "koan.atoomic/" in prompt

    def test_placeholders_replaced(self):
        skill_dir = Path(__file__).resolve().parent.parent / "skills" / "core" / "fix"
        prompt = _build_prompt(
            issue_url="https://github.com/o/r/issues/42",
            issue_title="Test title",
            issue_body="Test body",
            context="Test context",
            skill_dir=skill_dir,
            issue_number="42",
        )
        # Verify no unreplaced placeholders
        assert "{ISSUE_URL}" not in prompt
        assert "{ISSUE_TITLE}" not in prompt
        assert "{ISSUE_BODY}" not in prompt
        assert "{CONTEXT}" not in prompt

    def test_prompt_includes_pr_creation_phase(self):
        """fix.md must instruct Claude to push the branch and create a draft PR."""
        skill_dir = Path(__file__).resolve().parent.parent / "skills" / "core" / "fix"
        prompt = _build_prompt(
            issue_url="https://github.com/o/r/issues/42",
            issue_title="Test",
            issue_body="Body",
            context="ctx",
            skill_dir=skill_dir,
            issue_number="42",
        )
        assert "Submit Pull Request" in prompt
        assert "gh pr create --draft" in prompt
        assert "git push" in prompt
        assert "Closes https://github.com/o/r/issues/42" in prompt
        assert "{KOAN_PYTHON}" not in prompt
        assert " -m app.issue_cli" in prompt


class TestExecuteFix:
    def test_uses_mission_model_key(self):
        with patch(f"{_FIX_MODULE}._build_prompt", return_value="prompt"), \
             patch("app.skill_memory.build_memory_block_for_skill", return_value=""), \
             patch("app.cli_provider.run_command_streaming",
                   return_value="ok") as mock_run:
            result = _execute_fix(
                project_path="/project",
                issue_url="https://github.com/o/r/issues/42",
                issue_title="Bug",
                issue_body="Body",
                context="Fix it",
            )
        assert result == "ok"
        assert mock_run.call_args.kwargs["model_key"] == "mission"


# ---------------------------------------------------------------------------
# guess_project_name (shared via app.pr_submit)
# ---------------------------------------------------------------------------

class TestGuessProjectName:
    def test_simple_path(self):
        assert guess_project_name("/home/user/workspace/investmindr") == "investmindr"

    def test_nested_path(self):
        assert guess_project_name("/Users/atoobot/workspace/anantys/investmindr") == "investmindr"


# ---------------------------------------------------------------------------
# get_current_branch (shared via app.pr_submit)
# ---------------------------------------------------------------------------

class TestGetCurrentBranch:
    @patch(f"{_PR_MODULE}._git_get_current_branch", return_value="koan.atoomic/fix-issue-42")
    def test_returns_branch(self, mock_git):
        assert get_current_branch("/path") == "koan.atoomic/fix-issue-42"
        mock_git.assert_called_once_with(cwd="/path")

    @patch(f"{_PR_MODULE}._git_get_current_branch", return_value="main")
    def test_fallback_on_error(self, mock_git):
        assert get_current_branch("/path") == "main"


# ---------------------------------------------------------------------------
# get_commit_subjects (shared via app.pr_submit)
# ---------------------------------------------------------------------------

class TestGetCommitSubjects:
    @patch(f"{_PR_MODULE}._git_get_commit_subjects", return_value=["Fix auth bug", "Add test"])
    def test_returns_subjects(self, mock_git):
        subjects = get_commit_subjects("/path")
        assert subjects == ["Fix auth bug", "Add test"]

    @patch(f"{_PR_MODULE}._git_get_commit_subjects", return_value=[])
    def test_empty_on_no_commits(self, mock_git):
        assert get_commit_subjects("/path") == []

    @patch(f"{_PR_MODULE}._git_get_commit_subjects", return_value=[])
    def test_empty_on_error(self, mock_git):
        assert get_commit_subjects("/path") == []


# ---------------------------------------------------------------------------
# get_fork_owner (shared via app.pr_submit)
# ---------------------------------------------------------------------------

class TestGetForkOwner:
    @patch(f"{_PR_MODULE}.run_gh", return_value="atoomic\n")
    def test_returns_owner(self, mock_gh):
        assert get_fork_owner("/path") == "atoomic"

    @patch(f"{_PR_MODULE}.run_gh", side_effect=RuntimeError("fail"))
    def test_empty_on_error(self, mock_gh):
        assert get_fork_owner("/path") == ""


# ---------------------------------------------------------------------------
# resolve_submit_target (shared via app.pr_submit)
# ---------------------------------------------------------------------------

class TestResolveSubmitTarget:
    @patch(f"{_PR_MODULE}.resolve_target_repo", return_value=None)
    @patch.dict("os.environ", {"KOAN_ROOT": ""}, clear=False)
    def test_fallback_to_issue_repo(self, mock_resolve):
        result = resolve_submit_target("/path", "proj", "my-org", "my-toolkit")
        assert result == {"repo": "my-org/my-toolkit", "is_fork": False}

    @patch(f"{_PR_MODULE}.resolve_target_repo", return_value="upstream/repo")
    @patch.dict("os.environ", {"KOAN_ROOT": ""}, clear=False)
    def test_fork_detected(self, mock_resolve):
        result = resolve_submit_target("/path", "proj", "o", "r")
        assert result == {"repo": "upstream/repo", "is_fork": True}


# ---------------------------------------------------------------------------
# run_fix
# ---------------------------------------------------------------------------

class TestRunFix:
    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value=_MOCK_DIAGNOSTIC)
    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan.atoomic/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_success_with_pr(self, mock_fetch, mock_execute, mock_branch, mock_pr, _d1, _d2):
        mock_fetch.return_value = _github_issue()
        notify = MagicMock()

        success, summary = run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=notify,
        )

        assert success is True
        assert "https://github.com/o/r/pull/1" in summary

    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value=_MOCK_DIAGNOSTIC)
    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_private_gate_runs_after_pr_creation(
        self, mock_fetch, mock_execute, mock_branch, mock_pr, _d1, _d2, tmp_path,
    ):
        mock_fetch.return_value = _github_issue()
        gate_result = SimpleNamespace(
            ran=True,
            summary="Private review gate passed",
        )
        notify = MagicMock()

        with patch(
            "app.private_review_gate.run_private_review_gate",
            return_value=gate_result,
        ) as mock_gate:
            success, summary = run_fix(
                project_path=str(tmp_path),
                issue_url="https://github.com/o/r/issues/42",
                notify_fn=notify,
                project_name="app",
            )

        assert success is True
        assert "Private gate: Private review gate passed" in summary
        mock_gate.assert_called_once()
        assert mock_gate.call_args.kwargs["pr_url"] == "https://github.com/o/r/pull/1"
        assert mock_gate.call_args.kwargs["skill_origin"] == "fix"

    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value=_MOCK_DIAGNOSTIC)
    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_private_gate_failure_does_not_fail_fix(
        self, mock_fetch, mock_execute, mock_branch, mock_pr, _d1, _d2, tmp_path,
    ):
        mock_fetch.return_value = _github_issue()
        gate_result = SimpleNamespace(
            ran=True,
            summary="Private review gate could not produce a fix: no changes",
            clean=False,
        )

        with patch(
            "app.private_review_gate.run_private_review_gate",
            return_value=gate_result,
        ):
            success, summary = run_fix(
                project_path=str(tmp_path),
                issue_url="https://github.com/o/r/issues/42",
                notify_fn=MagicMock(),
                project_name="app",
            )

        assert success is True
        assert "Draft PR: https://github.com/o/r/pull/1" in summary
        assert "Private review gate could not produce a fix" in summary

    @patch(f"{_FIX_MODULE}.fetch_issue", side_effect=ValueError("bad url"))
    def test_invalid_url(self, mock_fetch):
        notify = MagicMock()
        success, summary = run_fix(
            project_path="/path",
            issue_url="not-a-url",
            notify_fn=notify,
        )
        assert success is False

    @patch(
        f"{_FIX_MODULE}.fetch_issue",
        side_effect=UnresolvedJiraProjectError(
            "Unmapped Jira issue 'PROJ-42': no Koan project was resolved. "
            "Add this mapping in projects.yaml under projects.<name>.issue_tracker "
            "with provider: jira and jira_project: PROJ.",
        ),
    )
    def test_unmapped_jira_project_notifies_and_fails(self, _mock_fetch):
        notify = MagicMock()
        success, summary = run_fix(
            project_path="/path",
            issue_url="https://org.atlassian.net/browse/PROJ-42",
            notify_fn=notify,
        )
        assert success is False
        assert "projects.yaml" in summary
        assert "PROJ-42" in summary
        notify.assert_called_once()

    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_empty_issue(self, mock_fetch):
        mock_fetch.return_value = _github_issue(body="", comments=[])
        notify = MagicMock()

        success, summary = run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=notify,
        )
        assert success is False
        assert "no content" in summary.lower()

    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value=_MOCK_DIAGNOSTIC)
    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value=None)
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan.atoomic/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_success_no_pr(self, mock_fetch, mock_execute, mock_branch, mock_pr, _d1, _d2):
        mock_fetch.return_value = _github_issue(body="Body text")
        notify = MagicMock()

        success, summary = run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=notify,
        )
        assert success is True
        assert "Branch: koan.atoomic/fix-issue-42" in summary

    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value=_MOCK_DIAGNOSTIC)
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_empty_claude_output(self, mock_fetch, mock_execute, _d1, _d2):
        mock_fetch.return_value = _github_issue(body="Body")
        notify = MagicMock()

        success, summary = run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=notify,
        )
        assert success is False
        assert "empty output" in summary.lower()

    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_closed_issue_skipped(self, mock_fetch):
        """A closed issue should be skipped immediately without invoking Claude."""
        mock_fetch.return_value = _github_issue(state="closed")
        notify = MagicMock()

        success, summary = run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=notify,
        )

        assert success is True
        assert "already closed" in summary.lower()
        # Verify notification was sent with skip icon
        notify.assert_called_once()
        notification_text = notify.call_args[0][0]
        assert "already closed" in notification_text.lower()
        assert "⏭" in notification_text

    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value=_MOCK_DIAGNOSTIC)
    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_explicit_project_name_reaches_tracker_and_memory(
        self, mock_fetch, mock_execute, mock_branch, mock_pr, _d1, _d2,
    ):
        mock_fetch.return_value = _github_issue()

        run_fix(
            project_path="/workspace/webpros-shield",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=MagicMock(),
            project_name="webpros-shield",
            instance_dir="/koan/instance",
        )

        assert mock_fetch.call_args.kwargs["project_name"] == "webpros-shield"
        assert mock_execute.call_args.kwargs["project_name"] == "webpros-shield"
        assert mock_execute.call_args.kwargs["instance_dir"] == "/koan/instance"
        assert mock_pr.call_args.kwargs["project_name"] == "webpros-shield"


# ---------------------------------------------------------------------------
# main (CLI entry point)
# ---------------------------------------------------------------------------

class TestMain:
    @patch(f"{_FIX_MODULE}.run_fix", return_value=(True, "Fix complete"))
    def test_success_exit_code(self, mock_run):
        result = main(["--project-path", "/path", "--issue-url", "https://github.com/o/r/issues/1"])
        assert result == 0

    @patch(f"{_FIX_MODULE}.run_fix", return_value=(False, "Failed"))
    def test_failure_exit_code(self, mock_run):
        result = main(["--project-path", "/path", "--issue-url", "https://github.com/o/r/issues/1"])
        assert result == 1

    @patch(f"{_FIX_MODULE}.run_fix", return_value=(True, "Done"))
    def test_context_passed(self, mock_run):
        main([
            "--project-path", "/path",
            "--issue-url", "https://github.com/o/r/issues/1",
            "--context", "backend only",
        ])
        _, kwargs = mock_run.call_args
        assert kwargs.get("context") == "backend only" or mock_run.call_args[0][2] == "backend only"

    @patch(f"{_FIX_MODULE}.run_fix", return_value=(True, "Done"))
    def test_project_identity_args_passed(self, mock_run):
        main([
            "--project-path", "/path",
            "--issue-url", "https://github.com/o/r/issues/1",
            "--project-name", "webpros-shield",
            "--instance-dir", "/koan/instance",
        ])
        _, kwargs = mock_run.call_args
        assert kwargs["project_name"] == "webpros-shield"
        assert kwargs["instance_dir"] == "/koan/instance"

    @patch(f"{_FIX_MODULE}.run_fix", return_value=(True, "Done"))
    def test_base_branch_passed(self, mock_run):
        main([
            "--project-path", "/path",
            "--issue-url", "https://github.com/o/r/issues/1",
            "--base-branch", "main",
        ])
        _, kwargs = mock_run.call_args
        assert kwargs["base_branch"] == "main"


# ---------------------------------------------------------------------------
# _build_branch_section
# ---------------------------------------------------------------------------

class TestBuildBranchSection:
    def test_new_issue_contains_branch_name(self):
        section = _build_branch_section(
            branch_prefix="koan/",
            issue_number="42",
            base_branch="main",
        )
        assert "koan/fix-issue-42" in section
        assert "main" in section
        assert "Never commit on" in section

    def test_existing_branch_instructs_checkout(self):
        section = _build_branch_section(
            branch_prefix="koan/",
            issue_number="42",
            base_branch="main",
            existing_branch="koan/fix-issue-42",
        )
        assert "git checkout koan/fix-issue-42" in section
        assert "git push origin koan/fix-issue-42" in section
        assert "Skip Phase 7" in section
        assert "PR already exists" in section

    def test_existing_branch_no_new_branch_creation_instruction(self):
        section = _build_branch_section(
            branch_prefix="koan/",
            issue_number="42",
            base_branch="main",
            existing_branch="koan/fix-issue-42",
        )
        # Should NOT tell Claude to create a new branch or use "Branch naming:"
        assert "Branch naming:" not in section
        assert "do not create a new one" in section.lower()


# ---------------------------------------------------------------------------
# _get_existing_koan_branch
# ---------------------------------------------------------------------------

class TestGetExistingKoanBranch:
    def test_issue_url_returns_none(self):
        result = _get_existing_koan_branch("https://github.com/o/r/issues/42")
        assert result is None

    def test_non_koan_pr_returns_none(self):
        with patch("skills.core.fix.fix_runner.parse_pr_url", return_value=("o", "r", "1")), \
             patch("app.github_skill_helpers.is_own_pr", return_value=(False, "main")):
            result = _get_existing_koan_branch("https://github.com/o/r/pull/1")
        assert result is None

    def test_koan_pr_returns_branch(self):
        with patch("skills.core.fix.fix_runner.parse_pr_url", return_value=("o", "r", "1")), \
             patch("skills.core.fix.fix_runner._get_existing_koan_branch.__wrapped__",
                   create=True), \
             patch("app.github_skill_helpers.is_own_pr",
                   return_value=(True, "koan/fix-issue-1")):
            result = _get_existing_koan_branch("https://github.com/o/r/pull/1")
        assert result == "koan/fix-issue-1"

    def test_gh_error_returns_none(self):
        with patch("skills.core.fix.fix_runner.parse_pr_url", return_value=("o", "r", "1")), \
             patch("app.github_skill_helpers.is_own_pr",
                   side_effect=RuntimeError("gh failed")):
            result = _get_existing_koan_branch("https://github.com/o/r/pull/1")
        assert result is None


# ---------------------------------------------------------------------------
# run_fix — in-place PR fix path
# ---------------------------------------------------------------------------

class TestRunFixInPlace:
    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value=_MOCK_DIAGNOSTIC)
    @patch(f"{_FIX_MODULE}._get_existing_koan_branch", return_value="koan/fix-issue-99")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-issue-99")
    @patch(f"{_FIX_MODULE}._submit_fix_pr")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_in_place_skips_pr_creation(
        self, mock_fetch, mock_execute, mock_submit, mock_branch, mock_existing, _d1, _d2,
    ):
        """When fixing an existing koan PR, _submit_fix_pr must NOT be called."""
        mock_fetch.return_value = _github_issue(body="Some content")
        notify = MagicMock()

        success, summary = run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/pull/99",
            notify_fn=notify,
        )

        assert success is True
        mock_submit.assert_not_called()
        assert "koan/fix-issue-99" in summary

    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value=_MOCK_DIAGNOSTIC)
    @patch(f"{_FIX_MODULE}._get_existing_koan_branch", return_value="koan/fix-issue-99")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-issue-99")
    @patch(f"{_FIX_MODULE}._submit_fix_pr")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_in_place_passes_existing_branch_to_execute(
        self, mock_fetch, mock_execute, mock_submit, mock_branch, mock_existing, _d1, _d2,
    ):
        """existing_branch must be forwarded to _execute_fix."""
        mock_fetch.return_value = _github_issue(body="Some content")

        run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/pull/99",
            notify_fn=MagicMock(),
        )

        assert mock_execute.call_args.kwargs["existing_branch"] == "koan/fix-issue-99"

    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value=_MOCK_DIAGNOSTIC)
    @patch(f"{_FIX_MODULE}._get_existing_koan_branch", return_value=None)
    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_non_koan_pr_creates_new_pr(
        self, mock_fetch, mock_execute, mock_branch, mock_submit, mock_existing, _d1, _d2,
    ):
        """When the PR is not koan-owned, the normal PR creation path runs."""
        mock_fetch.return_value = _github_issue(body="Some content")
        notify = MagicMock()

        success, summary = run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=notify,
        )

        assert success is True
        mock_submit.assert_called_once()
        assert "pull/1" in summary


# ---------------------------------------------------------------------------
# _build_prompt — existing_branch propagated
# ---------------------------------------------------------------------------

class TestBuildPromptExistingBranch:
    def test_existing_branch_in_prompt(self):
        skill_dir = Path(__file__).resolve().parent.parent / "skills" / "core" / "fix"
        prompt = _build_prompt(
            issue_url="https://github.com/o/r/pull/99",
            issue_title="PR title",
            issue_body="PR body",
            context="fix the thing",
            skill_dir=skill_dir,
            issue_number="99",
            existing_branch="koan/fix-issue-99",
        )
        assert "koan/fix-issue-99" in prompt
        assert "git checkout koan/fix-issue-99" in prompt
        assert "Skip Phase 7" in prompt

    def test_no_existing_branch_uses_new_branch(self):
        skill_dir = Path(__file__).resolve().parent.parent / "skills" / "core" / "fix"
        prompt = _build_prompt(
            issue_url="https://github.com/o/r/issues/42",
            issue_title="Issue title",
            issue_body="Issue body",
            context="fix it",
            skill_dir=skill_dir,
            branch_prefix="koan/",
            issue_number="42",
        )
        assert "koan/fix-issue-42" in prompt
        assert "Never commit on" in prompt


# ---------------------------------------------------------------------------
# _extract_skip_diagnose
# ---------------------------------------------------------------------------

class TestExtractSkipDiagnose:
    def test_extracts_flag(self):
        skip, ctx = _extract_skip_diagnose("--skip-diagnose focus on backend")
        assert skip is True
        assert ctx == "focus on backend"

    def test_no_flag(self):
        skip, ctx = _extract_skip_diagnose("focus on backend")
        assert skip is False
        assert ctx == "focus on backend"

    def test_empty_context(self):
        skip, ctx = _extract_skip_diagnose("")
        assert skip is False
        assert ctx == ""

    def test_none_context(self):
        skip, ctx = _extract_skip_diagnose(None)
        assert skip is False
        assert ctx == ""

    def test_flag_only(self):
        skip, ctx = _extract_skip_diagnose("--skip-diagnose")
        assert skip is True
        assert ctx == ""


# ---------------------------------------------------------------------------
# run_fix — diagnostic integration
# ---------------------------------------------------------------------------

class TestRunFixDiagnostic:
    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="## Diagnostic\nHigh confidence")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value={
        "confidence": "HIGH", "hypothesis": "Bug in X",
        "code_paths": "a.py:1", "analysis": "Root cause", "raw": "",
    })
    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_diagnostic_called_before_fix(
        self, mock_fetch, mock_execute, mock_branch, mock_pr, mock_diag, mock_format,
    ):
        mock_fetch.return_value = _github_issue()
        run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=MagicMock(),
        )
        mock_diag.assert_called_once()
        mock_execute.assert_called_once()
        assert mock_execute.call_args.kwargs["diagnostic"] == "## Diagnostic\nHigh confidence"

    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_skip_diagnose_bypasses_diagnostic(
        self, mock_fetch, mock_execute, mock_branch, mock_pr,
    ):
        mock_fetch.return_value = _github_issue()
        with patch(f"{_DIAG_MODULE}.run_diagnostic") as mock_diag:
            run_fix(
                project_path="/path",
                issue_url="https://github.com/o/r/issues/42",
                context="--skip-diagnose focus on backend",
                notify_fn=MagicMock(),
            )
            mock_diag.assert_not_called()
        assert "--skip-diagnose" not in (mock_execute.call_args.kwargs.get("context", ""))

    @patch(f"{_DIAG_MODULE}.format_diagnostic_context", return_value="")
    @patch(f"{_DIAG_MODULE}.run_diagnostic", return_value={
        "confidence": "LOW", "hypothesis": "", "code_paths": "",
        "analysis": "", "raw": "unstructured", "error": "",
    })
    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_low_confidence_emits_warning(
        self, mock_fetch, mock_execute, mock_branch, mock_pr, mock_diag, mock_format,
    ):
        mock_fetch.return_value = _github_issue()
        notify = MagicMock()
        run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=notify,
        )
        warning_calls = [
            c for c in notify.call_args_list
            if "low-confidence" in c[0][0].lower() or "Low-confidence" in c[0][0]
        ]
        assert len(warning_calls) == 1
        mock_execute.assert_called_once()
