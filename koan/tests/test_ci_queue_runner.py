"""Tests for ci_queue_runner — CI queue drain, error handling, and fix pipeline."""

import json
from unittest.mock import MagicMock, patch

from app.claude_step import CI_STATUS_BLOCKED_APPROVAL

import pytest


PR_URL = "https://github.com/owner/repo/pull/42"
PROJECT_PATH = "/tmp/test-project"


@pytest.fixture
def _mock_pr_context():
    """Patch external dependencies so run_ci_check_and_fix can run without real git/GitHub."""
    fake_context = {"branch": "fix-branch", "base": "main", "url": PR_URL}
    with (
        patch("app.rebase_pr.fetch_pr_context", return_value=fake_context),
        patch("app.ci_queue_runner.check_ci_status", return_value=("failure", 123)),
        patch("app.claude_step._fetch_failed_logs", return_value="Error: test failed"),
        patch("app.rebase_pr._check_pr_state", return_value=("OPEN", "MERGEABLE")),
        patch("app.claude_step._get_current_branch", return_value="main"),
        patch("app.claude_step._run_git"),
        patch("app.claude_step._safe_checkout"),
        patch("app.claude_step._fetch_branch"),
        patch("app.rebase_pr._find_remote_for_repo", return_value="origin"),
        patch("app.git_utils.ordered_remotes", return_value=["origin"]),
    ):
        yield


class TestRunCiCheckAndFixErrorHandling:
    """Verify that exceptions in the fix pipeline are caught, not propagated."""

    @pytest.mark.usefixtures("_mock_pr_context")
    def test_exception_in_fix_returns_failure_tuple(self):
        """When _attempt_ci_fixes raises, run_ci_check_and_fix returns (False, summary)."""
        from app.ci_queue_runner import run_ci_check_and_fix

        with patch(
            "app.ci_queue_runner._attempt_ci_fixes",
            side_effect=RuntimeError("Claude crashed"),
        ):
            success, summary = run_ci_check_and_fix(PR_URL, PROJECT_PATH)

        assert success is False
        assert "Claude crashed" in summary

    @pytest.mark.usefixtures("_mock_pr_context")
    def test_exception_in_fix_still_restores_branch(self):
        """After a crash, _safe_checkout is still called to restore the original branch."""
        from app.ci_queue_runner import run_ci_check_and_fix

        with (
            patch(
                "app.ci_queue_runner._attempt_ci_fixes",
                side_effect=RuntimeError("boom"),
            ),
            patch("app.claude_step._safe_checkout") as mock_checkout,
        ):
            run_ci_check_and_fix(PR_URL, PROJECT_PATH)

        mock_checkout.assert_called_once_with("main", PROJECT_PATH)

    def test_ci_already_passing_returns_success(self):
        """If CI is already passing, return success without attempting fixes."""
        from app.ci_queue_runner import run_ci_check_and_fix

        fake_context = {"branch": "fix-branch", "base": "main"}
        with (
            patch("app.rebase_pr.fetch_pr_context", return_value=fake_context),
            patch("app.ci_queue_runner.check_ci_status", return_value=("success", 123)),
        ):
            success, summary = run_ci_check_and_fix(PR_URL, PROJECT_PATH)

        assert success is True
        assert "already passing" in summary

    def test_ci_pending_returns_early(self):
        """If CI is still pending, return early without attempting fixes."""
        from app.ci_queue_runner import run_ci_check_and_fix

        fake_context = {"branch": "fix-branch", "base": "main"}
        with (
            patch("app.rebase_pr.fetch_pr_context", return_value=fake_context),
            patch("app.ci_queue_runner.check_ci_status", return_value=("pending", 123)),
        ):
            success, summary = run_ci_check_and_fix(PR_URL, PROJECT_PATH)

        assert success is False
        assert "pending" in summary.lower()

    def test_pr_already_merged_returns_success(self):
        """If PR is already merged, skip CI fix."""
        from app.ci_queue_runner import run_ci_check_and_fix

        fake_context = {"branch": "fix-branch", "base": "main"}
        with (
            patch("app.rebase_pr.fetch_pr_context", return_value=fake_context),
            patch("app.ci_queue_runner.check_ci_status", return_value=("failure", 123)),
            patch("app.claude_step._fetch_failed_logs", return_value="Error: test failed"),
            patch("app.rebase_pr._check_pr_state", return_value=("MERGED", "UNKNOWN")),
        ):
            success, summary = run_ci_check_and_fix(PR_URL, PROJECT_PATH)

        assert success is True
        assert "merged" in summary.lower()

    def test_pr_with_conflicts_returns_failure(self):
        """If PR has merge conflicts, skip CI fix."""
        from app.ci_queue_runner import run_ci_check_and_fix

        fake_context = {"branch": "fix-branch", "base": "main"}
        with (
            patch("app.rebase_pr.fetch_pr_context", return_value=fake_context),
            patch("app.ci_queue_runner.check_ci_status", return_value=("failure", 123)),
            patch("app.claude_step._fetch_failed_logs", return_value="Error: test failed"),
            patch("app.rebase_pr._check_pr_state", return_value=("OPEN", "CONFLICTING")),
        ):
            success, summary = run_ci_check_and_fix(PR_URL, PROJECT_PATH)

        assert success is False
        assert "conflicts" in summary.lower()


