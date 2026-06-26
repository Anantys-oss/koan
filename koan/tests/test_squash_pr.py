"""Tests for squash_pr.py — PR squash pipeline, text generation, git operations."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from app.squash_pr import (
    _build_squash_comment,
    _checkout_pr_branch,
    _count_commits_since_base,
    _extract_between,
    _force_push,
    _generate_squash_text,
    _parse_squash_output,
    _squash_commits,
    main,
    run_squash,
)


# ---------------------------------------------------------------------------
# _extract_between
# ---------------------------------------------------------------------------

class TestExtractBetween:
    def test_extracts_text_between_markers(self):
        text = "before===START===hello world===END===after"
        assert _extract_between(text, "===START===", "===END===") == "hello world"

    def test_strips_whitespace(self):
        text = "===START===  spaced  ===END==="
        assert _extract_between(text, "===START===", "===END===") == "spaced"

    def test_missing_start_marker_returns_empty(self):
        assert _extract_between("no markers here", "===START===", "===END===") == ""

    def test_missing_end_marker_returns_rest(self):
        text = "===START===everything after"
        assert _extract_between(text, "===START===", "===END===") == "everything after"

    def test_empty_content_between_markers(self):
        text = "===START======END==="
        assert _extract_between(text, "===START===", "===END===") == ""

    def test_multiline_content(self):
        text = "===START===\nline1\nline2\n===END==="
        result = _extract_between(text, "===START===", "===END===")
        assert "line1" in result
        assert "line2" in result


# ---------------------------------------------------------------------------
# _parse_squash_output
# ---------------------------------------------------------------------------

class TestParseSquashOutput:
    def test_parses_all_sections(self):
        output = (
            "===COMMIT_MESSAGE===\nfix: clean up auth logic\n"
            "===PR_TITLE===\nfix: auth cleanup\n"
            "===PR_DESCRIPTION===\nRemoved dead code.\n===END==="
        )
        ctx = {"title": "fallback", "body": "fallback body"}
        result = _parse_squash_output(output, ctx)
        assert result["commit_message"] == "fix: clean up auth logic"
        assert result["pr_title"] == "fix: auth cleanup"
        assert result["pr_description"] == "Removed dead code."

    def test_falls_back_to_context_on_empty(self):
        output = "no markers here"
        ctx = {"title": "Original Title", "body": "Original body"}
        result = _parse_squash_output(output, ctx)
        assert result["commit_message"] == "Original Title"
        assert result["pr_title"] == "Original Title"
        assert result["pr_description"] == "Original body"

    def test_partial_markers(self):
        output = "===COMMIT_MESSAGE===\ngood message\n===PR_TITLE==="
        ctx = {"title": "fb", "body": ""}
        result = _parse_squash_output(output, ctx)
        assert result["commit_message"] == "good message"
        # PR title is empty (between PR_TITLE and missing PR_DESCRIPTION)
        # so falls back to context
        assert result["pr_title"] == "fb"


# ---------------------------------------------------------------------------
# _count_commits_since_base
# ---------------------------------------------------------------------------

class TestCountCommitsSinceBase:
    @patch("app.squash_pr._run_git")
    def test_counts_commits(self, mock_run):
        mock_run.side_effect = [
            "abc123\n",  # merge-base
            "commit1\ncommit2\ncommit3\n",  # rev-list
        ]
        assert _count_commits_since_base("origin/main", "/tmp/proj") == 3

    @patch("app.squash_pr._run_git")
    def test_zero_commits(self, mock_run):
        mock_run.side_effect = [
            "abc123\n",  # merge-base
            "",  # empty rev-list
        ]
        assert _count_commits_since_base("origin/main", "/tmp/proj") == 0

    @patch("app.squash_pr._run_git")
    def test_returns_negative_on_error(self, mock_run):
        mock_run.side_effect = Exception("git failed")
        assert _count_commits_since_base("origin/main", "/tmp/proj") == -1


# ---------------------------------------------------------------------------
# _squash_commits
# ---------------------------------------------------------------------------

class TestSquashCommits:
    @patch("app.squash_pr._run_git")
    def test_squash_sequence(self, mock_run):
        mock_run.side_effect = [
            "abc123\n",  # merge-base
            "",  # reset --soft
            "",  # commit
        ]
        result = _squash_commits("origin/main", "/tmp/proj", "squash msg")
        assert result is True
        calls = mock_run.call_args_list
        assert calls[0][0][0] == ["git", "merge-base", "origin/main", "HEAD"]
        assert calls[1][0][0] == ["git", "reset", "--soft", "abc123"]
        # Happy path commits WITH hooks under a generous timeout; --no-verify is
        # only the timeout fallback so first-run hook installs get a chance.
        assert calls[2][0][0] == ["git", "commit", "-m", "squash msg"]
        assert calls[2][1].get("timeout") == 180

    @patch("app.squash_pr._run_git")
    def test_propagates_error(self, mock_run):
        mock_run.side_effect = Exception("merge-base failed")
        with pytest.raises(Exception, match="merge-base failed"):
            _squash_commits("origin/main", "/tmp/proj", "msg")


# ---------------------------------------------------------------------------
# _force_push
# ---------------------------------------------------------------------------

class TestForcePush:
    @patch("app.squash_pr._ordered_remotes", return_value=["origin", "upstream"])
    @patch("app.squash_pr._run_git")
    def test_force_with_lease_succeeds(self, mock_run, mock_remotes):
        mock_run.return_value = ""
        result = _force_push("feature-branch", "/tmp/proj")
        assert result == "origin"
        mock_run.assert_called_once_with(
            ["git", "push", "origin", "feature-branch", "--force-with-lease"],
            cwd="/tmp/proj",
        )

    @patch("app.squash_pr._ordered_remotes", return_value=["origin"])
    @patch("app.squash_pr._run_git")
    def test_falls_back_to_force(self, mock_run, mock_remotes):
        mock_run.side_effect = [
            Exception("lease rejected"),  # --force-with-lease fails
            "",  # --force succeeds
        ]
        result = _force_push("branch", "/tmp/proj")
        assert result == "origin"
        assert mock_run.call_count == 2

    @patch("app.squash_pr._ordered_remotes", return_value=["origin"])
    @patch("app.squash_pr._run_git")
    def test_all_remotes_fail_raises(self, mock_run, mock_remotes):
        mock_run.side_effect = Exception("push rejected")
        with pytest.raises(RuntimeError, match="Cannot push"):
            _force_push("branch", "/tmp/proj")


# ---------------------------------------------------------------------------
# _checkout_pr_branch
# ---------------------------------------------------------------------------

class TestCheckoutPrBranch:
    @patch("app.squash_pr._ordered_remotes", return_value=["origin"])
    @patch("app.squash_pr._run_git")
    @patch("app.squash_pr._fetch_branch")
    def test_checkout_succeeds(self, mock_fetch, mock_run, mock_remotes):
        result = _checkout_pr_branch("feature", "/tmp/proj")
        assert result == "origin"
        mock_fetch.assert_called_once_with("origin", "feature", cwd="/tmp/proj")

    @patch("app.squash_pr._ordered_remotes", return_value=["origin"])
    @patch("app.squash_pr._run_git")
    @patch("app.squash_pr._fetch_branch")
    def test_tries_fork_remote_on_failure(self, mock_fetch, mock_run, mock_remotes):
        # First fetch (origin) fails, fork remote succeeds
        mock_fetch.side_effect = [
            Exception("not found"),  # origin
            None,  # fork-alice
        ]
        result = _checkout_pr_branch(
            "feature", "/tmp/proj",
            head_owner="alice", repo="myrepo",
        )
        assert result == "fork-alice"

    @patch("app.squash_pr._ordered_remotes", return_value=["origin"])
    @patch("app.squash_pr._run_git")
    @patch("app.squash_pr._fetch_branch")
    def test_raises_when_all_fail(self, mock_fetch, mock_run, mock_remotes):
        mock_fetch.side_effect = Exception("not found")
        # For the fork remote addition, _run_git may succeed but fetch still fails
        with pytest.raises(RuntimeError, match="not found on any remote"):
            _checkout_pr_branch("feature", "/tmp/proj")


# ---------------------------------------------------------------------------
# _generate_squash_text
# ---------------------------------------------------------------------------

class TestGenerateSquashText:
    @patch("app.squash_pr.run_claude")
    @patch("app.squash_pr.build_full_command", return_value=["claude", "--prompt"])
    @patch("app.squash_pr.get_model_config", return_value={"mission": "opus", "fallback": "sonnet"})
    @patch("app.squash_pr.load_prompt_or_skill", return_value="prompt text")
    def test_success_parses_output(self, mock_prompt, mock_models, mock_cmd, mock_claude):
        mock_claude.return_value = {
            "success": True,
            "output": (
                "===COMMIT_MESSAGE===fix: thing===PR_TITLE===fix thing"
                "===PR_DESCRIPTION===Fixed the thing.===END==="
            ),
        }
        ctx = {"title": "old", "body": "old body", "branch": "b", "base": "main"}
        result = _generate_squash_text(ctx, "diff text")
        assert result["commit_message"] == "fix: thing"
        assert result["pr_title"] == "fix thing"

    @patch("app.squash_pr.run_claude")
    @patch("app.squash_pr.build_full_command", return_value=["cmd"])
    @patch("app.squash_pr.get_model_config", return_value={"mission": "m", "fallback": "f"})
    @patch("app.squash_pr.load_prompt_or_skill", return_value="prompt")
    def test_failure_falls_back(self, mock_prompt, mock_models, mock_cmd, mock_claude):
        mock_claude.return_value = {"success": False, "output": ""}
        ctx = {"title": "PR Title", "body": "PR Body"}
        result = _generate_squash_text(ctx, "diff")
        assert result["commit_message"] == "PR Title"
        assert result["pr_title"] == "PR Title"
        assert result["pr_description"] == "PR Body"


# ---------------------------------------------------------------------------
# _build_squash_comment
# ---------------------------------------------------------------------------

class TestBuildSquashComment:
    def test_builds_comment(self):
        actions = ["Squashed 5 commits into 1", "Force-pushed"]
        text = {"commit_message": "fix: cleanup", "pr_title": "t", "pr_description": "d"}
        result = _build_squash_comment("42", "feature", "main", 5, actions, text)
        assert "5 commits" in result
        assert "fix: cleanup" in result
        assert "Force-pushed" in result
        assert "Automated by Koan" in result

    def test_filters_comment_action(self):
        actions = ["Squashed 3 commits into 1", "Commented on PR"]
        text = {"commit_message": "m", "pr_title": "t", "pr_description": "d"}
        result = _build_squash_comment("1", "b", "main", 3, actions, text)
        assert "Commented on PR" not in result


# ---------------------------------------------------------------------------
# run_squash — integration-level (all git/gh mocked)
# ---------------------------------------------------------------------------

class TestRunSquash:
    def _mock_context(self, **overrides):
        ctx = {
            "title": "feat: add feature",
            "body": "Adds a new feature",
            "branch": "feature-branch",
            "base": "main",
            "state": "OPEN",
            "head_owner": "",
        }
        ctx.update(overrides)
        return ctx

    @patch("app.squash_pr.run_gh")
    @patch("app.squash_pr._force_push", return_value="origin")
    @patch("app.squash_pr._squash_commits", return_value=True)
    @patch("app.squash_pr._generate_squash_text")
    @patch("app.squash_pr._run_git")
    @patch("app.squash_pr._count_commits_since_base", return_value=5)
    @patch("app.squash_pr._fetch_branch")
    @patch("app.squash_pr._checkout_pr_branch", return_value="origin")
    @patch("app.squash_pr._get_current_branch", return_value="main")
    @patch("app.squash_pr._find_remote_for_repo", return_value="origin")
    @patch("app.squash_pr.fetch_pr_context")
    @patch("app.squash_pr.resolve_pr_location", return_value=("owner", "repo"))
    def test_full_pipeline_success(
        self, mock_resolve, mock_fetch_ctx, mock_find_remote,
        mock_branch, mock_checkout, mock_fetch_br, mock_count,
        mock_run_git, mock_gen_text, mock_squash, mock_push, mock_gh,
    ):
        mock_fetch_ctx.return_value = self._mock_context()
        mock_gen_text.return_value = {
            "commit_message": "feat: combined",
            "pr_title": "feat: combined",
            "pr_description": "All changes combined.",
        }
        mock_run_git.return_value = "diff content"
        notify = MagicMock()

        success, summary = run_squash(
            "owner", "repo", "42", "/tmp/proj", notify_fn=notify,
        )

        assert success is True
        assert "squashed" in summary.lower()
        assert mock_squash.called
        assert mock_push.called

    @patch("app.squash_pr.resolve_pr_location")
    def test_resolve_failure(self, mock_resolve):
        mock_resolve.side_effect = RuntimeError("Cannot resolve")
        notify = MagicMock()
        success, summary = run_squash("o", "r", "1", "/tmp", notify_fn=notify)
        assert success is False
        assert "Cannot resolve" in summary

    @patch("app.squash_pr.resolve_pr_location", return_value=("o", "r"))
    @patch("app.squash_pr.fetch_pr_context")
    def test_merged_pr_skipped(self, mock_ctx, mock_resolve):
        mock_ctx.return_value = self._mock_context(state="MERGED")
        notify = MagicMock()
        success, summary = run_squash("o", "r", "1", "/tmp", notify_fn=notify)
        assert success is True
        assert "already merged" in summary.lower()

    @patch("app.squash_pr.resolve_pr_location", return_value=("o", "r"))
    @patch("app.squash_pr.fetch_pr_context")
    def test_closed_pr_skipped(self, mock_ctx, mock_resolve):
        mock_ctx.return_value = self._mock_context(state="CLOSED")
        notify = MagicMock()
        success, summary = run_squash("o", "r", "1", "/tmp", notify_fn=notify)
        assert success is True
        assert "closed" in summary.lower()

    @patch("app.squash_pr.resolve_pr_location", return_value=("o", "r"))
    @patch("app.squash_pr.fetch_pr_context")
    def test_empty_branch_fails(self, mock_ctx, mock_resolve):
        mock_ctx.return_value = self._mock_context(branch="")
        notify = MagicMock()
        success, summary = run_squash("o", "r", "1", "/tmp", notify_fn=notify)
        assert success is False
        assert "branch" in summary.lower()

    @patch("app.squash_pr._safe_checkout")
    @patch("app.squash_pr._count_commits_since_base", return_value=1)
    @patch("app.squash_pr._fetch_branch")
    @patch("app.squash_pr._checkout_pr_branch", return_value="origin")
    @patch("app.squash_pr._get_current_branch", return_value="main")
    @patch("app.squash_pr._find_remote_for_repo", return_value="origin")
    @patch("app.squash_pr.fetch_pr_context")
    @patch("app.squash_pr.resolve_pr_location", return_value=("o", "r"))
    def test_single_commit_skips(
        self, mock_resolve, mock_ctx, mock_find, mock_branch,
        mock_checkout, mock_fetch, mock_count, mock_safe,
    ):
        mock_ctx.return_value = self._mock_context()
        notify = MagicMock()
        success, summary = run_squash("o", "r", "1", "/tmp", notify_fn=notify)
        assert success is True
        assert "nothing to squash" in summary.lower()

    @patch("app.squash_pr.resolve_pr_location", return_value=("o", "r"))
    @patch("app.squash_pr.fetch_pr_context")
    def test_fetch_context_error(self, mock_ctx, mock_resolve):
        mock_ctx.side_effect = Exception("API error")
        notify = MagicMock()
        success, summary = run_squash("o", "r", "1", "/tmp", notify_fn=notify)
        assert success is False
        assert "API error" in summary

    @patch("app.squash_pr._safe_checkout")
    @patch("app.squash_pr._count_commits_since_base", return_value=-1)
    @patch("app.squash_pr._fetch_branch")
    @patch("app.squash_pr._checkout_pr_branch", return_value="origin")
    @patch("app.squash_pr._get_current_branch", return_value="main")
    @patch("app.squash_pr._find_remote_for_repo", return_value="origin")
    @patch("app.squash_pr.fetch_pr_context")
    @patch("app.squash_pr.resolve_pr_location", return_value=("o", "r"))
    def test_commit_count_failure_returns_error(
        self, mock_resolve, mock_ctx, mock_find, mock_branch,
        mock_checkout, mock_fetch, mock_count, mock_safe,
    ):
        """When merge-base fails, squash should error instead of silently skipping."""
        mock_ctx.return_value = self._mock_context()
        notify = MagicMock()
        success, summary = run_squash("o", "r", "1", "/tmp", notify_fn=notify)
        assert success is False
        assert "merge-base failed" in summary.lower()
        mock_safe.assert_called_once()


# ---------------------------------------------------------------------------
# main (CLI entry point)
# ---------------------------------------------------------------------------

class TestMain:
    @patch("app.squash_pr.run_squash", return_value=(True, "ok"))
    @patch("app.github_url_parser.parse_pr_url", return_value=("o", "r", "1"))
    def test_success_returns_zero(self, mock_parse, mock_run):
        code = main(["https://github.com/o/r/pull/1", "--project-path", "/tmp"])
        assert code == 0

    def test_invalid_url_returns_one(self):
        code = main(["https://not-github.com/bad/url", "--project-path", "/tmp"])
        assert code == 1

    @patch("app.github_url_parser.parse_pr_url", return_value=("o", "r", "1"))
    @patch("app.squash_pr.run_squash", return_value=(False, "failed"))
    def test_failure_returns_one(self, mock_run, mock_parse):
        code = main(["https://github.com/o/r/pull/1", "--project-path", "/tmp"])
        assert code == 1


class TestSquashOutcomeGating:
    def test_success_emits_single_outcome_with_pr_url(self, monkeypatch):
        import app.squash_pr as sp
        sent = []
        monkeypatch.setattr("app.messaging_level.is_debug", lambda: False)
        monkeypatch.setattr("app.notify.send_telegram", lambda m, **k: sent.append(m))

        def _impl(owner, repo, pr_number, project_path, notify_fn=None, **k):
            notify_fn("Squashing PR #7...")
            return True, "done"

        monkeypatch.setattr(sp, "_run_squash_impl", _impl)
        ok, _ = sp.run_squash("o", "r", "7", "/tmp/x")
        assert ok is True
        assert sent == ["✅ Squashed https://github.com/o/r/pull/7"]

    def test_failure_outcome_with_context(self, monkeypatch):
        import app.squash_pr as sp
        sent = []
        monkeypatch.setattr("app.messaging_level.is_debug", lambda: False)
        monkeypatch.setattr("app.notify.send_telegram", lambda m, **k: sent.append(m))
        monkeypatch.setattr(sp, "_run_squash_impl", lambda *a, **k: (False, "boom"))
        ok, _ = sp.run_squash("o", "r", "7", "/tmp/x")
        assert ok is False
        assert "https://github.com/o/r/pull/7" in sent[0] and "boom" in sent[0]
