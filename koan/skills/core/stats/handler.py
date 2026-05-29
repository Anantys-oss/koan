"""Kōan stats skill — session outcome statistics per project."""

import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional


def handle(ctx):
    """Show session productivity stats, optionally filtered by project."""
    instance_dir = ctx.instance_dir
    raw_args = ctx.args.strip() if ctx.args else ""

    # Phase 1: parse --week / --month flags (last flag wins, default 7 days)
    days, project_filter = _parse_args(raw_args)

    # Phase 4: filter outcomes to the requested window
    all_outcomes = _load_outcomes(instance_dir / "session_outcomes.json")
    outcomes = _filter_by_days(all_outcomes, days)

    if not outcomes:
        return "No session data yet. Stats will appear after the first completed run."

    if project_filter:
        # case-insensitive project lookup
        filtered = [o for o in outcomes if o.get("project", "").lower() == project_filter.lower()]
        if not filtered:
            known = sorted(set(o.get("project", "") for o in all_outcomes))
            return (
                f"No data for '{project_filter}'.\n"
                f"Known projects: {', '.join(known)}"
            )
        canonical = filtered[0].get("project", project_filter)
        return _format_project_detail(canonical, filtered, instance_dir, days)

    return _format_overview(outcomes, instance_dir, days)


def _parse_args(raw: str):
    """Parse flag/project args. Returns (days, project_name).

    Last --week/--month flag wins; remaining token is the project name.
    """
    days = 7
    tokens = raw.split()
    remaining = []
    for token in tokens:
        if token == "--week":
            days = 7
        elif token == "--month":
            days = 30
        else:
            remaining.append(token)
    project = " ".join(remaining).strip()
    return days, project


def _filter_by_days(outcomes: list, days: int) -> list:
    """Return outcomes from the last N days."""
    cutoff = datetime.now() - timedelta(days=days)
    filtered = []
    for o in outcomes:
        ts_str = o.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            filtered.append(o)
    return filtered


