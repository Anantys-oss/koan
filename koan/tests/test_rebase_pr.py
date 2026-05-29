"""Tests for rebase_pr.py — PR rebase pipeline, URL parsing, git operations."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from app.claude_step import _rebase_onto_target, _run_git
from app.utils import truncate_text
from app.github_url_parser import parse_pr_url
from app.git_utils import ordered_remotes
from app.rebase_pr import (
    fetch_pr_context,
    build_comment_summary,
    parse_severity,
    run_rebase,
    severity_at_or_above,
    SEVERITY_LEVELS,
    _apply_review_feedback,
    _build_ci_fix_prompt,
    _build_rebase_comment,
    _build_rebase_prompt,
    _checkout_pr_branch,
    _check_if_already_solved,
    _close_pr_as_duplicate,
    _filter_bot_issue_comments,
    _find_remote_for_repo,
    _fix_existing_ci_failures,
    _get_conflicted_files,
    _truncate_recent,
    _get_current_branch,
    _is_conflict_failure,
    _push_with_fallback,
    _rebase_with_conflict_resolution,
    check_pr_state,
    _run_ci_check_and_fix,
    _run_ci_fix_step_with_timeout_retry,
    _safe_checkout,
    _UNMERGED_STATUSES,
    MAX_CI_FIX_ATTEMPTS,
)
from app.claude_step import _is_permission_error, check_existing_ci, wait_for_ci


# ---------------------------------------------------------------------------
# parse_pr_url (from pr_review)
# ---------------------------------------------------------------------------

class TestParsePrUrl:
    def test_standard_url(self):
        owner, repo, num = parse_pr_url("https://github.com/sukria/koan/pull/29")
        assert owner == "sukria"
        assert repo == "koan"
        assert num == "29"

    def test_url_with_fragment(self):
        owner, repo, num = parse_pr_url(
            "https://github.com/sukria/koan/pull/29#pullrequestreview-123"
        )
        assert num == "29"

    def test_url_with_trailing_whitespace(self):
        owner, repo, num = parse_pr_url("  https://github.com/foo/bar/pull/1  ")
        assert owner == "foo"
        assert repo == "bar"

    def test_http_url(self):
        owner, repo, num = parse_pr_url("http://github.com/a/b/pull/99")
        assert owner == "a"
        assert num == "99"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Invalid PR URL"):
            parse_pr_url("https://github.com/sukria/koan/issues/29")

    def test_not_github_raises(self):
        with pytest.raises(ValueError, match="Invalid PR URL"):
            parse_pr_url("https://gitlab.com/sukria/koan/pull/29")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid PR URL"):
            parse_pr_url("")


# ---------------------------------------------------------------------------
# truncate_text (shared utility)
# ---------------------------------------------------------------------------

class TestTruncateText:
    def test_short_text_unchanged(self):
        assert truncate_text("hello", 100) == "hello"

    def test_exact_length_unchanged(self):
        assert truncate_text("12345", 5) == "12345"

    def test_long_text_truncated(self):
        result = truncate_text("a" * 20, 10)
        assert len(result) < 30
        assert "truncated" in result

    def test_empty_string(self):
        assert truncate_text("", 100) == ""


# ---------------------------------------------------------------------------
# _run_git (local helper)
# ---------------------------------------------------------------------------

class TestRunGit:
    def test_returns_stdout_stripped(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  main  "
        with patch("app.claude_step.subprocess.run", return_value=mock_result):
            assert _run_git(["git", "status"]) == "main"

    def test_raises_on_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        with patch("app.claude_step.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="git failed"):
                _run_git(["git", "checkout", "foo"])

    def test_passes_cwd(self):
        mock_result = MagicMock(returncode=0, stdout="ok")
        with patch("app.claude_step.subprocess.run", return_value=mock_result) as mock_run:
            _run_git(["git", "status"], cwd="/project")
            mock_run.assert_called_once()
            assert mock_run.call_args.kwargs.get("cwd") == "/project"


# ---------------------------------------------------------------------------
# _get_current_branch
# ---------------------------------------------------------------------------

class TestGetCurrentBranch:
    def test_returns_branch_name(self):
        with patch("app.claude_step._git_utils_get_current_branch", return_value="koan/my-feature"):
            assert _get_current_branch("/project") == "koan/my-feature"

    def test_fallback_on_error(self):
        with patch("app.claude_step._git_utils_get_current_branch", return_value="main"):
            assert _get_current_branch("/project") == "main"


# ---------------------------------------------------------------------------
# _checkout_pr_branch
# ---------------------------------------------------------------------------

class TestCheckoutPrBranch:
    def test_checkout_uses_dash_B_flag(self):
        """Should fetch and use -B to create/reset the local branch."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _checkout_pr_branch("koan/fix", "/project")

        assert result == "origin"
        cmds = [c[:3] for c in calls]
        assert ["git", "fetch", "origin"] in cmds
        # Must use -B, not -b or plain checkout
        checkout_cmds = [c for c in calls if "checkout" in c]
        assert len(checkout_cmds) == 1
        assert "-B" in checkout_cmds[0]
        assert "origin/koan/fix" in checkout_cmds[0]

    def test_resets_existing_local_branch(self):
        """A stale local branch with the same name must not block checkout."""
        # -B handles this — create or reset. Verify no "branch already exists" error.
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            _checkout_pr_branch("koan/fix", "/project")

        checkout_cmds = [c for c in calls if "checkout" in c]
        # Only ONE checkout call expected — -B handles both cases
        assert len(checkout_cmds) == 1
        assert "-B" in checkout_cmds[0]

    def test_falls_back_to_upstream(self):
        """If origin fetch fails, tries upstream and returns 'upstream'."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock(returncode=0, stdout="", stderr="")
            # origin fetch fails
            if cmd[:3] == ["git", "fetch", "origin"]:
                raise RuntimeError("remote ref not found")
            return result

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _checkout_pr_branch("feat/upstream-only", "/project")

        assert result == "upstream"
        fetch_cmds = [c for c in calls if c[:2] == ["git", "fetch"]]
        assert ["git", "fetch", "origin", "+refs/heads/feat/upstream-only:refs/remotes/origin/feat/upstream-only"] in fetch_cmds
        assert ["git", "fetch", "upstream", "+refs/heads/feat/upstream-only:refs/remotes/upstream/feat/upstream-only"] in fetch_cmds

        # Checkout should use upstream, not origin
        checkout_cmds = [c for c in calls if "checkout" in c]
        assert len(checkout_cmds) == 1
        assert "upstream/feat/upstream-only" in checkout_cmds[0]

    def test_raises_if_all_remotes_fail(self):
        """If all remotes fail and no fork info, raises RuntimeError."""
        def mock_run(cmd, **kwargs):
            if cmd[:2] == ["git", "fetch"]:
                raise RuntimeError("remote ref not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            with pytest.raises(RuntimeError, match="not found on"):
                _checkout_pr_branch("nonexistent", "/project")

    def test_tries_head_remote_first(self):
        """When head_remote is given, it should be tried before origin."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _checkout_pr_branch(
                "feat/branch", "/project", head_remote="myfork",
            )

        assert result == "myfork"
        fetch_cmds = [c for c in calls if c[:2] == ["git", "fetch"]]
        # head_remote should be tried first
        assert fetch_cmds[0] == ["git", "fetch", "myfork", "+refs/heads/feat/branch:refs/remotes/myfork/feat/branch"]

    def test_adds_fork_remote_when_no_match(self):
        """When branch not found on any known remote, adds fork remote."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            # All standard remotes fail for fetch
            if cmd[:2] == ["git", "fetch"] and cmd[2] in ("origin", "upstream"):
                raise RuntimeError("remote ref not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _checkout_pr_branch(
                "feat/fix", "/project",
                head_owner="someuser", repo="somerepo",
            )

        assert result == "fork-someuser"
        # Should have added the remote
        add_cmds = [c for c in calls if "remote" in c and "add" in c]
        assert len(add_cmds) == 1
        assert "fork-someuser" in add_cmds[0]
        assert "https://github.com/someuser/somerepo.git" in add_cmds[0]
        # Should have fetched from the fork remote
        fetch_cmds = [c for c in calls if c[:2] == ["git", "fetch"]]
        fork_fetches = [c for c in fetch_cmds if c[2] == "fork-someuser"]
        assert len(fork_fetches) == 1

    def test_origin_only_repo_does_not_try_upstream(self):
        """When only origin is configured, checkout must not probe upstream."""
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["git", "fetch"]:
                raise RuntimeError("remote ref not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.rebase_pr._ordered_remotes", return_value=["origin"]), \
             patch("app.claude_step.subprocess.run", side_effect=mock_run):
            with pytest.raises(RuntimeError, match="not found on"):
                _checkout_pr_branch("feat/missing", "/project")

        fetch_cmds = [c for c in calls if c[:2] == ["git", "fetch"]]
        assert len(fetch_cmds) == 1
        assert fetch_cmds[0][2] == "origin"


# ---------------------------------------------------------------------------
# _get_conflicted_files
# ---------------------------------------------------------------------------

class TestGetConflictedFiles:
    """Verify _get_conflicted_files uses git status --porcelain to detect unmerged entries."""

    def test_detects_uu_conflict(self):
        """UU (both modified) is the most common conflict type."""
        mock_result = MagicMock(
            stdout="UU file_a.txt\nM  file_b.txt\n",
            returncode=0,
        )
        with patch("app.rebase_pr.subprocess.run", return_value=mock_result) as mock_run:
            files = _get_conflicted_files("/project")
            assert files == ["file_a.txt"]
            # Verify stdin=subprocess.DEVNULL is passed
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("stdin") == subprocess.DEVNULL

    def test_detects_multiple_conflict_types(self):
        """All unmerged status codes are detected (UU, AA, DU, UD, AU, UA, DD)."""
        mock_result = MagicMock(
            stdout=(
                "UU both_modified.py\n"
                "AA both_added.py\n"
                "DU deleted_by_us.py\n"
                "UD deleted_by_them.py\n"
                "AU added_by_us.py\n"
                "UA added_by_them.py\n"
                "DD both_deleted.py\n"
                "M  cleanly_staged.py\n"
                " M unstaged.py\n"
            ),
            returncode=0,
        )
        with patch("app.rebase_pr.subprocess.run", return_value=mock_result):
            files = _get_conflicted_files("/project")
            assert files == [
                "both_modified.py",
                "both_added.py",
                "deleted_by_us.py",
                "deleted_by_them.py",
                "added_by_us.py",
                "added_by_them.py",
                "both_deleted.py",
            ]

    def test_no_conflicts_returns_empty(self):
        """When no unmerged entries exist, returns empty list."""
        mock_result = MagicMock(
            stdout="M  staged.py\n M unstaged.py\n?? untracked.py\n",
            returncode=0,
        )
        with patch("app.rebase_pr.subprocess.run", return_value=mock_result):
            assert _get_conflicted_files("/project") == []

    def test_empty_output_returns_empty(self):
        mock_result = MagicMock(stdout="", returncode=0)
        with patch("app.rebase_pr.subprocess.run", return_value=mock_result):
            assert _get_conflicted_files("/project") == []

    def test_exception_returns_empty(self):
        with patch("app.rebase_pr.subprocess.run", side_effect=OSError("fail")):
            assert _get_conflicted_files("/project") == []

    def test_paths_with_spaces(self):
        mock_result = MagicMock(
            stdout="UU path with spaces/file.txt\n",
            returncode=0,
        )
        with patch("app.rebase_pr.subprocess.run", return_value=mock_result):
            files = _get_conflicted_files("/project")
            assert files == ["path with spaces/file.txt"]

    def test_unmerged_statuses_constant_covers_all_types(self):
        """The frozen set covers all git unmerged status codes."""
        assert _UNMERGED_STATUSES == {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


# ---------------------------------------------------------------------------
# _rebase_onto_target (local helper)
# ---------------------------------------------------------------------------

class TestRebaseOntoTarget:
    def test_successful_rebase_on_origin(self):
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("app.claude_step.subprocess.run", return_value=mock_result):
            result = _rebase_onto_target("main", "/project")
            assert result == "origin"

    def test_falls_back_to_upstream(self):
        def mock_run(cmd, **kwargs):
            result = MagicMock(returncode=0, stdout="", stderr="")
            if "rebase" in cmd and any("origin" in a for a in cmd) and "--abort" not in cmd:
                raise RuntimeError("rebase failed")
            return result

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _rebase_onto_target("main", "/project")
            assert result == "upstream"

    def test_returns_none_on_conflict(self):
        def mock_run(cmd, **kwargs):
            if "rebase" in cmd and "--abort" not in cmd:
                raise RuntimeError("conflict")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _rebase_onto_target("main", "/project")
            assert result is None


# ---------------------------------------------------------------------------
# _is_permission_error
# ---------------------------------------------------------------------------

class TestIsPermissionError:
    def test_permission_denied(self):
        assert _is_permission_error("permission denied") is True

    def test_forbidden_403(self):
        assert _is_permission_error("HTTP 403: Forbidden") is True

    def test_protected_branch(self):
        assert _is_permission_error("protected branch") is True

    def test_auth_failed(self):
        assert _is_permission_error("authentication failed for url") is True

    def test_normal_error_not_permission(self):
        assert _is_permission_error("fatal: remote ref does not exist") is False

    def test_empty_string(self):
        assert _is_permission_error("") is False


# ---------------------------------------------------------------------------
# _safe_checkout
# ---------------------------------------------------------------------------

class TestSafeCheckout:
    def test_succeeds_silently(self):
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("app.claude_step.subprocess.run", return_value=mock_result):
            _safe_checkout("main", "/project")

    def test_fails_silently(self):
        with patch("app.claude_step.subprocess.run", side_effect=RuntimeError("oops")):
            _safe_checkout("main", "/project")  # Should not raise


# ---------------------------------------------------------------------------
# _find_remote_for_repo / ordered_remotes
# ---------------------------------------------------------------------------

class TestFindRemoteForRepo:
    """Test matching a GitHub owner/repo to a local git remote."""

    @patch("app.rebase_pr.subprocess.run")
    def test_finds_origin_https(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "origin\thttps://github.com/atoomic/Crypt-OpenSSL-RSA.git (fetch)\n"
                "origin\thttps://github.com/atoomic/Crypt-OpenSSL-RSA.git (push)\n"
                "upstream\thttps://github.com/cpan-authors/Crypt-OpenSSL-RSA.git (fetch)\n"
                "upstream\thttps://github.com/cpan-authors/Crypt-OpenSSL-RSA.git (push)\n"
            ),
        )
        assert _find_remote_for_repo(
            "cpan-authors", "Crypt-OpenSSL-RSA", "/tmp/project"
        ) == "upstream"

    @patch("app.rebase_pr.subprocess.run")
    def test_finds_origin_ssh(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "origin\tgit@github.com:owner/repo.git (fetch)\n"
                "origin\tgit@github.com:owner/repo.git (push)\n"
            ),
        )
        assert _find_remote_for_repo("owner", "repo", "/tmp/p") == "origin"

    @patch("app.rebase_pr.subprocess.run")
    def test_case_insensitive(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="upstream\thttps://github.com/OWNER/REPO.git (fetch)\n",
        )
        assert _find_remote_for_repo("owner", "repo", "/tmp/p") == "upstream"

    @patch("app.rebase_pr.subprocess.run")
    def test_no_match_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="origin\thttps://github.com/other/repo.git (fetch)\n",
        )
        assert _find_remote_for_repo("owner", "repo", "/tmp/p") is None

    @patch("app.rebase_pr.subprocess.run")
    def test_subprocess_failure_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _find_remote_for_repo("o", "r", "/tmp/p") is None


class TestOrderedRemotes:
    """Test remote ordering with preferred remote."""

    def test_no_preferred(self):
        assert ordered_remotes(None) == ["origin", "upstream"]

    def test_preferred_origin(self):
        # origin already in default list — should be first, no duplicate
        assert ordered_remotes("origin") == ["origin", "upstream"]

    def test_preferred_upstream(self):
        assert ordered_remotes("upstream") == ["upstream", "origin"]

    def test_preferred_custom(self):
        assert ordered_remotes("fork") == ["fork", "origin", "upstream"]


# ---------------------------------------------------------------------------
# build_comment_summary
# ---------------------------------------------------------------------------

class TestBuildCommentSummary:
    def test_with_reviews_and_comments(self):
        context = {
            "reviews": "@alice (APPROVED): LGTM",
            "review_comments": "[file.py:10] @bob: Fix this",
            "issue_comments": "@carol: Can we also handle edge case?",
        }
        result = build_comment_summary(context)
        assert "Reviews" in result
        assert "alice" in result
        assert "Inline Comments" in result
        assert "bob" in result
        assert "Discussion" in result
        assert "carol" in result

    def test_no_comments(self):
        context = {"reviews": "", "review_comments": "", "issue_comments": ""}
        result = build_comment_summary(context)
        assert "No comments" in result

    def test_partial_comments(self):
        context = {"reviews": "some review", "review_comments": "", "issue_comments": ""}
        result = build_comment_summary(context)
        assert "Reviews" in result
        assert "Inline" not in result


# ---------------------------------------------------------------------------
# _build_rebase_comment
# ---------------------------------------------------------------------------

class TestBuildRebaseComment:
    def test_basic_comment(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main", "Force-pushed"],
            {"title": "Fix bug"},
        )
        assert "## Simple rebase" in result
        assert "`koan/fix`" in result
        assert "`main`" in result
        assert "no additional changes" in result
        assert "Kōan" in result

    def test_empty_actions(self):
        result = _build_rebase_comment(
            "1", "br", "main", [],
            {"title": "PR"},
        )
        assert "no additional changes" in result

    def test_diffstat_included(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main"],
            {"title": "Fix bug"},
            diffstat="3 files changed, 15 insertions(+), 5 deletions(-)",
        )
        assert "3 files changed" in result
        assert "### Stats" in result

    def test_no_diffstat_when_empty(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main"],
            {"title": "Fix bug"},
            diffstat="",
        )
        assert "### Stats" not in result

    def test_review_feedback_noted(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main", "Applied review feedback"],
            {"title": "Fix bug", "review_comments": "please fix the typo"},
        )
        assert "## Rebase with requested adjustments" in result
        assert "review feedback was applied" in result

    def test_feedback_timeout_note_does_not_count_as_adjustments(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            [
                "Rebased onto origin/main",
                "Review feedback timed out; restored clean rebased state and continuing with rebase-only push",
            ],
            {"title": "Fix bug", "review_comments": "please fix the typo"},
        )
        assert "## Simple rebase" in result

    def test_conflict_resolution_noted(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Resolved merge conflicts (1 round(s))"],
            {"title": "Fix bug"},
        )
        assert "## Rebase with conflict resolution" in result
        assert "automatic conflict resolution" in result

    def test_mechanical_actions_filtered(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Read PR comments and review feedback", "Rebased", "Commented on PR"],
            {"title": "Fix bug"},
        )
        assert "Read PR comments" not in result
        assert "Commented on PR" not in result
        assert "Rebased" in result

    def test_actions_in_collapsible_details(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main", "Force-pushed"],
            {"title": "Fix bug"},
        )
        assert "<details>" in result
        assert "Actions performed" in result
        assert "</details>" in result


# ---------------------------------------------------------------------------
# fetch_pr_context
# ---------------------------------------------------------------------------

class TestFetchPrContext:
    @patch("app.github.subprocess.run")
    def test_parses_pr_metadata(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "Fix auth",
                "body": "Fixes #42",
                "headRefName": "koan/fix-auth",
                "baseRefName": "main",
                "state": "OPEN",
                "author": {"login": "sukria"},
                "url": "https://github.com/sukria/koan/pull/42",
            })),
            MagicMock(returncode=0, stdout="1"),  # review_comments count
            MagicMock(returncode=0, stdout="+added line"),
            MagicMock(returncode=0, stdout="[auth.py:10] @reviewer: Fix this"),
            MagicMock(returncode=0, stdout="@reviewer (CHANGES_REQUESTED): Please fix"),
            MagicMock(returncode=0, stdout="@sukria: Will do"),
        ]

        context = fetch_pr_context("sukria", "koan", "42")
        assert context["title"] == "Fix auth"
        assert context["branch"] == "koan/fix-auth"
        assert context["base"] == "main"
        assert context["state"] == "OPEN"
        assert context["author"] == "sukria"
        assert context["diff"] == "+added line"
        assert "Fix this" in context["review_comments"]
        assert "Please fix" in context["reviews"]
        assert "Will do" in context["issue_comments"]
        assert context["has_pending_reviews"] is False  # comments fetched OK

    @patch("app.github.subprocess.run")
    def test_handles_empty_responses(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({"title": "T", "headRefName": "br"})),
            MagicMock(returncode=0, stdout="0"),  # review_comments count
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert context["branch"] == "br"
        assert context["diff"] == ""
        assert context["review_comments"] == ""
        assert context["has_pending_reviews"] is False

    @patch("app.github.subprocess.run")
    def test_handles_invalid_json(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="not json"),
            MagicMock(returncode=0, stdout="0"),  # review_comments count
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert context["title"] == ""
        assert context["base"] == "main"

    @patch("app.github.subprocess.run")
    def test_diff_fetch_failure_graceful(self, mock_run):
        """Large PR diffs (HTTP 406) should not crash the entire fetch."""
        mock_run.side_effect = [
            # Metadata succeeds
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "Big PR",
                "headRefName": "feat/big",
                "baseRefName": "main",
                "state": "OPEN",
                "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=0, stdout="0"),  # review_comments count
            # Diff fails (HTTP 406 — too large)
            MagicMock(returncode=1, stderr="HTTP 406: diff exceeded maximum"),
            # Comments succeed
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert context["title"] == "Big PR"
        assert context["branch"] == "feat/big"
        assert context["diff"] == ""  # Graceful fallback
        assert "HTTP 406" in context["diff_error"]

    @patch("app.rebase_pr._fetch_diff_locally")
    @patch("app.github.subprocess.run")
    def test_diff_406_falls_back_to_local_diff(
        self, mock_run, mock_local,
    ):
        """When the diff endpoint returns 406 and project_path is set,
        fetch_pr_context falls back to a local git diff."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "Huge PR",
                "headRefName": "feat/huge",
                "baseRefName": "develop",
                "state": "OPEN",
                "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/9",
            })),
            MagicMock(returncode=0, stdout="0"),
            # Diff fails with 406
            MagicMock(
                returncode=1,
                stderr="HTTP 406: diff exceeded the maximum number of files",
            ),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        mock_local.return_value = "diff --git a/x b/x\n+local fallback diff"

        context = fetch_pr_context("o", "r", "9", project_path="/tmp/checkout")

        mock_local.assert_called_once_with(
            "/tmp/checkout", "o", "r", "9", "develop",
        )
        assert "local fallback diff" in context["diff"]
        assert context["diff_error"] == ""

    @patch("app.rebase_pr._fetch_diff_locally")
    @patch("app.github.subprocess.run")
    def test_diff_406_without_project_path_skips_fallback(
        self, mock_run, mock_local,
    ):
        """No project_path → no fallback attempt, diff stays empty."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "Huge PR",
                "headRefName": "feat/huge",
                "baseRefName": "main",
                "state": "OPEN",
                "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/9",
            })),
            MagicMock(returncode=0, stdout="0"),
            MagicMock(
                returncode=1,
                stderr="HTTP 406: diff exceeded the maximum number of files",
            ),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]

        context = fetch_pr_context("o", "r", "9")

        mock_local.assert_not_called()
        assert context["diff"] == ""
        assert "HTTP 406" in context["diff_error"]

    @patch("app.rebase_pr._fetch_diff_locally")
    @patch("app.github.subprocess.run")
    def test_diff_non_406_failure_skips_fallback(
        self, mock_run, mock_local,
    ):
        """Other gh failures (e.g. transient 5xx) should not trigger the
        local fallback — only the 'too many files' signature does."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "PR",
                "headRefName": "br",
                "baseRefName": "main",
                "state": "OPEN",
                "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=0, stdout="0"),
            MagicMock(returncode=1, stderr="HTTP 404: not found"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]

        context = fetch_pr_context("o", "r", "1", project_path="/tmp/checkout")

        mock_local.assert_not_called()
        assert context["diff"] == ""
        assert "HTTP 404" in context["diff_error"]

    @patch("app.github.subprocess.run")
    def test_comments_fetch_failure_graceful(self, mock_run):
        """API failures on comments should not crash the fetch."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "PR", "headRefName": "br", "baseRefName": "main",
                "state": "OPEN", "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=0, stdout="0"),  # review_comments count
            MagicMock(returncode=0, stdout="+diff"),
            # All comment APIs fail
            MagicMock(returncode=1, stderr="rate limited"),
            MagicMock(returncode=1, stderr="rate limited"),
            MagicMock(returncode=1, stderr="rate limited"),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert context["branch"] == "br"
        assert context["diff"] == "+diff"
        assert context["review_comments"] == ""
        assert context["reviews"] == ""
        assert context["issue_comments"] == ""

    @patch("app.rebase_pr.get_rebase_include_bot_feedback", return_value=False)
    @patch("app.github.subprocess.run")
    def test_filters_bot_feedback_when_disabled(self, mock_run, _mock_include_bots):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "PR", "headRefName": "br", "baseRefName": "main",
                "state": "OPEN", "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=0, stdout="0"),
            MagicMock(returncode=0, stdout="+diff"),
            MagicMock(
                returncode=0,
                stdout=(
                    "[f.py:10] @github-actions[bot]: bot inline\n"
                    "[f.py:11] @alice: human inline"
                ),
            ),
            MagicMock(
                returncode=0,
                stdout=(
                    "@github-actions[bot] (COMMENTED): bot review\n"
                    "@alice (COMMENTED): human review"
                ),
            ),
            MagicMock(
                returncode=0,
                stdout=(
                    "@github-actions[bot]: bot issue line 1\n"
                    "bot continuation\n"
                    "@alice: human issue"
                ),
            ),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert "@github-actions[bot]" not in context["review_comments"]
        assert "@github-actions[bot]" not in context["reviews"]
        assert "@github-actions[bot]" not in context["issue_comments"]
        assert "@alice: human issue" in context["issue_comments"]
        assert "@alice (COMMENTED): human review" in context["reviews"]
        assert "@alice: human inline" in context["review_comments"]

    @patch("app.rebase_pr.get_rebase_include_bot_feedback", return_value=True)
    @patch("app.github.subprocess.run")
    def test_can_include_bot_feedback_when_enabled(self, mock_run, _mock_include_bots):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "PR", "headRefName": "br", "baseRefName": "main",
                "state": "OPEN", "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=0, stdout="0"),
            MagicMock(returncode=0, stdout="+diff"),
            MagicMock(returncode=0, stdout="[f.py:10] @github-actions[bot]: bot inline"),
            MagicMock(returncode=0, stdout="@github-actions[bot] (COMMENTED): bot review"),
            MagicMock(returncode=0, stdout="@github-actions[bot]: bot issue"),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert "@github-actions[bot]" in context["review_comments"]
        assert "@github-actions[bot]" in context["reviews"]
        assert "@github-actions[bot]" in context["issue_comments"]

    @patch("app.rebase_pr.get_rebase_include_bot_feedback", return_value=False)
    @patch("app.github.subprocess.run")
    def test_pending_reviews_not_triggered_by_filtered_bot_comments(
        self, mock_run, _mock_include_bots,
    ):
        # The API reports inline review comments (count > 0), but they are all
        # bot-authored and get filtered out of the prompt. Pending-review
        # detection must count the raw comments so it does not false-positive.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "PR", "headRefName": "br", "baseRefName": "main",
                "state": "OPEN", "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=0, stdout="1"),  # .review_comments count
            MagicMock(returncode=0, stdout="+diff"),
            MagicMock(returncode=0, stdout="[f.py:10] @github-actions[bot]: bot inline"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        context = fetch_pr_context("o", "r", "1")
        # Bot comment filtered from the prompt, but no false pending warning.
        assert "@github-actions[bot]" not in context["review_comments"]
        assert context["has_pending_reviews"] is False

    @patch("app.github.subprocess.run")
    def test_detects_pending_reviews(self, mock_run):
        """Detect when GitHub reports review comments but API returns empty."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "PR", "headRefName": "br", "baseRefName": "main",
                "state": "OPEN", "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=0, stdout="2"),  # API says 2 review comments
            MagicMock(returncode=0, stdout="+diff"),
            MagicMock(returncode=0, stdout=""),    # but comments endpoint returns empty
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert context["has_pending_reviews"] is True

    @patch("app.github.subprocess.run")
    def test_no_pending_reviews_when_comments_fetched(self, mock_run):
        """No pending flag when review comments are successfully fetched."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "PR", "headRefName": "br", "baseRefName": "main",
                "state": "OPEN", "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=0, stdout="1"),  # API says 1 review comment
            MagicMock(returncode=0, stdout="+diff"),
            MagicMock(returncode=0, stdout="[file.py:10] @reviewer: Fix this"),  # fetched OK
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert context["has_pending_reviews"] is False

    @patch("app.retry.time.sleep")
    @patch("app.github.subprocess.run")
    def test_pending_review_count_fetch_failure_graceful(self, mock_run, mock_sleep):
        """If the review_comments count fetch fails twice, assume no pending reviews."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "PR", "headRefName": "br", "baseRefName": "main",
                "state": "OPEN", "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=1, stderr="rate limited"),  # count fetch fails (attempt 1)
            MagicMock(returncode=1, stderr="rate limited"),  # count fetch fails (retry)
            MagicMock(returncode=0, stdout="+diff"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert context["has_pending_reviews"] is False
        # retry_with_backoff handles sleep internally — it sleeps once between the two attempts
        assert mock_sleep.call_count == 1

    @patch("app.retry.time.sleep")
    @patch("app.github.subprocess.run")
    def test_pending_review_count_retry_succeeds(self, mock_run, mock_sleep):
        """If count fetch fails once but retry succeeds, use the retried value."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "PR", "headRefName": "br", "baseRefName": "main",
                "state": "OPEN", "author": {"login": "dev"},
                "url": "https://github.com/o/r/pull/1",
            })),
            MagicMock(returncode=1, stderr="transient error"),  # count fetch fails
            MagicMock(returncode=0, stdout="2"),                # retry succeeds
            MagicMock(returncode=0, stdout="+diff"),
            MagicMock(returncode=0, stdout=""),  # comments endpoint returns empty
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        context = fetch_pr_context("o", "r", "1")
        assert context["has_pending_reviews"] is True
        # retry_with_backoff sleeps once between the failed and successful attempt
        assert mock_sleep.call_count == 1


# ---------------------------------------------------------------------------
# _fetch_diff_locally
# ---------------------------------------------------------------------------

class TestFetchDiffLocally:
    @patch("app.rebase_pr._resolve_fetch_source", return_value=("origin", None))
    @patch("app.rebase_pr.subprocess.run")
    def test_runs_three_git_commands_and_returns_diff(self, mock_run, _mock_src):
        """The fallback fetches PR head + base then runs git diff."""
        from app.rebase_pr import _fetch_diff_locally

        # head fetch, base fetch, diff, then two best-effort update-ref deletes
        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stdout="diff --git a/f b/f\n+new", stderr=""),
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stderr=b""),
        ]

        diff = _fetch_diff_locally("/tmp/co", "o", "r", "42", "main")

        assert "diff --git" in diff
        # The first three calls should be git fetch / fetch / diff in that order
        first_three = [c.args[0][:2] for c in mock_run.call_args_list[:3]]
        assert first_three == [["git", "fetch"], ["git", "fetch"], ["git", "diff"]]
        # PR head fetch must use the pull/<N>/head refspec and the resolved remote
        head_fetch_args = mock_run.call_args_list[0].args[0]
        assert "origin" in head_fetch_args
        assert any("pull/42/head" in a for a in head_fetch_args)

    @patch("app.rebase_pr._resolve_fetch_source", return_value=(None, None))
    @patch("app.rebase_pr.subprocess.run")
    def test_returns_empty_when_no_fetch_source(self, mock_run, _mock_src):
        """No matching remote and no token → no fetch attempt, empty diff."""
        from app.rebase_pr import _fetch_diff_locally

        diff = _fetch_diff_locally("/tmp/co", "o", "r", "42", "main")

        assert diff == ""
        mock_run.assert_not_called()

    @patch("app.rebase_pr._token_fetch_url", return_value=(None, None))
    @patch("app.rebase_pr._resolve_fetch_source", return_value=("origin", None))
    @patch("app.rebase_pr.subprocess.run")
    def test_returns_empty_on_fetch_failure(self, mock_run, _mock_src, _mock_tok):
        """Returns empty string when the PR head fetch fails and no token retry is possible."""
        from app.rebase_pr import _fetch_diff_locally

        mock_run.side_effect = [
            MagicMock(returncode=128, stderr=b"fatal: couldn't find remote ref"),
            # Cleanup calls still run
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stderr=b""),
        ]

        diff = _fetch_diff_locally("/tmp/co", "o", "r", "42", "main")

        assert diff == ""

    @patch(
        "app.rebase_pr._token_fetch_url",
        return_value=("https://x-access-token:tok@github.com/o/r.git", "tok"),
    )
    @patch("app.rebase_pr._resolve_fetch_source", return_value=("origin", None))
    @patch("app.rebase_pr.subprocess.run")
    def test_retries_with_token_url_when_remote_fetch_fails(
        self, mock_run, _mock_src, _mock_tok,
    ):
        """An HTTPS remote without a helper fails, then the token-URL retry succeeds."""
        from app.rebase_pr import _fetch_diff_locally

        mock_run.side_effect = [
            # First attempt via "origin": head fetch fails (no credentials)
            MagicMock(
                returncode=128,
                stderr=b"fatal: could not read Username for 'https://github.com'",
            ),
            # Retry attempt via token URL: head, base, diff all succeed
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stdout="diff --git a/f b/f\n+new", stderr=""),
            # cleanup x2
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stderr=b""),
        ]

        diff = _fetch_diff_locally("/tmp/co", "o", "r", "42", "main")

        assert "diff --git" in diff
        # The retry (second call) must use the authenticated token URL
        retry_head_args = mock_run.call_args_list[1].args[0]
        assert any("x-access-token" in a for a in retry_head_args)

    @patch("app.rebase_pr._token_fetch_url", return_value=(None, None))
    @patch(
        "app.rebase_pr._resolve_fetch_source",
        return_value=("https://x-access-token:tok@github.com/o/r.git", "tok"),
    )
    @patch("app.rebase_pr.subprocess.run")
    def test_no_token_retry_when_source_already_token_url(
        self, mock_run, _mock_src, mock_tok,
    ):
        """If the resolved source is already a token URL, don't retry (would loop)."""
        from app.rebase_pr import _fetch_diff_locally

        mock_run.side_effect = [
            MagicMock(returncode=128, stderr=b"fatal: repository not found"),
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stderr=b""),
        ]

        diff = _fetch_diff_locally("/tmp/co", "o", "r", "42", "main")

        assert diff == ""
        # secret was not None, so the token-URL retry path must be skipped
        mock_tok.assert_not_called()

    @patch("app.rebase_pr._resolve_fetch_source", return_value=("origin", None))
    @patch("app.rebase_pr.subprocess.run")
    def test_cleans_up_temp_refs_on_success(self, mock_run, _mock_src):
        """Temp refs are deleted after a successful diff."""
        from app.rebase_pr import _fetch_diff_locally

        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stdout="+x", stderr=""),
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stderr=b""),
        ]

        _fetch_diff_locally("/tmp/co", "o", "r", "7", "main")

        cleanup_calls = [
            c for c in mock_run.call_args_list
            if c.args[0][:2] == ["git", "update-ref"]
        ]
        assert len(cleanup_calls) == 2

    @patch("app.rebase_pr._resolve_fetch_source")
    @patch("app.rebase_pr.subprocess.run")
    def test_redacts_token_from_error_logs(self, mock_run, mock_src, capsys):
        """A token in an authenticated URL must not leak into stderr logs."""
        from app.rebase_pr import _fetch_diff_locally

        token = "ghs_supersecret"
        url = f"https://x-access-token:{token}@github.com/o/r.git"
        mock_src.return_value = (url, token)
        mock_run.side_effect = [
            MagicMock(
                returncode=128,
                stderr=f"fatal: unable to access '{url}'".encode(),
            ),
            MagicMock(returncode=0, stderr=b""),
            MagicMock(returncode=0, stderr=b""),
        ]

        _fetch_diff_locally("/tmp/co", "o", "r", "42", "main")

        captured = capsys.readouterr()
        assert token not in captured.err
        assert "***" in captured.err


