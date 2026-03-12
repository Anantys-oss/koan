"""Tests for the /done skill handler."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Ensure the koan package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.core.done.handler import (
    handle,
    _parse_args,
    _fetch_merged_prs,
    _format_output,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def instance_dir(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    return inst


@pytest.fixture
def koan_root(tmp_path):
    return tmp_path


def _make_ctx(koan_root, instance_dir, args=""):
    return SimpleNamespace(
        koan_root=koan_root,
        instance_dir=instance_dir,
        command_name="done",
        args=args,
        send_message=None,
        handle_chat=None,
    )


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_empty_args(self):
        project, hours = _parse_args("")
        assert project == ""
        assert hours == 24

    def test_project_only(self):
        project, hours = _parse_args("koan")
        assert project == "koan"
        assert hours == 24

    def test_hours_only(self):
        project, hours = _parse_args("--hours=48")
        assert project == ""
        assert hours == 48

    def test_project_and_hours(self):
        project, hours = _parse_args("myproject --hours=12")
        assert project == "myproject"
        assert hours == 12

    def test_hours_capped_at_168(self):
        _, hours = _parse_args("--hours=999")
        assert hours == 168

    def test_hours_minimum_1(self):
        _, hours = _parse_args("--hours=0")
        assert hours == 1


# ---------------------------------------------------------------------------
# _fetch_merged_prs
# ---------------------------------------------------------------------------

class TestFetchMergedPrs:
    def test_returns_prs_within_window(self):
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        merged_at = datetime.now(timezone.utc) - timedelta(hours=2)
        merged_at_str = merged_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        pr_data = [
            {
                "number": 42,
                "title": "feat: add thing",
                "url": "https://github.com/org/repo/pull/42",
                "mergedAt": merged_at_str,
            }
        ]

        with patch("app.github.run_gh", return_value=json.dumps(pr_data)):
            result = _fetch_merged_prs("org/repo", "testuser", since)

        assert len(result) == 1
        assert result[0]["number"] == 42
        assert result[0]["title"] == "feat: add thing"

    def test_filters_old_prs(self):
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        old_merged = datetime.now(timezone.utc) - timedelta(hours=48)
        old_str = old_merged.strftime("%Y-%m-%dT%H:%M:%SZ")

        pr_data = [
            {
                "number": 10,
                "title": "old PR",
                "url": "https://github.com/org/repo/pull/10",
                "mergedAt": old_str,
            }
        ]

        with patch("app.github.run_gh", return_value=json.dumps(pr_data)):
            result = _fetch_merged_prs("org/repo", "testuser", since)

        assert len(result) == 0

    def test_handles_runtime_error(self):
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        with patch("app.github.run_gh", side_effect=RuntimeError("gh failed")):
            result = _fetch_merged_prs("org/repo", "testuser", since)

        assert result == []

    def test_handles_empty_output(self):
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        with patch("app.github.run_gh", return_value=""):
            result = _fetch_merged_prs("org/repo", "testuser", since)

        assert result == []


# ---------------------------------------------------------------------------
# _format_output
# ---------------------------------------------------------------------------

class TestFormatOutput:
    def test_single_project(self):
        prs = [
            {"number": 1, "title": "feat: X", "url": "...", "merged_at": "", "project": "koan"},
            {"number": 2, "title": "fix: Y", "url": "...", "merged_at": "", "project": "koan"},
        ]
        output = _format_output(prs, 24)
        assert "Merged PRs (last 24h): 2" in output
        assert "#1 feat: X" in output
        assert "#2 fix: Y" in output

    def test_multi_project(self):
        prs = [
            {"number": 1, "title": "A", "url": "...", "merged_at": "", "project": "alpha"},
            {"number": 2, "title": "B", "url": "...", "merged_at": "", "project": "beta"},
        ]
        output = _format_output(prs, 24)
        assert "alpha:" in output
        assert "beta:" in output

    def test_long_title_truncated(self):
        long_title = "x" * 80
        prs = [
            {"number": 1, "title": long_title, "url": "...", "merged_at": "", "project": "p"},
        ]
        output = _format_output(prs, 24)
        assert "..." in output
        # Truncated title should be <= 70 chars
        for line in output.splitlines():
            if "#1" in line:
                title_part = line.strip().split(" ", 1)[1]
                assert len(title_part) <= 70

    def test_custom_hours(self):
        prs = [
            {"number": 1, "title": "A", "url": "...", "merged_at": "", "project": "p"},
        ]
        output = _format_output(prs, 48)
        assert "last 48h" in output


# ---------------------------------------------------------------------------
# handle (integration)
# ---------------------------------------------------------------------------

class TestHandle:
    def test_no_github_user(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir)
        with patch("app.github.get_gh_username", return_value=""), \
             patch("app.utils.get_known_projects", return_value=[("p", "/p")]):
            result = handle(ctx)
        assert "Cannot determine GitHub username" in result

    def test_no_projects(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir)
        with patch("app.github.get_gh_username", return_value="user"), \
             patch("app.utils.get_known_projects", return_value=[]):
            result = handle(ctx)
        assert "No projects configured" in result

    def test_project_not_found(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="nonexistent")
        with patch("app.github.get_gh_username", return_value="user"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/koan")]):
            result = handle(ctx)
        assert "not found" in result

    def test_no_merged_prs(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir)
        with patch("app.github.get_gh_username", return_value="user"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/koan")]), \
             patch("app.utils.get_github_remote", return_value="org/koan"), \
             patch("app.github.run_gh", return_value="[]"):
            result = handle(ctx)
        assert "No merged PRs" in result

    def test_returns_merged_prs(self, koan_root, instance_dir):
        merged_at = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pr_data = json.dumps([{
            "number": 99,
            "title": "feat: awesome",
            "url": "https://github.com/org/koan/pull/99",
            "mergedAt": merged_at,
        }])

        ctx = _make_ctx(koan_root, instance_dir)
        with patch("app.github.get_gh_username", return_value="user"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/koan")]), \
             patch("app.utils.get_github_remote", return_value="org/koan"), \
             patch("app.github.run_gh", return_value=pr_data):
            result = handle(ctx)

        assert "#99" in result
        assert "feat: awesome" in result
        assert "Merged PRs" in result

    def test_filters_by_project(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="koan")
        with patch("app.github.get_gh_username", return_value="user"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/k"), ("other", "/o")]), \
             patch("app.utils.get_github_remote", return_value="org/koan"), \
             patch("app.github.run_gh", return_value="[]"):
            result = handle(ctx)

        assert "No merged PRs" in result

    def test_no_repo_slug_skips_project(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir)
        with patch("app.github.get_gh_username", return_value="user"), \
             patch("app.utils.get_known_projects", return_value=[("local", "/local")]), \
             patch("app.utils.get_github_remote", return_value=None):
            result = handle(ctx)
        assert "No merged PRs" in result
