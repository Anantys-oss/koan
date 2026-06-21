#!/usr/bin/env python3
"""
Kōan — Crash recovery

Detects missions left in "In Progress" from a previous interrupted run.
Classifies each stale mission and takes appropriate action:

  - dead:          Standard crash — move back to Pending
  - partial:       Interrupted run with pending.md context — recover with context
  - unrecoverable: Too many recovery attempts — move to Failed, notify human

Recovery attempts are now tracked in the stagnation_monitor tracker
(instance/.stagnation-retries.json), keyed by mission title. The legacy
[r:N] tag embedded in mission text is still supported for backward
compatibility — if a mission carries an [r:N] tag from a previous Kōan
version and that count exceeds the tracker value, the tag value is used
instead. New missions will not have [r:N] tags written back to missions.md.

All recovery events are logged to instance/recovery.jsonl for forensics.

Complex mission format (### project:X sub-headers in In Progress):
The ### block format is used for multi-step missions that group related sub-tasks
under a project sub-header.  Recovery handles these as atomic blocks — the entire
block is either requeued to Pending or escalated to Failed together.  The block
boundary ends at the next blank line or the next ### header, whichever comes first.
This is a second safety net; the primary is that start_mission() flushes stale
In Progress entries to Failed via _flush_in_progress_to_failed().

Usage from shell:
    python3 recover.py /path/to/instance [--dry-run]

Returns via stdout:
    Number of recovered missions (0 if none).
    Missions file is updated in-place if recovery happens.
"""

import contextlib
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.notify import format_and_send


# Regex to parse and strip the [r:N] recovery counter tag from mission text.
# Matches any content inside [r:...] (not just digits) so malformed tags
# are still caught by strip/set operations.
_RECOVERY_COUNTER_RE = re.compile(r"\s*\[r:([^\]]*)\]")


def _strip_recovery_counter(mission_line: str) -> str:
    """Remove the [r:N] counter from a mission line for clean display."""
    return _RECOVERY_COUNTER_RE.sub("", mission_line).rstrip()


# ---------------------------------------------------------------------------
# State classification
# ---------------------------------------------------------------------------

def classify_mission_state(
    crash_count: int = 0,
    max_crash_retries: int = 3,
    has_pending_journal: bool = False,
    has_checkpoint: bool = False,
    total_attempts: int = 0,
    max_total_retries: int = 0,
) -> str:
    """Classify a stale in-progress mission's recovery state.

    States:
        "unrecoverable" — Too many attempts. Escalate to Failed, notify human.
        "partial"       — Has checkpoint or pending.md context. Recover with context.
        "dead"          — Standard crash, no special context. Simple recovery.

    Args:
        crash_count: Number of crash recovery attempts so far (from tracker).
        max_crash_retries: Maximum crash retries before escalation.
        has_pending_journal: True if a pending.md exists from an interrupted run.
        has_checkpoint: True if a structured checkpoint file exists for this mission.
        total_attempts: Total number of attempts (crash + stagnation) from tracker.
        max_total_retries: Maximum total retries before escalation (0 = disabled).

    Returns:
        One of "unrecoverable", "partial", or "dead".
    """
    if crash_count >= max_crash_retries:
        return "unrecoverable"
    if max_total_retries > 0 and total_attempts >= max_total_retries:
        return "unrecoverable"
    if has_checkpoint or has_pending_journal:
        return "partial"
    return "dead"


# ---------------------------------------------------------------------------
# JSONL audit log
# ---------------------------------------------------------------------------

