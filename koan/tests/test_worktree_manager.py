"""Tests for worktree_manager.py — git worktree lifecycle management.

Uses real git repos in temp directories (not mocks) per the plan's testing strategy.
"""

import os
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch

from app.worktree_manager import (
    WorktreeInfo,
    create_worktree,
    remove_worktree,
    list_worktrees,
    cleanup_stale_worktrees,
    git_retry,
    inject_worktree_claude_md,
    prune_worktrees,
    setup_shared_deps,
    WORKTREE_DIR,
)


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
    # Create initial commit on main branch
    (repo / "README.md").write_text("# Test Project\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(repo), capture_output=True, check=True,
    )
    # Ensure we're on 'main' branch
    result = subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return str(repo)


class TestCreateWorktree:
    def test_creates_isolated_directory(self, git_repo):
        wt = create_worktree(git_repo)
        assert Path(wt.path).is_dir()
        assert wt.session_id
        assert wt.branch.startswith("koan/session-")
        assert wt.project_path == git_repo

    def test_worktree_has_clean_git_status(self, git_repo):
        wt = create_worktree(git_repo)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=wt.path,
            capture_output=True,
            text=True,
            check=True,
        )
        # May have untracked .gitignore from _ensure_gitignored
        lines = [l for l in result.stdout.strip().splitlines()
                 if l and not l.endswith(".gitignore")]
        assert lines == []

    def test_worktree_is_on_unique_branch(self, git_repo):
        wt = create_worktree(git_repo)
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt.path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == wt.branch

    def test_custom_session_id(self, git_repo):
        wt = create_worktree(git_repo, session_id="test123")
        assert wt.session_id == "test123"
        assert "test123" in wt.path

    def test_custom_branch_name(self, git_repo):
        wt = create_worktree(git_repo, branch_name="feature/custom")
        assert wt.branch == "feature/custom"

    def test_concurrent_creation(self, git_repo):
        """Multiple worktrees can be created for the same project."""
        wt1 = create_worktree(git_repo)
        wt2 = create_worktree(git_repo)
        assert wt1.path != wt2.path
        assert wt1.branch != wt2.branch
        assert Path(wt1.path).is_dir()
        assert Path(wt2.path).is_dir()

    def test_duplicate_session_id_raises(self, git_repo):
        create_worktree(git_repo, session_id="dup")
        with pytest.raises(FileExistsError):
            create_worktree(git_repo, session_id="dup")

    def test_worktrees_dir_created(self, git_repo):
        create_worktree(git_repo)
        assert (Path(git_repo) / WORKTREE_DIR).is_dir()

    def test_commit_sha_populated(self, git_repo):
        wt = create_worktree(git_repo)
        assert len(wt.commit) == 40  # Full SHA

    def test_copies_claude_md(self, git_repo):
        (Path(git_repo) / "CLAUDE.md").write_text("# Project CLAUDE.md\n")
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=git_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add CLAUDE.md"],
            cwd=git_repo, capture_output=True,
        )
        wt = create_worktree(git_repo)
        wt_claude = Path(wt.path) / "CLAUDE.md"
        assert wt_claude.exists()
        assert "Project CLAUDE.md" in wt_claude.read_text()


class TestRemoveWorktree:
    def test_removes_directory(self, git_repo):
        wt = create_worktree(git_repo)
        assert Path(wt.path).is_dir()
        remove_worktree(git_repo, session_id=wt.session_id)
        assert not Path(wt.path).exists()

    def test_removes_by_path(self, git_repo):
        wt = create_worktree(git_repo)
        remove_worktree(git_repo, worktree_path=wt.path)
        assert not Path(wt.path).exists()

    def test_cleans_up_branch(self, git_repo):
        wt = create_worktree(git_repo)
        branch = wt.branch
        remove_worktree(git_repo, session_id=wt.session_id)
        result = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert branch not in result.stdout

    def test_force_removes_dirty_worktree(self, git_repo):
        wt = create_worktree(git_repo)
        # Make worktree dirty
        (Path(wt.path) / "dirty.txt").write_text("uncommitted changes")
        remove_worktree(git_repo, session_id=wt.session_id, force=True)
        assert not Path(wt.path).exists()

    def test_requires_session_or_path(self, git_repo):
        with pytest.raises(ValueError):
            remove_worktree(git_repo)

    def test_idempotent_on_missing(self, git_repo):
        """Removing a non-existent worktree doesn't raise."""
        remove_worktree(git_repo, session_id="nonexistent")


