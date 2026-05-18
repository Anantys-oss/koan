"""Tests for update_manager.py — git operations for code updates."""

from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from app.update_manager import (
    UpdateResult,
    pull_upstream,
    _run_git,
    _get_current_branch,
    _get_short_sha,
    _is_dirty,
    find_upstream_remote,
    _count_commits_between,
)


class TestUpdateResult:
    """Tests for UpdateResult dataclass."""

    def test_changed_true_when_commits_pulled(self):
        r = UpdateResult(success=True, old_commit="abc", new_commit="def", commits_pulled=3)
        assert r.changed is True

    def test_changed_false_when_no_commits(self):
        r = UpdateResult(success=True, old_commit="abc", new_commit="abc", commits_pulled=0)
        assert r.changed is False

    def test_summary_success_with_changes(self):
        r = UpdateResult(success=True, old_commit="abc1234", new_commit="def5678", commits_pulled=5)
        assert "abc1234" in r.summary()
        assert "def5678" in r.summary()
        assert "5 new commits" in r.summary()

    def test_summary_single_commit(self):
        r = UpdateResult(success=True, old_commit="abc", new_commit="def", commits_pulled=1)
        assert "1 new commit" in r.summary()
        assert "commits" not in r.summary()

    def test_summary_no_changes(self):
        r = UpdateResult(success=True, old_commit="abc", new_commit="abc", commits_pulled=0)
        assert "up to date" in r.summary()

    def test_summary_failure(self):
        r = UpdateResult(success=False, old_commit="abc", new_commit="abc", commits_pulled=0, error="network error")
        assert "failed" in r.summary().lower()
        assert "network error" in r.summary()


class TestRunGit:
    """Tests for _run_git() helper."""

    @patch("app.update_manager._run_git_core")
    def test_calls_git_with_args(self, mock_core):
        mock_core.return_value = (0, "ok", "")
        result = _run_git(["status"], Path("/repo"))
        mock_core.assert_called_once_with(
            "status",
            cwd="/repo",
            timeout=60,
        )
        assert result.returncode == 0
        assert result.stdout == "ok"


class TestGetCurrentBranch:
    """Tests for _get_current_branch()."""

    @patch("app.update_manager._run_git")
    def test_returns_branch_name(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="main\n")
        assert _get_current_branch(Path("/repo")) == "main"

    @patch("app.update_manager._run_git")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _get_current_branch(Path("/repo")) is None


class TestGetShortSha:
    """Tests for _get_short_sha()."""

    @patch("app.update_manager._run_git")
    def test_returns_sha(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="abc1234\n")
        assert _get_short_sha(Path("/repo")) == "abc1234"

    @patch("app.update_manager._run_git")
    def test_returns_unknown_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _get_short_sha(Path("/repo")) == "unknown"


class TestIsDirty:
    """Tests for _is_dirty()."""

    @patch("app.update_manager._run_git")
    def test_clean_repo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        assert _is_dirty(Path("/repo")) is False

    @patch("app.update_manager._run_git")
    def test_dirty_repo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=" M file.py\n")
        assert _is_dirty(Path("/repo")) is True


class TestFindUpstreamRemote:
    """Tests for find_upstream_remote()."""

    @patch("app.update_manager._run_git")
    def test_prefers_upstream(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="origin\nupstream\n")
        assert find_upstream_remote(Path("/repo")) == "upstream"

    @patch("app.update_manager._run_git")
    def test_falls_back_to_origin(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="origin\n")
        assert find_upstream_remote(Path("/repo")) == "origin"

    @patch("app.update_manager._run_git")
    def test_returns_first_remote(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="fork\n")
        assert find_upstream_remote(Path("/repo")) == "fork"

    @patch("app.update_manager._run_git")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert find_upstream_remote(Path("/repo")) is None

    @patch("app.update_manager._run_git")
    def test_returns_none_when_no_remotes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        assert find_upstream_remote(Path("/repo")) is None


