"""Persistent trackers for processed GitHub notifications.

Three parallel trackers live here:

- **Comment tracker** (``instance/.koan-github-processed.json``):
  records comment IDs for @mention notifications. Used as a fallback when
  the reactions API fails to confirm a 👍/👀 was placed.
- **Thread tracker** (``instance/.koan-github-processed-threads.json``):
  records ``"<notification_id>:<updated_at>"`` keys for assignment
  notifications (``review_requested`` / ``assign``) and review cooldowns.
  These have no comment to react to, so without persistent tracking the same
  notification gets re-processed on every restart.
- **Reply-breaker counter** (``instance/.koan-github-reply-counts.json``):
  records one timestamped key per bot reply, for the per-thread reply circuit
  breaker. Kept in its own file (see the breaker section below) so its high
  churn cannot evict durable dedup/cooldown keys.

All survive process restarts and use the same cap/locking pattern; the first
two prune on a 7-day TTL, the reply-breaker counter on a 1-hour window.
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


_TRACKER_FILE = ".koan-github-processed.json"
_TRACKER_FILE_THREADS = ".koan-github-processed-threads.json"
_TTL_SECONDS = 7 * 86400  # 7 days
_MAX_ENTRIES = 5000


def _tracker_path(instance_dir: str) -> Path:
    return Path(instance_dir) / _TRACKER_FILE


def _load(instance_dir: str) -> dict:
    """Load tracker data, pruning expired entries."""
    path = _tracker_path(instance_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
    except (json.JSONDecodeError, OSError):
        return {}
    # Prune expired
    now = time.time()
    return {k: v for k, v in data.items() if now - v < _TTL_SECONDS}


def _threads_path(instance_dir: str) -> Path:
    return Path(instance_dir) / _TRACKER_FILE_THREADS


def _load_threads(instance_dir: str) -> dict:
    """Load thread-tracker data, pruning expired entries."""
    path = _threads_path(instance_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
    except (json.JSONDecodeError, OSError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if now - v < _TTL_SECONDS}


def is_comment_tracked(instance_dir: str, comment_id: str) -> bool:
    """Check if a comment ID has been persistently recorded."""
    if not comment_id:
        return False
    data = _load(instance_dir)
    return comment_id in data


def _prune_expired(data: dict) -> None:
    """Remove expired entries (in-place)."""
    now = time.time()
    expired = [k for k, v in data.items() if now - v >= _TTL_SECONDS]
    for k in expired:
        del data[k]


def _cap_entries(data: dict) -> None:
    """Evict oldest entries beyond _MAX_ENTRIES (in-place)."""
    if len(data) > _MAX_ENTRIES:
        sorted_items = sorted(data.items(), key=lambda x: x[1])
        data.clear()
        data.update(dict(sorted_items[-_MAX_ENTRIES:]))


def track_comment(instance_dir: str, comment_id: str) -> None:
    """Record a comment ID as processed (with file lock for thread safety)."""
    if not comment_id:
        return
    try:
        from app.locked_file import locked_json_modify

        def _update(data):
            _prune_expired(data)
            data[comment_id] = time.time()
            _cap_entries(data)

        locked_json_modify(_tracker_path(instance_dir), _update)
    except Exception as e:  # noqa: BLE001 — best-effort; must not break notification processing
        log.debug("track_comment: failed to record %s: %s", comment_id, e)


def is_thread_tracked(instance_dir: str, thread_key: str) -> bool:
    """Check if an assignment-notification thread key has been recorded.

    ``thread_key`` is a composite ``"<notification_id>:<updated_at>"``.
    Bumping ``updated_at`` (e.g. a re-requested review or a new commit
    pushed to the PR) yields a fresh key so the next notification cycle
    is not deduped — a renewed request still queues a new mission.
    """
    if not thread_key:
        return False
    data = _load_threads(instance_dir)
    return thread_key in data


def track_thread(instance_dir: str, thread_key: str) -> None:
    """Record an assignment-notification thread key as processed.

    Uses an exclusive file lock for thread/process safety.
    Best-effort: file errors are swallowed rather than breaking the
    notification pipeline.
    """
    if not thread_key:
        return
    try:
        from app.locked_file import locked_json_modify

        def _update(data):
            _prune_expired(data)
            data[thread_key] = time.time()
            _cap_entries(data)

        locked_json_modify(_threads_path(instance_dir), _update)
    except Exception as e:  # noqa: BLE001 — best-effort; must not break notification processing
        log.debug("track_thread: failed to record %s: %s", thread_key, e)


# ---------------------------------------------------------------------------
# Review cooldown — prevents re-review after bot's own rebase
# ---------------------------------------------------------------------------

_REVIEW_COOLDOWN_SECONDS = 30 * 60  # 30 minutes


def is_review_on_cooldown(instance_dir: str, owner: str, repo: str, pr_number: str) -> bool:
    """Check if a review for this PR was recently queued.

    Returns True if a review was queued within the cooldown window.
    Prevents the review_requested → review → rebase → new SHA → re-review
    feedback loop.
    """
    key = f"review_cd:{owner}/{repo}#{pr_number}"
    data = _load_threads(instance_dir)
    ts = data.get(key)
    if ts is None:
        return False
    return time.time() - ts < _REVIEW_COOLDOWN_SECONDS


def set_review_cooldown(instance_dir: str, owner: str, repo: str, pr_number: str) -> None:
    """Record that a review was just queued for this PR."""
    key = f"review_cd:{owner}/{repo}#{pr_number}"
    try:
        from app.locked_file import locked_json_modify

        def _update(data):
            _prune_expired(data)
            data[key] = time.time()
            _cap_entries(data)

        locked_json_modify(_threads_path(instance_dir), _update)
    except Exception:  # noqa: BLE001 — best-effort; must not break notification processing
        log.warning("Failed to set review cooldown for %s/%s#%s", owner, repo, pr_number)


def clear_review_cooldown(instance_dir: str, owner: str, repo: str, pr_number: str) -> None:
    """Remove the review cooldown for a PR.

    Called when a human explicitly re-requests a review, proving the
    cooldown should not block the new review mission.
    """
    key = f"review_cd:{owner}/{repo}#{pr_number}"
    try:
        from app.locked_file import locked_json_modify

        def _update(data):
            data.pop(key, None)

        locked_json_modify(_threads_path(instance_dir), _update)
    except Exception:  # noqa: BLE001 — best-effort; must not break notification processing
        log.warning("Failed to clear review cooldown for %s/%s#%s", owner, repo, pr_number)


# ---------------------------------------------------------------------------
# Per-thread reply circuit breaker — caps bot comments per thread per window
# ---------------------------------------------------------------------------
#
# Breaker state lives in its OWN file, separate from the dedup/cooldown thread
# tracker. Reply events are high-churn (one key per posted reply) and only
# meaningful for a rolling hour. Keeping them out of the shared threads file
# means their volume can never evict durable dedup/cooldown keys through
# ``_cap_entries`` (which would silently reintroduce re-processing), and lets
# us prune them on the 1-hour window instead of the 7-day TTL so storage is
# reclaimed promptly.

_REPLY_WINDOW_SECONDS = 3600  # rolling 1 hour
_TRACKER_FILE_REPLIES = ".koan-github-reply-counts.json"


def _replies_path(instance_dir: str) -> Path:
    return Path(instance_dir) / _TRACKER_FILE_REPLIES


def _reply_key_prefix(owner: str, repo: str, number: str) -> str:
    return f"reply:{owner}/{repo}#{number}:"


def _prune_reply_window(data: dict) -> None:
    """Drop reply entries older than the rolling window (in-place)."""
    now = time.time()
    expired = [k for k, v in data.items() if now - v >= _REPLY_WINDOW_SECONDS]
    for k in expired:
        del data[k]


def _load_replies(instance_dir: str) -> dict:
    """Load reply-counter data, pruning entries older than the rolling window."""
    path = _replies_path(instance_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
    except (json.JSONDecodeError, OSError):
        return {}
    now = time.time()
    return {
        k: v for k, v in data.items()
        if isinstance(v, (int, float)) and now - v < _REPLY_WINDOW_SECONDS
    }


def thread_reply_count(instance_dir: str, owner: str, repo: str, number: str) -> int:
    """Count bot replies recorded for a thread within the rolling window.

    Each recorded reply is stored as its own timestamped key; ``_load_replies``
    drops keys older than the window, so a simple prefix match is the count.
    """
    prefix = _reply_key_prefix(owner, repo, str(number))
    data = _load_replies(instance_dir)
    return sum(1 for k in data if k.startswith(prefix))


def record_thread_reply(instance_dir: str, owner: str, repo: str, number: str) -> int:
    """Record one bot reply on a thread; return the count within the window.

    The returned count includes the reply just recorded. Best-effort: on file
    error the recorded count falls back to a best-guess read so callers still
    get a sane number.
    """
    prefix = _reply_key_prefix(owner, repo, str(number))
    holder = {"n": 0}
    try:
        from app.locked_file import locked_json_modify

        def _update(data):
            _prune_reply_window(data)
            now = time.time()
            # Unique key per event (microsecond timestamp avoids collisions).
            data[f"{prefix}{now:.6f}"] = now
            holder["n"] = sum(1 for k in data if k.startswith(prefix))
            _cap_entries(data)

        locked_json_modify(_replies_path(instance_dir), _update)
    except Exception as e:  # noqa: BLE001 — best-effort; must not break notification processing
        log.debug("record_thread_reply: tracker access failed: %s", e)
        holder["n"] = thread_reply_count(instance_dir, owner, repo, number) + 1
    return holder["n"]


def try_consume_reply_budget(
    instance_dir: str, owner: str, repo: str, number: str, cap: int,
) -> bool:
    """Atomically check the rolling-window reply count and record one reply
    iff still under ``cap``.

    Returns True when the reply is allowed (and a slot was recorded), False
    when the cap is already reached (nothing recorded). The count check and
    the record happen inside a single locked read-modify-write, so concurrent
    callers cannot both pass the check and overshoot the cap (closes the
    check-then-act TOCTOU race). Fails open on file error.
    """
    if cap <= 0:
        return True
    prefix = _reply_key_prefix(owner, repo, str(number))
    holder = {"allowed": True}
    try:
        from app.locked_file import locked_json_modify

        def _update(data):
            _prune_reply_window(data)
            count = sum(1 for k in data if k.startswith(prefix))
            if count >= cap:
                holder["allowed"] = False
                return
            now = time.time()
            # Unique key per event (microsecond timestamp avoids collisions).
            data[f"{prefix}{now:.6f}"] = now
            _cap_entries(data)

        locked_json_modify(_replies_path(instance_dir), _update)
    except Exception as e:  # noqa: BLE001 — best-effort; fail open so replies aren't lost
        log.warning("try_consume_reply_budget: tracker access failed, allowing: %s", e)
        holder["allowed"] = True
    return holder["allowed"]
