"""Kōan brief skill — daily digest of agent activity."""

import json
from datetime import date, datetime, timedelta


def handle(ctx):
    args = ctx.args.strip() if ctx.args else ""
    if args == "--schedule":
        return _seed_schedule(ctx.instance_dir)

    digest = _build_digest(ctx)

    if "[brief-" in args:
        _maybe_reschedule(ctx.instance_dir)

    return digest


def _build_digest(ctx):
    instance_dir = ctx.instance_dir
    koan_root = ctx.koan_root

    parts = ["◉ Kōan Daily Brief"]

    _add_loop_status(parts, koan_root)
    _add_mission_summary(parts, instance_dir)
    _add_quota_health(parts, instance_dir)
    _add_journal_highlights(parts, instance_dir)

    return "\n".join(parts)


def _add_loop_status(parts, koan_root):
    status_file = koan_root / ".koan-status"
    if status_file.exists():
        try:
            status = status_file.read_text().strip()
            if status:
                parts.append(f"  Loop: {status}")
                return
        except OSError:
            pass
    parts.append("  Loop: unknown")


def _add_mission_summary(parts, instance_dir):
    missions_file = instance_dir / "missions.md"
    if not missions_file.exists():
        parts.append("  Missions: no data")
        return

    try:
        from app.missions import extract_timestamps
        from app.mission_store.transition import read_sections

        sections = read_sections(instance_dir)
    except (OSError, ImportError):
        parts.append("  Missions: no data")
        return

    pending = len(sections.get("pending", []))
    in_progress = len(sections.get("in_progress", []))
    ci = len(sections.get("ci", []))
    done_lines = sections.get("done", [])

    cutoff = datetime.now() - timedelta(hours=24)
    done_24h = 0
    for line in done_lines:
        ts = extract_timestamps(line)
        if ts.get("completed") and ts["completed"] >= cutoff:
            done_24h += 1

    summary = f"  Missions: {pending} pending, {in_progress} active"
    if done_24h:
        summary += f", {done_24h} done (24h)"
    parts.append(summary)

    if ci:
        parts.append(f"  CI: {ci} fix{'es' if ci != 1 else ''} queued")


def _add_quota_health(parts, instance_dir):
    try:
        from app.burn_rate import burn_rate_pct_per_minute
    except ImportError:
        return

    rate = burn_rate_pct_per_minute(instance_dir)
    if rate is not None:
        rate_per_hour = rate * 60
        parts.append(f"  Burn rate: {rate_per_hour:.1f}%/h")
    else:
        parts.append("  Burn rate: no data")


def _add_journal_highlights(parts, instance_dir):
    try:
        from app.journal import read_all_journals
    except ImportError:
        return

    try:
        yesterday = date.today() - timedelta(days=1)
        content = read_all_journals(instance_dir, yesterday)
        if not content.strip():
            content = read_all_journals(instance_dir, date.today())
    except OSError:
        parts.append("  Journal: unavailable")
        return

    if not content.strip():
        return

    lines = [
        ln.strip() for ln in content.splitlines()
        if ln.strip() and not ln.strip().startswith("---")
    ]
    if lines:
        parts.append("  Journal:")
        for line in lines[:3]:
            truncated = line[:80] + "…" if len(line) > 80 else line
            parts.append(f"    {truncated}")


def _maybe_reschedule(instance_dir):
    """Ensure tomorrow's brief event exists (idempotent)."""
    try:
        from app.event_scheduler import write_event_file
    except ImportError:
        return

    events_dir = instance_dir / "events"
    tomorrow = datetime.now().replace(
        hour=7, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)

    tag = tomorrow.strftime("brief-%Y%m%d")
    if events_dir.is_dir():
        for f in events_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if tag in data.get("mission", ""):
                    return
            except (OSError, json.JSONDecodeError, ValueError):
                continue

    write_event_file(events_dir, tomorrow, f"/brief [{tag}]")


def _seed_schedule(instance_dir):
    """Explicitly seed the daily brief schedule."""
    try:
        from app.event_scheduler import write_event_file
    except ImportError:
        return "event_scheduler not available."

    events_dir = instance_dir / "events"
    tomorrow = datetime.now().replace(
        hour=7, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)

    tag = tomorrow.strftime("brief-%Y%m%d")
    if events_dir.is_dir():
        for f in events_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if tag in data.get("mission", ""):
                    return f"Already scheduled for {tomorrow.strftime('%Y-%m-%d %H:%M')}."
            except (OSError, json.JSONDecodeError, ValueError):
                continue

    write_event_file(events_dir, tomorrow, f"/brief [{tag}]")
    return f"Daily brief scheduled for {tomorrow.strftime('%Y-%m-%d %H:%M')}."