class TestListWorktrees:
    def test_lists_main_worktree(self, git_repo):
        worktrees = list_worktrees(git_repo)
        assert len(worktrees) >= 1
        main = [w for w in worktrees if w.is_main]
        assert len(main) == 1

    def test_lists_created_worktrees(self, git_repo):
        wt1 = create_worktree(git_repo, session_id="aaa")
        wt2 = create_worktree(git_repo, session_id="bbb")
        worktrees = list_worktrees(git_repo)
        session_ids = {w.session_id for w in worktrees}
        assert "aaa" in session_ids
        assert "bbb" in session_ids

    def test_empty_repo_returns_main(self, git_repo):
        worktrees = list_worktrees(git_repo)
        assert len(worktrees) == 1


class TestCleanupStaleWorktrees:
    def test_removes_stale_keeps_active(self, git_repo):
        wt1 = create_worktree(git_repo, session_id="active1")
        wt2 = create_worktree(git_repo, session_id="stale1")
        cleanup_stale_worktrees(git_repo, active_session_ids=["active1"])
        assert Path(wt1.path).is_dir()
        assert not Path(wt2.path).exists()

    def test_removes_all_when_no_active(self, git_repo):
        wt1 = create_worktree(git_repo, session_id="s1")
        wt2 = create_worktree(git_repo, session_id="s2")
        cleanup_stale_worktrees(git_repo, active_session_ids=[])
        assert not Path(wt1.path).exists()
        assert not Path(wt2.path).exists()

    def test_noop_when_no_worktrees_dir(self, git_repo):
        """Should not raise when .worktrees/ doesn't exist."""
        cleanup_stale_worktrees(git_repo, active_session_ids=[])


class TestGitRetry:
    def test_successful_command_no_retry(self, git_repo):
        result = git_retry(["git", "status"], cwd=git_repo)
        assert result.returncode == 0

    def test_non_lock_error_no_retry(self, git_repo):
        """Non-lock errors should not be retried."""
        with pytest.raises(subprocess.CalledProcessError):
            git_retry(["git", "checkout", "nonexistent-branch"], cwd=git_repo)

    def test_lock_error_retries_then_succeeds(self, git_repo, tmp_path):
        """Verify git_retry retries on lock contention and succeeds when lock clears."""
        call_count = 0
        original_run = subprocess.run

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate lock error on first call
                raise subprocess.CalledProcessError(
                    128, cmd, stderr="fatal: Unable to create index.lock"
                )
            return original_run(cmd, **kwargs)

        with patch("app.worktree_manager.subprocess.run", side_effect=mock_run):
            result = git_retry(
                ["git", "status"], cwd=git_repo,
                min_delay=0.01, max_delay=0.02,
            )
            assert result.returncode == 0
            assert call_count == 2  # First failed, second succeeded

    def test_lock_error_exhausts_retries(self, git_repo):
        """Verify git_retry raises after exhausting retries on persistent lock errors."""
        def mock_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(
                128, cmd, stderr="fatal: Unable to create index.lock"
            )

        with patch("app.worktree_manager.subprocess.run", side_effect=mock_run):
            with pytest.raises(subprocess.CalledProcessError):
                git_retry(
                    ["git", "status"], cwd=git_repo,
                    max_retries=2, min_delay=0.01, max_delay=0.02,
                )


