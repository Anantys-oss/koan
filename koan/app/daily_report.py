#!/usr/bin/env python3
"""
Kōan -- Daily report generator

Builds a concise Telegram-friendly digest of the day's activity.
Called from run.py at session boundaries (morning or evening).

Usage from shell:
    python3 daily_report.py [--yesterday]

Usage from Python:
    from app.daily_report import generate_report, should_send_report
"""

import os
import re
import sys
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import List, Optional

from app.notify import format_and_send
from app.signals import DAILY_REPORT_FILE, QUOTA_RESET_FILE

from app.utils import atomic_write, load_dotenv

load_dotenv()

if "KOAN_ROOT" not in os.environ:
    raise SystemExit("KOAN_ROOT environment variable is required")
KOAN_ROOT = Path(os.environ["KOAN_ROOT"])
INSTANCE_DIR = KOAN_ROOT / "instance"
MISSIONS_FILE = INSTANCE_DIR / "missions.md"
REPORT_MARKER = KOAN_ROOT / DAILY_REPORT_FILE


def should_send_report() -> Optional[str]:
    """Check if a daily report should be sent now.

    Returns:
        "morning" if 7-9am and no report sent today,
        "evening" if after 8pm and quota exhausted,
        None otherwise.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Don't send the same report twice in one day
    if REPORT_MARKER.exists():
        marker_date = REPORT_MARKER.read_text().strip()
        if marker_date == today:
            return None

    # Morning report: 7am-9am -> report on yesterday
    if 7 <= now.hour <= 9:
        return "morning"

    # Evening report: after 8pm if quota exhausted
    if now.hour >= 20:
        quota_file = KOAN_ROOT / QUOTA_RESET_FILE
        if quota_file.exists():
            return "evening"

    return None


def _read_journal(target_date: date) -> str:
    """Read all journal entries for a given date across all project subdirs."""
    from app.journal import read_all_journals
    return read_all_journals(INSTANCE_DIR, target_date)


def _parse_completed_missions(target_date: Optional[date] = None) -> List[str]:
    """Extract completed missions from the mission store.

    Args:
        target_date: If provided, only return missions completed on this date.
                     If None, return all completed missions (legacy behavior).
    """
    try:
        from app.mission_store import MissionStore
        store = MissionStore()
        done_records = store.get_by_status("done")
    except (OSError, ValueError):
        return []

    completed = []
    target_str = target_date.strftime("%Y-%m-%d") if target_date is not None else None
    for record in done_records:
        if target_str is not None:
            if not record.completed_at:
                continue
            if not record.completed_at.startswith(target_str):
                continue
        if record.text:
            completed.append(record.text)
    return completed


def _count_pending_missions() -> int:
    """Count pending missions."""
    from app.mission_store import MissionStore

    return len(MissionStore().get_by_status("pending"))


def generate_report(report_type: str = "morning") -> str:
    """Generate a daily digest.

    Args:
        report_type: "morning" (yesterday's recap) or "evening" (today's recap)
    """
    if report_type == "morning":
        target = date.today() - timedelta(days=1)
        header = f"Report for {target.strftime('%Y-%m-%d')}"
    else:
        target = date.today()
        header = "Daily Summary"

    journal = _read_journal(target)
    completed = _parse_completed_missions(target_date=target)
    pending = _count_pending_missions()

    lines = [f"-- {header} --", ""]

    # Completed missions
    if completed:
        lines.append("Completed missions:")
        lines.extend(f"  . {m}" for m in completed[-5:])
        lines.append("")

    # Pending missions
    if pending > 0:
        lines.append(f"Pending: {pending} mission(s)")
        lines.append("")

    # Journal summary (extract key sections)
    if journal:
        # Extract section headers as activity summary
        activities = []
        for line in journal.splitlines():
            if line.startswith("## ") and not line.startswith("## Quota"):
                activity = line[3:].strip()
                # Remove timestamps
                activity = re.sub(r"\s*[—–-]{1,2}\s*\d{2}:\d{2}(:\d{2})?", "", activity)
                if activity:
                    activities.append(activity)

        if activities:
            lines.append("Activity:")
            lines.extend(f"  . {a}" for a in activities[-6:])
            lines.append("")
    else:
        lines.append("No activity recorded.")
        lines.append("")

    # In-progress items
    try:
        from app.mission_store import MissionStore
        store = MissionStore()
        in_progress_records = store.get_by_status("in_progress")
        if in_progress_records:
            lines.append("In Progress:")
            lines.extend(f"  . {r.display_title()}" for r in in_progress_records)
            lines.append("")
    except (OSError, ValueError):
        pass

    lines.append("-- Kōan")
    return "\n".join(lines)


def mark_report_sent():
    """Mark today's report as sent."""
    atomic_write(REPORT_MARKER, date.today().strftime("%Y-%m-%d"))


def send_daily_report(report_type: str = None) -> bool:
    """Check conditions and send daily report if appropriate.

    Args:
        report_type: Force a specific type ("morning"/"evening"), or None for auto-detect.

    Returns:
        True if a report was sent.
    """
    if report_type is None:
        report_type = should_send_report()

    if report_type is None:
        return False

    report = generate_report(report_type)
    success = format_and_send(report)

    if success:
        mark_report_sent()
        print(f"[daily-report] {report_type} report sent")
    else:
        print(f"[daily-report] Failed to send {report_type} report")

    return success


if __name__ == "__main__":
    forced_type = None
    if "--yesterday" in sys.argv or "--morning" in sys.argv:
        forced_type = "morning"
    elif "--evening" in sys.argv or "--today" in sys.argv:
        forced_type = "evening"

    if forced_type:
        report = generate_report(forced_type)
        print(report)
        print()
        success = format_and_send(report)
        if success:
            mark_report_sent()
        sys.exit(0 if success else 1)
    else:
        sent = send_daily_report()
        sys.exit(0 if sent else 1)