# ---------------------------------------------------------------------------
# _resolve_fetch_source
# ---------------------------------------------------------------------------

class TestResolveFetchSource:
    @patch("app.rebase_pr._find_remote_for_repo", return_value="origin")
    def test_prefers_matching_remote(self, _mock_find):
        from app.rebase_pr import _resolve_fetch_source

        source, secret = _resolve_fetch_source("o", "r", "/tmp/co")
        assert source == "origin"
        assert secret is None

    @patch("app.rebase_pr.run_gh", return_value="ghs_token123")
    @patch("app.rebase_pr._find_remote_for_repo", return_value=None)
    def test_falls_back_to_token_url(self, _mock_find, _mock_gh):
        from app.rebase_pr import _resolve_fetch_source

        source, secret = _resolve_fetch_source("o", "r", "/tmp/co")
        assert source == "https://x-access-token:ghs_token123@github.com/o/r.git"
        assert secret == "ghs_token123"

    @patch("app.rebase_pr.run_gh", side_effect=RuntimeError("not logged in"))
    @patch("app.rebase_pr._find_remote_for_repo", return_value=None)
    def test_returns_none_when_no_remote_and_no_token(self, _mock_find, _mock_gh):
        from app.rebase_pr import _resolve_fetch_source

        source, secret = _resolve_fetch_source("o", "r", "/tmp/co")
        assert source is None
        assert secret is None


