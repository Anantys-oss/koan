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
instead. Normally [r:N] tags are not written back to missions.md (the
tracker owns the count). The one exception is the degraded path: if the
tracker module fails to import, recovery falls back to persisting the
incremented count inline as [r:N] so escalation still progresses instead
of resetting to 0 every cycle.

All recovery events are logged to instance/recovery.jsonl for forensics.

Complex mission format (### project:X sub-headers in In Progress):
The ### block format is used for multi-step missions that group related sub-tasks
under a project sub-header.  Recovery handles these as atomic blocks — the entire
block is either requeued to Pending or escalated to Failed together.  The block
boundary ends at the next blank line or the next ### header, whichever comes first.
This is the primary handler for stale complex missions. The
_flush_in_progress_to_failed() call inside start_mission() acts as a secondary
safety net, catching any stale entries recover.py missed.

Usage from shell:
    python3 recover.py /path/to/instance [--dry-run]

Returns via stdout:
    Number of recovered missions (0 if none).
    Missions file is updated in-place if recovery happens.
"""

import contextlib
import json
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


def _set_recovery_counter(mission_line: str, count: int) -> str:
    """Return *mission_line* with any existing [r:N] replaced by [r:count].

    Used only in the degraded path where the stagnation tracker is unavailable:
    the crash count must then be persisted inline so the next recovery cycle
    sees a higher value and escalation still progresses (otherwise the count
    resets to 0 every cycle and the mission retries forever).
    """
    return f"{_strip_recovery_counter(mission_line)} [r:{count}]"


# ---------------------------------------------------------------------------
# State classification
# ---------------------------------------------------------------------------

def classify_mission_state(
    *,
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

    Uses modify_missions_file() for atomic read-modify-write under exclusive lock,
    preventing race conditions with concurrent mission additions.

    This is the *primary* crash-recovery safety net — it runs once at startup,
    before the agent loop, and recovers to Pending. A second, narrower net lives
    in ``missions._flush_in_progress_to_failed`` (invoked per-mission by
    ``start_mission()``): it sweeps anything this function misses (e.g. complex
    ``###`` blocks) into Failed. If you are debugging a stale In Progress
    mission, check both paths.

    Args:
        instance_dir: Path to instance directory.
        dry_run: If True, classify and log but do not modify missions.md.
        has_pending_journal: Optional pre-computed pending.md presence. When
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
    try:
        missions_path.read_text()
    except FileNotFoundError:
        return 0, []

    from app.missions import find_section_boundaries, normalize_content
    from app.utils import atomic_write, modify_missions_file

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

    # Retry limits and the crash tracker are loaded as two SEPARATE concerns,
    # because they fail differently:
    #
    #  1. Config load — safe to fall back to defaults. The defaults below match
    #     get_stagnation_config()'s own defaults, so escalation still works.
    #  2. Tracker functions — these are the escalation safety net. If they fail
    #     and we silently set everything to 0/None, classify_mission_state can
    #     NEVER return "unrecoverable" (crash_count is always 0, max_total is 0),
    #     so a repeatedly crashing mission loops Pending→crash→Pending forever.
    #     When the tracker is unavailable we fall back to the legacy inline
    #     [r:N] counter (read by _get_old_r_count, persisted via
    #     _set_recovery_counter) as the sole crash-count rail so escalation
    #     still progresses.
    try:
        from app.config import get_stagnation_config as _get_stag_cfg
    except ImportError as e:
        print(f"[recover] Warning: could not import stagnation config; "
              f"using defaults (max_crash_retries=3, max_total_retries=0): {e}",
              file=sys.stderr)
        _get_stag_cfg = None
    _max_total_retries = 0
    _max_crash_retries = 3
    if _get_stag_cfg is not None:
        try:
            _stagnation_cfg = _get_stag_cfg()
            _max_total_retries = int(_stagnation_cfg.get("max_total_retries", 0))
            _max_crash_retries = int(_stagnation_cfg.get("max_crash_retries", 3))
        except Exception as e:
            print(f"[recover] Warning: could not load stagnation config; "
                  f"using defaults (max_crash_retries={_max_crash_retries}, "
                  f"max_total_retries={_max_total_retries}): {e}",
                  file=sys.stderr)

    try:
        from app.stagnation_monitor import (
            get_total_attempts as _get_total,
            get_crash_count as _get_crash,
            increment_crash_count as _inc_crash,
            seed_crash_count as _seed_crash,
        )
    except (ImportError, AttributeError) as e:
        print(f"[recover] Warning: retry tracker unavailable; falling back to inline "
              f"[r:N] counter so escalation still works: {e}", file=sys.stderr)
        _get_total = None
        _get_crash = None
        _inc_crash = None
        _seed_crash = None

    recovered_count = 0
    escalated_missions: list = []
    recovered_mission_texts: list = []  # clean mission texts for checkpoint lookup

    def _recover_transform(content: str) -> str:
        nonlocal recovered_count, escalated_missions, recovered_mission_texts
        lines = content.splitlines()

        boundaries = find_section_boundaries(lines)
        if "pending" not in boundaries or "in_progress" not in boundaries:
            return content

        pending_start = boundaries["pending"][0]
        in_progress_start, in_progress_end = boundaries["in_progress"]
        failed_bounds = boundaries.get("failed")

        # Classify and sort each candidate mission
        recovered = []      # missions to move to Pending (simple items or full ### blocks)
        escalated = []      # missions to move to Failed
        remaining_in_progress = []
        # pending.md context belongs to at most one mission (the one that was
        # running when the process was interrupted). Consume it on first use so
        # subsequent missions in the same In Progress block are not all marked
        # "partial" — which would give them misleading "recovery context" status.
        journal_available = has_pending_journal
        complex_block_header: str = ""   # raw header line for current ### block
        complex_block_lines: list = []   # all lines in the current ### block

        def _safe_get_crash(title: str) -> int:
            if _get_crash is None:
                return 0
            try:
                return _get_crash(instance_dir, title)
            except Exception as e:
                print(f"[recover] Warning: tracker get_crash failed for "
                      f"{title!r}, using 0: {e}", file=sys.stderr)
                return 0

        def _safe_get_total(title: str) -> int:
            if _get_total is None:
                return 0
            try:
                return _get_total(instance_dir, title)
            except Exception as e:
                print(f"[recover] Warning: tracker get_total failed for "
                      f"{title!r}, using 0: {e}", file=sys.stderr)
                return 0

        def _safe_seed_crash(title: str, value: int) -> None:
            if _seed_crash is None:
                return
            try:
                _seed_crash(instance_dir, title, value)
            except Exception as e:
                print(f"[recover] Warning: tracker seed_crash failed for "
                      f"{title!r}, legacy [r:{value}] may be lost: {e}",
                      file=sys.stderr)

        def _safe_inc_crash(title: str) -> bool:
            """Try to increment via tracker. Returns True on success."""
            if _inc_crash is None:
                return False
            try:
                _inc_crash(instance_dir, title)
                return True
            except Exception as e:
                print(f"[recover] Warning: tracker inc_crash failed for "
                      f"{title!r}, falling back to inline [r:N]: {e}",
                      file=sys.stderr)
                return False

        def _get_old_r_count(line: str) -> int:
            m = _RECOVERY_COUNTER_RE.search(line)
            if not m:
                return 0
            with contextlib.suppress(ValueError, TypeError):
                return int(m.group(1))
            return 0

        def _append_escalated_entry(out: list, m: str) -> None:
            """Append one escalated item to out, handling complex blocks (multi-line)."""
            if "\n" in m:
                block_lines = m.splitlines()
                header = _strip_recovery_counter(block_lines[0]).rstrip().removeprefix("### ")
                out.append(f"- ❌ needs_input: {header}")
                out.extend(f"  {sub.rstrip()}" for sub in block_lines[1:])
            else:
                clean = _strip_recovery_counter(m).rstrip()
                out.append(f"- ❌ needs_input: {clean.removeprefix('- ')}")

        def _finalize_complex_block():
            """Classify the collected complex mission block and dispatch it."""
            nonlocal journal_available
            if not complex_block_header:
                return
            header = complex_block_header.strip()
            clean_title = _strip_recovery_counter(header).removeprefix("### ").strip()
            has_checkpoint = False
            if _read_cp is not None:
                cp = _read_cp(instance_dir, clean_title)
                has_checkpoint = cp is not None

            old_r = _get_old_r_count(header)
            crash_count = _safe_get_crash(clean_title)
            if old_r > crash_count:
                crash_count = old_r
                _safe_seed_crash(clean_title, old_r)
            total = _safe_get_total(clean_title)

            state = classify_mission_state(
                crash_count=crash_count,
                max_crash_retries=_max_crash_retries,
                has_pending_journal=journal_available,
                has_checkpoint=has_checkpoint,
                total_attempts=total,
                max_total_retries=_max_total_retries,
            )
            if journal_available and state == "partial":
                journal_available = False

            if dry_run:
                print(f"[recover] [dry-run] mission={header!r:.60} state={state} "
                      f"attempts={crash_count} checkpoint={has_checkpoint}")
                _log_recovery_event(instance_dir, header, state, "dry_run", crash_count,
                                    has_checkpoint=has_checkpoint)
                remaining_in_progress.extend(complex_block_lines)
                return

            if state == "unrecoverable":
                escalated.append("\n".join(complex_block_lines))
                _log_recovery_event(instance_dir, header, state, "escalated", crash_count,
                                    has_checkpoint=has_checkpoint)
            else:
                # Convert ### block to - item: extract_next_pending() treats ### as
                # project sub-headers in Pending, which would fragment the block on
                # the next mission pick. Use - format so it's picked up as a unit.
                dash_line = f"- {clean_title}"
                if not _safe_inc_crash(clean_title):
                    dash_line = _set_recovery_counter(dash_line, crash_count + 1)
                recovered.append(dash_line)
                recovered_mission_texts.append(clean_title)
                _log_recovery_event(instance_dir, header, state, "recovered", crash_count + 1,
                                    has_checkpoint=has_checkpoint)

        for i in range(in_progress_start + 1, in_progress_end):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("### "):
                # Finalize any previous complex block before starting a new one
                _finalize_complex_block()
                complex_block_header = line
                complex_block_lines = [line]
                continue

            # Blank lines end the current complex mission block
            if stripped == "":
                if complex_block_header:
                    _finalize_complex_block()
                    complex_block_header = ""
                    complex_block_lines = []
                remaining_in_progress.append(line)
                continue

            if complex_block_header:
                complex_block_lines.append(line)
                continue

            if stripped.startswith("- ") and "~~" not in stripped:
                old_r = _get_old_r_count(line)
                clean_line = _strip_recovery_counter(line).rstrip()
                clean_text = clean_line.removeprefix("- ").strip()

                crash_count = _safe_get_crash(clean_text)
                if old_r > crash_count:
                    crash_count = old_r
                    _safe_seed_crash(clean_text, old_r)
                total = _safe_get_total(clean_text)

                # Check for a structured checkpoint for this mission
                has_checkpoint = False
                if _read_cp is not None:
                    cp = _read_cp(instance_dir, clean_text)
                    has_checkpoint = cp is not None

                # Classify this mission; journal context is single-use
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
                    attempts = crash_count
                    print(f"[recover] [dry-run] mission={stripped!r:.60} state={state} "
                          f"attempts={attempts} checkpoint={has_checkpoint}")
                    _log_recovery_event(instance_dir, line, state, "dry_run", attempts,
                                        has_checkpoint=has_checkpoint)
                    remaining_in_progress.append(line)
                    continue

                if state == "unrecoverable":
                    escalated.append(line)
                    _log_recovery_event(instance_dir, line, state, "escalated", crash_count,
                                        has_checkpoint=has_checkpoint)
                else:
                    if _safe_inc_crash(clean_text):
                        recovered.append(clean_line)
                    else:
                        recovered.append(_set_recovery_counter(clean_line, crash_count + 1))
                    recovered_mission_texts.append(clean_text)
                    _log_recovery_event(instance_dir, line, state, "recovered", crash_count + 1,
                                        has_checkpoint=has_checkpoint)

            elif stripped == "(aucune)" or stripped == "(none)":
                remaining_in_progress.append(line)
            else:
                remaining_in_progress.append(line)

        # Finalize any complex block that ends at the section boundary (no trailing blank line)
        _finalize_complex_block()

        if not recovered and not escalated:
            return content

        recovered_count = len(recovered_mission_texts)
        escalated_missions = escalated

        # Rebuild file: recovered → Pending, escalated → Failed, rest stays
        new_lines = []
        for i, line in enumerate(lines):
            if pending_start < i < in_progress_start:
                if line.strip() in ("(aucune)", "(none)"):
                    continue

            if in_progress_start < i < in_progress_end:
                continue

            # Skip existing failed section content — we'll rebuild it below
            if failed_bounds and failed_bounds[0] < i < failed_bounds[1]:
                continue

            new_lines.append(line)

            if i == pending_start:
                new_lines.append("")
                new_lines.extend(recovered)

            if i == in_progress_start:
                new_lines.extend(remaining_in_progress)
                if not any(m.strip() for m in remaining_in_progress):
                    new_lines.append("")

            # Restore failed section content then append escalated missions
            if failed_bounds and i == failed_bounds[0]:
                # Re-insert original failed content (minus section boundaries we'll re-emit)
                orig_failed = lines[failed_bounds[0] + 1 : failed_bounds[1]]
                new_lines.extend(orig_failed)
                if escalated:
                    for m in escalated:
                        _append_escalated_entry(new_lines, m)
                    new_lines.append("")

        # If there's no Failed section but we have escalated missions, append one
        if escalated and not failed_bounds:
            new_lines.append("")
            new_lines.append("## Failed")
            new_lines.append("")
            for m in escalated:
                _append_escalated_entry(new_lines, m)
            new_lines.append("")

        return normalize_content("\n".join(new_lines) + "\n")

    modify_missions_file(missions_path, _recover_transform)

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
    # Single read of pending.md: check_pending_journal() reads + logs once,
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
