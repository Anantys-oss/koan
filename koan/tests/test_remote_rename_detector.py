"""Tests for remote_rename_detector module."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")

from app.remote_rename_detector import (
    _build_new_url,
    _extract_slug,
    _query_canonical_name,
    detect_and_fix_renamed_remotes,
)


class TestExtractSlug:
    def test_ssh_url(self):
        assert _extract_slug("git@github.com:owner/repo.git") == "owner/repo"

    def test_https_url(self):
        assert _extract_slug("https://github.com/Owner/Repo.git") == "owner/repo"

    def test_https_no_git_suffix(self):
        assert _extract_slug("https://github.com/Owner/Repo") == "owner/repo"

    def test_non_github_url(self):
        assert _extract_slug("git@gitlab.com:owner/repo.git") is None

    def test_empty_string(self):
        assert _extract_slug("") is None


class TestBuildNewUrl:
    def test_ssh_preserved(self):
        old = "git@github.com:oldowner/oldrepo.git"
        result = _build_new_url(old, "newowner", "newrepo")
        assert result == "git@github.com:newowner/newrepo.git"

    def test_https_with_git_suffix(self):
        old = "https://github.com/oldowner/oldrepo.git"
        result = _build_new_url(old, "newowner", "newrepo")
        assert result == "https://github.com/newowner/newrepo.git"

    def test_https_without_git_suffix(self):
        old = "https://github.com/oldowner/oldrepo"
        result = _build_new_url(old, "newowner", "newrepo")
        assert result == "https://github.com/newowner/newrepo"


class TestQueryCanonicalName:
    @patch("app.github.api")
    def test_returns_canonical_name(self, mock_api):
        mock_api.return_value = '"NewOwner/NewRepo"'
        result = _query_canonical_name("oldowner/oldrepo")
        assert result == "NewOwner/NewRepo"
        mock_api.assert_called_once_with("repos/oldowner/oldrepo", jq=".full_name")

    @patch("app.github.api")
    def test_returns_none_on_api_error(self, mock_api):
        mock_api.side_effect = RuntimeError("not found")
        result = _query_canonical_name("oldowner/oldrepo")
        assert result is None

    @patch("app.github.api")
    def test_returns_none_on_empty_response(self, mock_api):
        mock_api.return_value = ""
        result = _query_canonical_name("oldowner/oldrepo")
        assert result is None


class TestDetectAndFixRenamedRemotes:
    def _init_repo(self, tmp_path, remote_url="git@github.com:owner/repo.git"):
        """Create a minimal git repo with an origin remote."""
        repo = tmp_path / "myrepo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=repo, capture_output=True, check=True,
        )
        return repo

    @patch("app.remote_rename_detector._query_canonical_name")
    def test_no_rename_no_changes(self, mock_query, tmp_path):
        repo = self._init_repo(tmp_path)
        mock_query.return_value = "owner/repo"

        msgs = detect_and_fix_renamed_remotes(
            [("myrepo", str(repo))], str(tmp_path)
        )
        assert not any("Rename" in m for m in msgs)

    @patch("app.remote_rename_detector._update_projects_config")
    @patch("app.remote_rename_detector._query_canonical_name")
    def test_rename_updates_git_remote(self, mock_query, mock_config, tmp_path):
        repo = self._init_repo(tmp_path)
        mock_query.return_value = "newowner/newrepo"

        msgs = detect_and_fix_renamed_remotes(
            [("myrepo", str(repo))], str(tmp_path)
        )

        assert any("Rename detected" in m for m in msgs)
        assert any("Updated .git/config" in m for m in msgs)

        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo, capture_output=True, text=True,
        )
        assert "newowner/newrepo" in result.stdout

    @patch("app.remote_rename_detector._update_projects_config")
    @patch("app.remote_rename_detector._query_canonical_name")
    def test_rename_preserves_ssh_format(self, mock_query, mock_config, tmp_path):
        repo = self._init_repo(tmp_path, "git@github.com:old/name.git")
        mock_query.return_value = "new/name"

        detect_and_fix_renamed_remotes([("myrepo", str(repo))], str(tmp_path))

        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "git@github.com:new/name.git"

    @patch("app.remote_rename_detector._update_projects_config")
    @patch("app.remote_rename_detector._query_canonical_name")
    def test_rename_preserves_https_format(self, mock_query, mock_config, tmp_path):
        repo = self._init_repo(tmp_path, "https://github.com/old/name.git")
        mock_query.return_value = "new/name"

        detect_and_fix_renamed_remotes([("myrepo", str(repo))], str(tmp_path))

        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "https://github.com/new/name.git"

    @patch("app.remote_rename_detector._query_canonical_name")
    def test_skips_non_git_directories(self, mock_query, tmp_path):
        plain_dir = tmp_path / "notgit"
        plain_dir.mkdir()

        msgs = detect_and_fix_renamed_remotes(
            [("notgit", str(plain_dir))], str(tmp_path)
        )
        assert msgs == []
        mock_query.assert_not_called()

    @patch("app.remote_rename_detector._query_canonical_name")
    def test_skips_missing_directories(self, mock_query, tmp_path):
        msgs = detect_and_fix_renamed_remotes(
            [("gone", str(tmp_path / "nonexistent"))], str(tmp_path)
        )
        assert msgs == []
        mock_query.assert_not_called()

    @patch("app.remote_rename_detector._query_canonical_name")
    def test_api_failure_skips_gracefully(self, mock_query, tmp_path):
        repo = self._init_repo(tmp_path)
        mock_query.return_value = None

        msgs = detect_and_fix_renamed_remotes(
            [("myrepo", str(repo))], str(tmp_path)
        )
        assert msgs == []

    @patch("app.remote_rename_detector._update_projects_config")
    @patch("app.remote_rename_detector._query_canonical_name")
    def test_calls_update_projects_config(self, mock_query, mock_config, tmp_path):
        repo = self._init_repo(tmp_path)
        mock_query.return_value = "newowner/newrepo"

        detect_and_fix_renamed_remotes([("myrepo", str(repo))], str(tmp_path))

        mock_config.assert_called_once_with(
            str(tmp_path), {"myrepo": "newowner/newrepo"}
        )

    @patch("app.remote_rename_detector._update_projects_config")
    @patch("app.remote_rename_detector._query_canonical_name")
    def test_multiple_projects_partial_rename(self, mock_query, mock_config, tmp_path):
        (tmp_path / "a").mkdir(parents=True, exist_ok=True)
        (tmp_path / "b").mkdir(parents=True, exist_ok=True)
        repo_a = self._init_repo(tmp_path / "a", "git@github.com:owner/a.git")
        repo_b = self._init_repo(tmp_path / "b", "git@github.com:owner/b.git")

        mock_query.side_effect = lambda slug: (
            "owner/a" if slug == "owner/a" else "newowner/b-renamed"
        )

        msgs = detect_and_fix_renamed_remotes(
            [("proj-a", str(repo_a)), ("proj-b", str(repo_b))],
            str(tmp_path),
        )

        assert any("proj-b" in m and "Rename" in m for m in msgs)
        assert not any("proj-a" in m and "Rename" in m for m in msgs)
        mock_config.assert_called_once_with(
            str(tmp_path), {"proj-b": "newowner/b-renamed"}
        )