class TestCountCommitsBetween:
    """Tests for _count_commits_between()."""

    @patch("app.update_manager._run_git")
    def test_returns_count(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="7\n")
        assert _count_commits_between(Path("/repo"), "abc", "def") == 7

    @patch("app.update_manager._run_git")
    def test_returns_zero_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _count_commits_between(Path("/repo"), "abc", "def") == 0


class TestPullUpstream:
    """Tests for pull_upstream() — the main update orchestration."""

    @patch("app.update_manager._run_git")
    def test_successful_update(self, mock_run):
        """Happy path: clean repo, on main, upstream exists, pull succeeds."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234\n"),   # _get_short_sha (old)
            MagicMock(returncode=0, stdout="origin\nupstream\n"),  # find_upstream_remote
            MagicMock(returncode=0, stdout=""),              # _is_dirty (clean)
            MagicMock(returncode=0, stdout="main\n"),        # _get_current_branch
            MagicMock(returncode=0, stdout=""),               # fetch upstream
            MagicMock(returncode=0, stdout="Updating abc..def\n"),  # pull --ff-only
            MagicMock(returncode=0, stdout="def5678\n"),     # _get_short_sha (new)
            MagicMock(returncode=0, stdout="5\n"),           # _count_commits_between
        ]

        result = pull_upstream(Path("/repo"))
        assert result.success is True
        assert result.commits_pulled == 5
        assert result.old_commit == "abc1234"
        assert result.new_commit == "def5678"

    @patch("app.update_manager._run_git")
    def test_already_up_to_date(self, mock_run):
        """No new commits — same SHA before and after."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234\n"),   # _get_short_sha (old)
            MagicMock(returncode=0, stdout="upstream\n"),  # find_upstream_remote
            MagicMock(returncode=0, stdout=""),              # _is_dirty
            MagicMock(returncode=0, stdout="main\n"),        # _get_current_branch
            MagicMock(returncode=0, stdout=""),               # fetch
            MagicMock(returncode=0, stdout="Already up to date.\n"),  # pull
            MagicMock(returncode=0, stdout="abc1234\n"),     # _get_short_sha (same)
        ]

        result = pull_upstream(Path("/repo"))
        assert result.success is True
        assert result.changed is False
        assert result.commits_pulled == 0

    @patch("app.update_manager._run_git")
    def test_no_remote_found(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234\n"),   # _get_short_sha
            MagicMock(returncode=1, stdout=""),              # find_upstream_remote fails
        ]

        result = pull_upstream(Path("/repo"))
        assert result.success is False
        assert "No git remote" in result.error

    @patch("app.update_manager._run_git")
    def test_stashes_dirty_work(self, mock_run):
        """Dirty working tree gets stashed before checkout."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234\n"),   # _get_short_sha
            MagicMock(returncode=0, stdout="upstream\n"),  # find_upstream_remote
            MagicMock(returncode=0, stdout=" M dirty.py\n"),  # _is_dirty = True
            MagicMock(returncode=0, stdout=""),               # stash push
            MagicMock(returncode=0, stdout="koan/feature\n"), # _get_current_branch (not main)
            MagicMock(returncode=0, stdout=""),               # checkout main
            MagicMock(returncode=0, stdout=""),               # fetch
            MagicMock(returncode=0, stdout="Updating..\n"),   # pull
            MagicMock(returncode=0, stdout="def5678\n"),      # _get_short_sha (new)
            MagicMock(returncode=0, stdout="3\n"),            # _count_commits_between
            MagicMock(returncode=0, stdout=""),               # checkout original branch
            MagicMock(returncode=0, stdout=""),               # stash pop
        ]

        result = pull_upstream(Path("/repo"))
        assert result.success is True
        assert result.stashed is True

    @patch("app.update_manager._run_git")
    def test_stash_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234\n"),
            MagicMock(returncode=0, stdout="upstream\n"),
            MagicMock(returncode=0, stdout=" M dirty.py\n"),  # dirty
            MagicMock(returncode=1, stdout="", stderr="stash error"),  # stash fails
        ]

        result = pull_upstream(Path("/repo"))
        assert result.success is False
        assert "stash" in result.error.lower()

    @patch("app.update_manager._run_git")
    def test_checkout_main_failure(self, mock_run):
        """Checkout main fails — should restore state."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234\n"),
            MagicMock(returncode=0, stdout="upstream\n"),
            MagicMock(returncode=0, stdout=""),               # clean
            MagicMock(returncode=0, stdout="koan/feature\n"), # not on main
            MagicMock(returncode=1, stdout="", stderr="checkout error"),  # checkout fails
        ]

        result = pull_upstream(Path("/repo"))
        assert result.success is False
        assert "checkout" in result.error.lower()

    @patch("app.update_manager._run_git")
    def test_fetch_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234\n"),
            MagicMock(returncode=0, stdout="upstream\n"),
            MagicMock(returncode=0, stdout=""),               # clean
            MagicMock(returncode=0, stdout="main\n"),          # already on main
            MagicMock(returncode=1, stdout="", stderr="network error"),  # fetch fails
        ]

        result = pull_upstream(Path("/repo"))
        assert result.success is False
        assert "fetch" in result.error.lower()

    @patch("app.update_manager._run_git")
    def test_pull_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc1234\n"),
            MagicMock(returncode=0, stdout="upstream\n"),
            MagicMock(returncode=0, stdout=""),               # clean
            MagicMock(returncode=0, stdout="main\n"),          # on main
            MagicMock(returncode=0, stdout=""),                # fetch ok
            MagicMock(returncode=1, stdout="", stderr="merge conflict"),  # pull fails
        ]

        result = pull_upstream(Path("/repo"))
        assert result.success is False
        assert "pull" in result.error.lower()

    @patch("app.update_manager._run_git")
    def test_skips_checkout_when_already_on_main(self, mock_run):
        """No checkout command issued when already on main."""
        calls = []
        def track_calls(args, cwd=None):
            calls.append(args)
            if args == ["rev-parse", "--short", "HEAD"]:
                return MagicMock(returncode=0, stdout="abc1234\n")
            if args == ["remote"]:
                return MagicMock(returncode=0, stdout="upstream\n")
            if args == ["status", "--porcelain"]:
                return MagicMock(returncode=0, stdout="")
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return MagicMock(returncode=0, stdout="main\n")
            if args[:1] == ["fetch"]:
                return MagicMock(returncode=0, stdout="")
            if args[:1] == ["pull"]:
                return MagicMock(returncode=0, stdout="Already up to date.\n")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = track_calls

        result = pull_upstream(Path("/repo"))
        # No "checkout" call should appear
        checkout_calls = [c for c in calls if "checkout" in c]
        assert len(checkout_calls) == 0

    @patch("app.update_manager._run_git")
    def test_restores_branch_on_fetch_failure(self, mock_run):
        """When fetch fails on a non-main branch, checkout back to original."""
        calls = []
        def track_calls(args, cwd=None):
            calls.append(args)
            if args == ["rev-parse", "--short", "HEAD"]:
                return MagicMock(returncode=0, stdout="abc1234\n")
            if args == ["remote"]:
                return MagicMock(returncode=0, stdout="upstream\n")
            if args == ["status", "--porcelain"]:
                return MagicMock(returncode=0, stdout="")
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return MagicMock(returncode=0, stdout="koan/feature\n")
            if args == ["checkout", "main"]:
                return MagicMock(returncode=0, stdout="")
            if args[:1] == ["fetch"]:
                return MagicMock(returncode=1, stdout="", stderr="network error")
            if args == ["checkout", "koan/feature"]:
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = track_calls

        result = pull_upstream(Path("/repo"))
        assert result.success is False
        # Should have attempted to restore original branch
        checkout_restore = [c for c in calls if c == ["checkout", "koan/feature"]]
        assert len(checkout_restore) == 1