def _log_recovery_event(
    instance_dir: str,
    mission: str,
    state: str,
    action: str,
    attempts: int,
    has_checkpoint: bool = False,
) -> None:
    """Append a recovery event to recovery.jsonl for audit trail.

    Args:
        instance_dir: Path to instance directory.
        mission: The mission text (raw line).
        state: Classified state ("dead", "partial", "unrecoverable").
        action: Action taken ("recovered", "escalated", "skipped").
        attempts: Recovery attempt count at the time of this event.
        has_checkpoint: Whether a structured checkpoint file was found.
    """
    event = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mission": _strip_recovery_counter(mission).strip(),
        "state": state,
        "action": action,
        "attempts": attempts,
        "has_checkpoint": has_checkpoint,
    }
    log_path = Path(instance_dir) / "recovery.jsonl"
    try:
        from app.locked_file import locked_jsonl_append
        locked_jsonl_append(log_path, event)
    except OSError as e:
        print(f"[recover] Warning: could not write recovery log: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Pending journal check (unchanged)
# ---------------------------------------------------------------------------

def check_pending_journal(instance_dir: str) -> bool:
    """Check if a pending.md exists from an interrupted run. Returns True if found.

    We do NOT delete it — the next Claude session reads it for recovery context.
    We just log its presence so the human knows recovery will happen.
    """
    pending_path = Path(instance_dir) / "journal" / "pending.md"
    try:
        content = pending_path.read_text().strip()
    except FileNotFoundError:
        return False
    if content:
        lines = content.splitlines()
        # Count progress lines (after the --- separator)
        separator_seen = False
        progress_lines = 0
        for line in lines:
            if line.strip() == "---":
                separator_seen = True
                continue
            if separator_seen and line.strip():
                progress_lines += 1
        print(f"[recover] Found pending.md with {progress_lines} progress entries — next run will resume")
        return True
    return False


# ---------------------------------------------------------------------------
# Tracker call isolation
# ---------------------------------------------------------------------------
#
# The stagnation tracker is the crash-count safety net, but a corrupt or
# unwritable tracker file must never abort recovery for ALL missions. Each
# per-mission tracker call is routed through a _safe_* wrapper that catches
# runtime errors, logs a warning, and degrades to the inline [r:N] counter
# (persisted automatically when store.requeue() bumps crash_count).


def _safe_get_crash(get_crash, instance_dir: str, text: str) -> int:
    """Read crash_count from the tracker, returning 0 if it raises."""
    if get_crash is None:
        return 0
    try:
        return get_crash(instance_dir, text)
    except Exception as e:
        print(f"[recover] Warning: tracker get_crash failed for {text!r}: {e}", file=sys.stderr)
        return 0


def _safe_get_total(get_total, instance_dir: str, text: str) -> int:
    """Read total_attempts from the tracker, returning 0 if it raises."""
    if get_total is None:
        return 0
    try:
        return get_total(instance_dir, text)
    except Exception as e:
        print(f"[recover] Warning: tracker get_total failed for {text!r}: {e}", file=sys.stderr)
        return 0


def _safe_inc_crash(inc_crash, instance_dir: str, text: str) -> None:
    """Increment crash_count in the tracker, swallowing runtime errors.

    Failure here is non-fatal: store.requeue() has already bumped the record's
    crash_count (rendered as inline [r:N]), so escalation still progresses.
    """
    if inc_crash is None:
        return
    try:
        inc_crash(instance_dir, text)
    except Exception as e:
        print(f"[recover] Warning: tracker inc_crash failed for {text!r}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main recovery logic
# ---------------------------------------------------------------------------

def recover_missions(
    instance_dir: str,
    dry_run: bool = False,
    has_pending_journal: Optional[bool] = None,
) -> tuple:
    """Move stale in-progress missions back to pending or escalate to failed.

    Enhanced recovery with state classification:
    - Simple stale missions (dead/partial): move back to Pending
    - Repeatedly failing missions (unrecoverable): move to Failed, notify human

    All events are logged to recovery.jsonl for forensics.

    Uses the mission store's locked_store() context manager for an atomic
    load → mutate → save cycle under exclusive lock, preventing race conditions
    with concurrent mission additions.

    This is the *primary* crash-recovery safety net — it runs once at startup,
    before the agent loop, and recovers to Pending. A second, narrower net lives
    in ``missions._flush_in_progress_to_failed`` (invoked per-mission by
    ``start_mission()``): it sweeps anything this function misses (e.g. complex
    ``###`` blocks) into Failed. If you are debugging a stale In Progress
    mission, check both paths.

    Args:
        instance_dir: Path to instance directory.
        dry_run: If True, classify and log but do not modify missions.md.
        has_pending_journal: Optional pre-computed pending.md presence (S6). When
            a caller has already read pending.md (e.g. the CLI entry point via
            ``check_pending_journal()``), it passes the result here so this
            function does not read the same file a second time — removing the
            redundant double-read and closing the TOCTOU window between the two
            reads. When ``None`` (default, daemon path), it is computed here as
            before.

    Returns:
        Tuple of (count of missions moved to Pending, list of escalated mission lines).
    """
    missions_path = Path(instance_dir) / "missions.md"
    json_path = Path(instance_dir) / "missions.json"
    if not json_path.exists() and not missions_path.exists():
        return 0, []

    # Determine pending.md presence for partial-state detection. Reuse a
    # caller-supplied value when available (single read); otherwise read once
    # here. try/except avoids a TOCTOU race (file deleted between check and read).
    if has_pending_journal is None:
        pending_path = Path(instance_dir) / "journal" / "pending.md"
        try:
            has_pending_journal = pending_path.read_text().strip() != ""
        except FileNotFoundError:
            has_pending_journal = False

    # Import checkpoint manager for per-mission checkpoint lookup
    try:
        from app.checkpoint_manager import read_checkpoint as _read_cp
    except ImportError:
        _read_cp = None

    # Load stagnation config for retry limits
    try:
        from app.config import get_stagnation_config as _get_stag_cfg
        from app.stagnation_monitor import (
            get_total_attempts as _get_total,
            get_crash_count as _get_crash,
            increment_crash_count as _inc_crash,
        )
        _stagnation_cfg = _get_stag_cfg()
        _max_total_retries = int(_stagnation_cfg.get("max_total_retries", 0))
        _max_crash_retries = int(_stagnation_cfg.get("max_crash_retries", 3))
    except Exception as e:
        print(f"[recover] Warning: could not load stagnation config or tracker: {e}", file=sys.stderr)
        _get_total = None
        _get_crash = None
        _inc_crash = None
        _max_total_retries = 0
        _max_crash_retries = 3

    recovered_count = 0
    escalated_missions: list = []
    recovered_mission_texts: list = []  # clean mission texts for checkpoint lookup

    from app.mission_store import MissionStore, locked_store

    # In dry-run mode we must not persist anything, so load a detached store
    # under a null context; otherwise use the locked load→mutate→save cycle.
    if dry_run:
        store_ctx = contextlib.nullcontext(MissionStore())
    else:
        store_ctx = locked_store()

    # pending.md context belongs to at most one mission (the one that was
    # running when the process was interrupted). Consume it on first use so
    # subsequent in-progress missions are not all marked "partial".
    journal_available = has_pending_journal

    with store_ctx as store:
        in_progress_records = store.get_by_status("in_progress")[:]

        for record in in_progress_records:
            clean_text = record.text

            # crash_count comes from the record; the stagnation tracker may
            # hold a higher value (legacy crashes recorded before the store).
            crash_count = record.crash_count
            tracker_count = _safe_get_crash(_get_crash, instance_dir, clean_text)
            if tracker_count > crash_count:
                crash_count = tracker_count
            total = _safe_get_total(_get_total, instance_dir, clean_text)

            has_checkpoint = False
            if _read_cp is not None:
                has_checkpoint = _read_cp(instance_dir, clean_text) is not None

            state = classify_mission_state(
                crash_count=crash_count,
                max_crash_retries=_max_crash_retries,
                has_pending_journal=journal_available,
                has_checkpoint=has_checkpoint,
                total_attempts=total,
                max_total_retries=_max_total_retries,
            )
            # Once a mission claims the journal context, mark it consumed
            if journal_available and state == "partial":
                journal_available = False

            if dry_run:
                print(f"[recover] [dry-run] mission={clean_text!r:.60} state={state} "
                      f"attempts={crash_count} checkpoint={has_checkpoint}")
                _log_recovery_event(instance_dir, clean_text, state, "dry_run",
                                    crash_count, has_checkpoint=has_checkpoint)
                continue

            if state == "unrecoverable":
                store.fail(clean_text, extra_tags=["needs_input"])
                escalated_missions.append(f"- {clean_text}")
                _log_recovery_event(instance_dir, clean_text, state, "escalated",
                                    crash_count, has_checkpoint=has_checkpoint)
            else:
                store.requeue(clean_text)
                recovered_count += 1
                recovered_mission_texts.append(clean_text)
                _safe_inc_crash(_inc_crash, instance_dir, clean_text)
                _log_recovery_event(instance_dir, clean_text, state, "recovered",
                                    crash_count + 1, has_checkpoint=has_checkpoint)

    # Write checkpoint recovery context to pending.md if available.
    # This makes structured checkpoint data visible to the agent's normal
    # recovery flow (which reads pending.md at session start).
    if recovered_count > 0 and _read_cp is not None and not dry_run:
        _inject_checkpoint_context(instance_dir, recovered_mission_texts)

    return recovered_count, escalated_missions


# ---------------------------------------------------------------------------
# Checkpoint context injection
# ---------------------------------------------------------------------------

def _inject_checkpoint_context(instance_dir: str, mission_texts: list) -> None:
    """Write checkpoint recovery context to pending.md for recovered missions.

    When a mission has a structured checkpoint, appends formatted recovery
    context to pending.md so the agent reads it on restart.
    Only processes the first mission with a checkpoint (FIFO queue means
    only one mission runs at a time).
    """
    try:
        from app.checkpoint_manager import read_checkpoint, format_recovery_context
    except ImportError:
        return

    from app.utils import atomic_write

    for mission_text in mission_texts:
        cp = read_checkpoint(instance_dir, mission_text)
        if cp is None:
            continue

        context = format_recovery_context(cp)
        pending_path = Path(instance_dir) / "journal" / "pending.md"
        try:
            existing = ""
            with contextlib.suppress(FileNotFoundError):
                existing = pending_path.read_text()
            # Append checkpoint context after existing content
            new_content = ""
            if existing.strip():
                new_content = existing.rstrip() + "\n\n"
            new_content += context + "\n"
            atomic_write(pending_path, new_content)
        except OSError:
            pass
        break  # Only inject for the first mission with a checkpoint


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print(f"Usage: {sys.argv[0]} <instance_dir> [--dry-run]", file=sys.stderr)
        sys.exit(1)

    instance_dir = args[0]
    # Single read of pending.md (S6): check_pending_journal() reads + logs once,
    # then hands the result to recover_missions() so it does not re-read the file.
    has_pending = check_pending_journal(instance_dir)
    count, escalated_lines = recover_missions(
        instance_dir, dry_run=dry_run, has_pending_journal=has_pending,
    )

    # Build escalated message list from current run only (not historical log)
    escalated_msgs = [
        _strip_recovery_counter(m.split("\n")[0]).strip().removeprefix("### ").removeprefix("- ")[:80]
        for m in escalated_lines
    ]

    if count > 0 or has_pending or escalated_msgs:
        parts = []
        if count > 0:
            parts.append(f"{count} mission(s) moved back to Pending")
        if has_pending:
            parts.append("interrupted run detected (pending.md) — will resume")
        msg = "Restart — " + ", ".join(parts) + "." if parts else ""

        if escalated_msgs:
            escalated_summary = "; ".join(escalated_msgs[:3])
            if len(escalated_msgs) > 3:
                escalated_summary += f" (+{len(escalated_msgs) - 3} more)"
            needs_input_msg = (
                f"⚠️ Recovery escalation: {len(escalated_msgs)} mission(s) failed "
                f"the maximum number of recovery attempts and need human review:\n"
                f"{escalated_summary}"
            )
            format_and_send(needs_input_msg)
            print(f"[recover] {needs_input_msg}")

        if msg:
            format_and_send(msg)
            print(f"[recover] {msg}")
    else:
        print("[recover] No stale missions found")

    print(count)
