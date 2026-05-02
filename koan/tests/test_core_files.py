"""Tests for core_files — unversioned file integrity checker."""

import os
import subprocess
import pytest
from pathlib import Path

from app.core_files import (
    CORE_PATHS,
    PROJECT_CORE_PATHS,
    snapshot_core_files,
    check_core_files,
    log_integrity_warnings,
    recover_project_files,
)


@pytest.fixture
def fake_koan_root(tmp_path):
    """Create a minimal koan root with all core files present."""
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "missions.md").write_text("# Missions\n")
    (instance / "config.yaml").write_text("enabled: true\n")
    (instance / "soul.md").write_text("# Soul\n")
    (instance / "memory").mkdir()
    (tmp_path / "projects.yaml").write_text("projects: []\n")
    return tmp_path


@pytest.fixture
def fake_project(tmp_path):
    """Create a minimal project directory with .env and CLAUDE.md."""
    proj = tmp_path / "myproject"
    proj.mkdir()
    (proj / ".env").write_text("SECRET=xxx\n")
    (proj / "CLAUDE.md").write_text("# Project\n")
    return proj


class TestSnapshotCoreFiles:
    def test_all_present(self, fake_koan_root):
        snap = snapshot_core_files(str(fake_koan_root))
        assert "instance/" in snap
        assert "instance/missions.md" in snap
        assert "instance/config.yaml" in snap
        assert "instance/soul.md" in snap
        assert "instance/memory/" in snap
        assert "projects.yaml" in snap

    def test_missing_file(self, fake_koan_root):
        (fake_koan_root / "projects.yaml").unlink()
        snap = snapshot_core_files(str(fake_koan_root))
        assert "projects.yaml" not in snap
        assert "instance/" in snap  # other files still present

    def test_missing_directory(self, fake_koan_root):
        import shutil
        shutil.rmtree(fake_koan_root / "instance" / "memory")
        snap = snapshot_core_files(str(fake_koan_root))
        assert "instance/memory/" not in snap
        assert "instance/" in snap

    def test_with_project_path(self, fake_koan_root, fake_project):
        snap = snapshot_core_files(str(fake_koan_root), str(fake_project))
        assert "project:.env" in snap
        assert "project:CLAUDE.md" in snap

    def test_project_env_missing(self, fake_koan_root, tmp_path):
        proj = tmp_path / "noproj"
        proj.mkdir()
        snap = snapshot_core_files(str(fake_koan_root), str(proj))
        assert "project:.env" not in snap

    def test_no_project_path(self, fake_koan_root):
        snap = snapshot_core_files(str(fake_koan_root), None)
        # Should only contain koan root paths
        assert all(not p.startswith("project:") for p in snap)


class TestCheckCoreFiles:
    def test_no_changes(self, fake_koan_root):
        before = snapshot_core_files(str(fake_koan_root))
        warnings = check_core_files(str(fake_koan_root), before)
        assert warnings == []

    def test_file_removed(self, fake_koan_root):
        before = snapshot_core_files(str(fake_koan_root))
        (fake_koan_root / "projects.yaml").unlink()
        warnings = check_core_files(str(fake_koan_root), before)
        assert len(warnings) == 1
        assert "projects.yaml" in warnings[0]

    def test_directory_removed(self, fake_koan_root):
        before = snapshot_core_files(str(fake_koan_root))
        import shutil
        shutil.rmtree(fake_koan_root / "instance" / "memory")
        warnings = check_core_files(str(fake_koan_root), before)
        assert any("instance/memory/" in w for w in warnings)

    def test_multiple_removals(self, fake_koan_root):
        before = snapshot_core_files(str(fake_koan_root))
        (fake_koan_root / "projects.yaml").unlink()
        (fake_koan_root / "instance" / "soul.md").unlink()
        warnings = check_core_files(str(fake_koan_root), before)
        assert len(warnings) == 2

    def test_project_env_removed(self, fake_koan_root, fake_project):
        before = snapshot_core_files(str(fake_koan_root), str(fake_project))
        (fake_project / ".env").unlink()
        warnings = check_core_files(str(fake_koan_root), before, str(fake_project))
        assert any("Project file disappeared: .env" in w for w in warnings)

    def test_project_claudemd_removed(self, fake_koan_root, fake_project):
        before = snapshot_core_files(str(fake_koan_root), str(fake_project))
        assert "project:CLAUDE.md" in before
        (fake_project / "CLAUDE.md").unlink()
        warnings = check_core_files(str(fake_koan_root), before, str(fake_project))
        assert any("Project file disappeared: CLAUDE.md" in w for w in warnings)

    def test_file_added_no_warning(self, fake_koan_root):
        """Adding new files should not trigger warnings."""
        # Snapshot without projects.yaml
        (fake_koan_root / "projects.yaml").unlink()
        before = snapshot_core_files(str(fake_koan_root))
        # Recreate it
        (fake_koan_root / "projects.yaml").write_text("projects: []\n")
        warnings = check_core_files(str(fake_koan_root), before)
        assert warnings == []

    def test_empty_snapshot_no_warnings(self, tmp_path):
        """If nothing existed before, nothing can disappear."""
        before = snapshot_core_files(str(tmp_path))
        warnings = check_core_files(str(tmp_path), before)
        assert warnings == []


