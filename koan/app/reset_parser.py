#!/usr/bin/env python3
"""
Kōan -- Quota Reset Time Parser

Parses Claude's "resets Xam/Xpm (Europe/Paris)" messages and computes
the actual UNIX timestamp for when the quota resets.

Examples of input formats:
- "resets 10am (Europe/Paris)"
- "resets 5pm (Europe/Paris)"
- "resets Feb 4 at 10am (Europe/Paris)"
- "resets tomorrow at 10am (Europe/Paris)"
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

try:
    import zoneinfo
    ZoneInfo = zoneinfo.ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo


def parse_reset_time(text: str, now: Optional[datetime] = None) -> Tuple[Optional[int], str]:
    """
    Parse reset time from Claude output and compute UNIX timestamp.

    Args:
        text: Raw text containing reset info (e.g., "resets 10am (Europe/Paris)")
        now: Optional datetime for testing (defaults to current time)

    Returns:
        Tuple of (unix_timestamp, human_readable_reset_info)
        If parsing fails, returns (None, original_text)
    """
    if now is None:
        now = datetime.now()

    # Extract the full reset string including timezone
    # Pattern: "resets <time_info> (<timezone>)" or just "resets <time_info>"
    match = re.search(r'resets?\s+(.+?\([^)]+\)|[^·\n]+)', text, re.IGNORECASE)
    if not match:
        resets_at = re.search(r'resetsAt\s*:?\s*(\d{9,})', text, re.IGNORECASE)
        if resets_at:
            reset_ts = int(resets_at.group(1))
            reset_dt = datetime.fromtimestamp(reset_ts, timezone.utc)
            reset_info = f"resets at {reset_dt:%Y-%m-%d %H:%M UTC}"
            return reset_ts, reset_info
        return None, text.strip()

    reset_str = match.group(1).strip()

    # Extract timezone (default to Europe/Paris)
    tz_match = re.search(r'\(([^)]+)\)\s*$', reset_str)
    tz_name = "Europe/Paris"
    if tz_match:
        tz_name = tz_match.group(1).strip()
        reset_str = reset_str[:tz_match.start()].strip()

    try:
        tz = ZoneInfo(tz_name)
    except (KeyError, ValueError):
        tz = ZoneInfo("Europe/Paris")

    # Parse the time component
    now_tz = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)

    # Pattern: "10am" or "5pm" (today or next occurrence)
    time_only = re.match(r'^(\d{1,2})\s*(am|pm)$', reset_str, re.IGNORECASE)
    if time_only:
        hour = int(time_only.group(1))
        is_pm = time_only.group(2).lower() == 'pm'
        if is_pm and hour != 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0

        reset_dt = now_tz.replace(hour=hour, minute=0, second=0, microsecond=0)
        # If time has passed today, it means tomorrow
        if reset_dt <= now_tz:
            reset_dt += timedelta(days=1)

        reset_info = f"resets {time_only.group(1)}{time_only.group(2).lower()} ({tz_name})"
        return int(reset_dt.timestamp()), reset_info

    # Pattern: "Feb 4 at 10am" or "Feb 4, 10am"
    date_time = re.match(
        r'^(\w+)\s+(\d{1,2})(?:\s+at\s*|\s*,?\s*)(\d{1,2})\s*(am|pm)$',
        reset_str, re.IGNORECASE
    )
    if date_time:
        month_str = date_time.group(1)
        day = int(date_time.group(2))
        hour = int(date_time.group(3))
        is_pm = date_time.group(4).lower() == 'pm'

        if is_pm and hour != 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0

        # Parse month name
        month = _parse_month(month_str)
        if month is None:
            return None, text.strip()

        year = now_tz.year
        # If the date has passed this year, it's next year
        try:
            reset_dt = datetime(year, month, day, hour, 0, 0, tzinfo=tz)
        except ValueError:
            return None, text.strip()

        if reset_dt <= now_tz:
            reset_dt = datetime(year + 1, month, day, hour, 0, 0, tzinfo=tz)

        reset_info = f"resets {month_str} {day} at {date_time.group(3)}{date_time.group(4).lower()} ({tz_name})"
        return int(reset_dt.timestamp()), reset_info

    # Pattern: "tomorrow at 10am"
    tomorrow = re.match(r'^tomorrow\s+at\s+(\d{1,2})\s*(am|pm)$', reset_str, re.IGNORECASE)
    if tomorrow:
        hour = int(tomorrow.group(1))
        is_pm = tomorrow.group(2).lower() == 'pm'
        if is_pm and hour != 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0

        reset_dt = (now_tz + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
        reset_info = f"resets tomorrow at {tomorrow.group(1)}{tomorrow.group(2).lower()} ({tz_name})"
        return int(reset_dt.timestamp()), reset_info

    # Pattern: "in Xh" or "in X hours"
    in_hours = re.match(r'^in\s+(\d+)\s*h(?:ours?)?$', reset_str, re.IGNORECASE)
    if in_hours:
        hours = int(in_hours.group(1))
        reset_dt = now_tz + timedelta(hours=hours)
        reset_info = f"resets in {hours}h"
        return int(reset_dt.timestamp()), reset_info

    # Couldn't parse — return None but preserve the text
    return None, text.strip()


def _parse_month(month_str: str) -> Optional[int]:
    """Parse month name to number."""
    months = {
        'jan': 1, 'january': 1,
        'feb': 2, 'february': 2,
        'mar': 3, 'march': 3,
        'apr': 4, 'april': 4,
        'may': 5,
        'jun': 6, 'june': 6,
        'jul': 7, 'july': 7,
        'aug': 8, 'august': 8,
        'sep': 9, 'september': 9,
        'oct': 10, 'october': 10,
        'nov': 11, 'november': 11,
        'dec': 12, 'december': 12,
    }
    return months.get(month_str.lower())


def time_until_reset(reset_timestamp: int, now: Optional[datetime] = None) -> str:
    """
    Return human-readable time until reset.

    Args:
        reset_timestamp: UNIX timestamp of reset time
        now: Optional datetime for testing

    Returns:
        String like "2h 15m" or "tomorrow at 10am"
    """
    if now is None:
        now = datetime.now()

    now_ts = int(now.timestamp())
    diff = reset_timestamp - now_ts

    if diff <= 0:
        return "now"

    hours = diff // 3600
    minutes = (diff % 3600) // 60

    if hours == 0:
        return f"{minutes}m"
    elif hours < 24:
        if minutes > 0:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
    else:
        days = hours // 24
        remaining_hours = hours % 24
        if remaining_hours > 0:
            return f"{days}d {remaining_hours}h"
        return f"{days}d"


def should_auto_resume(reset_timestamp: int, now: Optional[datetime] = None) -> bool:
    """
    Check if we should auto-resume based on reset time.

    Returns True if current time >= reset_timestamp.
    """
    if now is None:
        now = datetime.now()

    return int(now.timestamp()) >= reset_timestamp


# CLI interface
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: reset_parser.py <command> [args...]", file=sys.stderr)
        print("Commands:", file=sys.stderr)
        print("  parse <text>     - Parse reset time, output: timestamp|info", file=sys.stderr)
        print("  check <timestamp> - Check if should resume (exit 0=yes, 1=no)", file=sys.stderr)
        print("  until <timestamp> - Human-readable time until reset", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "parse":
        text = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        timestamp, info = parse_reset_time(text)
        if timestamp:
            print(f"{timestamp}|{info}")
        else:
            print(f"|{info}")

    elif cmd == "check":
        if len(sys.argv) < 3:
            sys.exit(1)
        try:
            timestamp = int(sys.argv[2])
            if should_auto_resume(timestamp):
                sys.exit(0)
            else:
                sys.exit(1)
        except ValueError:
            sys.exit(1)

    elif cmd == "until":
        if len(sys.argv) < 3:
            print("unknown")
            sys.exit(0)
        try:
            timestamp = int(sys.argv[2])
            print(time_until_reset(timestamp))
        except ValueError:
            print("unknown")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
