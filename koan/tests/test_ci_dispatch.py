"""Tests for CI dispatch auto-fix mission generation."""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")

from app.ci_dispatch import (
    check_and_dispatch_ci_fixes,
    compute_ci_fingerprint,
    fetch_failing_check_runs,
    fetch_koan_open_prs,
    fetch_check_run_log_snippet,
)


@pytest.fixture
def instance_dir(tmp_path):
    missions = tmp_path / "missions.md"
    missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    return str(tmp_path)


@pytest.fixture
def koan_root(tmp_path):
    root = tmp_path / "koan-root"
    root.mkdir()
    return str(root)


class TestComputeCiFingerprint:
    def test_deterministic(self):
        fp1 = compute_ci_fingerprint(42, "abc123", "test-job")
        fp2 = compute_ci_fingerprint(42, "abc123", "test-job")
        assert fp1 == fp2

    def test_different_sha_different_fingerprint(self):
        fp1 = compute_ci_fingerprint(42, "abc123", "test-job")
        fp2 = compute_ci_fingerprint(42, "def456", "test-job")
        assert fp1 != fp2

    def test_different_job_different_fingerprint(self):
        fp1 = compute_ci_fingerprint(42, "abc123", "build")
        fp2 = compute_ci_fingerprint(42, "abc123", "lint")
        assert fp1 != fp2

    def test_length(self):
        fp = compute_ci_fingerprint(1, "sha", "job")
        assert len(fp) == 16

    def test_unnamed_jobs_differ_by_run_id(self):
        fp1 = compute_ci_fingerprint(42, "abc123", "unknown", "111")
        fp2 = compute_ci_fingerprint(42, "abc123", "unknown", "222")
        assert fp1 != fp2

    def test_run_id_defaults_to_empty(self):
        fp1 = compute_ci_fingerprint(42, "abc123", "job")
        fp2 = compute_ci_fingerprint(42, "abc123", "job", "")
        assert fp1 == fp2


class TestFetchKoanOpenPrs:
    @patch("app.ci_dispatch.run_gh")
    @patch("app.ci_dispatch._get_branch_prefix", return_value="koan/")
    def test_filters_by_prefix(self, _prefix, mock_gh):
        mock_gh.return_value = json.dumps([
            {"number": 1, "title": "Fix", "headRefName": "koan/fix-x", "headRefOid": "abc"},
            {"number": 2, "title": "Other", "headRefName": "feature/y", "headRefOid": "def"},
        ])
        result = fetch_koan_open_prs("/project")
        assert len(result) == 1
        assert result[0]["number"] == 1

    @patch("app.ci_dispatch.run_gh", side_effect=RuntimeError("network"))
    def test_returns_empty_on_error(self, _gh):
        assert fetch_koan_open_prs("/project") == []

    @patch("app.ci_dispatch.run_gh", return_value="not json")
    def test_returns_empty_on_bad_json(self, _gh):
        assert fetch_koan_open_prs("/project") == []


class TestFetchFailingCheckRuns:
    @patch("app.ci_dispatch.run_gh")
    def test_returns_only_failures(self, mock_gh):
        mock_gh.return_value = "\n".join([
            json.dumps({"id": 1, "name": "build", "conclusion": "failure", "html_url": "u1"}),
            json.dumps({"id": 2, "name": "lint", "conclusion": "success", "html_url": "u2"}),
            json.dumps({"id": 3, "name": "test", "conclusion": "failure", "html_url": "u3"}),
        ])
        result = fetch_failing_check_runs("owner/repo", "abc123")
        assert len(result) == 2
        assert result[0]["name"] == "build"
        assert result[1]["name"] == "test"

    @patch("app.ci_dispatch.run_gh", side_effect=RuntimeError("err"))
    def test_returns_none_on_api_error(self, _gh):
        assert fetch_failing_check_runs("owner/repo", "sha") is None

    @patch("app.ci_dispatch.run_gh", return_value="")
    def test_returns_empty_on_no_runs(self, _gh):
        assert fetch_failing_check_runs("owner/repo", "sha") == []


