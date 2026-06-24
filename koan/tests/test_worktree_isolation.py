"""Tests for worktree isolation in the main mission loop.

Covers:
- Config getter (get_worktree_isolation)
- Startup recovery (recover_orphaned_worktrees)
- run.py integration (_cleanup_worktree)
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, call

import pytest


# ---------------------------------------------------------------------------
# Config getter tests
# ---------------------------------------------------------------------------


class TestGetWorktreeIsolation:
    """Tests for config.get_worktree_isolation()."""

    def test_default_is_false(self):
        with patch("app.config._load_config", return_value={}):
            from app.config import get_worktree_isolation
            assert get_worktree_isolation() is False

    def test_enabled_when_true(self):
        with patch("app.config._load_config", return_value={"worktree_isolation": True}):
            from app.config import get_worktree_isolation
            assert get_worktree_isolation() is True

    def test_disabled_when_false(self):
        with patch("app.config._load_config", return_value={"worktree_isolation": False}):
            from app.config import get_worktree_isolation
            assert get_worktree_isolation() is False

    def test_falsy_values_return_false(self):
        with patch("app.config._load_config", return_value={"worktree_isolation": 0}):
            from app.config import get_worktree_isolation
            assert get_worktree_isolation() is False

    def test_truthy_values_return_true(self):
        with patch("app.config._load_config", return_value={"worktree_isolation": "yes"}):
            from app.config import get_worktree_isolation
            assert get_worktree_isolation() is True


# ---------------------------------------------------------------------------
# Startup recovery tests
# ---------------------------------------------------------------------------


class TestRecoverOrphanedWorktrees:
    """Tests for startup_manager.recover_orphaned_worktrees()."""

    def test_runs_regardless_of_config(self, tmp_path):
        """Recovery runs even when worktree_isolation is False."""
        from app.startup_manager import recover_orphaned_worktrees
        proj = tmp_path / "proj"
        proj.mkdir()
        wt_dir = proj / ".worktrees" / "stale-session"
        wt_dir.mkdir(parents=True)

        with patch("app.worktree_manager.cleanup_stale_worktrees") as mock_cleanup, \
             patch("app.worktree_manager.push_worktree_branch_or_preserve",
                   return_value=(True, "no new commits")):
            recover_orphaned_worktrees(str(tmp_path), [("proj", str(proj))])

        # Safe-to-remove worktrees are not preserved, so none are passed as active.
        mock_cleanup.assert_called_once_with(str(proj), active_session_ids=[])

    def test_cleans_orphaned_worktrees(self, tmp_path):
        """Calls cleanup_stale_worktrees for projects with .worktrees."""
        from app.startup_manager import recover_orphaned_worktrees
        proj = tmp_path / "proj"
        proj.mkdir()
        wt_dir = proj / ".worktrees" / "stale-session"
        wt_dir.mkdir(parents=True)

        with patch("app.worktree_manager.cleanup_stale_worktrees") as mock_cleanup, \
             patch("app.worktree_manager.push_worktree_branch_or_preserve",
                   return_value=(True, "pushed")):
            recover_orphaned_worktrees(str(tmp_path), [("proj", str(proj))])

        mock_cleanup.assert_called_once_with(str(proj), active_session_ids=[])

    def test_preserves_orphaned_worktree_on_push_failure(self, tmp_path):
        """Worktrees that fail to push are preserved (passed as active), not pruned."""
        from app.startup_manager import recover_orphaned_worktrees
        proj = tmp_path / "proj"
        proj.mkdir()
        wt_dir = proj / ".worktrees" / "stale-session"
        wt_dir.mkdir(parents=True)

        with patch("app.worktree_manager.cleanup_stale_worktrees") as mock_cleanup, \
             patch("app.worktree_manager.push_worktree_branch_or_preserve",
                   return_value=(False, "push failed")):
            recover_orphaned_worktrees(str(tmp_path), [("proj", str(proj))])

        # The unpushable worktree is preserved by being marked active.
        mock_cleanup.assert_called_once_with(
            str(proj), active_session_ids=["stale-session"]
        )

    def test_skips_projects_without_worktrees_dir(self, tmp_path):
        """Projects without .worktrees/ are silently skipped."""
        from app.startup_manager import recover_orphaned_worktrees
        proj = tmp_path / "proj"
        proj.mkdir()

        with patch("app.worktree_manager.cleanup_stale_worktrees") as mock_cleanup:
            recover_orphaned_worktrees(str(tmp_path), [("proj", str(proj))])

        mock_cleanup.assert_not_called()

    def test_handles_cleanup_error_gracefully(self, tmp_path):
        """Errors during cleanup are logged, not raised."""
        from app.startup_manager import recover_orphaned_worktrees
        proj = tmp_path / "proj"
        proj.mkdir()
        wt_dir = proj / ".worktrees" / "broken"
        wt_dir.mkdir(parents=True)

        with patch("app.worktree_manager.cleanup_stale_worktrees",
                   side_effect=OSError("git failed")):
            # Should not raise
            recover_orphaned_worktrees(str(tmp_path), [("proj", str(proj))])


# ---------------------------------------------------------------------------
# _cleanup_worktree tests
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repository with an initial commit and remote."""
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), capture_output=True, check=True,
    )
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return str(repo)


