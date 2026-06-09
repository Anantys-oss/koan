"""Tests for fix_runner.py — the fix execution pipeline."""

from pathlib import Path
from unittest.mock import patch, MagicMock

from skills.core.fix.fix_runner import (
    run_fix,
    _build_issue_body,
    _execute_fix,
    _build_prompt,
    _submit_fix_pr,
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
_PR_MODULE = "app.pr_submit"


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
    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan.atoomic/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_success_with_pr(self, mock_fetch, mock_execute, mock_branch, mock_pr):
        mock_fetch.return_value = _github_issue()
        notify = MagicMock()

        success, summary = run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=notify,
        )

        assert success is True
        assert "https://github.com/o/r/pull/1" in summary

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

    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value=None)
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan.atoomic/fix-issue-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_success_no_pr(self, mock_fetch, mock_execute, mock_branch, mock_pr):
        mock_fetch.return_value = _github_issue(body="Body text")
        notify = MagicMock()

        success, summary = run_fix(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            notify_fn=notify,
        )
        assert success is True
        assert "Branch: koan.atoomic/fix-issue-42" in summary

    @patch(f"{_FIX_MODULE}._execute_fix", return_value="")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_empty_claude_output(self, mock_fetch, mock_execute):
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

    @patch(f"{_FIX_MODULE}._submit_fix_pr", return_value="https://github.com/o/r/pull/1")
    @patch(f"{_FIX_MODULE}.get_current_branch", return_value="koan/fix-42")
    @patch(f"{_FIX_MODULE}._execute_fix", return_value="Done")
    @patch(f"{_FIX_MODULE}.fetch_issue")
    def test_explicit_project_name_reaches_tracker_and_memory(
        self, mock_fetch, mock_execute, mock_branch, mock_pr,
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