class TestFetchCheckRunLogSnippet:
    @patch("app.ci_dispatch.run_gh")
    def test_extracts_summary_and_annotations(self, mock_gh):
        mock_gh.return_value = json.dumps({
            "summary": "Build failed",
            "text": "",
            "annotations": [
                {"message": "syntax error", "path": "src/main.py", "line": 42},
            ],
        })
        result = fetch_check_run_log_snippet("owner/repo", 1)
        assert "Build failed" in result
        assert "src/main.py:42" in result
        assert "syntax error" in result

    @patch("app.ci_dispatch.run_gh")
    def test_truncates_long_output(self, mock_gh):
        mock_gh.return_value = json.dumps({
            "summary": "x" * 5000,
            "text": "",
            "annotations": [],
        })
        result = fetch_check_run_log_snippet("owner/repo", 1, max_bytes=100)
        assert len(result) <= 100
        assert "truncated" in result

    @patch("app.ci_dispatch.run_gh", side_effect=RuntimeError("err"))
    def test_returns_empty_on_error(self, _gh):
        assert fetch_check_run_log_snippet("owner/repo", 1) == ""


class TestCheckAndDispatchCiFixes:
    @patch("app.ci_dispatch._get_ci_dispatch_config")
    def test_disabled_returns_zero(self, mock_config):
        mock_config.return_value = {
            "enabled": False, "cooldown_minutes": 30, "log_snippet_bytes": 4096,
        }
        assert check_and_dispatch_ci_fixes("/instance", "/root") == 0

    @patch("app.ci_dispatch.fetch_check_run_log_snippet", return_value="error log")
    @patch("app.ci_dispatch.fetch_failing_check_runs")
    @patch("app.ci_dispatch.fetch_koan_open_prs")
    @patch("app.ci_dispatch._resolve_full_repo", return_value="owner/repo")
    @patch("app.ci_dispatch._save_tracker")
    @patch("app.ci_dispatch._load_tracker", return_value={})
    @patch("app.ci_dispatch._get_ci_dispatch_config")
    def test_dispatches_mission_on_failure(
        self, mock_config, mock_load, mock_save, mock_repo,
        mock_prs, mock_fails, mock_log, instance_dir, koan_root,
    ):
        mock_config.return_value = {
            "enabled": True, "cooldown_minutes": 0, "log_snippet_bytes": 4096,
        }
        mock_prs.return_value = [
            {"number": 42, "title": "Fix", "headRefName": "koan/fix", "headRefOid": "sha123"},
        ]
        mock_fails.return_value = [
            {"id": 1, "name": "test-suite", "conclusion": "failure", "html_url": "u"},
        ]

        with patch("app.projects_config.load_projects_config") as mock_pc, \
             patch("app.projects_config.get_projects_from_config") as mock_gp:
            mock_pc.return_value = {}
            mock_gp.return_value = [("myproject", "/path/to/project")]

            count = check_and_dispatch_ci_fixes(instance_dir, koan_root)

        assert count == 1
        missions = Path(instance_dir) / "missions.md"
        content = missions.read_text()
        assert "Fix CI failure" in content
        assert "test-suite" in content
        assert "PR #42" in content

    @patch("app.ci_dispatch.fetch_failing_check_runs", return_value=[])
    @patch("app.ci_dispatch.fetch_koan_open_prs")
    @patch("app.ci_dispatch._resolve_full_repo", return_value="owner/repo")
    @patch("app.ci_dispatch._save_tracker")
    @patch("app.ci_dispatch._load_tracker", return_value={})
    @patch("app.ci_dispatch._get_ci_dispatch_config")
    def test_no_dispatch_when_ci_passes(
        self, mock_config, mock_load, mock_save, mock_repo,
        mock_prs, mock_fails, instance_dir, koan_root,
    ):
        mock_config.return_value = {
            "enabled": True, "cooldown_minutes": 0, "log_snippet_bytes": 4096,
        }
        mock_prs.return_value = [
            {"number": 42, "title": "Fix", "headRefName": "koan/fix", "headRefOid": "sha123"},
        ]

        with patch("app.projects_config.load_projects_config") as mock_pc, \
             patch("app.projects_config.get_projects_from_config") as mock_gp:
            mock_pc.return_value = {}
            mock_gp.return_value = [("myproject", "/path/to/project")]

            count = check_and_dispatch_ci_fixes(instance_dir, koan_root)

        assert count == 0

    @patch("app.ci_dispatch.fetch_check_run_log_snippet", return_value="log")
    @patch("app.ci_dispatch.fetch_failing_check_runs")
    @patch("app.ci_dispatch.fetch_koan_open_prs")
    @patch("app.ci_dispatch._resolve_full_repo", return_value="owner/repo")
    @patch("app.ci_dispatch._save_tracker")
    @patch("app.ci_dispatch._load_tracker")
    @patch("app.ci_dispatch._get_ci_dispatch_config")
    def test_dedup_prevents_double_dispatch(
        self, mock_config, mock_load, mock_save, mock_repo,
        mock_prs, mock_fails, mock_log, instance_dir, koan_root,
    ):
        mock_config.return_value = {
            "enabled": True, "cooldown_minutes": 0, "log_snippet_bytes": 4096,
        }
        mock_prs.return_value = [
            {"number": 42, "title": "Fix", "headRefName": "koan/fix", "headRefOid": "sha123"},
        ]
        mock_fails.return_value = [
            {"id": 1, "name": "test-suite", "conclusion": "failure", "html_url": "u"},
        ]

        fingerprint = compute_ci_fingerprint(42, "sha123", "test-suite", "1")
        mock_load.return_value = {f"owner/repo#{fingerprint}": fingerprint}

        with patch("app.projects_config.load_projects_config") as mock_pc, \
             patch("app.projects_config.get_projects_from_config") as mock_gp:
            mock_pc.return_value = {}
            mock_gp.return_value = [("myproject", "/path/to/project")]

            count = check_and_dispatch_ci_fixes(instance_dir, koan_root)

        assert count == 0

    @patch("app.ci_dispatch.fetch_koan_open_prs")
    @patch("app.ci_dispatch._resolve_full_repo", return_value="owner/repo")
    @patch("app.ci_dispatch._save_tracker")
    @patch("app.ci_dispatch._load_tracker")
    @patch("app.ci_dispatch._get_ci_dispatch_config")
    def test_cooldown_skips_project(
        self, mock_config, mock_load, mock_save, mock_repo,
        mock_prs, instance_dir, koan_root,
    ):
        import time
        mock_config.return_value = {
            "enabled": True, "cooldown_minutes": 60, "log_snippet_bytes": 4096,
        }
        mock_load.return_value = {"cooldown:myproject": time.time()}

        with patch("app.projects_config.load_projects_config") as mock_pc, \
             patch("app.projects_config.get_projects_from_config") as mock_gp:
            mock_pc.return_value = {}
            mock_gp.return_value = [("myproject", "/path/to/project")]

            check_and_dispatch_ci_fixes(instance_dir, koan_root)

        mock_prs.assert_not_called()

    @patch("app.ci_dispatch.fetch_failing_check_runs", return_value=None)
    @patch("app.ci_dispatch.fetch_koan_open_prs")
    @patch("app.ci_dispatch._resolve_full_repo", return_value="owner/repo")
    @patch("app.ci_dispatch._save_tracker")
    @patch("app.ci_dispatch._load_tracker", return_value={})
    @patch("app.ci_dispatch._get_ci_dispatch_config")
    def test_api_failure_skips_cooldown_update(
        self, mock_config, mock_load, mock_save, mock_repo,
        mock_prs, mock_fails, instance_dir, koan_root,
    ):
        mock_config.return_value = {
            "enabled": True, "cooldown_minutes": 0, "log_snippet_bytes": 4096,
        }
        mock_prs.return_value = [
            {"number": 42, "title": "Fix", "headRefName": "koan/fix", "headRefOid": "sha123"},
        ]

        with patch("app.projects_config.load_projects_config") as mock_pc, \
             patch("app.projects_config.get_projects_from_config") as mock_gp:
            mock_pc.return_value = {}
            mock_gp.return_value = [("myproject", "/path/to/project")]

            count = check_and_dispatch_ci_fixes(instance_dir, koan_root)

        assert count == 0
        mock_save.assert_not_called()

    @patch("app.ci_dispatch._get_ci_dispatch_config")
    def test_handles_missing_projects_config(self, mock_config):
        mock_config.return_value = {
            "enabled": True, "cooldown_minutes": 30, "log_snippet_bytes": 4096,
        }

        with patch("app.projects_config.load_projects_config", side_effect=OSError("no file")):
            count = check_and_dispatch_ci_fixes("/instance", "/root")

        assert count == 0