@pytest.fixture
def git_project(tmp_path):
    """Create a project directory with a git repo and tracked CLAUDE.md."""
    proj = tmp_path / "gitproject"
    proj.mkdir()
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(proj), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(proj), capture_output=True, check=True,
    )
    (proj / "CLAUDE.md").write_text("# Project\n")
    (proj / ".env").write_text("SECRET=xxx\n")
    subprocess.run(["git", "add", "CLAUDE.md"], cwd=str(proj), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(proj), capture_output=True, check=True,
    )
    return proj


class TestRecoverProjectFiles:
    def test_recover_tracked_file(self, git_project):
        """CLAUDE.md is tracked — should be auto-recovered via git checkout."""
        (git_project / "CLAUDE.md").unlink()
        assert not (git_project / "CLAUDE.md").exists()

        missing = {"project:CLAUDE.md"}
        recovered, unrecoverable = recover_project_files(missing, str(git_project))

        assert recovered == ["CLAUDE.md"]
        assert unrecoverable == []
        assert (git_project / "CLAUDE.md").exists()

    def test_untracked_file_not_recovered(self, git_project):
        """.env is not tracked — cannot be recovered."""
        (git_project / ".env").unlink()

        missing = {"project:.env"}
        recovered, unrecoverable = recover_project_files(missing, str(git_project))

        assert recovered == []
        assert len(unrecoverable) == 1
        assert ".env" in unrecoverable[0]

    def test_core_files_not_recoverable(self, git_project):
        """Instance-level files can't be recovered via git."""
        missing = {"projects.yaml", "instance/soul.md"}
        recovered, unrecoverable = recover_project_files(missing, str(git_project))

        assert recovered == []
        assert len(unrecoverable) == 2

    def test_mixed_recovery(self, git_project):
        """Mix of recoverable and unrecoverable files."""
        (git_project / "CLAUDE.md").unlink()

        missing = {"project:CLAUDE.md", "project:.env", "projects.yaml"}
        # .env still exists, but we're testing the logic with the set
        (git_project / ".env").unlink()
        recovered, unrecoverable = recover_project_files(missing, str(git_project))

        assert "CLAUDE.md" in recovered
        assert any(".env" in u for u in unrecoverable)
        assert any("projects.yaml" in u for u in unrecoverable)

    def test_no_project_path(self):
        """Without project_path, all items are unrecoverable."""
        missing = {"project:CLAUDE.md", "projects.yaml"}
        recovered, unrecoverable = recover_project_files(missing, None)

        assert recovered == []
        assert len(unrecoverable) == 2

    def test_empty_missing_set(self, git_project):
        """No missing files — nothing to do."""
        recovered, unrecoverable = recover_project_files(set(), str(git_project))
        assert recovered == []
        assert unrecoverable == []


class TestLogIntegrityWarnings:
    def test_no_warnings(self, capsys):
        log_integrity_warnings([])
        assert capsys.readouterr().err == ""

    def test_with_warnings(self, capsys):
        log_integrity_warnings(["Core file disappeared: projects.yaml"])
        err = capsys.readouterr().err
        assert "INTEGRITY CHECK FAILED" in err
        assert "projects.yaml" in err