class TestTokenFetchUrl:
    @patch("app.rebase_pr.run_gh", return_value="ghs_abc")
    def test_builds_authenticated_url(self, _mock_gh):
        from app.rebase_pr import _token_fetch_url

        url, secret = _token_fetch_url("o", "r")
        assert url == "https://x-access-token:ghs_abc@github.com/o/r.git"
        assert secret == "ghs_abc"

    @patch("app.rebase_pr.run_gh", return_value="")
    def test_returns_none_when_token_empty(self, _mock_gh):
        from app.rebase_pr import _token_fetch_url

        url, secret = _token_fetch_url("o", "r")
        assert url is None
        assert secret is None

    @patch("app.rebase_pr.run_gh", side_effect=RuntimeError("not logged in"))
    def test_returns_none_when_gh_fails(self, _mock_gh):
        from app.rebase_pr import _token_fetch_url

        url, secret = _token_fetch_url("o", "r")
        assert url is None
        assert secret is None


# ---------------------------------------------------------------------------
# _push_with_fallback
# ---------------------------------------------------------------------------

class TestPushWithFallback:
    def test_successful_force_with_lease(self):
        """Happy path: force-with-lease on origin succeeds."""
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("app.claude_step.subprocess.run", return_value=mock_result):
            result = _push_with_fallback(
                "koan/fix", "main", "sukria/koan", "42",
                {"title": "Fix", "url": "https://..."}, "/project"
            )
            assert result["success"] is True
            assert any("Force-pushed" in a for a in result["actions"])
            assert any("origin" in a for a in result["actions"])

    def test_falls_back_to_plain_force_on_origin(self):
        """If force-with-lease fails on origin, tries plain --force on origin."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if "--force-with-lease" in cmd:
                raise RuntimeError("stale tracking ref")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _push_with_fallback(
                "koan/fix", "main", "sukria/koan", "42",
                {"title": "Fix", "url": "https://..."}, "/project"
            )
            assert result["success"] is True
            push_cmds = [c for c in calls if c[:2] == ["git", "push"]]
            assert any("--force" in c and "--force-with-lease" not in c for c in push_cmds)

    def test_falls_back_to_upstream(self):
        """If both origin push strategies fail, tries upstream."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["git", "push"] and "origin" in cmd:
                raise RuntimeError("permission denied")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _push_with_fallback(
                "koan/fix", "main", "sukria/koan", "42",
                {"title": "Fix", "url": "https://..."}, "/project"
            )
            assert result["success"] is True
            assert any("upstream" in a for a in result["actions"])

    def test_never_creates_new_pr(self):
        """When all pushes fail, should fail — NOT create a new branch/PR."""
        def mock_run(cmd, **kwargs):
            if cmd[:2] == ["git", "push"]:
                raise RuntimeError("permission denied on all remotes")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _push_with_fallback(
                "koan/fix", "main", "sukria/koan", "42",
                {"title": "Fix", "url": ""}, "/project"
            )
            assert result["success"] is False
            assert "all remotes rejected" in result["error"]
            # Must NOT contain any "new branch" or "draft PR" actions
            assert not any("new branch" in a.lower() for a in result["actions"])
            assert not any("draft PR" in a for a in result["actions"])

    def test_all_remotes_fail_returns_error(self):
        """Comprehensive failure: all 4 push attempts (2 remotes x 2 strategies) fail."""
        push_count = [0]
        def mock_run(cmd, **kwargs):
            if cmd[:2] == ["git", "push"]:
                push_count[0] += 1
                raise RuntimeError("rejected")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _push_with_fallback(
                "koan/fix", "main", "sukria/koan", "42",
                {"title": "Fix", "url": ""}, "/project"
            )
            assert result["success"] is False
            assert push_count[0] == 4  # 2 remotes x 2 strategies

    def test_origin_only_repo_does_not_try_upstream(self):
        """When only origin exists, push fallback must stop after origin attempts."""
        push_count = [0]

        def mock_run(cmd, **kwargs):
            if cmd[:2] == ["git", "push"]:
                push_count[0] += 1
                raise RuntimeError("rejected")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.rebase_pr._ordered_remotes", return_value=["origin"]), \
             patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _push_with_fallback(
                "koan/fix", "main", "sukria/koan", "42",
                {"title": "Fix", "url": ""}, "/project"
            )

        assert result["success"] is False
        assert push_count[0] == 2  # origin: --force-with-lease + --force


# ---------------------------------------------------------------------------
# run_rebase — integration tests
# ---------------------------------------------------------------------------

