"""Tests for commit tracking in auto_update.py — Kōan self-commit tracking across startups."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.auto_update import (
    MAX_LOG_LINES,
    TRACKER_FILE,
    _get_koan_head,
    _get_commit_log,
    _load_commit_state,
    _save_commit_state,
    record_and_report,
)


# --- Persistence ---


class TestStatePersistence:
    def test_load_missing_file(self, tmp_path):
        assert _load_commit_state(str(tmp_path)) == {}

    def test_load_corrupt_json(self, tmp_path):
        (tmp_path / TRACKER_FILE).write_text("not json")
        assert _load_commit_state(str(tmp_path)) == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        data = {"koan": "abc123"}
        _save_commit_state(str(tmp_path), data)
        loaded = _load_commit_state(str(tmp_path))
        assert loaded == data

    def test_save_overwrites(self, tmp_path):
        _save_commit_state(str(tmp_path), {"koan": "aaa"})
        _save_commit_state(str(tmp_path), {"koan": "bbb"})
        loaded = _load_commit_state(str(tmp_path))
        assert loaded == {"koan": "bbb"}


# --- _get_head ---


class TestGetHead:
    def test_returns_sha(self):
        with patch("app.auto_update._run_git_utils", return_value=(0, "abc123def\n", "")):
            assert _get_koan_head("/koan") == "abc123def"

    def test_returns_empty_on_failure(self):
        with patch("app.auto_update._run_git_utils", return_value=(1, "", "fatal")):
            assert _get_koan_head("/koan") == ""


# --- _get_log ---


class TestGetLog:
    def test_returns_lines_and_count(self):
        log_output = "\n".join(f"abc{i} commit {i}" for i in range(5))
        with patch("app.auto_update._run_git_utils", return_value=(0, log_output, "")):
            lines, total = _get_commit_log("/koan", "old_sha", limit=3)
            assert len(lines) == 3
            assert total == 5

    def test_returns_all_when_under_limit(self):
        log_output = "abc1 commit 1\nabc2 commit 2"
        with patch("app.auto_update._run_git_utils", return_value=(0, log_output, "")):
            lines, total = _get_commit_log("/koan", "old_sha")
            assert len(lines) == 2
            assert total == 2

    def test_returns_empty_on_failure(self):
        with patch("app.auto_update._run_git_utils", return_value=(1, "", "fatal")):
            lines, total = _get_commit_log("/koan", "old_sha")
            assert lines == []
            assert total == 0

    def test_returns_empty_on_no_output(self):
        with patch("app.auto_update._run_git_utils", return_value=(0, "", "")):
            lines, total = _get_commit_log("/koan", "old_sha")
            assert lines == []
            assert total == 0


# --- record_and_report ---


class TestRecordAndReport:
    def test_first_run_records_head_returns_none(self, tmp_path):
        """First run with no prior state: records HEAD, no message."""
        with patch("app.auto_update._run_git_utils", return_value=(0, "abc123def456\n", "")):
            msg = record_and_report("/koan", str(tmp_path))

        state = _load_commit_state(str(tmp_path))
        assert state["koan"] == "abc123def456"
        assert msg is None

    def test_no_change_returns_none(self, tmp_path):
        """Same HEAD as last startup: no message."""
        _save_commit_state(str(tmp_path), {"koan": "abc123"})
        with patch("app.auto_update._run_git_utils", return_value=(0, "abc123\n", "")):
            msg = record_and_report("/koan", str(tmp_path))
        assert msg is None

    def test_changed_head_reports_commits(self, tmp_path):
        """HEAD changed: reports new commits."""
        _save_commit_state(str(tmp_path), {"koan": "oldsha111"})

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return (0, "newsha222\n", "")
            if "log" in args:
                return (0, "newsha22 feat: new feature\nabc1234 fix: bug fix\n", "")
            return (1, "", "")

        with patch("app.auto_update._run_git_utils", side_effect=mock_run_git):
            msg = record_and_report("/koan", str(tmp_path))

        assert msg is not None
        assert "2 new commit(s)" in msg
        assert "feat: new feature" in msg
        assert "fix: bug fix" in msg
        state = _load_commit_state(str(tmp_path))
        assert state["koan"] == "newsha222"

    def test_changed_head_truncates_long_log(self, tmp_path):
        """Long log is capped at MAX_LOG_LINES with '… and N more'."""
        _save_commit_state(str(tmp_path), {"koan": "oldsha"})
        log_lines = "\n".join(f"sha{i:04d} commit {i}" for i in range(20))

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return (0, "newsha\n", "")
            if "log" in args:
                return (0, log_lines, "")
            return (1, "", "")

        with patch("app.auto_update._run_git_utils", side_effect=mock_run_git):
            msg = record_and_report("/koan", str(tmp_path))

        assert f"… and {20 - MAX_LOG_LINES} more" in msg

    def test_head_unreadable_returns_none(self, tmp_path):
        """If HEAD can't be read, returns None and doesn't update state."""
        _save_commit_state(str(tmp_path), {"koan": "oldsha"})
        with patch("app.auto_update._run_git_utils", return_value=(1, "", "fatal")):
            msg = record_and_report("/koan", str(tmp_path))
        assert msg is None
        assert _load_commit_state(str(tmp_path))["koan"] == "oldsha"

    def test_nonlinear_history_reports_sha_change(self, tmp_path):
        """Force-push: HEAD changed but no linear log available."""
        _save_commit_state(str(tmp_path), {"koan": "oldsha111"})

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return (0, "newsha222\n", "")
            if "log" in args:
                return (0, "", "")
            return (1, "", "")

        with patch("app.auto_update._run_git_utils", side_effect=mock_run_git):
            msg = record_and_report("/koan", str(tmp_path))

        assert msg is not None
        assert "non-linear" in msg
        assert "oldsha111"[:10] in msg

    def test_preserves_other_keys_in_state(self, tmp_path):
        """Other keys in the tracker file are preserved."""
        _save_commit_state(str(tmp_path), {"koan": "old", "other_key": "keep"})
        with patch("app.auto_update._run_git_utils", return_value=(0, "new\n", "")):
            record_and_report("/koan", str(tmp_path))
        state = _load_commit_state(str(tmp_path))
        assert state["other_key"] == "keep"
        assert state["koan"] == "new"
