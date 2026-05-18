"""Tests for app.ci_queue — persistent CI check queue."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.ci_queue import (
    _is_expired,
    _load,
    _queue_path,
    _save,
    enqueue,
    list_entries,
    peek,
    remove,
    size,
)


@pytest.fixture
def instance_dir(tmp_path):
    d = tmp_path / "instance"
    d.mkdir()
    return d


def _make_entry(pr_url="https://github.com/owner/repo/pull/1",
                branch="koan/feat",
                full_repo="owner/repo",
                pr_number="1",
                project_path="/tmp/proj",
                queued_at=None):
    """Build a queue entry dict with sensible defaults."""
    if queued_at is None:
        queued_at = datetime.now(timezone.utc).isoformat()
    return {
        "pr_url": pr_url,
        "branch": branch,
        "full_repo": full_repo,
        "pr_number": pr_number,
        "project_path": project_path,
        "queued_at": queued_at,
    }


# ---------------------------------------------------------------------------
# _queue_path
# ---------------------------------------------------------------------------

class TestQueuePath:
    def test_returns_json_file_in_instance(self, instance_dir):
        p = _queue_path(instance_dir)
        assert p.name == ".ci-queue.json"
        assert p.parent == instance_dir


# ---------------------------------------------------------------------------
# _load / _save
# ---------------------------------------------------------------------------

class TestLoadSave:
    def test_load_returns_empty_list_when_no_file(self, instance_dir):
        assert _load(instance_dir) == []

    def test_load_returns_empty_list_on_invalid_json(self, instance_dir):
        _queue_path(instance_dir).write_text("not json")
        assert _load(instance_dir) == []

    def test_load_returns_empty_list_when_json_is_not_a_list(self, instance_dir):
        _queue_path(instance_dir).write_text('{"key": "val"}')
        assert _load(instance_dir) == []

    @patch("app.utils.atomic_write")
    def test_save_calls_atomic_write(self, mock_aw, instance_dir):
        entries = [_make_entry()]
        _save(instance_dir, entries)
        mock_aw.assert_called_once()
        path_arg, data_arg = mock_aw.call_args[0]
        assert path_arg == _queue_path(instance_dir)
        assert json.loads(data_arg) == entries

    def test_roundtrip(self, instance_dir):
        entries = [_make_entry()]
        # Write directly (bypass atomic_write for roundtrip test)
        _queue_path(instance_dir).write_text(json.dumps(entries))
        loaded = _load(instance_dir)
        assert len(loaded) == 1
        assert loaded[0]["pr_url"] == entries[0]["pr_url"]


# ---------------------------------------------------------------------------
# _is_expired
# ---------------------------------------------------------------------------

class TestIsExpired:
    def test_fresh_entry_is_not_expired(self):
        entry = _make_entry()
        assert _is_expired(entry) is False

    def test_old_entry_is_expired(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        entry = _make_entry(queued_at=old_ts)
        assert _is_expired(entry) is True

    def test_entry_at_boundary_is_not_expired(self):
        # 23h59m old — should still be valid
        ts = (datetime.now(timezone.utc) - timedelta(hours=23, minutes=59)).isoformat()
        entry = _make_entry(queued_at=ts)
        assert _is_expired(entry) is False

    def test_missing_queued_at_is_expired(self):
        entry = {"pr_url": "http://example.com"}
        assert _is_expired(entry) is True

    def test_invalid_queued_at_is_expired(self):
        entry = _make_entry(queued_at="not-a-date")
        assert _is_expired(entry) is True


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:
    @patch("app.utils.atomic_write")
    def test_enqueue_new_entry_returns_true(self, mock_aw, instance_dir):
        result = enqueue(
            instance_dir,
            pr_url="https://github.com/o/r/pull/1",
            branch="koan/feat",
            full_repo="o/r",
            pr_number="1",
            project_path="/tmp/p",
        )
        assert result is True
        mock_aw.assert_called_once()
        saved = json.loads(mock_aw.call_args[0][1])
        assert len(saved) == 1
        assert saved[0]["pr_url"] == "https://github.com/o/r/pull/1"

    @patch("app.utils.atomic_write")
    def test_enqueue_duplicate_returns_false_and_updates(self, mock_aw, instance_dir):
        # Pre-seed the queue with an existing entry
        existing = [_make_entry(
            pr_url="https://github.com/o/r/pull/1",
            branch="old-branch",
        )]
        _queue_path(instance_dir).write_text(json.dumps(existing))

        result = enqueue(
            instance_dir,
            pr_url="https://github.com/o/r/pull/1",
            branch="new-branch",
            full_repo="o/r",
            pr_number="1",
            project_path="/tmp/p",
        )
        assert result is False
        saved = json.loads(mock_aw.call_args[0][1])
        assert len(saved) == 1
        assert saved[0]["branch"] == "new-branch"

    @patch("app.utils.atomic_write")
    def test_enqueue_multiple_distinct_prs(self, mock_aw, instance_dir):
        enqueue(instance_dir, "https://github.com/o/r/pull/1",
                "b1", "o/r", "1", "/tmp/p")
        # Simulate the first write persisting
        saved_first = json.loads(mock_aw.call_args[0][1])
        _queue_path(instance_dir).write_text(json.dumps(saved_first))

        enqueue(instance_dir, "https://github.com/o/r/pull/2",
                "b2", "o/r", "2", "/tmp/p")
        saved_second = json.loads(mock_aw.call_args[0][1])
        assert len(saved_second) == 2


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

class TestRemove:
    @patch("app.utils.atomic_write")
    def test_remove_existing_returns_true(self, mock_aw, instance_dir):
        entries = [_make_entry(pr_url="https://github.com/o/r/pull/1")]
        _queue_path(instance_dir).write_text(json.dumps(entries))

        result = remove(instance_dir, "https://github.com/o/r/pull/1")
        assert result is True
        saved = json.loads(mock_aw.call_args[0][1])
        assert len(saved) == 0

    def test_remove_nonexistent_returns_false(self, instance_dir):
        result = remove(instance_dir, "https://github.com/o/r/pull/999")
        assert result is False

    @patch("app.utils.atomic_write")
    def test_remove_only_matching_entry(self, mock_aw, instance_dir):
        entries = [
            _make_entry(pr_url="https://github.com/o/r/pull/1"),
            _make_entry(pr_url="https://github.com/o/r/pull/2"),
        ]
        _queue_path(instance_dir).write_text(json.dumps(entries))

        remove(instance_dir, "https://github.com/o/r/pull/1")
        saved = json.loads(mock_aw.call_args[0][1])
        assert len(saved) == 1
        assert saved[0]["pr_url"] == "https://github.com/o/r/pull/2"


# ---------------------------------------------------------------------------
# peek
# ---------------------------------------------------------------------------

class TestPeek:
    def test_peek_empty_queue_returns_none(self, instance_dir):
        assert peek(instance_dir) is None

    def test_peek_returns_oldest_valid_entry(self, instance_dir):
        older = _make_entry(
            pr_url="https://github.com/o/r/pull/1",
            queued_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        )
        newer = _make_entry(
            pr_url="https://github.com/o/r/pull/2",
        )
        _queue_path(instance_dir).write_text(json.dumps([older, newer]))

        result = peek(instance_dir)
        assert result["pr_url"] == "https://github.com/o/r/pull/1"

    @patch("app.utils.atomic_write")
    def test_peek_prunes_expired_entries(self, mock_aw, instance_dir):
        expired = _make_entry(
            pr_url="https://github.com/o/r/pull/old",
            queued_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        )
        valid = _make_entry(pr_url="https://github.com/o/r/pull/new")
        _queue_path(instance_dir).write_text(json.dumps([expired, valid]))

        result = peek(instance_dir)
        assert result["pr_url"] == "https://github.com/o/r/pull/new"
        # The expired entry was pruned and the queue was saved
        mock_aw.assert_called()
        saved = json.loads(mock_aw.call_args[0][1])
        assert len(saved) == 1

    @patch("app.utils.atomic_write")
    def test_peek_all_expired_returns_none(self, mock_aw, instance_dir):
        expired = _make_entry(
            queued_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        )
        _queue_path(instance_dir).write_text(json.dumps([expired]))

        result = peek(instance_dir)
        assert result is None


# ---------------------------------------------------------------------------
# list_entries
# ---------------------------------------------------------------------------

class TestListEntries:
    def test_empty_queue(self, instance_dir):
        assert list_entries(instance_dir) == []

    def test_filters_expired(self, instance_dir):
        expired = _make_entry(
            pr_url="https://github.com/o/r/pull/old",
            queued_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        )
        valid = _make_entry(pr_url="https://github.com/o/r/pull/new")
        _queue_path(instance_dir).write_text(json.dumps([expired, valid]))

        entries = list_entries(instance_dir)
        assert len(entries) == 1
        assert entries[0]["pr_url"] == "https://github.com/o/r/pull/new"

    def test_returns_all_valid(self, instance_dir):
        entries = [
            _make_entry(pr_url=f"https://github.com/o/r/pull/{i}")
            for i in range(3)
        ]
        _queue_path(instance_dir).write_text(json.dumps(entries))

        result = list_entries(instance_dir)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# size
# ---------------------------------------------------------------------------

class TestSize:
    def test_empty_queue(self, instance_dir):
        assert size(instance_dir) == 0

    def test_counts_non_expired(self, instance_dir):
        expired = _make_entry(
            queued_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        )
        valid = _make_entry()
        _queue_path(instance_dir).write_text(json.dumps([expired, valid]))

        assert size(instance_dir) == 1