class TestRunRebase:
    @pytest.fixture(autouse=True)
    def mock_already_solved(self):
        with patch("app.rebase_pr._check_if_already_solved", return_value=(False, None)):
            yield

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr._run_git")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_successful_rebase(self, mock_ctx, mock_git, mock_gh, mock_safe, mock_ci_check, mock_fix_ci):
        mock_ctx.return_value = {
            "title": "Fix auth",
            "body": "Fix",
            "branch": "koan/fix-auth",
            "base": "main",
            "state": "OPEN",
            "author": "sukria",
            "url": "https://...",
            "diff": "",
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
        }
        mock_git.return_value = "ok"
        notify = MagicMock()

        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed `koan/fix-auth`"], "error": ""
             }):
            success, summary = run_rebase(
                "sukria", "koan", "42", "/project", notify_fn=notify
            )
            assert success is True
            assert "Rebased" in summary

    @patch("app.rebase_pr.fetch_pr_context")
    def test_fetch_failure(self, mock_ctx):
        mock_ctx.side_effect = RuntimeError("network error")
        notify = MagicMock()
        success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)
        assert success is False
        assert "Failed to fetch" in summary

    @patch("app.rebase_pr.fetch_pr_context")
    def test_skip_merged_pr(self, mock_ctx):
        """Rebase should skip and succeed when the PR is already merged."""
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "MERGED", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)
        assert success is True
        assert "merged" in summary.lower()

    @patch("app.rebase_pr.fetch_pr_context")
    def test_skip_closed_pr(self, mock_ctx):
        """Rebase should skip and succeed when the PR is closed."""
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "CLOSED", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)
        assert success is True
        assert "closed" in summary.lower()

    @patch("app.rebase_pr.fetch_pr_context")
    def test_missing_branch(self, mock_ctx):
        mock_ctx.return_value = {"branch": "", "base": "main", "title": "T",
                                  "body": "", "state": "", "author": "", "url": "",
                                  "diff": "", "review_comments": "", "reviews": "", "issue_comments": ""}
        notify = MagicMock()
        success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)
        assert success is False
        assert "branch name" in summary.lower()

    @patch("app.rebase_pr.fetch_pr_context")
    def test_checkout_failure(self, mock_ctx):
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch", side_effect=RuntimeError("no such branch")):
            success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)
            assert success is False
            assert "checkout" in summary.lower()

    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_rebase_conflict_restores_branch(self, mock_ctx, mock_safe):
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="original"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value=None):
            success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)
            assert success is False
            assert "conflict" in summary.lower()
            mock_safe.assert_called_with("original", "/p")

    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_rebase_conflict_lists_actual_attempted_remotes(self, mock_ctx, mock_safe):
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="original"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._ordered_remotes", return_value=["origin"]), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value=None):
            success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)

        assert success is False
        assert "tried: origin" in summary
        assert "upstream" not in summary

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_comment_failure_non_fatal(self, mock_ctx, mock_gh, mock_safe, mock_ci_check, mock_fix_ci):
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        mock_gh.side_effect = RuntimeError("no perms to comment")
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)
            assert success is True
            assert "Comment failed" in summary

    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr._checkout_pr_branch")
    @patch("app.rebase_pr._rebase_with_conflict_resolution")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_warns_on_pending_reviews(self, mock_ctx, mock_rebase, mock_checkout, mock_safe):
        """Rebase should warn but proceed when pending reviews are detected."""
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
            "has_pending_reviews": True,
        }
        mock_checkout.return_value = "origin"
        mock_rebase.return_value = None  # rebase fails (not the point of this test)
        notify = MagicMock()
        success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)
        # Should have warned via notify_fn about pending reviews
        pending_calls = [c for c in notify.call_args_list if "pending" in str(c).lower()]
        assert len(pending_calls) >= 1
        # Should NOT have aborted — it proceeded to the rebase step
        mock_checkout.assert_called_once()

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_logs_comments_read(self, mock_ctx, mock_apply, mock_gh, mock_safe, mock_ci_check, mock_fix_ci):
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "+code", "review_comments": "@reviewer: fix this",
            "reviews": "@reviewer (CHANGES_REQUESTED): please fix",
            "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify)
            assert success is True
            assert "comments" in summary.lower()
            # Claude step should be called when feedback exists
            mock_apply.assert_called_once()

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_restores_branch_after_success(self, mock_ctx, mock_gh, mock_safe, mock_ci_check, mock_fix_ci):
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="original"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            run_rebase("o", "r", "1", "/p", notify_fn=notify)
            mock_safe.assert_called_with("original", "/p")

    @patch("app.rebase_pr.fetch_pr_context")
    def test_default_notify_fn(self, mock_ctx):
        """When no notify_fn provided, defaults to send_telegram."""
        mock_ctx.return_value = {"branch": "", "base": "main", "title": "",
                                  "body": "", "state": "", "author": "", "url": "",
                                  "diff": "", "review_comments": "", "reviews": "", "issue_comments": ""}
        with patch("app.notify.send_telegram") as mock_tg:
            success, _ = run_rebase("o", "r", "1", "/p")
            mock_tg.assert_called()

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_passes_preferred_remote_to_rebase(self, mock_ctx, mock_gh, mock_safe, mock_ci_check, mock_fix_ci):
        """run_rebase must determine the correct base remote and pass it through."""
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "koan/fix",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._find_remote_for_repo", return_value="upstream") as mock_find, \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="upstream") as mock_rebase, \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            run_rebase("cpan-authors", "Crypt-OpenSSL-RSA", "87", "/p", notify_fn=notify)
            mock_find.assert_called_once_with("cpan-authors", "Crypt-OpenSSL-RSA", "/p")
            mock_rebase.assert_called_once()
            # Verify preferred_remote kwarg was passed
            _, kwargs = mock_rebase.call_args
            assert kwargs.get("preferred_remote") == "upstream"


# ---------------------------------------------------------------------------
# _push_with_fallback — cross-linking
# ---------------------------------------------------------------------------

