"""Tests for review_comment_dispatch.py — auto-dispatch missions on new review comments."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")


class TestComputeCommentFingerprint:
    """Fingerprint is a stable hash of sorted comment IDs."""

    def test_empty_comments(self):
        from app.review_comment_dispatch import compute_comment_fingerprint

        fp = compute_comment_fingerprint([])
        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_deterministic(self):
        from app.review_comment_dispatch import compute_comment_fingerprint

        comments = [{"id": 1}, {"id": 2}, {"id": 3}]
        assert compute_comment_fingerprint(comments) == compute_comment_fingerprint(comments)

    def test_order_independent(self):
        from app.review_comment_dispatch import compute_comment_fingerprint

        a = [{"id": 1}, {"id": 2}, {"id": 3}]
        b = [{"id": 3}, {"id": 1}, {"id": 2}]
        assert compute_comment_fingerprint(a) == compute_comment_fingerprint(b)

    def test_changes_on_new_comment(self):
        from app.review_comment_dispatch import compute_comment_fingerprint

        before = [{"id": 1}, {"id": 2}]
        after = [{"id": 1}, {"id": 2}, {"id": 3}]
        assert compute_comment_fingerprint(before) != compute_comment_fingerprint(after)


class TestFormatCommentSummary:
    """Summary text for mission descriptions."""

    def test_empty(self):
        from app.review_comment_dispatch import _format_comment_summary

        assert _format_comment_summary([]) == ""

    def test_single_user_single_file(self):
        from app.review_comment_dispatch import _format_comment_summary

        comments = [{"user": "alice", "path": "src/foo.py", "body": "fix this"}]
        summary = _format_comment_summary(comments)
        assert "alice" in summary
        assert "src/foo.py" in summary

    def test_multiple_users(self):
        from app.review_comment_dispatch import _format_comment_summary

        comments = [
            {"user": "alice", "path": "a.py", "body": "x"},
            {"user": "bob", "path": "b.py", "body": "y"},
        ]
        summary = _format_comment_summary(comments)
        assert "alice" in summary
        assert "bob" in summary

    def test_many_paths_truncated(self):
        from app.review_comment_dispatch import _format_comment_summary

        comments = [
            {"user": "alice", "path": f"file{i}.py", "body": "x"}
            for i in range(10)
        ]
        summary = _format_comment_summary(comments)
        assert "+7 more" in summary

    def test_max_len(self):
        from app.review_comment_dispatch import _format_comment_summary

        comments = [{"user": "alice", "path": f"very/long/path/file{i}.py", "body": "x"} for i in range(5)]
        summary = _format_comment_summary(comments, max_len=50)
        assert len(summary) <= 50


class TestTrackerPersistence:
    """Tracker file read/write/roundtrip."""

    def test_load_missing_file(self, tmp_path):
        from app.review_comment_dispatch import _load_tracker

        assert _load_tracker(str(tmp_path)) == {}

    def test_load_corrupt_file(self, tmp_path):
        from app.review_comment_dispatch import _load_tracker

        (tmp_path / ".review-dispatch-tracker.json").write_text("not json")
        assert _load_tracker(str(tmp_path)) == {}

    def test_roundtrip(self, tmp_path):
        from app.review_comment_dispatch import _load_tracker, _save_tracker

        data = {"key": "value", "num": 42}
        _save_tracker(str(tmp_path), data)
        loaded = _load_tracker(str(tmp_path))
        assert loaded == data


class TestFetchKoanOpenPrs:
    """fetch_koan_open_prs filters by branch prefix."""

    @patch("app.review_comment_dispatch._get_branch_prefix", return_value="koan/")
    @patch("app.review_comment_dispatch.run_gh")
    def test_filters_by_prefix(self, mock_gh, _):
        from app.review_comment_dispatch import fetch_koan_open_prs

        mock_gh.return_value = json.dumps([
            {"number": 1, "title": "PR 1", "headRefName": "koan/fix-bug", "updatedAt": "2026-01-01"},
            {"number": 2, "title": "PR 2", "headRefName": "main", "updatedAt": "2026-01-01"},
            {"number": 3, "title": "PR 3", "headRefName": "koan/add-feature", "updatedAt": "2026-01-01"},
        ])
        prs = fetch_koan_open_prs("/project")
        assert len(prs) == 2
        assert {p["number"] for p in prs} == {1, 3}

    @patch("app.review_comment_dispatch._get_branch_prefix", return_value="koan/")
    @patch("app.review_comment_dispatch.run_gh", side_effect=RuntimeError("gh failed"))
    def test_handles_gh_failure(self, _, __):
        from app.review_comment_dispatch import fetch_koan_open_prs

        assert fetch_koan_open_prs("/project") == []


class TestFetchUnresolvedReviewComments:
    """fetch_unresolved_review_comments filters bot comments."""

    @patch("app.review_comment_dispatch.run_gh")
    def test_filters_bot_comments(self, mock_gh):
        from app.review_comment_dispatch import fetch_unresolved_review_comments

        mock_gh.return_value = "\n".join([
            json.dumps({"id": 1, "user": "alice", "body": "fix this", "path": "a.py", "user_type": "User"}),
            json.dumps({"id": 2, "user": "koan-bot", "body": "auto-reply", "path": "b.py", "user_type": "Bot"}),
            json.dumps({"id": 3, "user": "bob", "body": "looks good", "path": "c.py", "user_type": "User"}),
        ])
        comments = fetch_unresolved_review_comments("owner/repo", 1, "koan-bot")
        assert len(comments) == 2
        assert {c["user"] for c in comments} == {"alice", "bob"}

    @patch("app.review_comment_dispatch.run_gh")
    def test_filters_by_username(self, mock_gh):
        from app.review_comment_dispatch import fetch_unresolved_review_comments

        mock_gh.return_value = json.dumps(
            {"id": 1, "user": "MyBot", "body": "hello", "path": "a.py", "user_type": "User"}
        )
        comments = fetch_unresolved_review_comments("owner/repo", 1, "mybot")
        assert len(comments) == 0

    @patch("app.review_comment_dispatch.run_gh", side_effect=RuntimeError("gh failed"))
    def test_handles_failure(self, _):
        from app.review_comment_dispatch import fetch_unresolved_review_comments

        assert fetch_unresolved_review_comments("owner/repo", 1) == []


class TestFetchReviewBodyComments:
    """fetch_review_body_comments filters approvals and empty bodies."""

    @patch("app.review_comment_dispatch.run_gh")
    def test_filters_approvals_and_empty(self, mock_gh):
        from app.review_comment_dispatch import fetch_review_body_comments

        mock_gh.return_value = "\n".join([
            json.dumps({"id": 10, "user": "alice", "body": "Please fix the error handling", "state": "CHANGES_REQUESTED", "user_type": "User"}),
            json.dumps({"id": 11, "user": "bob", "body": "", "state": "APPROVED", "user_type": "User"}),
            json.dumps({"id": 12, "user": "carol", "body": "Nice work!", "state": "COMMENTED", "user_type": "User"}),
            json.dumps({"id": 13, "user": "bot", "body": "CI passed", "state": "COMMENTED", "user_type": "Bot"}),
        ])
        comments = fetch_review_body_comments("owner/repo", 1)
        assert len(comments) == 2
        assert {c["user"] for c in comments} == {"alice", "carol"}


class TestCheckAndDispatch:
    """Integration test for the main dispatch orchestrator."""

    @pytest.fixture()
    def instance_dir(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
        return str(tmp_path)

    @patch("app.review_comment_dispatch._get_review_dispatch_config")
    def test_disabled_config_returns_zero(self, mock_config, instance_dir):
        from app.review_comment_dispatch import check_and_dispatch_review_comments

        mock_config.return_value = {"enabled": False, "cooldown_minutes": 30}
        assert check_and_dispatch_review_comments(instance_dir, "/koan") == 0

    @patch("app.review_comment_dispatch._get_review_dispatch_config")
    @patch("app.projects_config.load_projects_config")
    @patch("app.projects_config.get_projects_from_config")
    @patch("app.review_comment_dispatch._resolve_full_repo")
    @patch("app.review_comment_dispatch.fetch_koan_open_prs")
    @patch("app.review_comment_dispatch.fetch_unresolved_review_comments")
    @patch("app.review_comment_dispatch.fetch_review_body_comments")
    @patch("app.review_comment_dispatch._get_bot_username", return_value="koan-bot")
    def test_dispatches_on_new_comments(
        self, _, mock_review_body, mock_inline, mock_prs, mock_repo,
        mock_projects, mock_projects_config, mock_config, instance_dir,
    ):
        from app.review_comment_dispatch import check_and_dispatch_review_comments

        mock_config.return_value = {"enabled": True, "cooldown_minutes": 0}
        mock_projects_config.return_value = {}
        mock_projects.return_value = [("myproject", "/projects/myproject")]
        mock_repo.return_value = "owner/myproject"
        mock_prs.return_value = [
            {"number": 42, "title": "feat: add widget", "headRefName": "koan/add-widget", "updatedAt": "2026-01-01"},
        ]
        mock_inline.return_value = [
            {"id": 100, "user": "alice", "body": "fix error handling", "path": "src/widget.py"},
        ]
        mock_review_body.return_value = []

        result = check_and_dispatch_review_comments(instance_dir, "/koan")
        assert result == 1

        missions_text = (Path(instance_dir) / "missions.md").read_text()
        assert "Address review comments on #42" in missions_text
        assert "[project:myproject]" in missions_text

    @patch("app.review_comment_dispatch._get_review_dispatch_config")
    @patch("app.projects_config.load_projects_config")
    @patch("app.projects_config.get_projects_from_config")
    @patch("app.review_comment_dispatch._resolve_full_repo")
    @patch("app.review_comment_dispatch.fetch_koan_open_prs")
    @patch("app.review_comment_dispatch.fetch_unresolved_review_comments")
    @patch("app.review_comment_dispatch.fetch_review_body_comments")
    @patch("app.review_comment_dispatch._get_bot_username", return_value="koan-bot")
    def test_skips_on_same_fingerprint(
        self, _, mock_review_body, mock_inline, mock_prs, mock_repo,
        mock_projects, mock_projects_config, mock_config, instance_dir,
    ):
        from app.review_comment_dispatch import (
            check_and_dispatch_review_comments,
            compute_comment_fingerprint,
            _save_tracker,
        )

        mock_config.return_value = {"enabled": True, "cooldown_minutes": 0}
        mock_projects_config.return_value = {}
        mock_projects.return_value = [("myproject", "/projects/myproject")]
        mock_repo.return_value = "owner/myproject"
        mock_prs.return_value = [
            {"number": 42, "title": "feat: add widget", "headRefName": "koan/add-widget", "updatedAt": "2026-01-01"},
        ]
        comments = [{"id": 100, "user": "alice", "body": "fix this", "path": "a.py"}]
        mock_inline.return_value = comments
        mock_review_body.return_value = []

        fp = compute_comment_fingerprint(comments)
        _save_tracker(instance_dir, {"owner/myproject#42": fp})

        result = check_and_dispatch_review_comments(instance_dir, "/koan")
        assert result == 0

    @patch("app.review_comment_dispatch._get_review_dispatch_config")
    @patch("app.projects_config.load_projects_config")
    @patch("app.projects_config.get_projects_from_config")
    @patch("app.review_comment_dispatch._resolve_full_repo")
    @patch("app.review_comment_dispatch.fetch_koan_open_prs")
    @patch("app.review_comment_dispatch.fetch_unresolved_review_comments")
    @patch("app.review_comment_dispatch.fetch_review_body_comments")
    @patch("app.review_comment_dispatch._get_bot_username", return_value="koan-bot")
    def test_dispatches_when_fingerprint_changes(
        self, _, mock_review_body, mock_inline, mock_prs, mock_repo,
        mock_projects, mock_projects_config, mock_config, instance_dir,
    ):
        from app.review_comment_dispatch import (
            check_and_dispatch_review_comments,
            _save_tracker,
        )

        mock_config.return_value = {"enabled": True, "cooldown_minutes": 0}
        mock_projects_config.return_value = {}
        mock_projects.return_value = [("myproject", "/projects/myproject")]
        mock_repo.return_value = "owner/myproject"
        mock_prs.return_value = [
            {"number": 42, "title": "feat: add widget", "headRefName": "koan/add-widget", "updatedAt": "2026-01-01"},
        ]
        mock_inline.return_value = [
            {"id": 100, "user": "alice", "body": "fix this", "path": "a.py"},
            {"id": 200, "user": "bob", "body": "also this", "path": "b.py"},
        ]
        mock_review_body.return_value = []

        _save_tracker(instance_dir, {"owner/myproject#42": "old-fingerprint"})

        result = check_and_dispatch_review_comments(instance_dir, "/koan")
        assert result == 1

    @patch("app.review_comment_dispatch._get_review_dispatch_config")
    @patch("app.projects_config.load_projects_config")
    @patch("app.projects_config.get_projects_from_config")
    @patch("app.review_comment_dispatch._resolve_full_repo")
    @patch("app.review_comment_dispatch.fetch_koan_open_prs")
    @patch("app.review_comment_dispatch._get_bot_username", return_value="koan-bot")
    def test_respects_cooldown(
        self, _, mock_prs, mock_repo, mock_projects,
        mock_projects_config, mock_config, instance_dir,
    ):
        from app.review_comment_dispatch import (
            check_and_dispatch_review_comments,
            _save_tracker,
        )
        import time

        mock_config.return_value = {"enabled": True, "cooldown_minutes": 60}
        mock_projects_config.return_value = {}
        mock_projects.return_value = [("myproject", "/projects/myproject")]

        _save_tracker(instance_dir, {"cooldown:myproject": time.time()})

        result = check_and_dispatch_review_comments(instance_dir, "/koan")
        assert result == 0
        mock_repo.assert_not_called()

    @patch("app.review_comment_dispatch._get_review_dispatch_config")
    @patch("app.projects_config.load_projects_config")
    @patch("app.projects_config.get_projects_from_config")
    @patch("app.review_comment_dispatch._resolve_full_repo")
    @patch("app.review_comment_dispatch.fetch_koan_open_prs")
    @patch("app.review_comment_dispatch.fetch_unresolved_review_comments")
    @patch("app.review_comment_dispatch.fetch_review_body_comments")
    @patch("app.review_comment_dispatch._get_bot_username", return_value="koan-bot")
    def test_no_comments_cleans_tracker(
        self, _, mock_review_body, mock_inline, mock_prs, mock_repo,
        mock_projects, mock_projects_config, mock_config, instance_dir,
    ):
        from app.review_comment_dispatch import (
            check_and_dispatch_review_comments,
            _save_tracker,
            _load_tracker,
        )

        mock_config.return_value = {"enabled": True, "cooldown_minutes": 0}
        mock_projects_config.return_value = {}
        mock_projects.return_value = [("myproject", "/projects/myproject")]
        mock_repo.return_value = "owner/myproject"
        mock_prs.return_value = [
            {"number": 42, "title": "feat: add widget", "headRefName": "koan/add-widget", "updatedAt": "2026-01-01"},
        ]
        mock_inline.return_value = []
        mock_review_body.return_value = []

        _save_tracker(instance_dir, {"owner/myproject#42": "old-fingerprint"})

        check_and_dispatch_review_comments(instance_dir, "/koan")
        tracker = _load_tracker(instance_dir)
        assert "owner/myproject#42" not in tracker

    @patch("app.review_comment_dispatch._get_review_dispatch_config")
    @patch("app.projects_config.load_projects_config")
    @patch("app.projects_config.get_projects_from_config")
    @patch("app.review_comment_dispatch._resolve_full_repo")
    @patch("app.review_comment_dispatch.fetch_koan_open_prs")
    @patch("app.review_comment_dispatch._get_bot_username", return_value="koan-bot")
    def test_no_prs_still_updates_cooldown(
        self, _, mock_prs, mock_repo, mock_projects,
        mock_projects_config, mock_config, instance_dir,
    ):
        from app.review_comment_dispatch import (
            check_and_dispatch_review_comments,
            _load_tracker,
        )

        mock_config.return_value = {"enabled": True, "cooldown_minutes": 0}
        mock_projects_config.return_value = {}
        mock_projects.return_value = [("myproject", "/projects/myproject")]
        mock_repo.return_value = "owner/myproject"
        mock_prs.return_value = []

        check_and_dispatch_review_comments(instance_dir, "/koan")
        tracker = _load_tracker(instance_dir)
        assert "cooldown:myproject" in tracker