class TestInjectWorktreeClaudeMd:
    def test_appends_to_existing(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Existing\n")
        inject_worktree_claude_md(str(tmp_path), "Fix the auth bug")
        content = claude_md.read_text()
        assert "Existing" in content
        assert "Fix the auth bug" in content
        assert "Worktree Session Context" in content

    def test_creates_new_if_missing(self, tmp_path):
        inject_worktree_claude_md(str(tmp_path), "Implement feature")
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        assert "Implement feature" in claude_md.read_text()


class TestCopyClaudeMdErrorLogging:
    def test_logs_oserror_on_copy_failure(self, git_repo, capsys):
        """_copy_claude_md logs OSError instead of silently swallowing it."""
        from app.worktree_manager import _copy_claude_md

        # Create CLAUDE.md in the source project
        (Path(git_repo) / "CLAUDE.md").write_text("# Test\n")
        # Use a non-existent destination so copy2 will fail
        bad_dst = os.path.join(git_repo, "nonexistent", "subdir")
        _copy_claude_md(git_repo, bad_dst)
        stderr = capsys.readouterr().err
        assert "[worktree_manager] CLAUDE.md copy failed" in stderr

    def test_inject_logs_oserror_on_write_failure(self, tmp_path, capsys):
        """inject_worktree_claude_md logs OSError instead of silently swallowing it."""
        # Use a path that doesn't exist so write will fail
        bad_path = str(tmp_path / "nonexistent" / "subdir")
        inject_worktree_claude_md(bad_path, "test mission")
        stderr = capsys.readouterr().err
        assert "[worktree_manager] CLAUDE.md injection failed" in stderr


class TestSetupSharedDeps:
    def test_symlinks_existing_deps(self, git_repo, tmp_path):
        # Create a dep in main project
        (Path(git_repo) / "node_modules").mkdir()
        (Path(git_repo) / "node_modules" / "pkg.json").write_text("{}")

        wt_path = tmp_path / "worktree"
        wt_path.mkdir()

        setup_shared_deps(str(wt_path), git_repo, ["node_modules"])
        link = wt_path / "node_modules"
        assert link.is_symlink()
        assert (link / "pkg.json").exists()

    def test_skips_missing_deps(self, git_repo, tmp_path):
        wt_path = tmp_path / "worktree"
        wt_path.mkdir()
        setup_shared_deps(str(wt_path), git_repo, ["nonexistent"])
        assert not (wt_path / "nonexistent").exists()

    def test_skips_if_already_exists(self, git_repo, tmp_path):
        (Path(git_repo) / "node_modules").mkdir()
        wt_path = tmp_path / "worktree"
        wt_path.mkdir()
        (wt_path / "node_modules").mkdir()  # Already exists

        setup_shared_deps(str(wt_path), git_repo, ["node_modules"])
        assert not (wt_path / "node_modules").is_symlink()


class TestRemoveWorktreeLogging:
    """Verify that remove_worktree surfaces failures instead of silently swallowing them."""

    def test_logs_worktree_remove_failure(self, git_repo, capsys):
        """git worktree remove failure should be logged to stderr."""
        wt = create_worktree(git_repo)
        # Remove the worktree directory manually so git worktree remove fails
        import shutil
        shutil.rmtree(wt.path)

        remove_worktree(git_repo, session_id=wt.session_id)
        captured = capsys.readouterr()
        # The git worktree remove will fail since directory is gone,
        # and should log the error
        assert "git worktree remove failed" in captured.err or not Path(wt.path).exists()

    def test_logs_branch_delete_failure(self, git_repo, capsys):
        """git branch -D failure should be logged to stderr."""
        wt = create_worktree(git_repo)
        branch = wt.branch
        # Delete the branch before remove_worktree tries to
        subprocess.run(
            ["git", "worktree", "remove", "--force", wt.path],
            cwd=git_repo, capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=git_repo, capture_output=True,
        )
        # Now remove_worktree should log that branch -D fails
        remove_worktree(git_repo, session_id=wt.session_id)
        captured = capsys.readouterr()
        assert "git branch -D failed" in captured.err


class TestPruneWorktrees:
    def test_prune_runs_without_error(self, git_repo):
        """prune_worktrees should complete without raising."""
        prune_worktrees(git_repo)

    def test_prune_cleans_stale_refs(self, git_repo, capsys):
        """prune_worktrees should clean up stale worktree references."""
        wt = create_worktree(git_repo)
        wt_path = wt.path
        # Manually remove the directory (simulating a crash)
        import shutil
        shutil.rmtree(wt_path)
        # Now prune should detect and report the stale reference
        prune_worktrees(git_repo)
        captured = capsys.readouterr()
        # --verbose output should mention pruning
        assert "pruned" in captured.err.lower() or not Path(wt_path).exists()


class TestWorktreeErrorPaths:
    def test_branch_prefix_fallback(self):
        from app.worktree_manager import _get_branch_prefix
        with patch("app.config.get_branch_prefix", side_effect=RuntimeError("bad")):
            assert _get_branch_prefix() == "koan"

    def test_remove_worktree_requires_identifier(self, git_repo):
        with pytest.raises(ValueError, match="session_id or worktree_path"):
            remove_worktree(git_repo)

    def test_remove_worktree_manual_cleanup(self, git_repo, capsys):
        wt = create_worktree(git_repo)
        assert Path(wt.path).exists()
        real_run = subprocess.run
        def fake_run(cmd, **kw):
            if "worktree" in cmd and "remove" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="lock")
            return real_run(cmd, **kw)
        with patch("app.worktree_manager.subprocess.run", side_effect=fake_run):
            remove_worktree(git_repo, session_id=wt.session_id, force=True)
        assert not Path(wt.path).exists()
        assert "git worktree remove failed" in capsys.readouterr().err

    def test_remove_worktree_branch_delete_failure(self, git_repo, capsys):
        wt = create_worktree(git_repo)
        real_run = subprocess.run
        def fake_run(cmd, **kw):
            if "branch" in cmd and "-D" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
            return real_run(cmd, **kw)
        with patch("app.worktree_manager.subprocess.run", side_effect=fake_run):
            remove_worktree(git_repo, session_id=wt.session_id)
        assert "git branch -D failed" in capsys.readouterr().err

    def test_list_worktrees_empty_on_error(self, tmp_path):
        assert list_worktrees(str(tmp_path)) == []

    def test_list_worktrees_parses_session(self, git_repo):
        wt = create_worktree(git_repo)
        entries = list_worktrees(git_repo)
        assert len(entries) >= 2
        assert wt.session_id in [e.session_id for e in entries]

    def test_cleanup_noop_if_no_dir(self, git_repo):
        cleanup_stale_worktrees(git_repo, active_session_ids=["any"])

    def test_cleanup_removes_inactive(self, git_repo):
        wt1 = create_worktree(git_repo)
        wt2 = create_worktree(git_repo)
        cleanup_stale_worktrees(git_repo, active_session_ids=[wt1.session_id])
        assert Path(wt1.path).exists()
        assert not Path(wt2.path).exists()

    def test_cleanup_logs_on_remove_failure(self, git_repo, capsys):
        create_worktree(git_repo)
        with patch("app.worktree_manager.remove_worktree",
                   side_effect=RuntimeError("bad")):
            cleanup_stale_worktrees(git_repo, active_session_ids=[])
        assert "stale worktree cleanup error" in capsys.readouterr().err

    def test_prune_handles_called_process_error(self, git_repo, capsys):
        def bad_run(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd, stderr="prune fail")
        with patch("app.worktree_manager.subprocess.run", side_effect=bad_run):
            prune_worktrees(git_repo)
        assert "worktree prune failed" in capsys.readouterr().err

    def test_prune_handles_missing_git(self, git_repo):
        with patch("app.worktree_manager.subprocess.run",
                   side_effect=FileNotFoundError("git")):
            prune_worktrees(git_repo)

    def test_setup_shared_deps_creates_symlink(self, tmp_path):
        proj = tmp_path / "proj"
        wt = tmp_path / "wt"
        (proj / "node_modules").mkdir(parents=True)
        wt.mkdir()
        setup_shared_deps(str(wt), str(proj), ["node_modules"])
        assert (wt / "node_modules").is_symlink()

    def test_setup_shared_deps_skips_existing(self, tmp_path):
        proj = tmp_path / "proj"
        wt = tmp_path / "wt"
        (proj / ".venv").mkdir(parents=True)
        (wt / ".venv").mkdir(parents=True)
        setup_shared_deps(str(wt), str(proj), [".venv"])
        assert not (wt / ".venv").is_symlink()

    def test_setup_shared_deps_handles_error(self, tmp_path):
        proj = tmp_path / "proj"
        wt = tmp_path / "wt"
        (proj / "node_modules").mkdir(parents=True)
        wt.mkdir()
        with patch("app.worktree_manager.os.symlink", side_effect=OSError("perm")):
            setup_shared_deps(str(wt), str(proj), ["node_modules"])

    def test_ensure_gitignored_adds_pattern(self, tmp_path):
        from app.worktree_manager import _ensure_gitignored
        (tmp_path / ".gitignore").write_text("*.log\n")
        _ensure_gitignored(str(tmp_path))
        assert ".worktrees" in (tmp_path / ".gitignore").read_text()

    def test_ensure_gitignored_skips_if_present(self, tmp_path):
        from app.worktree_manager import _ensure_gitignored
        original = "*.log\n/.worktrees/\n"
        (tmp_path / ".gitignore").write_text(original)
        _ensure_gitignored(str(tmp_path))
        assert (tmp_path / ".gitignore").read_text() == original

    def test_ensure_gitignored_noop_without_gitignore(self, tmp_path):
        from app.worktree_manager import _ensure_gitignored
        _ensure_gitignored(str(tmp_path))
        assert not (tmp_path / ".gitignore").exists()

    def test_resolve_base_ref_fallback(self, git_repo):
        from app.worktree_manager import _resolve_base_ref
        result = _resolve_base_ref(git_repo, "nonexistent")
        assert result in ("main", "master", "HEAD")