class TestPushBranchRecycling:
    def test_reuses_same_branch_name(self):
        """Push must always target the original branch name, never create a new one."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _push_with_fallback(
                "koan/fix", "main", "sukria/koan", "42",
                {"title": "Fix", "url": "https://..."}, "/project"
            )
            assert result["success"] is True
            push_cmds = [c for c in calls if c[:2] == ["git", "push"]]
            # All push commands must target the original branch name
            for push_cmd in push_cmds:
                assert "koan/fix" in push_cmd
                # Must NOT create a new branch with a different name
                assert "-b" not in push_cmd
                assert "-u" not in push_cmd


# ---------------------------------------------------------------------------
# _build_rebase_prompt
# ---------------------------------------------------------------------------

REBASE_SKILL_DIR = Path(__file__).parent.parent / "skills" / "core" / "rebase"


class TestBuildRebasePrompt:
    def test_builds_prompt_with_skill_dir(self):
        context = {
            "title": "Fix auth",
            "body": "Fixes a bug",
            "branch": "koan/fix-auth",
            "base": "main",
            "diff": "+some code",
            "review_comments": "@reviewer: fix this",
            "reviews": "@reviewer (CHANGES_REQUESTED): Please fix",
            "issue_comments": "@author: will do",
        }
        prompt = _build_rebase_prompt(context, skill_dir=REBASE_SKILL_DIR)
        assert "Fix auth" in prompt
        assert "koan/fix-auth" in prompt
        assert "+some code" in prompt
        assert "fix this" in prompt
        assert "Please fix" in prompt

    def test_prompt_without_skill_dir_falls_back(self):
        """Without skill_dir, falls back to system-prompts directory."""
        context = {
            "title": "T", "body": "", "branch": "br", "base": "main",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        # This will raise FileNotFoundError since rebase.md doesn't exist
        # in system-prompts/, which is expected — the skill_dir path is
        # the intended usage
        with pytest.raises(FileNotFoundError):
            _build_rebase_prompt(context, skill_dir=None)


# ---------------------------------------------------------------------------
# _apply_review_feedback
# ---------------------------------------------------------------------------

class TestApplyReviewFeedback:
    @patch("app.rebase_pr.run_claude_step")
    def test_invokes_claude_step_and_returns_summary(self, mock_step):
        from app.claude_step import StepResult
        mock_step.return_value = StepResult(committed=True, output="Changed things.")
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        actions = []
        summary = _apply_review_feedback(
            context, "42", "/project", actions,
            skill_dir=REBASE_SKILL_DIR,
        )
        mock_step.assert_called_once()
        call_kwargs = mock_step.call_args.kwargs
        assert call_kwargs["commit_msg"] == "rebase: apply review feedback on #42"
        assert call_kwargs["success_label"] == "Applied review feedback"
        assert summary == "Changed things."

    @patch("app.rebase_pr.get_rebase_review_max_duration", return_value=10800)
    @patch("app.rebase_pr.get_rebase_review_idle_timeout", return_value=1800)
    @patch("app.rebase_pr.get_skill_timeout", return_value=7200)
    @patch("app.rebase_pr.get_skill_max_turns", return_value=200)
    @patch("app.rebase_pr.run_claude_step")
    def test_passes_activity_based_review_timeouts(
        self, mock_step, mock_turns, mock_timeout, mock_idle, mock_max_duration,
    ):
        from app.claude_step import StepResult

        mock_step.return_value = StepResult(committed=True, output="Changed things.")
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        actions = []
        _apply_review_feedback(
            context, "42", "/project", actions, skill_dir=REBASE_SKILL_DIR,
        )
        call_kwargs = mock_step.call_args.kwargs
        assert call_kwargs["timeout"] == 7200
        assert call_kwargs["idle_timeout"] == 1800
        assert call_kwargs["max_duration"] == 10800

    @patch("app.rebase_pr.run_claude_step")
    def test_passes_success_label(self, mock_step):
        from app.claude_step import StepResult
        mock_step.return_value = StepResult(committed=True, output="Applied changes.")
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        actions = []
        _apply_review_feedback(
            context, "42", "/project", actions,
            skill_dir=REBASE_SKILL_DIR,
        )
        # Verify run_claude_step receives the actions_log and correct label
        call_kwargs = mock_step.call_args.kwargs
        assert call_kwargs["success_label"] == "Applied review feedback"
        assert call_kwargs["actions_log"] is actions

    @patch("app.rebase_pr.run_claude_step")
    def test_returns_empty_when_no_commit(self, mock_step):
        from app.claude_step import StepResult
        mock_step.return_value = StepResult(committed=False, output="No changes needed.")
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        actions = []
        summary = _apply_review_feedback(
            context, "42", "/project", actions,
            skill_dir=REBASE_SKILL_DIR,
        )
        assert summary == ""

    @patch("app.rebase_pr.run_claude_step")
    def test_sets_feedback_timeout_metadata(self, mock_step):
        from app.claude_step import StepResult

        mock_step.return_value = StepResult(
            committed=False, output="", error="Timeout (600s)",
        )
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        actions = []
        meta = {}
        summary = _apply_review_feedback(
            context, "42", "/project", actions,
            skill_dir=REBASE_SKILL_DIR,
            result_meta=meta,
        )
        assert summary == ""
        assert meta["status"] == "feedback_timeout"
        assert "timed out" in actions[-1].lower()

    @patch("app.rebase_pr.run_claude_step")
    def test_sets_feedback_failed_metadata(self, mock_step):
        from app.claude_step import StepResult

        mock_step.return_value = StepResult(
            committed=False, output="", error="Exit code 1: no stderr",
        )
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        actions = []
        meta = {}
        summary = _apply_review_feedback(
            context, "42", "/project", actions,
            skill_dir=REBASE_SKILL_DIR,
            result_meta=meta,
        )
        assert summary == ""
        assert meta["status"] == "feedback_failed"
        assert "feedback failed" in actions[-1].lower()

    @patch("app.rebase_pr.run_claude_step")
    def test_sets_feedback_quota_metadata(self, mock_step):
        from app.claude_step import StepResult

        mock_step.return_value = StepResult(
            committed=False,
            output="You've hit your session limit",
            quota_exhausted=True,
            error="quota exhausted",
        )
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        actions = []
        meta = {}
        summary = _apply_review_feedback(
            context, "42", "/project", actions,
            skill_dir=REBASE_SKILL_DIR,
            result_meta=meta,
        )
        assert summary == ""
        assert meta["status"] == "feedback_quota"
        assert "quota" in actions[-1].lower()


# ---------------------------------------------------------------------------
# run_rebase — Claude step integration
# ---------------------------------------------------------------------------

class TestRunRebaseClaude:
    @pytest.fixture(autouse=True)
    def mock_already_solved(self):
        with patch("app.rebase_pr._check_if_already_solved", return_value=(False, None)):
            yield

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_claude_step_called_with_feedback(self, mock_ctx, mock_apply, mock_gh, mock_safe, mock_ci_check, mock_fix_ci):
        mock_ctx.return_value = {
            "title": "Fix auth", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "+code", "review_comments": "@reviewer: fix this",
            "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            success, _ = run_rebase("o", "r", "1", "/p", notify_fn=notify,
                                     skill_dir=REBASE_SKILL_DIR)
            assert success is True
            mock_apply.assert_called_once()

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_claude_step_skipped_without_feedback(self, mock_ctx, mock_apply, mock_gh, mock_safe, mock_ci_check, mock_fix_ci):
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "+code", "review_comments": "",
            "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            success, _ = run_rebase("o", "r", "1", "/p", notify_fn=notify)
            assert success is True
            mock_apply.assert_not_called()

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_skill_dir_passed_to_apply(self, mock_ctx, mock_apply, mock_gh, mock_safe, mock_ci_check, mock_fix_ci):
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "feedback",
            "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            run_rebase("o", "r", "1", "/p", notify_fn=notify,
                       skill_dir=REBASE_SKILL_DIR)
            call_kwargs = mock_apply.call_args
            assert call_kwargs[1].get("skill_dir") == REBASE_SKILL_DIR

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_claude_branch_switch_restored_after_feedback(
        self, mock_ctx, mock_apply, mock_gh, mock_safe, mock_ci_check, mock_fix_ci
    ):
        """If Claude switches branches during feedback, we restore the PR branch."""
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()

        # _get_current_branch returns different values:
        # 1st call: "main" (original branch before checkout)
        # 2nd call: "koan/some-branch" (Claude switched during feedback)
        branch_calls = iter(["main", "koan/some-branch"])
        with patch("app.rebase_pr._get_current_branch", side_effect=branch_calls), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify,
                                          skill_dir=REBASE_SKILL_DIR)
            assert success is True
            # _safe_checkout should be called to restore the PR branch
            # (once for restoration + once at end for original branch)
            checkout_calls = [c[0][0] for c in mock_safe.call_args_list]
            assert "feat" in checkout_calls  # restored to PR branch

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_claude_stays_on_branch_no_restore(
        self, mock_ctx, mock_apply, mock_gh, mock_safe, mock_ci_check, mock_fix_ci
    ):
        """If Claude stays on the correct branch, no extra checkout happens."""
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()

        # _get_current_branch returns "feat" after feedback (stayed on branch)
        branch_calls = iter(["main", "feat"])
        with patch("app.rebase_pr._get_current_branch", side_effect=branch_calls), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            success, summary = run_rebase("o", "r", "1", "/p", notify_fn=notify,
                                          skill_dir=REBASE_SKILL_DIR)
            assert success is True
            # _safe_checkout should only be called at the end (original branch)
            # NOT for branch restoration since Claude stayed on correct branch
            restore_calls = [
                c for c in mock_safe.call_args_list
                if c[0][0] == "feat"
            ]
            assert len(restore_calls) == 0  # no restoration needed

    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_feedback_timeout_pushes_rebase_without_feedback(
        self, mock_ctx, mock_apply, mock_safe,
    ):
        mock_ctx.return_value = {
            "title": "Fix auth", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "+code", "review_comments": "@reviewer: fix this",
            "reviews": "", "issue_comments": "",
        }

        def _apply_side_effect(*args, **kwargs):
            kwargs["result_meta"]["status"] = "feedback_timeout"
            kwargs["result_meta"]["error"] = "Timeout (600s)"
            return ""

        mock_apply.side_effect = _apply_side_effect
        notify = MagicMock()
        with patch("app.rebase_pr._check_if_already_solved", return_value=(False, None)), \
             patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._run_git", side_effect=["abc123\n", ""]), \
             patch("app.rebase_pr._fix_existing_ci_failures", return_value=False), \
             patch("app.rebase_pr._enqueue_ci_check", return_value="CI queued"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": "",
             }) as mock_push, \
             patch("app.rebase_pr.run_gh"):
            success, summary = run_rebase(
                "o", "r", "1", "/p", notify_fn=notify, skill_dir=REBASE_SKILL_DIR,
            )

        assert success is True
        assert "Review feedback timed out" in summary
        assert "rebase-only push" in summary
        mock_push.assert_called_once()

    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch(
        "app.rebase_pr._build_rebase_recovery_guidance",
        return_value="Recovery hints:\n- next: git status",
    )
    @patch("app.rebase_pr.fetch_pr_context")
    def test_feedback_timeout_recovery_failure_returns_error(
        self, mock_ctx, _mock_guidance, mock_apply, mock_safe,
    ):
        mock_ctx.return_value = {
            "title": "Fix auth", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "+code", "review_comments": "@reviewer: fix this",
            "reviews": "", "issue_comments": "",
        }

        def _apply_side_effect(*args, **kwargs):
            kwargs["result_meta"]["status"] = "feedback_timeout"
            kwargs["result_meta"]["error"] = "Timeout (600s)"
            return ""

        mock_apply.side_effect = _apply_side_effect
        notify = MagicMock()

        def _run_git_side_effect(cmd, **kwargs):
            if cmd[:2] == ["git", "rev-parse"]:
                return "abc123\n"
            if cmd[:3] == ["git", "reset", "--hard"]:
                raise RuntimeError("reset failed")
            return ""

        with patch("app.rebase_pr._check_if_already_solved", return_value=(False, None)), \
             patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._run_git", side_effect=_run_git_side_effect):
            success, summary = run_rebase(
                "o", "r", "1", "/p", notify_fn=notify, skill_dir=REBASE_SKILL_DIR,
            )

        assert success is False
        assert "automatic recovery" in summary
        assert "Recovery hints" in summary

    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch(
        "app.rebase_pr._build_rebase_recovery_guidance",
        return_value="Recovery hints:\n- next: git status",
    )
    @patch("app.rebase_pr.fetch_pr_context")
    def test_feedback_quota_returns_classified_failure(
        self, mock_ctx, mock_guidance, mock_apply, mock_safe,
    ):
        mock_ctx.return_value = {
            "title": "Fix auth", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "+code", "review_comments": "@reviewer: fix this",
            "reviews": "", "issue_comments": "",
        }

        def _apply_side_effect(*args, **kwargs):
            kwargs["result_meta"]["status"] = "feedback_quota"
            kwargs["result_meta"]["error"] = "quota exhausted"
            return ""

        mock_apply.side_effect = _apply_side_effect
        notify = MagicMock()
        with patch("app.rebase_pr._check_if_already_solved", return_value=(False, None)), \
             patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"):
            success, summary = run_rebase(
                "o", "r", "1", "/p", notify_fn=notify, skill_dir=REBASE_SKILL_DIR,
            )

        assert success is False
        assert "[feedback_quota]" in summary
        assert "Recovery hints" in summary

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr._apply_review_feedback")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_feedback_failed_pushes_rebase_best_effort(
        self, mock_ctx, mock_apply, mock_gh, mock_safe, mock_ci_check, mock_fix_ci,
    ):
        """A non-timeout, non-quota feedback error must not discard a clean
        rebase — the rebase should still be pushed, with a note."""
        mock_ctx.return_value = {
            "title": "Fix auth", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "+code", "review_comments": "@reviewer: fix this",
            "reviews": "", "issue_comments": "",
        }

        def _apply_side_effect(*args, **kwargs):
            kwargs["result_meta"]["status"] = "feedback_failed"
            kwargs["result_meta"]["error"] = "Exit code 1: no stderr"
            return ""

        mock_apply.side_effect = _apply_side_effect
        notify = MagicMock()
        push_mock = MagicMock(return_value={
            "success": True, "actions": ["Force-pushed"], "error": "",
        })
        with patch("app.rebase_pr._check_if_already_solved", return_value=(False, None)), \
             patch("app.rebase_pr._get_current_branch", return_value="feat"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", push_mock):
            success, summary = run_rebase(
                "o", "r", "1", "/p", notify_fn=notify, skill_dir=REBASE_SKILL_DIR,
            )

        assert success is True
        assert "[feedback_failed]" not in summary
        # The rebase was still pushed despite the feedback error.
        push_mock.assert_called_once()


# ---------------------------------------------------------------------------
# main() CLI entry point
# ---------------------------------------------------------------------------

from app.rebase_pr import main as rebase_main


class TestMain:
    def test_main_success(self):
        with patch("app.rebase_pr.run_rebase", return_value=(True, "Rebased OK")):
            code = rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            assert code == 0

    def test_main_failure(self):
        with patch("app.rebase_pr.run_rebase", return_value=(False, "Conflict")):
            code = rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            assert code == 1

    def test_main_invalid_url(self):
        code = rebase_main(["not-a-url", "--project-path", "/p"])
        assert code == 1

    def test_main_skill_dir_resolved(self):
        """Verify skill_dir is correctly computed relative to rebase_pr.py."""
        with patch("app.rebase_pr.run_rebase", return_value=(True, "OK")) as mock_rebase:
            rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            call_kwargs = mock_rebase.call_args
            skill_dir = call_kwargs[1].get("skill_dir")
            assert skill_dir is not None
            assert str(skill_dir).endswith("skills/core/rebase")

    def test_main_conflict_falls_back_to_recreate(self):
        """On rebase conflict, main() should fall back to /recreate."""
        conflict_msg = "Rebase conflict on `main` (tried origin and upstream). Manual resolution required."
        open_ctx = {"state": "OPEN", "branch": "feat", "base": "main"}
        with patch("app.rebase_pr.run_rebase", return_value=(False, conflict_msg)), \
             patch("app.rebase_pr.fetch_pr_context", return_value=open_ctx), \
             patch("app.recreate_pr.run_recreate", return_value=(True, "PR #42 recreated.")) as mock_recreate:
            code = rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            assert code == 0
            mock_recreate.assert_called_once()
            call_args = mock_recreate.call_args
            assert call_args[0][:3] == ("sukria", "koan", "42")
            assert call_args[0][3] == "/project"
            assert str(call_args[1]["skill_dir"]).endswith("skills/core/recreate")

    def test_main_non_conflict_failure_no_fallback(self):
        """Non-conflict failures should NOT trigger recreate fallback."""
        with patch("app.rebase_pr.run_rebase", return_value=(False, "Push failed: auth error")), \
             patch("app.recreate_pr.run_recreate") as mock_recreate:
            code = rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            assert code == 1
            mock_recreate.assert_not_called()

    def test_main_conflict_recreate_also_fails(self):
        """If recreate also fails after conflict, exit code should be 1."""
        conflict_msg = "Rebase conflict on `main` (tried origin and upstream). Manual resolution required."
        open_ctx = {"state": "OPEN", "branch": "feat", "base": "main"}
        with patch("app.rebase_pr.run_rebase", return_value=(False, conflict_msg)), \
             patch("app.rebase_pr.fetch_pr_context", return_value=open_ctx), \
             patch("app.recreate_pr.run_recreate", return_value=(False, "Recreation failed.")):
            code = rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            assert code == 1

    def test_main_conflict_merged_pr_no_fallback(self):
        """On conflict with a merged PR, should NOT fall back to recreate."""
        conflict_msg = "Rebase conflict on `main` (tried origin and upstream). Manual resolution required."
        merged_ctx = {"state": "MERGED", "branch": "feat", "base": "main"}
        with patch("app.rebase_pr.run_rebase", return_value=(False, conflict_msg)), \
             patch("app.rebase_pr.fetch_pr_context", return_value=merged_ctx), \
             patch("app.recreate_pr.run_recreate") as mock_recreate:
            code = rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            assert code == 1
            mock_recreate.assert_not_called()

    def test_main_conflict_closed_pr_no_fallback(self):
        """On conflict with a closed PR, should NOT fall back to recreate."""
        conflict_msg = "Rebase conflict on `main` (tried origin and upstream). Manual resolution required."
        closed_ctx = {"state": "CLOSED", "branch": "feat", "base": "main"}
        with patch("app.rebase_pr.run_rebase", return_value=(False, conflict_msg)), \
             patch("app.rebase_pr.fetch_pr_context", return_value=closed_ctx), \
             patch("app.recreate_pr.run_recreate") as mock_recreate:
            code = rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            assert code == 1
            mock_recreate.assert_not_called()

    def test_main_conflict_fetch_failure_still_falls_back(self):
        """If fetch_pr_context fails in fallback, proceed with recreate anyway."""
        conflict_msg = "Rebase conflict on `main` (tried origin and upstream). Manual resolution required."
        with patch("app.rebase_pr.run_rebase", return_value=(False, conflict_msg)), \
             patch("app.rebase_pr.fetch_pr_context", side_effect=RuntimeError("API error")), \
             patch("app.recreate_pr.run_recreate", return_value=(True, "PR #42 recreated.")) as mock_recreate:
            code = rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            assert code == 0
            mock_recreate.assert_called_once()
    def test_detects_conflict_message(self):
        msg = "Rebase conflict on `main` (tried origin and upstream). Manual resolution required."
        assert _is_conflict_failure(msg) is True

    def test_detects_classified_conflict_tag(self):
        msg = "[conflict_unresolved] Rebase failed on `main`."
        assert _is_conflict_failure(msg) is True

    def test_rejects_non_conflict(self):
        assert _is_conflict_failure("Push failed: auth error") is False

    def test_rejects_empty(self):
        assert _is_conflict_failure("") is False


# ---------------------------------------------------------------------------
# --onto rebase (cross-fork PR support)
# ---------------------------------------------------------------------------

class TestRebaseOntoTarget_OntoMode:
    """Tests for --onto rebase when head_remote differs from target remote."""

    def test_uses_onto_when_fork_diverged(self):
        """--onto should be used when fork has genuinely diverged from upstream."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run), \
             patch("app.claude_step._is_ancestor", return_value=False):
            result = _rebase_onto_target(
                "main", "/project",
                preferred_remote="upstream",
                head_remote="origin",
            )

        assert result == "upstream"
        # Should have fetched both remotes' base branches
        fetch_cmds = [c for c in calls if c[:2] == ["git", "fetch"]]
        assert ["git", "fetch", "upstream", "+refs/heads/main:refs/remotes/upstream/main"] in fetch_cmds
        # Should use --onto
        rebase_cmds = [c for c in calls if "rebase" in c and "--abort" not in c]
        assert len(rebase_cmds) == 1
        assert "--onto" in rebase_cmds[0]
        assert "upstream/main" in rebase_cmds[0]
        assert "origin/main" in rebase_cmds[0]

    def test_skips_onto_when_fork_is_behind(self):
        """When fork is simply behind upstream, skip --onto and use plain rebase."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run), \
             patch("app.claude_step._is_ancestor", return_value=True):
            result = _rebase_onto_target(
                "main", "/project",
                preferred_remote="upstream",
                head_remote="origin",
            )

        assert result == "upstream"
        rebase_cmds = [c for c in calls if "rebase" in c and "--abort" not in c]
        assert len(rebase_cmds) == 1
        assert "--onto" not in rebase_cmds[0]

    def test_plain_rebase_when_head_remote_same_as_target(self):
        """When head_remote == target remote, use plain rebase (same-repo PR)."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _rebase_onto_target(
                "main", "/project",
                preferred_remote="origin",
                head_remote="origin",
            )

        assert result == "origin"
        rebase_cmds = [c for c in calls if "rebase" in c and "--abort" not in c]
        assert len(rebase_cmds) == 1
        assert "--onto" not in rebase_cmds[0]

    def test_plain_rebase_when_head_remote_is_none(self):
        """When head_remote is None, use plain rebase."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _rebase_onto_target("main", "/project", head_remote=None)

        assert result == "origin"
        rebase_cmds = [c for c in calls if "rebase" in c and "--abort" not in c]
        assert len(rebase_cmds) == 1
        assert "--onto" not in rebase_cmds[0]

    def test_onto_failure_falls_back_to_plain_rebase(self):
        """If --onto rebase fails, fall back to plain rebase."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if "rebase" in cmd and "--onto" in cmd:
                raise RuntimeError("onto rebase conflict")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run), \
             patch("app.claude_step._is_ancestor", return_value=False):
            result = _rebase_onto_target(
                "main", "/project",
                preferred_remote="upstream",
                head_remote="origin",
            )

        assert result == "upstream"
        rebase_cmds = [c for c in calls if "rebase" in c and "--abort" not in c]
        # Should have tried --onto first, then plain rebase
        assert len(rebase_cmds) == 2
        assert "--onto" in rebase_cmds[0]
        assert "--onto" not in rebase_cmds[1]

    def test_onto_head_remote_fetch_failure_falls_back(self):
        """If fetching head_remote/base fails, fall back to plain rebase."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            # head_remote fetch fails
            if cmd[:3] == ["git", "fetch", "origin"] and any("main" in arg for arg in cmd):
                raise RuntimeError("fetch failed")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _rebase_onto_target(
                "main", "/project",
                preferred_remote="upstream",
                head_remote="origin",
            )

        assert result == "upstream"
        # Should have fallen back to plain rebase
        rebase_cmds = [c for c in calls if "rebase" in c and "--abort" not in c]
        assert len(rebase_cmds) == 1
        assert "--onto" not in rebase_cmds[0]


class TestRebaseWithConflictResolution_OntoMode:
    """Tests for --onto rebase in _rebase_with_conflict_resolution."""

    def _base_context(self):
        return {
            "title": "Fix", "body": "", "branch": "feat",
            "base": "main", "diff": "", "review_comments": "",
            "reviews": "", "issue_comments": "",
        }

    def test_uses_onto_when_fork_diverged(self):
        """--onto should be used when fork has genuinely diverged."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run), \
             patch("app.claude_step._is_ancestor", return_value=False):
            result = _rebase_with_conflict_resolution(
                "main", "/project", self._base_context(), [],
                preferred_remote="upstream",
                head_remote="origin",
            )

        assert result == "upstream"
        rebase_cmds = [c for c in calls if "rebase" in c and "--abort" not in c]
        assert any("--onto" in c for c in rebase_cmds)

    def test_plain_rebase_when_same_remote(self):
        """Same-repo PR: head_remote == target, no --onto."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _rebase_with_conflict_resolution(
                "main", "/project", self._base_context(), [],
                preferred_remote="origin",
                head_remote="origin",
            )

        assert result == "origin"
        rebase_cmds = [c for c in calls if "rebase" in c and "--abort" not in c]
        assert all("--onto" not in c for c in rebase_cmds)

    def test_onto_failure_falls_back_to_plain_rebase(self):
        """If --onto fails (non-conflict), should fall back to plain rebase."""
        calls = []
        rebase_dir = MagicMock()
        rebase_dir.exists.return_value = False

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if "rebase" in cmd and "--onto" in cmd:
                raise RuntimeError("onto failed")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run), \
             patch("app.claude_step._is_ancestor", return_value=False), \
             patch("app.claude_step.has_rebase_in_progress", return_value=False):
            result = _rebase_with_conflict_resolution(
                "main", "/project", self._base_context(), [],
                preferred_remote="upstream",
                head_remote="origin",
            )

        assert result == "upstream"
        rebase_cmds = [c for c in calls if "rebase" in c and "--abort" not in c]
        plain_rebases = [c for c in rebase_cmds if "--onto" not in c]
        assert len(plain_rebases) >= 1


class TestFetchPrContextHeadOwner:
    """Tests that fetch_pr_context extracts head_owner."""

    @patch("app.github.subprocess.run")
    def test_extracts_head_owner(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "Fix",
                "headRefName": "feat",
                "baseRefName": "main",
                "state": "OPEN",
                "author": {"login": "contributor"},
                "headRepositoryOwner": {"login": "contributor"},
                "url": "https://github.com/upstream/repo/pull/1",
            })),
            MagicMock(returncode=0, stdout="0"),  # review comment count
            MagicMock(returncode=0, stdout=""),  # diff
            MagicMock(returncode=0, stdout=""),  # review comments
            MagicMock(returncode=0, stdout=""),  # reviews
            MagicMock(returncode=0, stdout=""),  # issue comments
        ]

        context = fetch_pr_context("upstream", "repo", "1")
        assert context["head_owner"] == "contributor"

    @patch("app.github.subprocess.run")
    def test_head_owner_missing_defaults_empty(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({
                "title": "Fix",
                "headRefName": "feat",
                "baseRefName": "main",
            })),
            MagicMock(returncode=0, stdout="0"),  # review comment count
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]

        context = fetch_pr_context("o", "r", "1")
        assert context["head_owner"] == ""


class TestPushWithFallbackHeadRemote:
    """Tests that _push_with_fallback tries head_remote first."""

    def test_tries_head_remote_first(self):
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _push_with_fallback(
                "feat", "main", "upstream/repo", "1",
                {"title": "Fix"}, "/project",
                head_remote="myfork",
            )

        assert result["success"] is True
        # First push attempt should be to head_remote
        push_cmds = [c for c in calls if "push" in c]
        assert push_cmds[0][2] == "myfork"

    def test_falls_back_to_origin_when_head_remote_fails(self):
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if "push" in cmd and cmd[2] == "myfork":
                raise RuntimeError("push rejected")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.claude_step.subprocess.run", side_effect=mock_run):
            result = _push_with_fallback(
                "feat", "main", "upstream/repo", "1",
                {"title": "Fix"}, "/project",
                head_remote="myfork",
            )

        assert result["success"] is True
        push_cmds = [c for c in calls if "push" in c]
        # Should have tried myfork first (both lease and force), then origin
        assert any(c[2] == "origin" for c in push_cmds)


# ---------------------------------------------------------------------------
# CI checking and fixing
# ---------------------------------------------------------------------------

class TestWaitForCi:
    """Tests for wait_for_ci() in claude_step."""

    @patch("app.claude_step.time.sleep")
    @patch("app.claude_step.run_gh")
    def test_ci_passes(self, mock_gh, mock_sleep):
        mock_gh.return_value = json.dumps([{
            "databaseId": 123,
            "status": "completed",
            "conclusion": "success",
        }])
        status, run_id, logs = wait_for_ci("koan/fix", "owner/repo", timeout=60)
        assert status == "success"
        assert run_id == 123
        assert logs == ""

    @patch("app.claude_step.time.sleep")
    @patch("app.claude_step.run_gh")
    def test_no_ci_runs(self, mock_gh, mock_sleep):
        mock_gh.return_value = "[]"
        status, run_id, logs = wait_for_ci("koan/fix", "owner/repo", timeout=60)
        assert status == "none"
        assert run_id is None

    @patch("app.claude_step.time.sleep")
    @patch("app.claude_step._fetch_failed_logs", return_value="error in test_foo")
    @patch("app.claude_step.run_gh")
    def test_ci_fails(self, mock_gh, mock_fetch_logs, mock_sleep):
        mock_gh.return_value = json.dumps([{
            "databaseId": 456,
            "status": "completed",
            "conclusion": "failure",
        }])
        status, run_id, logs = wait_for_ci("koan/fix", "owner/repo", timeout=60)
        assert status == "failure"
        assert run_id == 456
        assert "error in test_foo" in logs

    @patch("app.claude_step.time.time")
    @patch("app.claude_step.time.sleep")
    @patch("app.claude_step.run_gh")
    def test_ci_timeout(self, mock_gh, mock_sleep, mock_time):
        # Simulate time progression past deadline
        mock_time.side_effect = [0, 100, 200, 700]  # deadline=600, exceeds on 3rd check
        mock_gh.return_value = json.dumps([{
            "databaseId": 789,
            "status": "in_progress",
            "conclusion": "",
        }])
        status, run_id, logs = wait_for_ci("koan/fix", "owner/repo", timeout=600)
        assert status == "timeout"


class TestCheckExistingCi:
    """Tests for check_existing_ci() in claude_step — single-shot CI status check."""

    @patch("app.claude_step.run_gh")
    def test_ci_success(self, mock_gh):
        mock_gh.return_value = json.dumps([{
            "databaseId": 100,
            "status": "completed",
            "conclusion": "success",
        }])
        status, run_id, logs = check_existing_ci("koan/fix", "owner/repo")
        assert status == "success"
        assert run_id == 100
        assert logs == ""

    @patch("app.claude_step._fetch_failed_logs", return_value="test_foo FAILED")
    @patch("app.claude_step.run_gh")
    def test_ci_failure(self, mock_gh, mock_logs):
        mock_gh.return_value = json.dumps([{
            "databaseId": 200,
            "status": "completed",
            "conclusion": "failure",
        }])
        status, run_id, logs = check_existing_ci("koan/fix", "owner/repo")
        assert status == "failure"
        assert run_id == 200
        assert "test_foo FAILED" in logs

    @patch("app.claude_step.run_gh")
    def test_ci_pending(self, mock_gh):
        mock_gh.return_value = json.dumps([{
            "databaseId": 300,
            "status": "in_progress",
            "conclusion": "",
        }])
        status, run_id, logs = check_existing_ci("koan/fix", "owner/repo")
        assert status == "pending"
        assert run_id == 300

    @patch("app.claude_step.run_gh")
    def test_no_ci_runs(self, mock_gh):
        mock_gh.return_value = "[]"
        status, run_id, logs = check_existing_ci("koan/fix", "owner/repo")
        assert status == "none"
        assert run_id is None

    @patch("app.claude_step.run_gh", side_effect=RuntimeError("network"))
    def test_gh_error_returns_none(self, mock_gh):
        status, run_id, logs = check_existing_ci("koan/fix", "owner/repo")
        assert status == "none"


class TestFixExistingCiFailures:
    """Tests for _fix_existing_ci_failures() — pre-push CI check and fix."""

    def _make_context(self):
        return {
            "title": "Fix bug",
            "branch": "koan/fix",
            "base": "main",
            "body": "",
            "diff": "",
            "url": "https://github.com/owner/repo/pull/42",
        }

    @patch("app.rebase_pr.check_existing_ci", return_value=("success", 100, ""))
    def test_ci_success_skips(self, mock_ci):
        actions = []
        result = _fix_existing_ci_failures(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert result is False
        assert any("previous run passed" in a for a in actions)

    @patch("app.rebase_pr.check_existing_ci", return_value=("none", None, ""))
    def test_no_ci_runs_skips(self, mock_ci):
        actions = []
        result = _fix_existing_ci_failures(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert result is False
        assert any("no CI runs" in a for a in actions)

    @patch("app.rebase_pr.check_existing_ci", return_value=("pending", 300, ""))
    def test_ci_pending_skips(self, mock_ci):
        actions = []
        result = _fix_existing_ci_failures(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert result is False
        assert any("pending" in a for a in actions)

    @patch("app.rebase_pr.run_claude_step", return_value=True)
    @patch("app.rebase_pr._build_ci_fix_prompt", return_value="fix prompt")
    @patch("app.rebase_pr._run_git", return_value="diff output")
    @patch("app.rebase_pr.check_existing_ci", return_value=("failure", 200, "test_foo FAILED"))
    def test_ci_failure_triggers_fix(self, mock_ci, mock_git, mock_prompt, mock_claude):
        actions = []
        notify_calls = []
        result = _fix_existing_ci_failures(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: notify_calls.append(m),
        )
        assert result is True
        mock_claude.assert_called_once()
        assert any("Pre-push CI fix applied" in a for a in actions)
        # Verify notify was called about analyzing logs
        assert any("analyzing" in m.lower() for m in notify_calls)

    @patch("app.rebase_pr.run_claude_step", return_value=False)
    @patch("app.rebase_pr._build_ci_fix_prompt", return_value="fix prompt")
    @patch("app.rebase_pr._run_git", return_value="diff output")
    @patch("app.rebase_pr.check_existing_ci", return_value=("failure", 200, "error"))
    def test_ci_failure_no_changes(self, mock_ci, mock_git, mock_prompt, mock_claude):
        actions = []
        result = _fix_existing_ci_failures(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert result is False
        assert any("no changes needed" in a for a in actions)


class TestRunCiCheckAndFix:
    """Tests for _run_ci_check_and_fix() in rebase_pr."""

    def _make_context(self):
        return {
            "title": "Fix bug",
            "branch": "koan/fix",
            "base": "main",
            "body": "",
            "diff": "",
        }

    @patch("app.rebase_pr.wait_for_ci", return_value=("success", 100, ""))
    def test_ci_passes_no_fix_needed(self, mock_wait):
        actions = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert "CI passed" in result
        assert "CI passed" in actions

    @patch("app.rebase_pr.wait_for_ci", return_value=("none", None, ""))
    def test_no_ci_runs(self, mock_wait):
        actions = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert result == ""
        assert "No CI runs found" in actions

    @patch("app.rebase_pr.check_pr_state", return_value=("OPEN", "MERGEABLE"))
    @patch("app.rebase_pr._force_push")
    @patch("app.rebase_pr._run_ci_fix_step_with_timeout_retry")
    @patch("app.rebase_pr.wait_for_ci", side_effect=[
        ("failure", 456, "test_foo FAILED"),
        ("success", 457, ""),
    ])
    def test_ci_fails_then_fixed(self, mock_wait, mock_fix_step, mock_push, mock_state):
        from app.claude_step import StepResult

        mock_fix_step.return_value = (StepResult(committed=True, output="done"), False, 1)
        actions = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert "fixed on attempt 1" in result
        mock_fix_step.assert_called_once()
        mock_push.assert_called_once()

    @patch("app.rebase_pr.check_pr_state", return_value=("OPEN", "MERGEABLE"))
    @patch("app.rebase_pr._force_push")
    @patch("app.rebase_pr._run_ci_fix_step_with_timeout_retry")
    @patch("app.rebase_pr.wait_for_ci", side_effect=[
        ("failure", 456, "persistent error"),
        ("failure", 457, "persistent error"),
        ("failure", 458, "persistent error"),
    ])
    def test_ci_fails_exhausts_retries(self, mock_wait, mock_fix_step, mock_push, mock_state):
        from app.claude_step import StepResult

        mock_fix_step.return_value = (StepResult(committed=True, output="done"), False, 1)
        actions = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert f"after {MAX_CI_FIX_ATTEMPTS} fix attempts" in result
        assert mock_fix_step.call_count == MAX_CI_FIX_ATTEMPTS
        assert mock_push.call_count == MAX_CI_FIX_ATTEMPTS

    @patch("app.rebase_pr.check_pr_state", return_value=("OPEN", "MERGEABLE"))
    @patch("app.rebase_pr._run_ci_fix_step_with_timeout_retry")
    @patch("app.rebase_pr.wait_for_ci", return_value=("failure", 456, "error"))
    def test_ci_fails_claude_no_changes(self, mock_wait, mock_fix_step, mock_state):
        """When CI fix step produces no changes, loop stops quickly."""
        from app.claude_step import StepResult

        mock_fix_step.return_value = (StepResult(committed=False, output=""), False, 1)
        actions = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert f"after {MAX_CI_FIX_ATTEMPTS} fix attempts" in result
        mock_fix_step.assert_called_once()

    @patch("app.rebase_pr.check_pr_state", return_value=("OPEN", "MERGEABLE"))
    @patch("app.rebase_pr._run_ci_fix_step_with_timeout_retry")
    @patch("app.rebase_pr.wait_for_ci", return_value=("failure", 456, "error"))
    def test_ci_fix_timeout_returns_actionable_message(
        self, mock_wait, mock_fix_step, mock_state,
    ):
        from app.claude_step import StepResult

        mock_fix_step.return_value = (StepResult(committed=False, output=""), True, 2)
        actions = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert "timed out during `/rebase`" in result
        assert "/rebase https://github.com/owner/repo/pull/42" in result


class TestCiFixTimeoutRetry:
    @patch("app.rebase_pr.get_skill_timeout", return_value=999)
    @patch("app.rebase_pr.get_skill_max_turns", return_value=77)
    @patch("app.rebase_pr.get_rebase_ci_max_duration", return_value=8888)
    @patch("app.rebase_pr.get_rebase_ci_idle_timeout", return_value=555)
    @patch("app.rebase_pr.run_claude_step")
    def test_timeout_retries_once_with_tight_prompt(
        self, mock_step, mock_idle, mock_max_duration, mock_turns, mock_timeout,
    ):
        from app.claude_step import StepResult

        mock_step.side_effect = [
            StepResult(committed=False, output="", error="Timeout (999s)"),
            StepResult(committed=True, output="fixed", error=""),
        ]
        actions = []
        result, timed_out, attempts = _run_ci_fix_step_with_timeout_retry(
            prompt="base prompt",
            project_path="/project",
            commit_msg="fix: test",
            success_label="ok",
            failure_label="failed",
            actions_log=actions,
            use_convention_subject=False,
        )

        assert result.committed is True
        assert timed_out is False
        assert attempts == 2
        assert mock_step.call_count == 2
        first_prompt = mock_step.call_args_list[0].kwargs["prompt"]
        second_prompt = mock_step.call_args_list[1].kwargs["prompt"]
        assert first_prompt == "base prompt"
        assert "Retry Constraints" in second_prompt
        assert mock_step.call_args_list[0].kwargs["timeout"] == 999
        assert mock_step.call_args_list[0].kwargs["max_turns"] == 77
        assert mock_step.call_args_list[0].kwargs["idle_timeout"] == 555
        assert mock_step.call_args_list[0].kwargs["max_duration"] == 8888

    @patch("app.rebase_pr.get_skill_timeout", return_value=999)
    @patch("app.rebase_pr.get_skill_max_turns", return_value=77)
    @patch("app.rebase_pr.get_rebase_ci_max_duration", return_value=8888)
    @patch("app.rebase_pr.get_rebase_ci_idle_timeout", return_value=555)
    @patch("app.rebase_pr.run_claude_step")
    def test_non_timeout_failure_does_not_retry(
        self, mock_step, mock_idle, mock_max_duration, mock_turns, mock_timeout,
    ):
        from app.claude_step import StepResult

        mock_step.return_value = StepResult(
            committed=False, output="", error="Exit code 1: no stderr",
        )
        actions = []
        result, timed_out, attempts = _run_ci_fix_step_with_timeout_retry(
            prompt="base prompt",
            project_path="/project",
            commit_msg="fix: test",
            success_label="ok",
            failure_label="failed",
            actions_log=actions,
            use_convention_subject=False,
        )

        assert result.committed is False
        assert timed_out is False
        assert attempts == 1
        mock_step.assert_called_once()


class TestCiCheckAndFixPrLink:
    """Tests that _run_ci_check_and_fix includes the PR link in notifications."""

    def _make_context(self):
        return {
            "title": "Fix bug",
            "branch": "koan/fix",
            "base": "main",
            "body": "",
            "diff": "",
            "url": "https://github.com/owner/repo/pull/42",
        }

    @patch("app.rebase_pr.wait_for_ci", return_value=("success", 100, ""))
    def test_initial_check_includes_pr_link(self, mock_wait):
        messages = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), [], lambda m: messages.append(m),
        )
        assert any("owner/repo/pull/42" in m for m in messages)

    @patch("app.rebase_pr.check_pr_state", return_value=("OPEN", "MERGEABLE"))
    @patch("app.rebase_pr._force_push")
    @patch("app.rebase_pr._run_ci_fix_step_with_timeout_retry")
    @patch("app.rebase_pr.wait_for_ci", side_effect=[
        ("failure", 456, "test FAILED"),
        ("success", 457, ""),
    ])
    def test_fix_attempt_includes_pr_link(self, mock_wait, mock_fix_step, mock_push, mock_state):
        from app.claude_step import StepResult

        mock_fix_step.return_value = (StepResult(committed=True, output=""), False, 1)
        messages = []
        _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), [], lambda m: messages.append(m),
        )
        # The "CI failed" notification should include the PR link
        ci_failed_msgs = [m for m in messages if "failed" in m.lower()]
        assert len(ci_failed_msgs) > 0
        assert all("owner/repo/pull/42" in m for m in ci_failed_msgs)


class TestCiCheckAndFixAbortOnMerged:
    """Tests that _run_ci_check_and_fix aborts if the PR has been merged."""

    def _make_context(self):
        return {
            "title": "Fix bug",
            "branch": "koan/fix",
            "base": "main",
            "body": "",
            "diff": "",
            "url": "https://github.com/owner/repo/pull/42",
        }

    @patch("app.rebase_pr.check_pr_state", return_value=("MERGED", "UNKNOWN"))
    @patch("app.rebase_pr.wait_for_ci", return_value=("failure", 456, "error"))
    def test_aborts_when_pr_merged(self, mock_wait, mock_state):
        actions = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert "merged" in result.lower()
        assert any("merged" in a.lower() for a in actions)

    @patch("app.rebase_pr.check_pr_state", return_value=("OPEN", "CONFLICTING"))
    @patch("app.rebase_pr.wait_for_ci", return_value=("failure", 456, "error"))
    def test_aborts_when_pr_has_conflicts(self, mock_wait, mock_state):
        actions = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert "conflict" in result.lower()
        assert any("conflict" in a.lower() for a in actions)

    @patch("app.rebase_pr.check_pr_state", return_value=("OPEN", "MERGEABLE"))
    @patch("app.rebase_pr._force_push")
    @patch("app.rebase_pr._run_ci_fix_step_with_timeout_retry")
    @patch("app.rebase_pr.wait_for_ci", side_effect=[
        ("failure", 456, "test FAILED"),
        ("success", 457, ""),
    ])
    def test_proceeds_when_pr_open_and_mergeable(
        self, mock_wait, mock_fix_step, mock_push, mock_state,
    ):
        from app.claude_step import StepResult

        mock_fix_step.return_value = (StepResult(committed=True, output=""), False, 1)
        actions = []
        result = _run_ci_check_and_fix(
            "koan/fix", "main", "owner/repo", "42", "/project",
            self._make_context(), actions, lambda m: None,
        )
        assert "fixed on attempt 1" in result
        mock_fix_step.assert_called_once()


class TestCheckPrState:
    """Tests for check_pr_state() helper."""

    @patch("app.rebase_pr.run_gh", return_value='{"state":"MERGED","mergeable":"UNKNOWN"}')
    def test_returns_merged(self, mock_gh):
        from app.rebase_pr import check_pr_state
        state, mergeable = check_pr_state("42", "owner/repo")
        assert state == "MERGED"
        assert mergeable == "UNKNOWN"

    @patch("app.rebase_pr.run_gh", return_value='{"state":"OPEN","mergeable":"CONFLICTING"}')
    def test_returns_conflicting(self, mock_gh):
        from app.rebase_pr import check_pr_state
        state, mergeable = check_pr_state("42", "owner/repo")
        assert state == "OPEN"
        assert mergeable == "CONFLICTING"

    @patch("app.rebase_pr.run_gh", side_effect=RuntimeError("API error"))
    def test_returns_unknown_on_error(self, mock_gh):
        from app.rebase_pr import check_pr_state
        state, mergeable = check_pr_state("42", "owner/repo")
        assert state == "UNKNOWN"
        assert mergeable == "UNKNOWN"


class TestBuildRebaseCommentWithCi:
    """Tests for CI section in _build_rebase_comment."""

    def test_ci_section_included(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main"],
            {"title": "Fix bug"},
            ci_section="CI passed.",
        )
        assert "### CI status" in result
        assert "CI passed." in result

    def test_no_ci_section_when_empty(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main"],
            {"title": "Fix bug"},
            ci_section="",
        )
        assert "### CI status" not in result


# ---------------------------------------------------------------------------
# Descriptive commit messages for review feedback (issue #964)
# ---------------------------------------------------------------------------

class TestApplyReviewFeedbackDescriptiveCommit:
    """_apply_review_feedback should return a change summary from Claude's output."""

    @patch("app.rebase_pr.run_claude_step")
    def test_returns_change_summary(self, mock_step):
        """When Claude produces changes, _apply_review_feedback returns the summary."""
        from app.claude_step import StepResult
        mock_step.return_value = StepResult(
            committed=True,
            output="Refactored auth to use JWT tokens and updated tests.",
        )
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        actions = []
        summary = _apply_review_feedback(
            context, "42", "/project", actions,
            skill_dir=REBASE_SKILL_DIR,
        )
        assert summary is not None
        assert len(summary) > 0

    @patch("app.rebase_pr.run_claude_step")
    def test_returns_empty_when_no_changes(self, mock_step):
        """When Claude produces no changes, returns empty string."""
        from app.claude_step import StepResult
        mock_step.return_value = StepResult(committed=False, output="No changes needed.")
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "looks good",
            "reviews": "", "issue_comments": "",
        }
        actions = []
        summary = _apply_review_feedback(
            context, "42", "/project", actions,
            skill_dir=REBASE_SKILL_DIR,
        )
        assert summary == ""

    @patch("app.rebase_pr.run_claude_step")
    def test_passes_correct_commit_msg(self, mock_step):
        """The commit_msg passed to run_claude_step should follow the convention."""
        from app.claude_step import StepResult
        mock_step.return_value = StepResult(committed=True, output="Updated error handling.")
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix error handling",
            "reviews": "", "issue_comments": "",
        }
        _apply_review_feedback(
            context, "42", "/project", [],
            skill_dir=REBASE_SKILL_DIR,
        )
        call_kwargs = mock_step.call_args.kwargs
        assert call_kwargs["commit_msg"] == "rebase: apply review feedback on #42"