class TestMainErrorHandling:
    """Verify that main() always produces JSON on stdout, even when run_ci_check_and_fix crashes."""

    def test_main_outputs_json_on_crash(self, capsys):
        """When run_ci_check_and_fix raises, main() still prints JSON to stdout."""
        from app.ci_queue_runner import main

        with patch(
            "app.ci_queue_runner.run_ci_check_and_fix",
            side_effect=RuntimeError("unexpected failure"),
        ):
            exit_code = main([PR_URL, "--project-path", PROJECT_PATH])

        assert exit_code == 1
        stdout = capsys.readouterr().out
        result = json.loads(stdout)
        assert result["success"] is False
        assert "unexpected failure" in result["summary"]

    def test_main_outputs_json_on_success(self, capsys):
        """Normal success path still produces JSON."""
        from app.ci_queue_runner import main

        with patch(
            "app.ci_queue_runner.run_ci_check_and_fix",
            return_value=(True, "CI passed"),
        ):
            exit_code = main([PR_URL, "--project-path", PROJECT_PATH])

        assert exit_code == 0
        stdout = capsys.readouterr().out
        result = json.loads(stdout)
        assert result["success"] is True


class TestDrainOneErrorHandling:
    """Verify drain_one handles CI status results correctly."""

    def _missions_with_ci_entry(self, attempt=0, max_attempts=5):
        """Return missions.md content with one CI entry."""
        return (
            "# Missions\n\n## CI\n\n"
            f"- [project:proj] {PR_URL} branch:fix-branch repo:owner/repo"
            f" queued:2026-04-01T10:00 (attempt {attempt}/{max_attempts})\n\n"
            "## Pending\n\n## Done\n"
        )

    def test_drain_one_no_entries(self):
        """When ## CI section is empty, drain_one returns None."""
        from app.ci_queue_runner import drain_one

        empty_missions = "# Missions\n\n## CI\n\n## Pending\n\n## Done\n"
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=empty_missions),
            patch("app.ci_queue_runner._maybe_migrate_json_queue"),
        ):
            result = drain_one("/tmp/instance")

        assert result is None

    def test_drain_one_success_removes_entry(self):
        """On CI success, entry is removed from ## CI section."""
        from app.ci_queue_runner import drain_one

        missions_content = self._missions_with_ci_entry()
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=missions_content),
            patch("app.ci_queue_runner._maybe_migrate_json_queue"),
            patch("app.utils.modify_missions_file") as mock_modify,
            patch("app.ci_queue_runner.check_ci_status", return_value=("success", 123)),
            patch("app.ci_queue_runner._write_outbox"),
        ):
            result = drain_one("/tmp/instance")

        assert result is not None
        assert "passed" in result.lower()
        mock_modify.assert_called()

    def test_drain_one_failure_injects_mission(self):
        """On CI failure under max attempts, a /ci_check mission is injected."""
        from app.ci_queue_runner import drain_one

        missions_content = self._missions_with_ci_entry(attempt=0, max_attempts=5)
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=missions_content),
            patch("app.ci_queue_runner._maybe_migrate_json_queue"),
            patch("app.utils.modify_missions_file"),
            patch("app.ci_queue_runner.check_ci_status", return_value=("failure", 456)),
            patch("app.ci_queue_runner._inject_ci_fix_mission") as mock_inject,
        ):
            result = drain_one("/tmp/instance")

        assert result is not None
        assert "failed" in result.lower()
        mock_inject.assert_called_once()

    def test_drain_one_failure_at_max_gives_up(self):
        """On CI failure at max attempts, entry is removed and failure notified."""
        from app.ci_queue_runner import drain_one

        missions_content = self._missions_with_ci_entry(attempt=5, max_attempts=5)
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=missions_content),
            patch("app.ci_queue_runner._maybe_migrate_json_queue"),
            patch("app.utils.modify_missions_file") as mock_modify,
            patch("app.ci_queue_runner.check_ci_status", return_value=("failure", 456)),
            patch("app.ci_queue_runner._write_outbox") as mock_outbox,
        ):
            result = drain_one("/tmp/instance")

        assert result is not None
        assert "giving up" in result.lower()
        mock_modify.assert_called()
        mock_outbox.assert_called_once()
        # Failure notification should mention the PR URL
        assert PR_URL in mock_outbox.call_args[0][1]


