"""Tests for commit_tracker.py — project HEAD tracking across startups."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.commit_tracker import (
    MAX_LOG_LINES,
    TRACKER_FILE,
    _get_head,
    _get_log,
    _load_state,
    _save_state,
    record_and_report,
)


# --- Persistence ---


class TestStatePersistence:
    def test_load_missing_file(self, tmp_path):
        assert _load_state(str(tmp_path)) == {}

    def test_load_corrupt_json(self, tmp_path):
        (tmp_path / TRACKER_FILE).write_text("not json")
        assert _load_state(str(tmp_path)) == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        data = {"my-toolkit": "abc123", "other": "def456"}
        _save_state(str(tmp_path), data)
        loaded = _load_state(str(tmp_path))
        assert loaded == data

    def test_save_overwrites(self, tmp_path):
        _save_state(str(tmp_path), {"old": "aaa"})
        _save_state(str(tmp_path), {"new": "bbb"})
        loaded = _load_state(str(tmp_path))
        assert loaded == {"new": "bbb"}


# --- _get_head ---


class TestGetHead:
    def test_returns_sha(self):
        with patch("app.commit_tracker.run_git", return_value=(0, "abc123def\n", "")):
            assert _get_head("/proj") == "abc123def"

    def test_returns_empty_on_failure(self):
        with patch("app.commit_tracker.run_git", return_value=(1, "", "fatal")):
            assert _get_head("/proj") == ""


# --- _get_log ---


class TestGetLog:
    def test_returns_lines_and_count(self):
        log_output = "\n".join(f"abc{i} commit {i}" for i in range(5))
        with patch("app.commit_tracker.run_git", return_value=(0, log_output, "")):
            lines, total = _get_log("/proj", "old_sha", limit=3)
            assert len(lines) == 3
            assert total == 5

    def test_returns_all_when_under_limit(self):
        log_output = "abc1 commit 1\nabc2 commit 2"
        with patch("app.commit_tracker.run_git", return_value=(0, log_output, "")):
            lines, total = _get_log("/proj", "old_sha")
            assert len(lines) == 2
            assert total == 2

    def test_returns_empty_on_failure(self):
        with patch("app.commit_tracker.run_git", return_value=(1, "", "fatal")):
            lines, total = _get_log("/proj", "old_sha")
            assert lines == []
            assert total == 0

    def test_returns_empty_on_no_output(self):
        with patch("app.commit_tracker.run_git", return_value=(0, "", "")):
            lines, total = _get_log("/proj", "old_sha")
            assert lines == []
            assert total == 0


# --- record_and_report ---


class TestRecordAndReport:
    def test_first_run_records_heads(self, tmp_path):
        """First run with no prior state: records HEADs, emits init message."""
        projects = [("my-toolkit", "/path/a"), ("other", "/path/b")]
        with patch("app.commit_tracker.run_git", return_value=(0, "abc123def456\n", "")):
            messages = record_and_report(projects, str(tmp_path))

        state = _load_state(str(tmp_path))
        assert state["my-toolkit"] == "abc123def456"
        assert state["other"] == "abc123def456"
        assert len(messages) == 1
        assert "Commit tracker initialized" in messages[0]

    def test_no_change_no_message(self, tmp_path):
        """No HEAD change between startups: no messages."""
        _save_state(str(tmp_path), {"proj": "abc123"})
        with patch("app.commit_tracker.run_git", return_value=(0, "abc123\n", "")):
            messages = record_and_report([("proj", "/p")], str(tmp_path))

        assert messages == []

    def test_head_changed_reports_commits(self, tmp_path):
        """HEAD moved: reports new commits."""
        _save_state(str(tmp_path), {"proj": "old_sha"})
        log_output = "new1 feat: add X\nnew2 fix: broken Y"

        def mock_git(*args, cwd=None, timeout=None):
            if args[0] == "rev-parse":
                return (0, "new_sha\n", "")
            if args[0] == "log":
                return (0, log_output, "")
            return (1, "", "")

        with patch("app.commit_tracker.run_git", side_effect=mock_git):
            messages = record_and_report([("proj", "/p")], str(tmp_path))

        assert len(messages) == 1
        assert "2 new commit(s)" in messages[0]
        assert "feat: add X" in messages[0]
        assert "fix: broken Y" in messages[0]

    def test_head_changed_truncates_long_log(self, tmp_path):
        """More commits than MAX_LOG_LINES: truncates with count."""
        _save_state(str(tmp_path), {"proj": "old_sha"})
        commit_lines = [f"sha{i} commit {i}" for i in range(20)]
        log_output = "\n".join(commit_lines)

        def mock_git(*args, cwd=None, timeout=None):
            if args[0] == "rev-parse":
                return (0, "new_sha\n", "")
            if args[0] == "log":
                return (0, log_output, "")
            return (1, "", "")

        with patch("app.commit_tracker.run_git", side_effect=mock_git):
            messages = record_and_report([("proj", "/p")], str(tmp_path))

        assert len(messages) == 1
        assert "20 new commit(s)" in messages[0]
        assert f"and {20 - MAX_LOG_LINES} more" in messages[0]

    def test_new_project_added(self, tmp_path):
        """New project appears after initial run: records without diff."""
        _save_state(str(tmp_path), {"existing": "abc"})

        def mock_git(*args, cwd=None, timeout=None):
            if args[0] == "rev-parse":
                if cwd == "/a":
                    return (0, "abc\n", "")
                return (0, "def456\n", "")
            return (1, "", "")

        with patch("app.commit_tracker.run_git", side_effect=mock_git):
            messages = record_and_report(
                [("existing", "/a"), ("brand_new", "/b")],
                str(tmp_path),
            )

        state = _load_state(str(tmp_path))
        assert state["brand_new"] == "def456"
        assert messages == []

    def test_force_push_detected(self, tmp_path):
        """HEAD changed but git log returns empty: force-push message."""
        _save_state(str(tmp_path), {"proj": "old_sha"})

        def mock_git(*args, cwd=None, timeout=None):
            if args[0] == "rev-parse":
                return (0, "new_sha\n", "")
            if args[0] == "log":
                return (0, "", "")
            return (1, "", "")

        with patch("app.commit_tracker.run_git", side_effect=mock_git):
            messages = record_and_report([("proj", "/p")], str(tmp_path))

        assert len(messages) == 1
        assert "force-push" in messages[0]

    def test_unreadable_project_skipped(self, tmp_path):
        """Project where git rev-parse fails: skipped gracefully."""
        _save_state(str(tmp_path), {"proj": "old_sha"})
        with patch("app.commit_tracker.run_git", return_value=(1, "", "fatal")):
            messages = record_and_report([("proj", "/p")], str(tmp_path))

        assert messages == []
        state = _load_state(str(tmp_path))
        assert "proj" not in state

    def test_multiple_projects_independent(self, tmp_path):
        """Each project tracked independently."""
        _save_state(str(tmp_path), {"a": "sha_a", "b": "sha_b"})

        def mock_git(*args, cwd=None, timeout=None):
            if args[0] == "rev-parse":
                if cwd == "/path/a":
                    return (0, "new_sha_a\n", "")
                return (0, "sha_b\n", "")
            if args[0] == "log":
                return (0, "c1 change in a\n", "")
            return (1, "", "")

        with patch("app.commit_tracker.run_git", side_effect=mock_git):
            messages = record_and_report(
                [("a", "/path/a"), ("b", "/path/b")],
                str(tmp_path),
            )

        assert len(messages) == 1
        assert "[a]" in messages[0]