class TestApplyReviewFeedbackConventionAware:
    """_apply_review_feedback should pass use_convention_subject to run_claude_step
    and strip COMMIT_SUBJECT from the returned change summary."""

    @patch("app.rebase_pr.run_claude_step")
    def test_enables_convention_subject_when_conventions_provided(self, mock_step):
        from app.claude_step import StepResult
        mock_step.return_value = StepResult(committed=True, output="Fixed it.")
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        _apply_review_feedback(
            context, "42", "/project", [],
            skill_dir=REBASE_SKILL_DIR,
            commit_conventions="## Commit Conventions\nUse Case PROJECT-XXXXX.",
        )
        call_kwargs = mock_step.call_args.kwargs
        assert call_kwargs["use_convention_subject"] is True

    @patch("app.rebase_pr.run_claude_step")
    def test_no_convention_subject_without_conventions(self, mock_step):
        from app.claude_step import StepResult
        mock_step.return_value = StepResult(committed=True, output="Fixed it.")
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        _apply_review_feedback(
            context, "42", "/project", [],
            skill_dir=REBASE_SKILL_DIR,
        )
        call_kwargs = mock_step.call_args.kwargs
        assert call_kwargs["use_convention_subject"] is False

    @patch("app.rebase_pr.run_claude_step")
    def test_strips_subject_line_from_summary(self, mock_step):
        """The COMMIT_SUBJECT line should not appear in the returned summary."""
        from app.claude_step import StepResult
        mock_step.return_value = StepResult(
            committed=True,
            output=(
                "Fixed auth bug.\n"
                "COMMIT_SUBJECT: Case PROJECT-123 Fix auth\n"
                "More details here."
            ),
        )
        context = {
            "title": "Fix", "body": "", "branch": "br", "base": "main",
            "diff": "+code", "review_comments": "fix this",
            "reviews": "", "issue_comments": "",
        }
        summary = _apply_review_feedback(
            context, "42", "/project", [],
            skill_dir=REBASE_SKILL_DIR,
            commit_conventions="## Commit Conventions\nUse Case prefix.",
        )
        assert "COMMIT_SUBJECT:" not in summary
        assert "Fixed auth bug" in summary