class TestCleanupWorktree:
    """Tests for run._cleanup_worktree()."""

    def test_removes_worktree_after_mission(self, git_repo):
        """Worktree directory is removed after cleanup."""
        from app.worktree_manager import create_worktree, WorktreeInfo
        from app.run import _cleanup_worktree

        wt = create_worktree(git_repo)
        assert Path(wt.path).exists()

        _cleanup_worktree(wt, git_repo)

        assert not Path(wt.path).exists()

    def test_handles_already_removed_worktree(self, git_repo):
        """Cleanup of a non-existent worktree doesn't crash."""
        from app.worktree_manager import WorktreeInfo
        from app.run import _cleanup_worktree

        wt = WorktreeInfo(
            path="/nonexistent/path",
            branch="koan/session-abc",
            session_id="abc",
            project_path=git_repo,
        )
        # Should not raise
        _cleanup_worktree(wt, git_repo)

    def test_preserves_worktree_on_push_failure(self, git_repo, tmp_path):
        """When push fails, worktree is preserved to avoid data loss."""
        from app.worktree_manager import create_worktree
        from app.run import _cleanup_worktree

        wt = create_worktree(git_repo)

        # Create a commit in the worktree
        test_file = Path(wt.path) / "new_file.txt"
        test_file.write_text("test content\n")
        subprocess.run(["git", "add", "."], cwd=wt.path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "test commit"],
            cwd=wt.path, capture_output=True, check=True,
        )

        # Push will fail (no remote) — worktree must be preserved
        _cleanup_worktree(wt, git_repo)

        assert Path(wt.path).exists()

    def test_notifies_operator_on_push_failure(self, git_repo, tmp_path):
        """When push fails and instance provided, sends Telegram notification."""
        from app.worktree_manager import create_worktree
        from app.run import _cleanup_worktree

        wt = create_worktree(git_repo)

        # Create a commit in the worktree
        test_file = Path(wt.path) / "new_file.txt"
        test_file.write_text("test content\n")
        subprocess.run(["git", "add", "."], cwd=wt.path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "test commit"],
            cwd=wt.path, capture_output=True, check=True,
        )

        instance_dir = str(tmp_path / "instance")
        with patch("app.run._notify") as mock_notify:
            _cleanup_worktree(wt, git_repo, instance=instance_dir)

        mock_notify.assert_called_once()
        assert "push failed" in mock_notify.call_args[0][1].lower()

    def test_skips_push_when_no_new_commits(self, git_repo):
        """Worktree with no new commits skips push and cleans up."""
        from app.worktree_manager import create_worktree
        from app.run import _cleanup_worktree

        wt = create_worktree(git_repo)
        # No commits made — should skip push entirely and remove worktree
        _cleanup_worktree(wt, git_repo)

        assert not Path(wt.path).exists()

    def test_preserves_worktree_on_detached_head_with_commits(self, git_repo):
        """Worktree with commits but detached HEAD is preserved (fail-safe)."""
        from app.worktree_manager import create_worktree, WorktreeInfo
        from app.run import _cleanup_worktree

        wt = create_worktree(git_repo)

        # Create a commit, then detach HEAD
        test_file = Path(wt.path) / "new_file.txt"
        test_file.write_text("test content\n")
        subprocess.run(["git", "add", "."], cwd=wt.path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "test commit"],
            cwd=wt.path, capture_output=True, check=True,
        )
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=wt.path, capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "checkout", head_sha],
            cwd=wt.path, capture_output=True, check=True,
        )

        _cleanup_worktree(wt, git_repo)

        # Worktree preserved because push couldn't be done (detached HEAD)
        assert Path(wt.path).exists()


# ---------------------------------------------------------------------------
# Config validator schema tests
# ---------------------------------------------------------------------------


class TestWorktreeConfigSchema:
    """Verify worktree_isolation is in the config schema."""

    def test_worktree_isolation_in_schema(self):
        from app.config_validator import CONFIG_SCHEMA
        assert "worktree_isolation" in CONFIG_SCHEMA
        assert CONFIG_SCHEMA["worktree_isolation"] == "bool"


# ---------------------------------------------------------------------------
# Shared push-or-preserve helper tests
# ---------------------------------------------------------------------------


class TestPushWorktreeBranchOrPreserve:
    """Tests for worktree_manager.push_worktree_branch_or_preserve()."""

    def test_no_new_commits_is_safe_to_remove(self, git_repo):
        from app.worktree_manager import create_worktree, push_worktree_branch_or_preserve

        wt = create_worktree(git_repo)
        safe, detail = push_worktree_branch_or_preserve(
            wt.path, initial_commit=wt.commit
        )
        assert safe is True
        assert "no new commits" in detail

    def test_push_failure_preserves(self, git_repo):
        from app.worktree_manager import create_worktree, push_worktree_branch_or_preserve

        wt = create_worktree(git_repo)
        (Path(wt.path) / "f.txt").write_text("x\n")
        subprocess.run(["git", "add", "."], cwd=wt.path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "c"], cwd=wt.path, capture_output=True, check=True
        )

        # No remote configured — push must fail and the worktree be preserved.
        safe, detail = push_worktree_branch_or_preserve(
            wt.path, initial_commit=wt.commit
        )
        assert safe is False
        assert "push failed" in detail or "push error" in detail

    def test_detached_head_with_commits_preserves(self, git_repo):
        from app.worktree_manager import create_worktree, push_worktree_branch_or_preserve

        wt = create_worktree(git_repo)
        (Path(wt.path) / "f.txt").write_text("x\n")
        subprocess.run(["git", "add", "."], cwd=wt.path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "c"], cwd=wt.path, capture_output=True, check=True
        )
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=wt.path, capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "checkout", sha], cwd=wt.path, capture_output=True, check=True
        )

        safe, detail = push_worktree_branch_or_preserve(
            wt.path, initial_commit=wt.commit
        )
        assert safe is False
        assert "unresolvable" in detail
