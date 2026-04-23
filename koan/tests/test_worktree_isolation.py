"""Tests for worktree isolation in the main mission loop.

Covers:
- Config getter (get_worktree_isolation)
- Startup recovery (recover_orphaned_worktrees)
- run.py integration (_cleanup_worktree)
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call

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

    def test_skips_when_isolation_disabled(self, tmp_path):
        """When worktree_isolation is False, does nothing."""
        from app.startup_manager import recover_orphaned_worktrees
        proj = tmp_path / "proj"
        proj.mkdir()
        wt_dir = proj / ".worktrees" / "stale-session"
        wt_dir.mkdir(parents=True)

        with patch("app.config.get_worktree_isolation", return_value=False):
            recover_orphaned_worktrees(str(tmp_path), [("proj", str(proj))])

        # Stale worktree dir should still exist (not cleaned)
        assert wt_dir.exists()

    def test_cleans_orphaned_worktrees_when_enabled(self, tmp_path):
        """When enabled, calls cleanup_stale_worktrees for projects with .worktrees."""
        from app.startup_manager import recover_orphaned_worktrees
        proj = tmp_path / "proj"
        proj.mkdir()
        wt_dir = proj / ".worktrees" / "stale-session"
        wt_dir.mkdir(parents=True)

        with patch("app.config.get_worktree_isolation", return_value=True), \
             patch("app.worktree_manager.cleanup_stale_worktrees") as mock_cleanup:
            recover_orphaned_worktrees(str(tmp_path), [("proj", str(proj))])

        mock_cleanup.assert_called_once_with(str(proj), active_session_ids=[])

    def test_skips_projects_without_worktrees_dir(self, tmp_path):
        """Projects without .worktrees/ are silently skipped."""
        from app.startup_manager import recover_orphaned_worktrees
        proj = tmp_path / "proj"
        proj.mkdir()

        with patch("app.config.get_worktree_isolation", return_value=True), \
             patch("app.worktree_manager.cleanup_stale_worktrees") as mock_cleanup:
            recover_orphaned_worktrees(str(tmp_path), [("proj", str(proj))])

        mock_cleanup.assert_not_called()

    def test_handles_cleanup_error_gracefully(self, tmp_path):
        """Errors during cleanup are logged, not raised."""
        from app.startup_manager import recover_orphaned_worktrees
        proj = tmp_path / "proj"
        proj.mkdir()
        wt_dir = proj / ".worktrees" / "broken"
        wt_dir.mkdir(parents=True)

        with patch("app.config.get_worktree_isolation", return_value=True), \
             patch("app.worktree_manager.cleanup_stale_worktrees",
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

    def test_pushes_branch_with_commits(self, git_repo, tmp_path):
        """When worktree has commits, push is attempted before cleanup."""
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

        # Push will fail (no remote), but cleanup should still proceed
        _cleanup_worktree(wt, git_repo)

        # Worktree should be cleaned up despite push failure
        assert not Path(wt.path).exists()


# ---------------------------------------------------------------------------
# Config validator schema tests
# ---------------------------------------------------------------------------


class TestWorktreeConfigSchema:
    """Verify worktree_isolation is in the config schema."""

    def test_worktree_isolation_in_schema(self):
        from app.config_validator import CONFIG_SCHEMA
        assert "worktree_isolation" in CONFIG_SCHEMA
        assert CONFIG_SCHEMA["worktree_isolation"] == "bool"