class TestBuildRebaseCommentChangeSummary:
    """_build_rebase_comment should include a change summary section
    when review feedback was applied (issue #964)."""

    def test_change_summary_included(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main", "Applied review feedback"],
            {"title": "Fix bug", "review_comments": "fix the typo"},
            change_summary="Fixed typo in error message and updated tests.",
        )
        assert "### Changes applied" in result
        assert "Fixed typo in error message" in result

    def test_no_change_summary_when_empty(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main"],
            {"title": "Fix bug"},
            change_summary="",
        )
        assert "### Changes applied" not in result

    def test_no_change_summary_when_none(self):
        result = _build_rebase_comment(
            "42", "koan/fix", "main",
            ["Rebased onto origin/main"],
            {"title": "Fix bug"},
        )
        assert "### Changes applied" not in result


class TestRunRebasePassesChangeSummary:
    """run_rebase should pass the change summary from _apply_review_feedback
    through to _build_rebase_comment."""

    @pytest.fixture(autouse=True)
    def mock_already_solved(self):
        with patch("app.rebase_pr._check_if_already_solved", return_value=(False, None)):
            yield

    @patch("app.rebase_pr._fix_existing_ci_failures", return_value=False)
    @patch("app.rebase_pr._run_ci_check_and_fix", return_value="")
    @patch("app.rebase_pr._safe_checkout")
    @patch("app.rebase_pr.run_gh")
    @patch("app.rebase_pr._build_rebase_comment")
    @patch("app.rebase_pr._apply_review_feedback", return_value="Fixed the auth bug.")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_summary_forwarded_to_comment(
        self, mock_ctx, mock_apply, mock_comment, mock_gh, mock_safe, mock_ci_check, mock_fix_ci,
    ):
        mock_ctx.return_value = {
            "title": "Fix auth", "body": "", "branch": "feat",
            "base": "main", "state": "", "author": "", "url": "",
            "diff": "+code", "review_comments": "@reviewer: fix this",
            "reviews": "", "issue_comments": "",
        }
        mock_comment.return_value = "comment body"
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch"), \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._get_diffstat", return_value=""), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }):
            run_rebase("o", "r", "1", "/p", notify_fn=notify,
                       skill_dir=REBASE_SKILL_DIR)
            # Verify _build_rebase_comment was called with change_summary
            call_kwargs = mock_comment.call_args
            assert call_kwargs[1].get("change_summary") == "Fixed the auth bug."

# ---------------------------------------------------------------------------
# _check_if_already_solved
# ---------------------------------------------------------------------------

