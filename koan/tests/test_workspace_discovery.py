"""Tests for workspace_discovery.py."""

import os
from pathlib import Path
from unittest.mock import patch, PropertyMock

import pytest

from app.workspace_discovery import _validate_entry, discover_workspace_projects


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace directory under a mock KOAN_ROOT."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return tmp_path


def test_empty_workspace(workspace):
    """Empty workspace returns empty list."""
    result = discover_workspace_projects(str(workspace))
    assert result == []


def test_no_workspace_dir(tmp_path):
    """Missing workspace/ returns empty list (not an error)."""
    result = discover_workspace_projects(str(tmp_path))
    assert result == []


def test_direct_directories(workspace):
    """Direct directories are discovered."""
    ws = workspace / "workspace"
    (ws / "alpha").mkdir()
    (ws / "beta").mkdir()
    (ws / "gamma").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 3
    assert result[0][0] == "alpha"
    assert result[1][0] == "beta"
    assert result[2][0] == "gamma"


def test_symlinks_resolved(workspace):
    """Symlinks are resolved to their real paths."""
    ws = workspace / "workspace"
    real_dir = workspace / "real-project"
    real_dir.mkdir()
    (ws / "my-link").symlink_to(real_dir)

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 1
    assert result[0][0] == "my-link"
    assert result[0][1] == str(real_dir.resolve())


def test_broken_symlinks_skipped(workspace):
    """Broken symlinks are skipped with a warning."""
    ws = workspace / "workspace"
    (ws / "broken").symlink_to("/nonexistent/path/that/should/wont/exist")
    (ws / "good").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 1
    assert result[0][0] == "good"


def test_symlink_loops_skipped(workspace):
    """Symlink loops are skipped without crashing."""
    ws = workspace / "workspace"
    link1 = ws / "loop1"
    link2 = ws / "loop2"
    link1.symlink_to(link2)
    link2.symlink_to(link1)
    (ws / "good").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 1
    assert result[0][0] == "good"


def test_hidden_directories_skipped(workspace):
    """Directories starting with . are skipped."""
    ws = workspace / "workspace"
    (ws / ".git").mkdir()
    (ws / "__pycache__").mkdir()  # Not hidden but shows non-hidden works
    (ws / ".hidden").mkdir()
    (ws / "visible").mkdir()

    result = discover_workspace_projects(str(workspace))
    names = [n for n, _ in result]
    assert "visible" in names
    assert "__pycache__" in names  # Not hidden (doesn't start with .)
    assert ".git" not in names
    assert ".hidden" not in names


def test_files_skipped(workspace):
    """Regular files in workspace are ignored."""
    ws = workspace / "workspace"
    (ws / "README.md").write_text("# Docs")
    (ws / "notes.txt").write_text("notes")
    (ws / "real-project").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 1
    assert result[0][0] == "real-project"


def test_sorted_alphabetically(workspace):
    """Results are sorted case-insensitively."""
    ws = workspace / "workspace"
    (ws / "Zebra").mkdir()
    (ws / "alpha").mkdir()
    (ws / "Beta").mkdir()

    result = discover_workspace_projects(str(workspace))
    names = [n for n, _ in result]
    assert names == ["alpha", "Beta", "Zebra"]


def test_resolved_paths_are_absolute(workspace):
    """All returned paths are absolute."""
    ws = workspace / "workspace"
    (ws / "proj").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert Path(result[0][1]).is_absolute()


class TestValidateEntryErrorPaths:
    """Cover OSError/RuntimeError branches in _validate_entry."""

    def test_is_file_oserror_continues_to_resolve(self, tmp_path):
        """When is_file() raises OSError, entry should still be validated (lines 59-60)."""
        entry = tmp_path / "tricky"
        entry.mkdir()

        with patch.object(Path, "is_file", side_effect=OSError("permission denied")):
            result = _validate_entry(entry)
        # Should still resolve successfully since entry is a real directory.
        assert result == str(entry.resolve())

    def test_resolve_oserror_returns_none(self, tmp_path):
        """When resolve() raises OSError, returns None (lines 65-67)."""
        entry = tmp_path / "bad-resolve"
        entry.mkdir()

        with patch.object(Path, "resolve", side_effect=OSError("ENOMEM")):
            result = _validate_entry(entry)
        assert result is None

    def test_resolve_runtime_error_returns_none(self, tmp_path):
        """When resolve() raises RuntimeError (e.g. loop), returns None."""
        entry = tmp_path / "loopy"
        entry.mkdir()

        with patch.object(Path, "resolve", side_effect=RuntimeError("symlink loop")):
            result = _validate_entry(entry)
        assert result is None

    def test_is_dir_oserror_returns_none(self, tmp_path):
        """When resolved.is_dir() raises OSError, returns None (lines 74-76)."""
        entry = tmp_path / "stat-fail"
        entry.mkdir()

        # We need is_file() to return False (so we proceed past line 57),
        # resolve() to succeed, but resolved.is_dir() to raise OSError.
        # Use a mock resolved path that raises on is_dir().
        from unittest.mock import MagicMock
        mock_resolved = MagicMock(spec=Path)
        mock_resolved.is_dir.side_effect = OSError("stat failed")

        with patch.object(Path, "is_file", return_value=False), \
             patch.object(Path, "resolve", return_value=mock_resolved):
            result = _validate_entry(entry)
        assert result is None

    def test_workspace_iterdir_oserror(self, tmp_path):
        """When iterdir() raises OSError, returns empty list (lines 29-31)."""
        ws = tmp_path / "workspace"
        ws.mkdir()

        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            result = discover_workspace_projects(str(tmp_path))
        assert result == []