class TestAttemptCiFixes:
    """Verify the fix pipeline attempts Claude-based fixes correctly."""

    def test_claude_produces_no_changes_gives_up(self):
        """If Claude produces no changes, the pipeline stops."""
        from app.ci_queue_runner import _attempt_ci_fixes

        with (
            patch("app.claude_step._run_git", return_value=""),
            patch("app.rebase_pr.truncate_text", side_effect=lambda t, n: t),
            patch("app.rebase_pr._build_ci_fix_prompt", return_value="fix this"),
            patch("app.claude_step.run_claude_step", return_value=False),
        ):
            actions_log = []
            result = _attempt_ci_fixes(
                branch="fix-branch",
                base="main",
                full_repo="owner/repo",
                pr_number="42",
                pr_url=PR_URL,
                project_path=PROJECT_PATH,
                context={"url": PR_URL},
                ci_logs="Error: test failed",
                actions_log=actions_log,
                max_attempts=2,
            )

        assert result is False
        assert any("no changes" in a.lower() for a in actions_log)

    def test_build_ci_fix_prompt_loads_without_error(self):
        """_build_ci_fix_prompt must load ci_fix.md without FileNotFoundError.

        Regression: ci_queue_runner called _build_ci_fix_prompt without a
        skill_dir, which fell back to system-prompts/ci_fix.md — but that
        file didn't exist, so every /ci_check mission crashed with
        FileNotFoundError and never attempted a fix.
        """
        from app.rebase_pr import _build_ci_fix_prompt

        context = {"title": "fix: test", "branch": "fix-branch", "base": "main"}
        prompt = _build_ci_fix_prompt(context, "Error: test failed", "diff content")

        assert "fix-branch" in prompt
        assert "Error: test failed" in prompt

    def test_successful_fix_and_push(self):
        """If Claude fixes and push succeeds, reports success when CI is pending."""
        from app.ci_queue_runner import _attempt_ci_fixes

        with (
            patch("app.claude_step._run_git", return_value=""),
            patch("app.rebase_pr.truncate_text", side_effect=lambda t, n: t),
            patch("app.rebase_pr._build_ci_fix_prompt", return_value="fix this"),
            patch("app.claude_step.run_claude_step", return_value=True),
            patch("app.rebase_pr._force_push"),
            patch("app.ci_queue_runner.check_ci_status", return_value=("pending", 789)),
            patch("app.ci_queue_runner._reenqueue_for_monitoring") as mock_reenqueue,
            patch("time.sleep"),
        ):
            actions_log = []
            result = _attempt_ci_fixes(
                branch="fix-branch",
                base="main",
                full_repo="owner/repo",
                pr_number="42",
                pr_url=PR_URL,
                project_path=PROJECT_PATH,
                context={"url": PR_URL},
                ci_logs="Error: test failed",
                actions_log=actions_log,
                max_attempts=2,
            )

        assert result is True
        assert any("pushed" in a.lower() for a in actions_log)
        # Verify re-enqueue was called so drain_one monitors the new CI run
        mock_reenqueue.assert_called_once_with(
            PR_URL, "fix-branch", "owner/repo", "42", PROJECT_PATH,
        )
        assert any("re-enqueued" in a.lower() for a in actions_log)

    def test_base_remote_used_for_diff(self):
        """The base_remote parameter is used for git diff instead of hardcoded origin."""
        from app.ci_queue_runner import _attempt_ci_fixes

        run_git_calls = []

        def capture_run_git(cmd, cwd=None, timeout=None):
            run_git_calls.append(cmd)
            return ""

        with (
            patch("app.claude_step._run_git", side_effect=capture_run_git),
            patch("app.rebase_pr.truncate_text", side_effect=lambda t, n: t),
            patch("app.rebase_pr._build_ci_fix_prompt", return_value="fix this"),
            patch("app.claude_step.run_claude_step", return_value=False),
        ):
            actions_log = []
            _attempt_ci_fixes(
                branch="fix-branch",
                base="main",
                full_repo="owner/repo",
                pr_number="42",
                pr_url=PR_URL,
                project_path=PROJECT_PATH,
                context={"url": PR_URL},
                ci_logs="Error: test failed",
                actions_log=actions_log,
                max_attempts=1,
                base_remote="upstream",
            )

        # Verify the diff command uses the specified base_remote
        diff_cmds = [c for c in run_git_calls if "diff" in c]
        assert any("upstream/main" in str(c) for c in diff_cmds), (
            f"Expected 'upstream/main' in diff command, got: {diff_cmds}"
        )

    def test_configurable_max_turns_used(self):
        """run_claude_step is called with get_skill_max_turns() not a hardcoded value."""
        from app.ci_queue_runner import _attempt_ci_fixes

        with (
            patch("app.claude_step._run_git", return_value=""),
            patch("app.rebase_pr.truncate_text", side_effect=lambda t, n: t),
            patch("app.rebase_pr._build_ci_fix_prompt", return_value="fix this"),
            patch("app.claude_step.run_claude_step", return_value=False) as mock_step,
            patch("app.config.get_skill_max_turns", return_value=42),
            patch("app.config.get_skill_timeout", return_value=999),
        ):
            _attempt_ci_fixes(
                branch="fix-branch",
                base="main",
                full_repo="owner/repo",
                pr_number="42",
                pr_url=PR_URL,
                project_path=PROJECT_PATH,
                context={"url": PR_URL},
                ci_logs="Error: test failed",
                actions_log=[],
                max_attempts=1,
            )

        # Verify configurable values are passed through
        call_kwargs = mock_step.call_args[1]
        assert call_kwargs["max_turns"] == 42
        assert call_kwargs["timeout"] == 999

    def test_reenqueue_called_on_pending_ci(self):
        """After pushing a fix, if CI is pending, the PR is re-enqueued in ## CI section."""
        from app.ci_queue_runner import _reenqueue_for_monitoring

        with (
            patch.dict("os.environ", {"KOAN_ROOT": "/tmp/test-koan"}),
            patch("app.utils.modify_missions_file") as mock_modify,
            patch("app.utils.load_config", return_value={"ci_fix_max_attempts": 5}),
            patch("pathlib.Path.exists", return_value=True),
        ):
            _reenqueue_for_monitoring(
                PR_URL, "fix-branch", "owner/repo", "42", PROJECT_PATH,
            )

        mock_modify.assert_called_once()


