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
                branch="koan/feat", full_repo="owner/repo",
                pr_number="1", project_path="/tmp/proj",
                queued_at=None):
    """Helper to build a queue entry dict."""
    return {
        "pr_url": pr_url,
        "branch": branch,
        "full_repo": full_repo,
        "pr_number": pr_number,
        "project_path": project_path,
        "queued_at": queued_at or datetime.now(timezone.utc).isoformat(),
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

    def test_load_handles_corrupt_json(self, instance_dir):
        _queue_path(instance_dir).write_text("not json{{{")
        assert _load(instance_dir) == []

    def test_load_handles_non_list_json(self, instance_dir):
        _queue_path(instance_dir).write_text('{"key": "val"}')
        assert _load(instance_dir) == []

    def test_roundtrip(self, instance_dir):
        entries = [_make_entry()]
        _save(instance_dir, entries)
        loaded = _load(instance_dir)
        assert loaded == entries

    def test_save_uses_atomic_write(self, instance_dir):
        with patch("app.utils.atomic_write") as mock_aw:
            _save(instance_dir, [])
            mock_aw.assert_called_once()
            call_args = mock_aw.call_args
            assert str(call_args[0][0]).endswith(".ci-queue.json")


# ---------------------------------------------------------------------------
# _is_expired
# ---------------------------------------------------------------------------

class TestIsExpired:
    def test_fresh_entry_is_not_expired(self):
        entry = _make_entry()
        assert _is_expired(entry) is False

    def test_old_entry_is_expired(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        entry = _make_entry(queued_at=old_time)
        assert _is_expired(entry) is True

    def test_entry_at_boundary_is_not_expired(self):
        # Just under 24 hours — should still be valid
        boundary = (datetime.now(timezone.utc) - timedelta(hours=23, minutes=59)).isoformat()
        entry = _make_entry(queued_at=boundary)
        assert _is_expired(entry) is False

    def test_missing_queued_at_is_expired(self):
        entry = {"pr_url": "https://example.com"}
        assert _is_expired(entry) is True

    def test_invalid_timestamp_is_expired(self):
        entry = _make_entry(queued_at="not-a-date")
        assert _is_expired(entry) is True


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:
    def test_enqueue_new_entry_returns_true(self, instance_dir):
        result = enqueue(instance_dir, "https://github.com/o/r/pull/1",
                         "koan/feat", "o/r", "1", "/tmp/proj")
        assert result is True

    def test_enqueue_creates_file(self, instance_dir):
        enqueue(instance_dir, "https://github.com/o/r/pull/1",
                "koan/feat", "o/r", "1", "/tmp/proj")
        assert _queue_path(instance_dir).exists()
        entries = _load(instance_dir)
        assert len(entries) == 1
        assert entries[0]["pr_url"] == "https://github.com/o/r/pull/1"
        assert entries[0]["branch"] == "koan/feat"

    def test_enqueue_duplicate_returns_false_and_updates(self, instance_dir):
        enqueue(instance_dir, "https://github.com/o/r/pull/1",
                "koan/feat", "o/r", "1", "/tmp/proj")
        result = enqueue(instance_dir, "https://github.com/o/r/pull/1",
                         "koan/new-branch", "o/r", "1", "/tmp/proj2")
        assert result is False
        entries = _load(instance_dir)
        assert len(entries) == 1
        # Updated fields
        assert entries[0]["branch"] == "koan/new-branch"
        assert entries[0]["project_path"] == "/tmp/proj2"

    def test_enqueue_different_prs(self, instance_dir):
        enqueue(instance_dir, "https://github.com/o/r/pull/1",
                "koan/a", "o/r", "1", "/tmp/proj")
        enqueue(instance_dir, "https://github.com/o/r/pull/2",
                "koan/b", "o/r", "2", "/tmp/proj")
        entries = _load(instance_dir)
        assert len(entries) == 2

    def test_enqueue_sets_queued_at(self, instance_dir):
        enqueue(instance_dir, "https://github.com/o/r/pull/1",
                "koan/feat", "o/r", "1", "/tmp/proj")
        entries = _load(instance_dir)
        ts = datetime.fromisoformat(entries[0]["queued_at"])
        # Should be very recent (within last minute)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        assert age < 60


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

class TestRemove:
    def test_remove_existing_returns_true(self, instance_dir):
        enqueue(instance_dir, "https://github.com/o/r/pull/1",
                "koan/feat", "o/r", "1", "/tmp/proj")
        result = remove(instance_dir, "https://github.com/o/r/pull/1")
        assert result is True
        assert _load(instance_dir) == []

    def test_remove_nonexistent_returns_false(self, instance_dir):
        result = remove(instance_dir, "https://github.com/o/r/pull/999")
        assert result is False

    def test_remove_only_target_entry(self, instance_dir):
        enqueue(instance_dir, "https://github.com/o/r/pull/1",
                "koan/a", "o/r", "1", "/tmp/proj")
        enqueue(instance_dir, "https://github.com/o/r/pull/2",
                "koan/b", "o/r", "2", "/tmp/proj")
        remove(instance_dir, "https://github.com/o/r/pull/1")
        entries = _load(instance_dir)
        assert len(entries) == 1
        assert entries[0]["pr_url"] == "https://github.com/o/r/pull/2"


# ---------------------------------------------------------------------------
# peek
# ---------------------------------------------------------------------------

class TestPeek:
    def test_peek_empty_queue(self, instance_dir):
        assert peek(instance_dir) is None

    def test_peek_returns_oldest_entry(self, instance_dir):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        entries = [
            _make_entry(pr_url="https://github.com/o/r/pull/1", queued_at=old_time),
            _make_entry(pr_url="https://github.com/o/r/pull/2"),
        ]
        _save(instance_dir, entries)
        result = peek(instance_dir)
        assert result["pr_url"] == "https://github.com/o/r/pull/1"

    def test_peek_does_not_remove_entry(self, instance_dir):
        enqueue(instance_dir, "https://github.com/o/r/pull/1",
                "koan/feat", "o/r", "1", "/tmp/proj")
        peek(instance_dir)
        assert len(_load(instance_dir)) == 1

    def test_peek_skips_expired_entries(self, instance_dir):
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        fresh_time = datetime.now(timezone.utc).isoformat()
        entries = [
            _make_entry(pr_url="https://github.com/o/r/pull/old", queued_at=expired_time),
            _make_entry(pr_url="https://github.com/o/r/pull/new", queued_at=fresh_time),
        ]
        _save(instance_dir, entries)
        result = peek(instance_dir)
        assert result["pr_url"] == "https://github.com/o/r/pull/new"

    def test_peek_prunes_expired_from_disk(self, instance_dir):
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        entries = [
            _make_entry(pr_url="https://github.com/o/r/pull/old", queued_at=expired_time),
            _make_entry(pr_url="https://github.com/o/r/pull/new"),
        ]
        _save(instance_dir, entries)
        peek(instance_dir)
        # Expired entry should be cleaned from disk
        on_disk = _load(instance_dir)
        assert len(on_disk) == 1
        assert on_disk[0]["pr_url"] == "https://github.com/o/r/pull/new"

    def test_peek_returns_none_when_all_expired(self, instance_dir):
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        entries = [_make_entry(queued_at=expired_time)]
        _save(instance_dir, entries)
        assert peek(instance_dir) is None


# ---------------------------------------------------------------------------
# list_entries
# ---------------------------------------------------------------------------

class TestListEntries:
    def test_empty_queue(self, instance_dir):
        assert list_entries(instance_dir) == []

    def test_filters_expired(self, instance_dir):
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        entries = [
            _make_entry(pr_url="https://github.com/o/r/pull/old", queued_at=expired_time),
            _make_entry(pr_url="https://github.com/o/r/pull/new"),
        ]
        _save(instance_dir, entries)
        result = list_entries(instance_dir)
        assert len(result) == 1
        assert result[0]["pr_url"] == "https://github.com/o/r/pull/new"

    def test_returns_all_valid(self, instance_dir):
        entries = [
            _make_entry(pr_url="https://github.com/o/r/pull/1"),
            _make_entry(pr_url="https://github.com/o/r/pull/2"),
        ]
        _save(instance_dir, entries)
        assert len(list_entries(instance_dir)) == 2


# ---------------------------------------------------------------------------
# size
# ---------------------------------------------------------------------------

class TestSize:
    def test_empty_queue(self, instance_dir):
        assert size(instance_dir) == 0

    def test_counts_valid_entries_only(self, instance_dir):
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        entries = [
            _make_entry(pr_url="https://github.com/o/r/pull/old", queued_at=expired_time),
            _make_entry(pr_url="https://github.com/o/r/pull/1"),
            _make_entry(pr_url="https://github.com/o/r/pull/2"),
        ]
        _save(instance_dir, entries)
        assert size(instance_dir) == 2
