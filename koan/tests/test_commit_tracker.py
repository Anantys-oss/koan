"""Tests for commit_tracker.py — koan's own HEAD tracking across startups."""

from unittest.mock import patch

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
        data = {"head": "abc123"}
        _save_state(str(tmp_path), data)
        assert _load_state(str(tmp_path)) == data

    def test_save_overwrites(self, tmp_path):
        _save_state(str(tmp_path), {"head": "aaa"})
        _save_state(str(tmp_path), {"head": "bbb"})
        assert _load_state(str(tmp_path)) == {"head": "bbb"}


# --- _get_head ---


class TestGetHead:
    def test_returns_sha(self):
        with patch("app.commit_tracker.run_git", return_value=(0, "abc123def\n", "")):
            assert _get_head("/repo") == "abc123def"

    def test_returns_empty_on_failure(self):
        with patch("app.commit_tracker.run_git", return_value=(1, "", "fatal")):
            assert _get_head("/repo") == ""


# --- _get_log ---


class TestGetLog:
    def test_returns_lines_and_count(self):
        log_output = "\n".join(f"abc{i} commit {i}" for i in range(5))
        with patch("app.commit_tracker.run_git", return_value=(0, log_output, "")):
            lines, total = _get_log("/repo", "old_sha", limit=3)
            assert len(lines) == 3
            assert total == 5

    def test_returns_all_when_under_limit(self):
        log_output = "abc1 commit 1\nabc2 commit 2"
        with patch("app.commit_tracker.run_git", return_value=(0, log_output, "")):
            lines, total = _get_log("/repo", "old_sha")
            assert len(lines) == 2
            assert total == 2

    def test_returns_empty_on_failure(self):
        with patch("app.commit_tracker.run_git", return_value=(1, "", "fatal")):
            lines, total = _get_log("/repo", "old_sha")
            assert lines == []
            assert total == 0

    def test_returns_empty_on_no_output(self):
        with patch("app.commit_tracker.run_git", return_value=(0, "", "")):
            lines, total = _get_log("/repo", "old_sha")
            assert lines == []
            assert total == 0


# --- record_and_report ---


class TestRecordAndReport:
    def test_first_run_records_head_no_message(self, tmp_path):
        """First run: records HEAD, returns None (nothing to compare)."""
        with patch("app.commit_tracker.run_git", return_value=(0, "abc123def456\n", "")):
            result = record_and_report(str(tmp_path), str(tmp_path))

        state = _load_state(str(tmp_path))
        assert state["head"] == "abc123def456"
        assert result is None

    def test_no_change_no_message(self, tmp_path):
        """HEAD unchanged between startups: no message."""
        _save_state(str(tmp_path), {"head": "abc123"})
        with patch("app.commit_tracker.run_git", return_value=(0, "abc123\n", "")):
            result = record_and_report(str(tmp_path), str(tmp_path))

        assert result is None

    def test_head_changed_reports_commits(self, tmp_path):
        """HEAD moved: reports new commits."""
        _save_state(str(tmp_path), {"head": "old_sha"})
        log_output = "new1 feat: add X\nnew2 fix: broken Y"

        def mock_git(*args, cwd=None, timeout=None):
            if args[0] == "rev-parse":
                return (0, "new_sha\n", "")
            if args[0] == "log":
                return (0, log_output, "")
            return (1, "", "")

        with patch("app.commit_tracker.run_git", side_effect=mock_git):
            result = record_and_report(str(tmp_path), str(tmp_path))

        assert result is not None
        assert "2 new koan commit(s)" in result
        assert "feat: add X" in result
        assert "fix: broken Y" in result

    def test_head_changed_truncates_long_log(self, tmp_path):
        """More commits than MAX_LOG_LINES: truncates with count."""
        _save_state(str(tmp_path), {"head": "old_sha"})
        commit_lines = [f"sha{i} commit {i}" for i in range(20)]
        log_output = "\n".join(commit_lines)

        def mock_git(*args, cwd=None, timeout=None):
            if args[0] == "rev-parse":
                return (0, "new_sha\n", "")
            if args[0] == "log":
                return (0, log_output, "")
            return (1, "", "")

        with patch("app.commit_tracker.run_git", side_effect=mock_git):
            result = record_and_report(str(tmp_path), str(tmp_path))

        assert result is not None
        assert "20 new koan commit(s)" in result
        assert f"and {20 - MAX_LOG_LINES} more" in result

    def test_force_push_detected(self, tmp_path):
        """HEAD changed but git log returns empty: force-push message."""
        _save_state(str(tmp_path), {"head": "old_sha"})

        def mock_git(*args, cwd=None, timeout=None):
            if args[0] == "rev-parse":
                return (0, "new_sha\n", "")
            if args[0] == "log":
                return (0, "", "")
            return (1, "", "")

        with patch("app.commit_tracker.run_git", side_effect=mock_git):
            result = record_and_report(str(tmp_path), str(tmp_path))

        assert result is not None
        assert "force-push" in result

    def test_unreadable_head_returns_none(self, tmp_path):
        """git rev-parse fails: returns None, state unchanged."""
        _save_state(str(tmp_path), {"head": "old_sha"})
        with patch("app.commit_tracker.run_git", return_value=(1, "", "fatal")):
            result = record_and_report(str(tmp_path), str(tmp_path))

        assert result is None

    def test_state_updated_after_change(self, tmp_path):
        """State file records new HEAD after detecting change."""
        _save_state(str(tmp_path), {"head": "old_sha"})

        def mock_git(*args, cwd=None, timeout=None):
            if args[0] == "rev-parse":
                return (0, "new_sha\n", "")
            if args[0] == "log":
                return (0, "c1 some change\n", "")
            return (1, "", "")

        with patch("app.commit_tracker.run_git", side_effect=mock_git):
            record_and_report(str(tmp_path), str(tmp_path))

        state = _load_state(str(tmp_path))
        assert state["head"] == "new_sha"
