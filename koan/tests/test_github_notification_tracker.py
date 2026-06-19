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
    clear_review_cooldown,
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

    def test_clear_cooldown(self, instance_dir):
        """Clearing a cooldown makes the PR reviewable again immediately."""
        set_review_cooldown(instance_dir, "owner", "repo", "42")
        assert is_review_on_cooldown(instance_dir, "owner", "repo", "42")
        clear_review_cooldown(instance_dir, "owner", "repo", "42")
        assert not is_review_on_cooldown(instance_dir, "owner", "repo", "42")

    def test_clear_cooldown_noop_when_absent(self, instance_dir):
        """Clearing a non-existent cooldown does not raise."""
        clear_review_cooldown(instance_dir, "owner", "repo", "999")
        assert not is_review_on_cooldown(instance_dir, "owner", "repo", "999")


class TestThreadReplyCounter:
    """Per-thread reply circuit-breaker counter."""

    def test_count_starts_at_zero(self, instance_dir):
        from app.github_notification_tracker import thread_reply_count
        assert thread_reply_count(instance_dir, "owner", "repo", "42") == 0

    def test_record_increments_within_window(self, instance_dir):
        from app.github_notification_tracker import (
            record_thread_reply,
            thread_reply_count,
        )
        assert record_thread_reply(instance_dir, "owner", "repo", "42") == 1
        assert record_thread_reply(instance_dir, "owner", "repo", "42") == 2
        assert thread_reply_count(instance_dir, "owner", "repo", "42") == 2

    def test_counter_is_per_thread(self, instance_dir):
        from app.github_notification_tracker import (
            record_thread_reply,
            thread_reply_count,
        )
        record_thread_reply(instance_dir, "owner", "repo", "1")
        record_thread_reply(instance_dir, "owner", "repo", "1")
        record_thread_reply(instance_dir, "owner", "repo", "2")
        assert thread_reply_count(instance_dir, "owner", "repo", "1") == 2
        assert thread_reply_count(instance_dir, "owner", "repo", "2") == 1

    def test_old_replies_outside_window_not_counted(self, instance_dir):
        from app.github_notification_tracker import (
            _REPLY_WINDOW_SECONDS,
            _replies_path,
            _reply_key_prefix,
            thread_reply_count,
        )
        prefix = _reply_key_prefix("owner", "repo", "42")
        stale = time.time() - _REPLY_WINDOW_SECONDS - 10
        recent = time.time() - 5
        _replies_path(instance_dir).write_text(
            json.dumps({f"{prefix}{stale}": stale, f"{prefix}{recent}": recent})
        )
        assert thread_reply_count(instance_dir, "owner", "repo", "42") == 1


class TestTryConsumeReplyBudget:
    """Atomic check-and-record reply budget (no check-then-act race)."""

    def test_allows_until_cap_then_blocks(self, instance_dir):
        from app.github_notification_tracker import (
            thread_reply_count,
            try_consume_reply_budget,
        )
        # cap=3: first three allowed and recorded, fourth blocked.
        assert try_consume_reply_budget(instance_dir, "o", "r", "42", 3) is True
        assert try_consume_reply_budget(instance_dir, "o", "r", "42", 3) is True
        assert try_consume_reply_budget(instance_dir, "o", "r", "42", 3) is True
        assert try_consume_reply_budget(instance_dir, "o", "r", "42", 3) is False
        # The blocked attempt recorded nothing — count stays exactly at the cap.
        assert thread_reply_count(instance_dir, "o", "r", "42") == 3

    def test_never_overshoots_cap_under_repeated_consume(self, instance_dir):
        """Sequential consume calls never record more slots than the cap.

        This is the regression guard for the TOCTOU race: even with many
        attempts, the recorded count is clamped to the cap because the check
        and the record share one lock.
        """
        from app.github_notification_tracker import (
            thread_reply_count,
            try_consume_reply_budget,
        )
        cap = 5
        allowed = sum(
            1 for _ in range(20)
            if try_consume_reply_budget(instance_dir, "o", "r", "7", cap)
        )
        assert allowed == cap
        assert thread_reply_count(instance_dir, "o", "r", "7") == cap

    def test_cap_zero_disables_breaker(self, instance_dir):
        from app.github_notification_tracker import (
            thread_reply_count,
            try_consume_reply_budget,
        )
        # cap<=0 means disabled: always allowed and nothing recorded.
        assert try_consume_reply_budget(instance_dir, "o", "r", "42", 0) is True
        assert thread_reply_count(instance_dir, "o", "r", "42") == 0

    def test_fails_open_on_tracker_error(self, instance_dir, monkeypatch):
        """A tracker write failure must allow the reply (fail open)."""
        import app.locked_file as locked_file
        from app.github_notification_tracker import try_consume_reply_budget

        def _boom(*a, **k):
            raise RuntimeError("disk gone")

        monkeypatch.setattr(locked_file, "locked_json_modify", _boom)
        assert try_consume_reply_budget(instance_dir, "o", "r", "42", 1) is True

    def test_breaker_state_isolated_from_dedup_tracker(self, instance_dir):
        """Reply-breaker keys never touch the shared dedup/cooldown file.

        The breaker is high-churn; if its keys shared the dedup tracker they
        could evict durable comment/cooldown keys via the entry cap and
        silently reintroduce re-processing. They must live in their own file.
        """
        from app.github_notification_tracker import (
            _threads_path,
            is_review_on_cooldown,
            record_thread_reply,
            set_review_cooldown,
            try_consume_reply_budget,
        )
        set_review_cooldown(instance_dir, "o", "r", "42")
        for _ in range(50):
            record_thread_reply(instance_dir, "o", "r", "42")
            try_consume_reply_budget(instance_dir, "o", "r", "42", 1000)
        # The shared threads file holds only the cooldown key — no reply churn.
        shared = json.loads(_threads_path(instance_dir).read_text())
        assert all(not k.startswith("reply:") for k in shared)
        assert is_review_on_cooldown(instance_dir, "o", "r", "42") is True

    def test_stale_keys_pruned_on_record(self, instance_dir):
        """Recording prunes entries older than the window (storage reclaimed)."""
        from app.github_notification_tracker import (
            _REPLY_WINDOW_SECONDS,
            _load_replies,
            _replies_path,
            _reply_key_prefix,
            record_thread_reply,
        )
        prefix = _reply_key_prefix("o", "r", "42")
        stale = time.time() - _REPLY_WINDOW_SECONDS - 10
        _replies_path(instance_dir).write_text(
            json.dumps({f"{prefix}{stale}": stale})
        )
        record_thread_reply(instance_dir, "o", "r", "42")
        # The stale key is gone; only the freshly recorded one remains on disk.
        on_disk = json.loads(_replies_path(instance_dir).read_text())
        assert len(on_disk) == 1
        assert all(v > stale for v in _load_replies(instance_dir).values())


class TestTrackCommentDefensive:
    """track_comment is best-effort: must swallow all errors, not just OSError."""

    def test_track_comment_swallows_non_oserror(self, instance_dir, monkeypatch):
        import app.locked_file as locked_file

        def _boom(*a, **k):
            raise RuntimeError("unexpected non-OSError")

        monkeypatch.setattr(locked_file, "locked_json_modify", _boom)
        # Must not raise — the caller's loop (and mark_notification_read) must
        # never be aborted by a best-effort tracker write.
        track_comment(instance_dir, "999")