class TestCheckIfAlreadySolved:
    """Unit tests for _check_if_already_solved()."""

    _PR_CONTEXT = {
        "title": "Fix auth bug",
        "body": "Fixes a login issue.",
        "branch": "koan/fix-auth",
        "base": "main",
        "diff": "+fix",
        "review_comments": "",
        "reviews": "",
        "issue_comments": "",
    }

    @patch("app.rebase_pr.run_claude")
    @patch("app.cli_provider.build_full_command", return_value=["claude", "--fake"])
    @patch("app.config.get_model_config", return_value={"mission": "m", "fallback": "f", "review": "r"})
    @patch("app.rebase_pr._run_git", return_value="abc1234 fix auth login\ndef5678 refactor utils")
    def test_high_confidence_positive_returns_true(self, _git, _mc, _cmd, mock_claude):
        mock_claude.return_value = {
            "success": True,
            "output": '{"already_solved": true, "resolved_by": "https://github.com/o/r/pull/99", "confidence": "high", "reasoning": "PR #99 already fixed this."}',
            "error": "",
        }
        actions = []
        result, resolved_by = _check_if_already_solved(actions, self._PR_CONTEXT, REBASE_SKILL_DIR, "/project")
        assert result is True
        assert resolved_by == "https://github.com/o/r/pull/99"
        assert any("positive" in a for a in actions)

    @patch("app.rebase_pr.run_claude")
    @patch("app.cli_provider.build_full_command", return_value=["claude", "--fake"])
    @patch("app.config.get_model_config", return_value={"mission": "m", "fallback": "f", "review": "r"})
    @patch("app.rebase_pr._run_git", return_value="abc1234 some commit")
    def test_negative_returns_false(self, _git, _mc, _cmd, mock_claude):
        mock_claude.return_value = {
            "success": True,
            "output": '{"already_solved": false, "resolved_by": null, "confidence": "high", "reasoning": "Work is still needed."}',
            "error": "",
        }
        actions = []
        result, resolved_by = _check_if_already_solved(actions, self._PR_CONTEXT, REBASE_SKILL_DIR, "/project")
        assert result is False
        assert resolved_by is None

    @patch("app.rebase_pr.run_claude")
    @patch("app.cli_provider.build_full_command", return_value=["claude", "--fake"])
    @patch("app.config.get_model_config", return_value={"mission": "m", "fallback": "f", "review": "r"})
    @patch("app.rebase_pr._run_git", return_value="")
    def test_medium_confidence_skipped(self, _git, _mc, _cmd, mock_claude):
        """Medium confidence should NOT close the PR."""
        mock_claude.return_value = {
            "success": True,
            "output": '{"already_solved": true, "resolved_by": "abc1234", "confidence": "medium", "reasoning": "Possibly."}',
            "error": "",
        }
        actions = []
        result, _ = _check_if_already_solved(actions, self._PR_CONTEXT, REBASE_SKILL_DIR, "/project")
        assert result is False
        assert any("skipped" in a.lower() or "not high" in a.lower() for a in actions)

    @patch("app.rebase_pr.run_claude")
    @patch("app.cli_provider.build_full_command", return_value=["claude", "--fake"])
    @patch("app.config.get_model_config", return_value={"mission": "m", "fallback": "f", "review": "r"})
    @patch("app.rebase_pr._run_git", return_value="")
    def test_claude_failure_returns_false(self, _git, _mc, _cmd, mock_claude):
        mock_claude.return_value = {"success": False, "output": "", "error": "timeout"}
        actions = []
        result, _ = _check_if_already_solved(actions, self._PR_CONTEXT, REBASE_SKILL_DIR, "/project")
        assert result is False
        assert any("skipped" in a for a in actions)

    @patch("app.rebase_pr.run_claude")
    @patch("app.cli_provider.build_full_command", return_value=["claude", "--fake"])
    @patch("app.config.get_model_config", return_value={"mission": "m", "fallback": "f", "review": "r"})
    @patch("app.rebase_pr._run_git", return_value="")
    def test_malformed_json_returns_false(self, _git, _mc, _cmd, mock_claude):
        mock_claude.return_value = {
            "success": True,
            "output": "This is not JSON at all.",
            "error": "",
        }
        actions = []
        result, _ = _check_if_already_solved(actions, self._PR_CONTEXT, REBASE_SKILL_DIR, "/project")
        assert result is False

    @patch("app.rebase_pr.run_claude")
    @patch("app.cli_provider.build_full_command", return_value=["claude", "--fake"])
    @patch("app.config.get_model_config", return_value={"mission": "m", "fallback": "f", "review": "r"})
    @patch("app.rebase_pr._run_git", return_value="")
    def test_json_embedded_in_text_is_parsed(self, _git, _mc, _cmd, mock_claude):
        """JSON embedded in verbose output should still be parsed."""
        mock_claude.return_value = {
            "success": True,
            "output": 'Let me analyze... {"already_solved": true, "resolved_by": "abc1234", "confidence": "high", "reasoning": "Fixed."} Done.',
            "error": "",
        }
        actions = []
        result, resolved_by = _check_if_already_solved(actions, self._PR_CONTEXT, REBASE_SKILL_DIR, "/project")
        assert result is True
        assert resolved_by == "abc1234"


# ---------------------------------------------------------------------------
# _close_pr_as_duplicate
# ---------------------------------------------------------------------------

class TestClosePrAsDuplicate:
    """Unit tests for _close_pr_as_duplicate()."""

    _PR_CONTEXT = {
        "title": "Fix auth bug",
        "body": "Fixes the login issue.\n\nCloses #123",
        "branch": "koan/fix-auth",
        "base": "main",
    }

    @patch("app.rebase_pr.run_gh")
    def test_posts_comment_and_closes_pr(self, mock_gh):
        _close_pr_as_duplicate(
            owner="o", repo="r", pr_number="42",
            resolved_by="https://github.com/o/r/pull/99",
            pr_context={"title": "Fix", "body": ""},
            project_path="/project",
        )
        gh_calls = [call[0] for call in mock_gh.call_args_list]
        # Must comment on the PR
        assert any(c[0] == "pr" and c[1] == "comment" for c in gh_calls)
        # Must close the PR
        assert any(c[0] == "pr" and c[1] == "close" for c in gh_calls)

    @patch("app.rebase_pr.run_gh")
    def test_closes_linked_issue(self, mock_gh):
        _close_pr_as_duplicate(
            owner="o", repo="r", pr_number="42",
            resolved_by="abc1234",
            pr_context=self._PR_CONTEXT,
            project_path="/project",
        )
        gh_calls = [call[0] for call in mock_gh.call_args_list]
        # Must comment on and close the linked issue #123
        assert any(c[0] == "issue" and c[1] == "comment" and c[2] == "123" for c in gh_calls)
        assert any(c[0] == "issue" and c[1] == "close" and c[2] == "123" for c in gh_calls)

    @patch("app.rebase_pr.run_gh")
    def test_no_linked_issue_skips_issue_close(self, mock_gh):
        _close_pr_as_duplicate(
            owner="o", repo="r", pr_number="42",
            resolved_by="abc1234",
            pr_context={"title": "Fix", "body": "No issue reference here."},
            project_path="/project",
        )
        gh_calls = [call[0] for call in mock_gh.call_args_list]
        assert not any(c[0] == "issue" for c in gh_calls)

    @patch("app.rebase_pr.run_gh")
    def test_notify_fn_called(self, mock_gh):
        notify = MagicMock()
        _close_pr_as_duplicate(
            owner="o", repo="r", pr_number="42",
            resolved_by="https://github.com/o/r/pull/99",
            pr_context={"title": "Fix auth", "body": ""},
            project_path="/project",
            notify_fn=notify,
        )
        notify.assert_called_once()
        msg = notify.call_args[0][0]
        assert "42" in msg
        assert "closed" in msg.lower()

    @patch("app.rebase_pr.run_gh")
    def test_gh_failure_non_fatal(self, mock_gh):
        """run_gh errors should not propagate — the function is best-effort."""
        mock_gh.side_effect = RuntimeError("network error")
        # Should not raise
        _close_pr_as_duplicate(
            owner="o", repo="r", pr_number="42",
            resolved_by=None,
            pr_context={"title": "Fix", "body": ""},
            project_path="/project",
        )


# ---------------------------------------------------------------------------
# run_rebase — already-solved integration
# ---------------------------------------------------------------------------

class TestRunRebaseAlreadySolved:
    """run_rebase should close PRs detected as already solved."""

    @patch("app.rebase_pr._close_pr_as_duplicate")
    @patch("app.rebase_pr._check_if_already_solved", return_value=(True, "https://github.com/o/r/pull/55"))
    @patch("app.rebase_pr._checkout_pr_branch")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_already_solved_closes_pr_without_checkout(
        self, mock_ctx, mock_checkout, mock_check, mock_close
    ):
        mock_ctx.return_value = {
            "title": "Fix auth", "body": "", "branch": "feat",
            "base": "main", "state": "OPEN", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        success, summary = run_rebase("o", "r", "42", "/project", notify_fn=notify)
        assert success is False
        assert "already solved" in summary.lower()
        mock_close.assert_called_once()
        # Checkout must NOT have been called
        mock_checkout.assert_not_called()

    @patch("app.rebase_pr._check_if_already_solved", return_value=(False, None))
    @patch("app.rebase_pr._close_pr_as_duplicate")
    @patch("app.rebase_pr.fetch_pr_context")
    def test_not_already_solved_continues_rebase(
        self, mock_ctx, mock_close, mock_check
    ):
        mock_ctx.return_value = {
            "title": "T", "body": "", "branch": "feat",
            "base": "main", "state": "OPEN", "author": "", "url": "",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        notify = MagicMock()
        with patch("app.rebase_pr._get_current_branch", return_value="main"), \
             patch("app.rebase_pr._checkout_pr_branch") as mock_checkout, \
             patch("app.rebase_pr._rebase_with_conflict_resolution", return_value="origin"), \
             patch("app.rebase_pr._push_with_fallback", return_value={
                 "success": True, "actions": ["Force-pushed"], "error": ""
             }), \
             patch("app.rebase_pr.run_gh"), \
             patch("app.rebase_pr._safe_checkout"), \
             patch("app.rebase_pr._run_ci_check_and_fix", return_value=""), \
             patch("app.rebase_pr._fix_existing_ci_failures", return_value=False):
            success, _ = run_rebase("o", "r", "1", "/project", notify_fn=notify)
        mock_close.assert_not_called()
        mock_checkout.assert_called_once()


# ---------------------------------------------------------------------------
# Severity filter — parse_severity / severity_at_or_above
# ---------------------------------------------------------------------------

class TestParseSeverity:
    def test_canonical_names(self):
        assert parse_severity("critical") == "critical"
        assert parse_severity("warning") == "warning"
        assert parse_severity("suggestion") == "suggestion"

    def test_aliases(self):
        assert parse_severity("important") == "warning"
        assert parse_severity("blocking") == "critical"
        assert parse_severity("all") == "suggestion"
        assert parse_severity("suggestions") == "suggestion"

    def test_case_insensitive(self):
        assert parse_severity("Critical") == "critical"
        assert parse_severity("IMPORTANT") == "warning"

    def test_strips_dashes(self):
        assert parse_severity("-critical") == "critical"
        assert parse_severity("--important") == "warning"
        assert parse_severity("---warning") == "warning"

    def test_strips_em_dash(self):
        assert parse_severity("\u2014critical") == "critical"
        assert parse_severity("\u2013important") == "warning"

    def test_unknown_returns_none(self):
        assert parse_severity("unknown") is None
        assert parse_severity("") is None
        assert parse_severity("---") is None


class TestSeverityAtOrAbove:
    def test_critical_only(self):
        assert severity_at_or_above("critical") == ["critical"]

    def test_warning_and_above(self):
        assert severity_at_or_above("warning") == ["critical", "warning"]

    def test_suggestion_includes_all(self):
        assert severity_at_or_above("suggestion") == ["critical", "warning", "suggestion"]

    def test_unknown_returns_all(self):
        assert severity_at_or_above("bogus") == list(SEVERITY_LEVELS)


class TestBuildRebasePromptSeverityFilter:
    def test_no_filter_by_default(self):
        context = {
            "title": "T", "body": "", "branch": "br", "base": "main",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        prompt = _build_rebase_prompt(context, skill_dir=REBASE_SKILL_DIR)
        assert "Severity Filter" not in prompt

    def test_critical_filter_appended(self):
        context = {
            "title": "T", "body": "", "branch": "br", "base": "main",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        prompt = _build_rebase_prompt(
            context, skill_dir=REBASE_SKILL_DIR, min_severity="critical",
        )
        assert "Severity Filter" in prompt
        assert "critical" in prompt.lower()
        assert "Skip" in prompt

    def test_warning_filter_includes_critical(self):
        context = {
            "title": "T", "body": "", "branch": "br", "base": "main",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        prompt = _build_rebase_prompt(
            context, skill_dir=REBASE_SKILL_DIR, min_severity="warning",
        )
        assert "Severity Filter" in prompt
        assert "critical" in prompt
        assert "warning" in prompt
        assert "suggestion" in prompt  # mentioned as skipped

    def test_suggestion_filter_not_appended(self):
        """suggestion means 'all' — no filter needed."""
        context = {
            "title": "T", "body": "", "branch": "br", "base": "main",
            "diff": "", "review_comments": "", "reviews": "", "issue_comments": "",
        }
        prompt = _build_rebase_prompt(
            context, skill_dir=REBASE_SKILL_DIR, min_severity="suggestion",
        )
        assert "Severity Filter" not in prompt


class TestMainMinSeverity:
    def test_passes_min_severity_to_run_rebase(self):
        with patch("app.rebase_pr.run_rebase", return_value=(True, "OK")) as mock:
            rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
                "--min-severity", "warning",
            ])
            assert mock.call_args[1]["min_severity"] == "warning"

    def test_no_min_severity_by_default(self):
        with patch("app.rebase_pr.run_rebase", return_value=(True, "OK")) as mock:
            rebase_main([
                "https://github.com/sukria/koan/pull/42",
                "--project-path", "/project",
            ])
            assert mock.call_args[1]["min_severity"] is None


class TestFilterBotIssueComments:
    """Tests for _filter_bot_issue_comments."""

    def test_removes_third_party_bot_comments(self):
        raw = (
            "@human: Please fix this bug\n"
            "@github-actions[bot]: ## CI run results\n"
            "All checks passed.\n"
            "### Stats\n"
            "7 files changed\n"
            "@human: Now add config option\n"
            "@human: @koan-bot rebase"
        )
        with patch("app.rebase_pr._resolve_own_login", return_value="koan-bot"):
            result = _filter_bot_issue_comments(raw)
        assert "@human: Please fix this bug" in result
        assert "@human: Now add config option" in result
        assert "@github-actions[bot]:" not in result
        assert "All checks passed" not in result

    def test_keeps_own_identity_comments(self):
        # Our own prior review/rebase feedback must survive filtering so a
        # later rebase can act on it (review + rebase flow).
        raw = (
            "@human: Please fix this bug\n"
            "@koan-bot: ## Review feedback from last iteration\n"
            "Consider renaming the helper.\n"
            "@github-actions[bot]: CI noise"
        )
        with patch("app.rebase_pr._resolve_own_login", return_value="koan-bot"):
            result = _filter_bot_issue_comments(raw)
        assert "@koan-bot: ## Review feedback from last iteration" in result
        assert "Consider renaming the helper." in result
        assert "@github-actions[bot]: CI noise" not in result

    def test_keeps_own_identity_even_with_bot_suffix(self):
        # If our configured identity is a GitHub App (login ends in [bot]),
        # it is still exempt from filtering; other [bot] authors are removed.
        raw = "@koan-app[bot]: our prior feedback\n@other[bot]: third-party noise"
        with patch("app.rebase_pr._resolve_own_login", return_value="koan-app[bot]"):
            result = _filter_bot_issue_comments(raw)
        assert "@koan-app[bot]: our prior feedback" in result
        assert "@other[bot]: third-party noise" not in result

    def test_no_own_login_keeps_non_bot_authors(self):
        raw = "@bot: some text\n@human: other text"
        with patch("app.rebase_pr._resolve_own_login", return_value=""):
            result = _filter_bot_issue_comments(raw)
        assert result == raw

    def test_empty_input(self):
        with patch("app.rebase_pr._resolve_own_login", return_value="koan-bot"):
            assert _filter_bot_issue_comments("") == ""


class TestTruncateRecent:
    """Tests for _truncate_recent — tail-prioritized truncation."""

    def test_short_text_unchanged(self):
        assert _truncate_recent("short text", 1000) == "short text"

    def test_long_text_keeps_tail(self):
        early = "A" * 5000
        late = "RECENT_FEEDBACK"
        text = early + "\n" + late
        result = _truncate_recent(text, 200)
        assert "RECENT_FEEDBACK" in result
        assert result.startswith("(earlier comments truncated)")
        assert len(result) <= 200

    def test_recent_user_comment_preserved(self):
        """Simulates the real bug: bot comments bloat early text, user
        comment at the end gets truncated by head-based truncation."""
        bot_noise = "Bot rebase summary " * 200  # ~3800 chars
        user_request = "@human: Add config option for review_compressor"
        text = bot_noise + "\n" + user_request
        result = _truncate_recent(text, 3000)
        assert "review_compressor" in result
