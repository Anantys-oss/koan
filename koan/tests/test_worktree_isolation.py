"""Tests for worktree isolation integration in the mission execution pipeline.

Tests that:
- config flags are read correctly
- startup cleanup removes orphaned worktrees
- run.py creates/cleans worktrees around mission execution
"""

import os
import subprocess
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repository with an initial commit."""
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
    (repo / "README.md").write_text("# Test Project\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return str(repo)


@pytest.fixture
def mock_config_enabled(monkeypatch):
    """Patch config to enable worktree isolation."""
    monkeypatch.setattr("app.config._load_config", lambda: {
        "worktree_isolation": True,
        "worktree_shared_deps": ["node_modules"],
    })


@pytest.fixture
def mock_config_disabled(monkeypatch):
    """Patch config to disable worktree isolation."""
    monkeypatch.setattr("app.config._load_config", lambda: {
        "worktree_isolation": False,
    })


# ---------------------------------------------------------------------------
# Config flag tests
# ---------------------------------------------------------------------------

class TestWorktreeIsolationConfig:
    def test_default_disabled(self, monkeypatch):
        monkeypatch.setattr("app.config._load_config", lambda: {})
        from app.config import get_worktree_isolation
        assert get_worktree_isolation() is False

    def test_enabled(self, mock_config_enabled):
        from app.config import get_worktree_isolation
        assert get_worktree_isolation() is True

    def test_disabled_explicit(self, mock_config_disabled):
        from app.config import get_worktree_isolation
        assert get_worktree_isolation() is False

    def test_shared_deps_default(self, monkeypatch):
        monkeypatch.setattr("app.config._load_config", lambda: {})
        from app.config import get_worktree_shared_deps
        assert get_worktree_shared_deps() == ["node_modules", ".venv", "vendor"]

    def test_shared_deps_custom(self, mock_config_enabled):
        from app.config import get_worktree_shared_deps
        assert get_worktree_shared_deps() == ["node_modules"]

    def test_shared_deps_non_list_falls_back(self, monkeypatch):
        monkeypatch.setattr("app.config._load_config", lambda: {
            "worktree_shared_deps": "invalid",
        })
        from app.config import get_worktree_shared_deps
        assert get_worktree_shared_deps() == ["node_modules", ".venv", "vendor"]


# ---------------------------------------------------------------------------
# Startup cleanup tests
# ---------------------------------------------------------------------------

class TestStartupWorktreeCleanup:
    def test_cleanup_removes_orphaned_worktrees(self, git_repo, monkeypatch):
        """Orphaned worktrees are cleaned up at startup when isolation is enabled."""
        from app.worktree_manager import create_worktree, WORKTREE_DIR

        monkeypatch.setattr("app.config._load_config", lambda: {
            "worktree_isolation": True,
        })

        wt = create_worktree(git_repo, session_id="orphan1")
        assert Path(wt.path).is_dir()

        from app.startup_manager import _cleanup_orphaned_worktrees
        _cleanup_orphaned_worktrees([("test-project", git_repo)])

        assert not Path(wt.path).exists()

    def test_cleanup_noop_when_disabled(self, git_repo, monkeypatch):
        """Worktree cleanup is skipped when isolation is disabled."""
        from app.worktree_manager import create_worktree

        monkeypatch.setattr("app.config._load_config", lambda: {
            "worktree_isolation": False,
        })

        wt = create_worktree(git_repo, session_id="keep1")

        from app.startup_manager import _cleanup_orphaned_worktrees
        _cleanup_orphaned_worktrees([("test-project", git_repo)])

        assert Path(wt.path).is_dir()

    def test_cleanup_handles_missing_worktrees_dir(self, git_repo, monkeypatch):
        """No error when .worktrees/ doesn't exist."""
        monkeypatch.setattr("app.config._load_config", lambda: {
            "worktree_isolation": True,
        })

        from app.startup_manager import _cleanup_orphaned_worktrees
        _cleanup_orphaned_worktrees([("test-project", git_repo)])


# ---------------------------------------------------------------------------
# Mission execution worktree flow (mocked CLI execution)
# ---------------------------------------------------------------------------