class TestAggregateCiRuns:
    """Aggregation rules for `gh run list` output — especially skip-conclusion handling."""

    def test_empty_input_returns_none(self):
        from app.claude_step import aggregate_ci_runs

        assert aggregate_ci_runs([]) == ("none", None)

    def test_all_success_returns_success(self):
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 1, "status": "completed", "conclusion": "success"},
            {"databaseId": 2, "status": "completed", "conclusion": "success"},
        ]
        assert aggregate_ci_runs(runs) == ("success", 1)

    def test_failure_wins_over_pending(self):
        """A failed completed run takes priority over an in-progress one."""
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 1, "status": "in_progress", "conclusion": ""},
            {"databaseId": 2, "status": "completed", "conclusion": "failure"},
            {"databaseId": 3, "status": "completed", "conclusion": "success"},
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == "failure"
        assert run_id == 2

    def test_pending_returned_when_no_completed_failures(self):
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 1, "status": "completed", "conclusion": "success"},
            {"databaseId": 2, "status": "in_progress", "conclusion": ""},
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == "pending"
        assert run_id == 2

    def test_dependabot_auto_merge_skip_is_ignored(self):
        """Regression: a 'Dependabot auto-merge' workflow that completes with
        conclusion='skipped' on a non-Dependabot PR must not be reported as a
        CI failure. See aio-libs/yarl PR #1681 — Kōan kept queueing /ci_check
        fix missions because `gh run list --limit 1` returned the skipped
        Dependabot run instead of the actual CI workflows.
        """
        from app.claude_step import aggregate_ci_runs

        # This mirrors the actual `gh run list` payload for the yarl PR:
        # the Dependabot auto-merge run lands first by databaseId order, but
        # the real CI workflows are all green.
        runs = [
            {
                "databaseId": 25970779376,
                "status": "completed",
                "conclusion": "skipped",
                "workflowName": "Dependabot auto-merge",
            },
            {
                "databaseId": 25970779403,
                "status": "completed",
                "conclusion": "success",
                "workflowName": "CodeQL",
            },
            {
                "databaseId": 25970779406,
                "status": "completed",
                "conclusion": "success",
                "workflowName": "Aiohttp",
            },
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == "success"
        # The reported run_id must point at a real CI workflow, never the
        # skipped Dependabot run — otherwise log fetching would target the
        # wrong run and report no failures.
        assert run_id != 25970779376

    def test_dependabot_skip_with_pending_real_ci_returns_pending(self):
        """If only the Dependabot run completed (skipped) and real CI is still
        running, surface pending — not failure, not success.
        """
        from app.claude_step import aggregate_ci_runs

        runs = [
            {
                "databaseId": 25970779376,
                "status": "completed",
                "conclusion": "skipped",
                "workflowName": "Dependabot auto-merge",
            },
            {
                "databaseId": 25970779458,
                "status": "in_progress",
                "conclusion": "",
                "workflowName": "CI/CD",
            },
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == "pending"
        assert run_id == 25970779458

    def test_cancelled_and_neutral_also_ignored(self):
        """`cancelled`, `neutral`, `action_required` are not real CI failures."""
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 1, "status": "completed", "conclusion": "cancelled"},
            {"databaseId": 2, "status": "completed", "conclusion": "neutral"},
            {"databaseId": 3, "status": "completed", "conclusion": "action_required"},
            {"databaseId": 4, "status": "completed", "conclusion": "success"},
        ]
        assert aggregate_ci_runs(runs) == ("success", 4)

    def test_all_skipped_returns_none(self):
        """When every workflow run was filtered out, we have no CI signal."""
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 1, "status": "completed", "conclusion": "skipped"},
            {"databaseId": 2, "status": "completed", "conclusion": "cancelled"},
        ]
        assert aggregate_ci_runs(runs) == ("none", None)

    def test_missing_conclusion_field_treated_as_pending(self):
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 1, "status": "queued"},
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == "pending"
        assert run_id == 1

    def test_action_required_status_returns_blocked_approval(self):
        """Workflow runs gated on maintainer approval (fork PR from a
        first-time contributor) come back with status='action_required'
        and no conclusion. They must surface as blocked_approval so
        callers stop retrying — pushing more commits won't unstick them.
        See https://github.com/aio-libs/aiohttp/pull/12553 — Kōan retried
        the same PR multiple times while every workflow run sat waiting
        for an approve click.
        """
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 10, "status": "action_required", "conclusion": None},
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == CI_STATUS_BLOCKED_APPROVAL
        assert run_id == 10

    def test_waiting_status_returns_blocked_approval(self):
        """`waiting` status signals an environment-protection gate — also
        a "human must click" state that Kōan can't move past.
        """
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 11, "status": "waiting", "conclusion": None},
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == CI_STATUS_BLOCKED_APPROVAL
        assert run_id == 11

    def test_failure_wins_over_blocked_approval(self):
        """If one workflow is genuinely failing and another is blocked on
        approval, prioritise the failure: that one CAN still be fixed by
        pushing new commits.
        """
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 1, "status": "action_required", "conclusion": None},
            {"databaseId": 2, "status": "completed", "conclusion": "failure"},
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == "failure"
        assert run_id == 2

    def test_blocked_approval_wins_over_pending(self):
        """A blocked run alongside an in-progress one should still surface
        as blocked — the in-progress run is a coincidence, the gate is the
        actionable state for the human.
        """
        from app.claude_step import aggregate_ci_runs

        runs = [
            {"databaseId": 1, "status": "in_progress", "conclusion": ""},
            {"databaseId": 2, "status": "action_required", "conclusion": None},
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == CI_STATUS_BLOCKED_APPROVAL
        assert run_id == 2

    def test_stale_failure_on_prior_sha_does_not_mask_green_head(self):
        """Regression: when CI on the current HEAD is green but a prior
        commit on the same branch had a failed run, aggregate_ci_runs must
        report success based on HEAD — not resurrect the stale failure.

        Without this, /ci_check enters an unbreakable retry loop: Claude
        looks at green HEAD, says "no changes needed", the fix step records
        zero changes and bails, and the next drain cycle re-reads the same
        stale failure and queues another /ci_check. See PR #264 incident
        on 2026-05-17.
        """
        from app.claude_step import aggregate_ci_runs

        runs = [
            {
                "databaseId": 1,
                "status": "completed",
                "conclusion": "failure",
                "headSha": "OLDSHA",
                "createdAt": "2026-05-17T20:00:00Z",
            },
            {
                "databaseId": 2,
                "status": "completed",
                "conclusion": "success",
                "headSha": "NEWSHA",
                "createdAt": "2026-05-17T21:00:00Z",
            },
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == "success"
        assert run_id == 2

    def test_runs_grouped_by_latest_sha(self):
        """Multiple workflows on the latest SHA aggregate together; runs
        on older SHAs are ignored entirely (no failure/pending leakage).
        """
        from app.claude_step import aggregate_ci_runs

        runs = [
            {
                "databaseId": 1,
                "status": "completed",
                "conclusion": "failure",
                "headSha": "OLDSHA",
                "createdAt": "2026-05-17T20:00:00Z",
            },
            {
                "databaseId": 2,
                "status": "in_progress",
                "conclusion": "",
                "headSha": "OLDSHA",
                "createdAt": "2026-05-17T20:00:30Z",
            },
            {
                "databaseId": 3,
                "status": "completed",
                "conclusion": "success",
                "headSha": "NEWSHA",
                "createdAt": "2026-05-17T21:00:00Z",
            },
            {
                "databaseId": 4,
                "status": "in_progress",
                "conclusion": "",
                "headSha": "NEWSHA",
                "createdAt": "2026-05-17T21:00:30Z",
            },
        ]
        status, run_id = aggregate_ci_runs(runs)
        # Latest SHA has one success + one in_progress → pending on HEAD.
        assert status == "pending"
        assert run_id == 4

    def test_failure_on_latest_sha_still_reported(self):
        """When HEAD genuinely fails, aggregate must still surface the
        failure — the SHA filter must not be over-eager.
        """
        from app.claude_step import aggregate_ci_runs

        runs = [
            {
                "databaseId": 1,
                "status": "completed",
                "conclusion": "success",
                "headSha": "OLDSHA",
                "createdAt": "2026-05-17T20:00:00Z",
            },
            {
                "databaseId": 2,
                "status": "completed",
                "conclusion": "failure",
                "headSha": "NEWSHA",
                "createdAt": "2026-05-17T21:00:00Z",
            },
        ]
        status, run_id = aggregate_ci_runs(runs)
        assert status == "failure"
        assert run_id == 2


class TestDrainOneBlockedApproval:
    """drain_one must remove a PR from ## CI when its workflows are
    blocked on maintainer approval, instead of polling forever.
    """

    PR_URL = "https://github.com/owner/repo/pull/42"

    def _missions_with_ci_entry(self):
        return (
            "# Missions\n\n## CI\n\n"
            f"- [project:proj] {self.PR_URL} branch:fix-branch repo:owner/repo"
            f" queued:2026-04-01T10:00 (attempt 0/5)\n\n"
            "## Pending\n\n## Done\n"
        )

    def test_blocked_approval_removes_entry_and_notifies(self):
        from app.ci_queue_runner import drain_one

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=self._missions_with_ci_entry()),
            patch("app.ci_queue_runner._maybe_migrate_json_queue"),
            patch("app.utils.modify_missions_file") as mock_modify,
            patch(
                "app.ci_queue_runner.check_ci_status",
                return_value=(CI_STATUS_BLOCKED_APPROVAL, 999),
            ),
            patch("app.ci_queue_runner._write_outbox") as mock_outbox,
            patch("app.ci_queue_runner._inject_ci_fix_mission") as mock_inject,
        ):
            result = drain_one("/tmp/instance")

        assert result is not None
        assert "approval" in result.lower()
        mock_modify.assert_called()
        mock_outbox.assert_called_once()
        # Outbox message should reference the PR so the human can act
        assert self.PR_URL in mock_outbox.call_args[0][1]
        assert "approval" in mock_outbox.call_args[0][1].lower()
        # No fix mission should be queued — Kōan can't unstick it
        mock_inject.assert_not_called()


