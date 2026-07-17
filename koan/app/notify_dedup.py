"""Cross-process, time-windowed dedup for idempotent lifecycle notices (#2426).

The messaging providers already suppress an identical outbound message repeated
within a 5-minute window (``TelegramProvider`` flood protection), but that state
is **in-memory and per-process** — it resets on every process (re-)incarnation.
So when the agent loop or bridge restarts several times in a short window (a
crash/restart loop, or a supervisor doing repeated ``stop``+``start``), each
fresh process re-announces the same idempotent notice — "🌅 Running morning
ritual…", "🛑 Shutting down…" — and the operator sees the notice duplicated N
times.

This module persists a small ``{hash: last_sent_ts}`` map under
``instance/.notify-dedup.json`` so those idempotent notices dedupe **across**
process incarnations and providers. Only notices whose call site opts in (via a
non-zero ``dedup_window``) are affected; every other message is untouched.

Fail-open by design: any error (no ``KOAN_ROOT``, unreadable/corrupt state,
lock failure) resolves to "send it" — dedup infrastructure must never be the
reason a message is dropped.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

# Default suppression window for opt-in lifecycle notices. Matches the
# in-process Telegram flood window (FLOOD_WINDOW_SECONDS) so cross-restart
# behaviour is consistent with same-process behaviour.
NOTICE_DEDUP_WINDOW_SECONDS = 300

_DEDUP_FILENAME = ".notify-dedup.json"


def _resolve_koan_root(koan_root: Optional[str]) -> Optional[Path]:
    if koan_root:
        return Path(koan_root)
    root = os.environ.get("KOAN_ROOT", "")
    return Path(root) if root else None


def _dedup_path(koan_root: Path) -> Path:
    return koan_root / "instance" / _DEDUP_FILENAME


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _prune(entries: dict, now: float, window: float) -> dict:
    """Drop entries older than the window so the file can't grow unbounded."""
    return {
        k: ts
        for k, ts in entries.items()
        if isinstance(ts, (int, float)) and (now - ts) < window
    }


def claim_notice(
    text: str,
    window: float = NOTICE_DEDUP_WINDOW_SECONDS,
    koan_root: Optional[str] = None,
) -> bool:
    """Atomically decide whether an idempotent notice should be sent.

    Returns ``True`` when the caller should SEND (no identical notice recorded
    within ``window`` seconds — the timestamp is reserved now), or ``False``
    when the caller should SUPPRESS (an identical notice was sent recently).

    The check-and-reserve happens under an exclusive file lock so concurrent
    processes (agent loop, bridge, ``stop`` CLI) can't both win the slot. On
    any failure this returns ``True`` (fail-open) — a duplicate is a lesser
    evil than a dropped notice.
    """
    if window <= 0:
        return True
    root = _resolve_koan_root(koan_root)
    if root is None:
        return True

    path = _dedup_path(root)
    key = _key(text)
    now = time.time()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Open "a+" (creates if missing, never truncates on open) and hold an
        # exclusive lock for the whole read-modify-write so the check and the
        # reserve are atomic. _rewrite() seeks to 0 and truncate()s before
        # writing, so the full map is overwritten (not appended) each time.
        with open(path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                raw = fh.read().strip()
                try:
                    entries = json.loads(raw) if raw else {}
                    if not isinstance(entries, dict):
                        entries = {}
                except ValueError:
                    entries = {}

                entries = _prune(entries, now, window)
                last = entries.get(key)
                if isinstance(last, (int, float)) and (now - last) < window:
                    # Recent identical notice — suppress. Persist the pruned
                    # map so stale entries still get cleaned up.
                    _rewrite(fh, entries)
                    return False

                entries[key] = now
                _rewrite(fh, entries)
                return True
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        return True


def release_notice(text: str, koan_root: Optional[str] = None) -> None:
    """Undo a reservation made by :func:`claim_notice` (best-effort).

    Called when the send that a claim authorised actually failed, so the notice
    can be retried within the window instead of being silently suppressed.
    """
    root = _resolve_koan_root(koan_root)
    if root is None:
        return
    path = _dedup_path(root)
    key = _key(text)
    with contextlib.suppress(OSError):
        if not path.exists():
            return
        with open(path, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                raw = fh.read().strip()
                try:
                    entries = json.loads(raw) if raw else {}
                except ValueError:
                    entries = {}
                if isinstance(entries, dict) and key in entries:
                    del entries[key]
                    _rewrite(fh, entries)
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _rewrite(fh, entries: dict) -> None:
    """Overwrite an already-locked file handle with ``entries`` as JSON."""
    fh.seek(0)
    fh.truncate()
    json.dump(entries, fh)
    fh.flush()