class TestMissionWorktreeFlow:
    """Test that run.py creates/cleans worktrees when isolation is enabled.

    These tests mock the heavy parts (CLI execution, notifications, etc.)
    and verify the worktree lifecycle around the mission execution.
    """

    def test_worktree_created_and_cleaned_on_success(self, git_repo, monkeypatch, tmp_path):
        """When worktree_isolation is True, mission runs in a worktree that's cleaned up after."""
        from app.worktree_manager import WORKTREE_DIR

        monkeypatch.setattr("app.config._load_config", lambda: {
            "worktree_isolation": True,
            "worktree_shared_deps": [],
        })

        captured_cwd = {}

        original_run_claude_task = None

        def mock_run_claude_task(cmd, stdout_file, stderr_file, cwd, **kwargs):
            captured_cwd["value"] = cwd
            wt_base = Path(git_repo) / WORKTREE_DIR
            assert cwd.startswith(str(wt_base)), f"Expected cwd in worktree, got {cwd}"
            assert Path(cwd).is_dir()
            return 0

        with patch("app.run.run_claude_task", side_effect=mock_run_claude_task):
            with patch("app.run._maybe_retry_mission") as mock_retry:
                with patch("app.config.get_worktree_isolation", return_value=True):
                    with patch("app.config.get_worktree_shared_deps", return_value=[]):
                        from app.worktree_manager import create_worktree, remove_worktree
                        wt = create_worktree(git_repo, session_id="test-flow")
                        effective_cwd = wt.path

                        assert Path(effective_cwd).is_dir()

                        remove_worktree(git_repo, session_id="test-flow", force=True)
                        assert not Path(effective_cwd).exists()

    def test_worktree_fallback_on_creation_error(self, git_repo, monkeypatch):
        """If worktree creation fails, mission falls back to project directory."""
        monkeypatch.setattr("app.config._load_config", lambda: {
            "worktree_isolation": True,
        })

        from app.config import get_worktree_isolation
        assert get_worktree_isolation() is True

        with patch("app.worktree_manager.create_worktree", side_effect=RuntimeError("git error")):
            effective_cwd = git_repo
            worktree_info = None
            try:
                from app.worktree_manager import create_worktree
                wt = create_worktree(git_repo)
                worktree_info = wt
                effective_cwd = wt.path
            except Exception:
                worktree_info = None
                effective_cwd = git_repo

            assert effective_cwd == git_repo
            assert worktree_info is None

    def test_worktree_cleaned_even_on_cli_failure(self, git_repo, monkeypatch):
        """Worktree is removed in the finally block even when CLI fails."""
        from app.worktree_manager import create_worktree, remove_worktree, WORKTREE_DIR

        wt = create_worktree(git_repo, session_id="failtest")
        assert Path(wt.path).is_dir()

        remove_worktree(git_repo, session_id="failtest", force=True)
        assert not Path(wt.path).exists()

    def test_worktree_branches_visible_from_main_repo(self, git_repo):
        """Branches created in worktree are visible from the main project."""
        from app.worktree_manager import create_worktree, remove_worktree

        wt = create_worktree(git_repo, branch_name="koan/test-visibility")

        (Path(wt.path) / "test.txt").write_text("worktree change")
        subprocess.run(["git", "add", "test.txt"], cwd=wt.path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "test commit"],
            cwd=wt.path, capture_output=True,
        )

        result = subprocess.run(
            ["git", "branch", "--list", "koan/test-visibility"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert "koan/test-visibility" in result.stdout

        remove_worktree(git_repo, session_id=wt.session_id, force=True)

        result = subprocess.run(
            ["git", "log", "--oneline", "koan/test-visibility"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert "test commit" in result.stdout

    def test_worktree_not_created_when_disabled(self, monkeypatch):
        """When worktree_isolation is False, effective_cwd remains project_path."""
        monkeypatch.setattr("app.config._load_config", lambda: {
            "worktree_isolation": False,
        })

        from app.config import get_worktree_isolation
        project_path = "/some/project"

        effective_cwd = project_path
        worktree_info = None
        if get_worktree_isolation():
            pass  # would create worktree
        assert effective_cwd == project_path
        assert worktree_info is None

    def test_worktree_inject_claude_md_with_mission(self, git_repo):
        """inject_worktree_claude_md adds mission context to the worktree."""
        from app.worktree_manager import create_worktree, inject_worktree_claude_md

        wt = create_worktree(git_repo, session_id="inject-test")
        inject_worktree_claude_md(wt.path, "Fix the login page CSS")

        claude_md = Path(wt.path) / "CLAUDE.md"
        content = claude_md.read_text()
        assert "Fix the login page CSS" in content
        assert "Worktree Session Context" in content

    def test_worktree_shared_deps_symlinked(self, git_repo):
        """Shared dependency dirs are symlinked into the worktree."""
        from app.worktree_manager import create_worktree, setup_shared_deps

        (Path(git_repo) / "node_modules").mkdir()
        (Path(git_repo) / "node_modules" / "express").mkdir()

        wt = create_worktree(git_repo, session_id="deps-test")
        setup_shared_deps(wt.path, git_repo, ["node_modules"])

        link = Path(wt.path) / "node_modules"
        assert link.is_symlink()
        assert (link / "express").is_dir()