class TestRunCiCheckBlockedApproval:
    """run_ci_check_and_fix must bail out, not attempt fixes, when CI is
    gated on maintainer approval.
    """

    PR_URL = "https://github.com/owner/repo/pull/42"
    PROJECT_PATH = "/tmp/test-project"

    def test_blocked_approval_returns_early_without_fix(self):
        from app.ci_queue_runner import run_ci_check_and_fix

        fake_context = {"branch": "fix-branch", "base": "main"}
        with (
            patch("app.rebase_pr.fetch_pr_context", return_value=fake_context),
            patch(
                "app.ci_queue_runner.check_ci_status",
                return_value=(CI_STATUS_BLOCKED_APPROVAL, 123),
            ),
            patch("app.ci_queue_runner._attempt_ci_fixes") as mock_fix,
        ):
            success, summary = run_ci_check_and_fix(self.PR_URL, self.PROJECT_PATH)

        assert success is False
        assert "approval" in summary.lower()
        # The pipeline must not attempt Claude-based fixes
        mock_fix.assert_not_called()


class TestCheckCiStatusDependabot:
    """End-to-end: check_ci_status must not treat skipped Dependabot runs as failures."""

    def test_dependabot_skip_does_not_trigger_failure(self):
        """Regression for aio-libs/yarl PR #1681 — Kōan repeatedly queued
        /ci_check fix missions because check_ci_status returned ('failure',
        <dependabot_skip_run_id>) for a healthy PR.
        """
        from app.ci_queue_runner import check_ci_status

        gh_payload = json.dumps([
            {
                "databaseId": 25970779376,
                "status": "completed",
                "conclusion": "skipped",
                "workflowName": "Dependabot auto-merge",
            },
            {
                "databaseId": 25970779403,
                "status": "completed",
                "conclusion": "success",
                "workflowName": "CodeQL",
            },
        ])
        with patch("app.claude_step.run_gh", return_value=gh_payload):
            status, run_id = check_ci_status("koan/fix-issue-1680", "aio-libs/yarl")

        assert status == "success"
        assert run_id == 25970779403

    def test_check_existing_ci_dependabot_skip_does_not_fetch_logs(self):
        """The other single-shot caller (`check_existing_ci`) must also ignore
        the skipped Dependabot run, otherwise we'd waste an `_fetch_failed_logs`
        call on a workflow that produced no logs.
        """
        from app.claude_step import check_existing_ci

        gh_payload = json.dumps([
            {
                "databaseId": 25970779376,
                "status": "completed",
                "conclusion": "skipped",
                "workflowName": "Dependabot auto-merge",
            },
            {
                "databaseId": 25970779403,
                "status": "completed",
                "conclusion": "success",
                "workflowName": "CodeQL",
            },
        ])
        with (
            patch("app.claude_step.run_gh", return_value=gh_payload),
            patch("app.claude_step._fetch_failed_logs") as mock_fetch_logs,
        ):
            status, run_id, logs = check_existing_ci("br", "owner/repo")

        assert status == "success"
        assert run_id == 25970779403
        assert logs == ""
        mock_fetch_logs.assert_not_called()