def _load_outcomes(path: Path) -> list:
    """Load session outcomes from JSON file."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _format_overview(outcomes: list, instance_dir: Path, days: int) -> str:
    """Format a cross-project overview."""
    by_project = {}
    for o in outcomes:
        project = o.get("project", "unknown")
        by_project.setdefault(project, []).append(o)

    total = len(outcomes)
    total_productive = sum(1 for o in outcomes if o.get("outcome") == "productive")
    total_empty = sum(1 for o in outcomes if o.get("outcome") == "empty")
    total_blocked = sum(1 for o in outcomes if o.get("outcome") == "blocked")

    pct = int(total_productive / max(1, total) * 100)

    # Streak
    streak = _productive_streak(outcomes)

    window_label = "30d" if days == 30 else "7d"
    lines = [
        f"Session Stats ({window_label})",
        f"  Total: {total} sessions | {pct}% productive",
        f"  {total_productive} productive | {total_empty} empty | {total_blocked} blocked",
    ]

    if streak >= 2:
        lines.append(f"  Streak: {streak} productive in a row")

    # Cost & cache summary line
    cost_cache_line = _format_cost_cache_summary(instance_dir, days)
    if cost_cache_line:
        lines.append(cost_cache_line)

    # Time-based breakdowns
    now = datetime.now()
    today_line = _format_period_line(
        _filter_by_period(outcomes, "today", now), "Today", now
    )
    week_line = _format_period_line(
        _filter_by_period(outcomes, "week", now), "This week", now
    )
    last_week_line = _format_period_line(
        _filter_by_period(outcomes, "last_week", now), "Last week", now
    )

    time_lines = [l for l in (today_line, week_line, last_week_line) if l]
    if time_lines:
        lines.append("")
        lines.extend(time_lines)

    lines.append("")

    # Per-project summary sorted by session count
    sorted_projects = sorted(by_project.items(), key=lambda x: -len(x[1]))
    for project, project_outcomes in sorted_projects:
        count = len(project_outcomes)
        productive = sum(1 for o in project_outcomes if o.get("outcome") == "productive")
        staleness = _consecutive_non_productive(project_outcomes)
        p_pct = int(productive / max(1, count) * 100)

        status = ""
        if staleness >= 5:
            status = " !!!"
        elif staleness >= 3:
            status = " !"

        lines.append(f"  {project}: {count} ({p_pct}% productive){status}")

    lines.append("")

    # Token spend overview
    token_block = _format_token_overview(instance_dir, days)
    if token_block:
        lines.append(token_block)
        lines.append("")

    lines.append("Use /stats <project> for details.")

    return "\n".join(lines)


def _format_project_detail(project: str, outcomes: list,
                           instance_dir: Path, days: int) -> str:
    """Format detailed stats for a single project."""
    total = len(outcomes)
    productive = sum(1 for o in outcomes if o.get("outcome") == "productive")
    empty = sum(1 for o in outcomes if o.get("outcome") == "empty")
    blocked = sum(1 for o in outcomes if o.get("outcome") == "blocked")
    pct = int(productive / max(1, total) * 100)

    # Mode breakdown
    mode_counter = Counter(o.get("mode", "unknown") for o in outcomes)

    # Duration stats
    durations = [o.get("duration_minutes", 0) for o in outcomes if o.get("duration_minutes")]
    avg_duration = int(sum(durations) / max(1, len(durations))) if durations else 0

    # Staleness
    staleness = _consecutive_non_productive(outcomes)

    # Streak
    streak = _productive_streak(outcomes)

    window_label = "30d" if days == 30 else "7d"
    lines = [
        f"Stats: {project} ({window_label})",
        f"  Sessions: {total} | {pct}% productive",
        f"  {productive} productive | {empty} empty | {blocked} blocked",
    ]

    if staleness > 0:
        if staleness >= 5:
            lines.append(f"  Staleness: {staleness} consecutive non-productive")
        elif staleness >= 3:
            lines.append(f"  Staleness: {staleness} (approaching limit)")

    if streak >= 2:
        lines.append(f"  Streak: {streak} productive in a row")

    # Cost & cache for this project
    cost_line = _format_project_cost(instance_dir, project, days, total, productive)
    if cost_line:
        lines.append(cost_line)

    # Time-based breakdowns
    now = datetime.now()
    today_line = _format_period_line(
        _filter_by_period(outcomes, "today", now), "Today", now
    )
    week_line = _format_period_line(
        _filter_by_period(outcomes, "week", now), "This week", now
    )

    time_lines = [l for l in (today_line, week_line) if l]
    if time_lines:
        lines.append("")
        lines.extend(time_lines)

    lines.append("")

    # Mode breakdown
    lines.append("By mode:")
    for mode in ("deep", "implement", "review", "wait"):
        count = mode_counter.get(mode, 0)
        if count > 0:
            mode_outcomes = [o for o in outcomes if o.get("mode") == mode]
            mode_productive = sum(1 for o in mode_outcomes if o.get("outcome") == "productive")
            lines.append(f"  {mode}: {count} ({mode_productive} productive)")

    # Show unknown modes if any
    for mode, count in mode_counter.items():
        if mode not in ("deep", "implement", "review", "wait") and count > 0:
            lines.append(f"  {mode}: {count}")

    if avg_duration > 0:
        lines.append(f"\nAvg duration: {avg_duration} min")

    # Tokens by mission type
    type_block = _format_type_breakdown(instance_dir, project, days)
    if type_block:
        lines.append("")
        lines.append(type_block)

    # Last 5 sessions
    recent = outcomes[-5:]
    lines.append("\nRecent:")
    for o in reversed(recent):
        outcome = o.get("outcome", "?")
        mode = o.get("mode", "?")
        ts = o.get("timestamp", "?")
        if "T" in ts:
            ts = ts.split("T")[1][:5]
        summary = o.get("summary", "")
        if len(summary) > 50:
            summary = summary[:47] + "..."

        icon = "+" if outcome == "productive" else "-" if outcome == "empty" else "~"
        line = f"  {icon} {ts} [{mode}]"
        if summary:
            line += f" {summary}"
        lines.append(line)

    return "\n".join(lines)


def _format_token_overview(instance_dir: Path, days: int) -> str:
    """Build a monospace token-spend block for the overview.

    Returns empty string when no JSONL data is present.
    """
    try:
        from app import cost_tracker
    except ImportError:
        return ""

    by_project = cost_tracker.summarize_by_project(instance_dir, days=days)
    if not by_project:
        return ""

    total_tokens = sum(
        v["input_tokens"] + v["output_tokens"]
        for v in by_project.values()
        if (v["input_tokens"] + v["output_tokens"]) > 0
    )
    if total_tokens == 0:
        return ""

    total_cost = sum(v.get("total_cost_usd", 0.0) for v in by_project.values())
    show_cost = total_cost > 0

    # Sort by descending total tokens, cap at 10
    sorted_projects = sorted(
        [(k, v) for k, v in by_project.items()
         if (v["input_tokens"] + v["output_tokens"]) > 0],
        key=lambda x: -(x[1]["input_tokens"] + x[1]["output_tokens"]),
    )
    overflow = max(0, len(sorted_projects) - 10)
    rows = sorted_projects[:10]

    window_label = "30d" if days == 30 else "7d"
    header_parts = ["project      ", " tokens(K)", "   %"]
    if show_cost:
        header_parts.append("  cost($)")
    header = "".join(header_parts)
    sep = "-" * len(header)

    table_lines = [header, sep]
    for proj_name, data in rows:
        tok = data["input_tokens"] + data["output_tokens"]
        tok_k = tok / 1000
        pct = int(tok / max(1, total_tokens) * 100)
        name = proj_name[:12] + ("…" if len(proj_name) > 12 else "")
        row = f"{name:<13} {tok_k:>8.1f}  {pct:>3}%"
        if show_cost:
            proj_cost = data.get("total_cost_usd", 0.0)
            row += f"  {proj_cost:>7.2f}"
        table_lines.append(row)

    if show_cost:
        table_lines.append(sep)
        total_k = total_tokens / 1000
        table_lines.append(f"{'TOTAL':<13} {total_k:>8.1f}  100%  {total_cost:>7.2f}")

    if overflow > 0:
        table_lines.append(f"(+{overflow} more)")

    inner = "\n".join(table_lines)
    return f"Token spend ({window_label}):\n```\n{inner}\n```"


def _format_type_breakdown(instance_dir: Path, project: str, days: int) -> str:
    """Build a monospace tokens-by-type block for a project detail view.

    Returns empty string when no JSONL data is present for the project.
    """
    try:
        from app import cost_tracker
    except ImportError:
        return ""

    by_project_and_type = cost_tracker.summarize_by_project_and_type(instance_dir, days=days)
    if not by_project_and_type:
        return ""

    # Case-insensitive lookup
    project_lower = project.lower()
    type_data = None
    for key, val in by_project_and_type.items():
        if key.lower() == project_lower:
            type_data = val
            break

    if not type_data:
        return ""

    total_tokens = sum(
        v["input_tokens"] + v["output_tokens"]
        for v in type_data.values()
    )
    if total_tokens == 0:
        return ""

    total_cost = sum(v.get("total_cost_usd", 0.0) for v in type_data.values())
    show_cost = total_cost > 0

    sorted_types = sorted(
        type_data.items(),
        key=lambda x: -(x[1]["input_tokens"] + x[1]["output_tokens"]),
    )[:10]

    header_parts = ["type          count  tokens(K)   %"]
    if show_cost:
        header_parts.append("  cost($)")
    header = "".join(header_parts)
    sep = "-" * len(header)
    table_lines = [header, sep]
    for mtype, data in sorted_types:
        tok = data["input_tokens"] + data["output_tokens"]
        tok_k = tok / 1000
        count = data["count"]
        pct = int(tok / max(1, total_tokens) * 100)
        name = mtype[:13] + ("…" if len(mtype) > 13 else "")
        row = f"{name:<14} {count:>5}  {tok_k:>8.1f}  {pct:>3}%"
        if show_cost:
            type_cost = data.get("total_cost_usd", 0.0)
            row += f"  {type_cost:>7.2f}"
        table_lines.append(row)

    inner = "\n".join(table_lines)
    window_label = "30d" if days == 30 else "7d"
    return f"Tokens by type ({window_label}):\n```\n{inner}\n```"


def _consecutive_non_productive(outcomes: list) -> int:
    """Count consecutive non-productive sessions from the end."""
    count = 0
    for o in reversed(outcomes):
        if o.get("outcome") == "productive":
            break
        count += 1
    return count


def _productive_streak(outcomes: list) -> int:
    """Count consecutive productive sessions from the end."""
    count = 0
    for o in reversed(outcomes):
        if o.get("outcome") != "productive":
            break
        count += 1
    return count


def _filter_by_period(outcomes: list, period: str,
                      now: datetime = None) -> list:
    """Filter outcomes by time period.

    Args:
        outcomes: List of outcome dicts with 'timestamp' field.
        period: One of "today", "week", "last_week".
        now: Override current time (for testing).

    Returns:
        Filtered list of outcomes within the period.
    """
    if now is None:
        now = datetime.now()

    if period == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = None
    elif period == "week":
        # Monday of current week at midnight
        cutoff = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = None
    elif period == "last_week":
        this_monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cutoff = this_monday - timedelta(days=7)
        end = this_monday
    else:
        return outcomes

    filtered = []
    for o in outcomes:
        ts_str = o.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        if ts >= cutoff and (end is None or ts < end):
            filtered.append(o)
    return filtered


def _format_period_line(outcomes: list, label: str,
                        now: datetime = None) -> str:
    """Format a single time-period summary line.

    Returns empty string if no sessions in the period.
    """
    if not outcomes:
        return ""
    total = len(outcomes)
    productive = sum(1 for o in outcomes if o.get("outcome") == "productive")
    pct = int(productive / max(1, total) * 100)
    return f"  {label}: {total} sessions ({pct}% productive)"


def _format_cost_cache_summary(instance_dir: Path, days: int) -> str:
    """Build a one-line cost + cache summary for the overview header.

    Returns empty string when no usage data exists.
    """
    try:
        from app import cost_tracker
    except ImportError:
        return ""

    summary = _get_range_summary(instance_dir, days)
    if not summary or summary["count"] == 0:
        return ""

    parts = []

    total_cost = summary.get("total_cost_usd", 0.0)
    if total_cost > 0:
        parts.append(f"${total_cost:.2f}")

    cache_read = summary.get("cache_read_input_tokens", 0)
    cache_create = summary.get("cache_creation_input_tokens", 0)
    if cache_read or cache_create:
        hit_rate = summary.get("cache_hit_rate", 0.0)
        parts.append(f"cache {hit_rate:.0%} hit")

    if not parts:
        return ""

    return "  " + " | ".join(parts)


def _format_project_cost(instance_dir: Path, project: str,
                         days: int, total_sessions: int,
                         productive_sessions: int) -> str:
    """Build a cost + cache line for a project detail view.

    Returns empty string when no cost data exists for the project.
    """
    try:
        from app import cost_tracker
    except ImportError:
        return ""

    by_project = cost_tracker.summarize_by_project(instance_dir, days=days)
    if not by_project:
        return ""

    project_lower = project.lower()
    data = None
    for key, val in by_project.items():
        if key.lower() == project_lower:
            data = val
            break

    if not data:
        return ""

    total_cost = data.get("total_cost_usd", 0.0)
    cache_read = data.get("cache_read_input_tokens", 0)
    cache_create = data.get("cache_creation_input_tokens", 0)

    if total_cost <= 0 and not cache_read and not cache_create:
        return ""

    parts = []
    if total_cost > 0:
        cost_str = f"${total_cost:.2f}"
        if total_sessions > 0:
            avg = total_cost / total_sessions
            cost_str += f" (${avg:.2f}/session"
            if productive_sessions > 0:
                cost_per_prod = total_cost / productive_sessions
                cost_str += f", ${cost_per_prod:.2f}/productive"
            cost_str += ")"
        parts.append(cost_str)

    if cache_read or cache_create:
        from app.token_parser import compute_cache_hit_rate
        inp = data.get("input_tokens", 0)
        hit_rate = compute_cache_hit_rate(inp, cache_read, cache_create)
        parts.append(f"cache {hit_rate:.0%} hit")

    if not parts:
        return ""

    return "  Cost: " + " | ".join(parts)


def _get_range_summary(instance_dir: Path, days: int) -> Optional[dict]:
    """Load the aggregated usage summary for a date range."""
    try:
        from app import cost_tracker
        end = date.today()
        start = end - timedelta(days=days - 1)
        return cost_tracker.summarize_range(instance_dir, start, end)
    except (ImportError, Exception):
        return None
