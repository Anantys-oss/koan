"""Tests for github_notification_tracker — persistent comment dedup."""

import json
import time

import pytest

from app.github_notification_tracker import (
    _MAX_ENTRIES,
    _REVIEW_COOLDOWN_SECONDS,
    _TTL_SECONDS,
    _threads_path,
    _tracker_path,
    is_comment_tracked,
    is_review_on_cooldown,
    is_thread_tracked,
    set_review_cooldown,
    track_comment,
    track_thread,
)


@pytest.fixture()
def instance_dir(tmp_path):
    return str(tmp_path)


def test_track_and_check(instance_dir):
    assert not is_comment_tracked(instance_dir, "123")
    track_comment(instance_dir, "123")
    assert is_comment_tracked(instance_dir, "123")


def test_empty_comment_id(instance_dir):
    track_comment(instance_dir, "")
    assert not is_comment_tracked(instance_dir, "")


def test_survives_reload(instance_dir):
    """Simulates process restart — data persists on disk."""
    track_comment(instance_dir, "abc")
    # Read directly from file to confirm persistence
    data = json.loads(_tracker_path(instance_dir).read_text())
    assert "abc" in data


def test_ttl_expiry(instance_dir):
    """Expired entries are pruned on load."""
    path = _tracker_path(instance_dir)
    old_ts = time.time() - _TTL_SECONDS - 1
    path.write_text(json.dumps({"old": old_ts, "fresh": time.time()}))

    assert not is_comment_tracked(instance_dir, "old")
    assert is_comment_tracked(instance_dir, "fresh")


def test_max_entries_cap(instance_dir):
    """Oldest entries are evicted when cap is exceeded."""
    now = time.time()
    data = {str(i): now - (_MAX_ENTRIES - i) for i in range(_MAX_ENTRIES)}
    _tracker_path(instance_dir).write_text(json.dumps(data))

    # Adding one more should evict the oldest
    track_comment(instance_dir, "new_entry")
    result = json.loads(_tracker_path(instance_dir).read_text())
    assert len(result) == _MAX_ENTRIES
    assert "new_entry" in result
    # Entry "0" had the oldest timestamp, should be evicted
    assert "0" not in result


def test_corrupt_file_handled(instance_dir):
    """Corrupt JSON is treated as empty tracker."""
    _tracker_path(instance_dir).write_text("not json{{{")
    assert not is_comment_tracked(instance_dir, "123")
    # Can still write
    track_comment(instance_dir, "123")
    assert is_comment_tracked(instance_dir, "123")


def test_multiple_comments(instance_dir):
    track_comment(instance_dir, "a")
    track_comment(instance_dir, "b")
    track_comment(instance_dir, "c")
    assert is_comment_tracked(instance_dir, "a")
    assert is_comment_tracked(instance_dir, "b")
    assert is_comment_tracked(instance_dir, "c")
    assert not is_comment_tracked(instance_dir, "d")


# ---------------------------------------------------------------------------
# Thread tracker (assignment notifications: review_requested, assign)
# ---------------------------------------------------------------------------


class TestThreadTracker:
    def test_track_and_check_thread(self, instance_dir):
        key = "77001:2026-03-21T01:00:00Z"
        assert not is_thread_tracked(instance_dir, key)
        track_thread(instance_dir, key)
        assert is_thread_tracked(instance_dir, key)

    def test_empty_thread_key(self, instance_dir):
        track_thread(instance_dir, "")
        assert not is_thread_tracked(instance_dir, "")

    def test_thread_survives_reload(self, instance_dir):
        track_thread(instance_dir, "k1")
        data = json.loads(_threads_path(instance_dir).read_text())
        assert "k1" in data

    def test_thread_ttl_expiry(self, instance_dir):
        path = _threads_path(instance_dir)
        old_ts = time.time() - _TTL_SECONDS - 1
        path.write_text(json.dumps({"old": old_ts, "fresh": time.time()}))
        assert not is_thread_tracked(instance_dir, "old")
        assert is_thread_tracked(instance_dir, "fresh")

    def test_thread_max_entries_cap(self, instance_dir):
        now = time.time()
        data = {f"k{i}": now - (_MAX_ENTRIES - i) for i in range(_MAX_ENTRIES)}
        _threads_path(instance_dir).write_text(json.dumps(data))
        track_thread(instance_dir, "new_k")
        result = json.loads(_threads_path(instance_dir).read_text())
        assert len(result) == _MAX_ENTRIES
        assert "new_k" in result
        assert "k0" not in result

    def test_thread_corrupt_file_handled(self, instance_dir):
        _threads_path(instance_dir).write_text("not json{{{")
        assert not is_thread_tracked(instance_dir, "k1")
        track_thread(instance_dir, "k1")
        assert is_thread_tracked(instance_dir, "k1")

    def test_thread_updated_at_change_is_new_key(self, instance_dir):
        """Re-requested review (new updated_at) is treated as a new thread."""
        track_thread(instance_dir, "77001:2026-03-21T01:00:00Z")
        assert is_thread_tracked(instance_dir, "77001:2026-03-21T01:00:00Z")
        assert not is_thread_tracked(instance_dir, "77001:2026-03-22T05:00:00Z")

    def test_thread_tracker_independent_from_comment_tracker(self, instance_dir):
        """The two trackers live in two distinct files and don't share state."""
        track_comment(instance_dir, "comment-X")
        track_thread(instance_dir, "thread-Y")
        assert not is_comment_tracked(instance_dir, "thread-Y")
        assert not is_thread_tracked(instance_dir, "comment-X")


# ---------------------------------------------------------------------------
# Review cooldown (prevents re-review after bot's own rebase)
# ---------------------------------------------------------------------------


class TestReviewCooldown:
    def test_not_on_cooldown_initially(self, instance_dir):
        assert not is_review_on_cooldown(instance_dir, "owner", "repo", "42")

    def test_on_cooldown_after_set(self, instance_dir):
        set_review_cooldown(instance_dir, "owner", "repo", "42")
        assert is_review_on_cooldown(instance_dir, "owner", "repo", "42")

    def test_different_pr_not_on_cooldown(self, instance_dir):
        set_review_cooldown(instance_dir, "owner", "repo", "42")
        assert not is_review_on_cooldown(instance_dir, "owner", "repo", "99")

    def test_cooldown_expires(self, instance_dir):
        """Cooldown expires after the configured window."""
        key = "review_cd:owner/repo#42"
        expired_ts = time.time() - _REVIEW_COOLDOWN_SECONDS - 1
        _threads_path(instance_dir).write_text(json.dumps({key: expired_ts}))
        assert not is_review_on_cooldown(instance_dir, "owner", "repo", "42")

    def test_cooldown_active_within_window(self, instance_dir):
        """Cooldown active within the configured window."""
        key = "review_cd:owner/repo#42"
        recent_ts = time.time() - 60  # 1 min ago
        _threads_path(instance_dir).write_text(json.dumps({key: recent_ts}))
        assert is_review_on_cooldown(instance_dir, "owner", "repo", "42")
