"""Kōan — Daily metrics snapshot for efficient historical queries.

Pre-aggregates per-day metrics from JSONL usage data and session outcomes
into lightweight JSON snapshots. Queries over date ranges read O(days)
snapshot files instead of scanning all raw JSONL entries.

Storage: instance/metrics/YYYY-MM-DD.json (one file per day).

Integration points:
- Write: mission_runner.run_post_mission() calls update_daily_snapshot()
  after each session completes.
- Read: dashboard, /stats command, and weekly/monthly report generators
  call read_metrics_range() for pre-aggregated data.
- Backfill: backfill_snapshots() rebuilds from raw JSONL + session_outcomes
  for days that have no snapshot yet.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from app import cost_tracker, session_tracker
from app.utils import atomic_write


def _metrics_dir(instance_dir: Path) -> Path:
    """Return the metrics directory path, creating it if needed."""
    d = Path(instance_dir) / "metrics"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshot_path(instance_dir: Path, d: date) -> Path:
    """Return the snapshot file path for a given date."""
    return _metrics_dir(instance_dir) / f"{d.isoformat()}.json"


def _build_snapshot(instance_dir: Path, d: date) -> dict:
    """Build a snapshot dict for a given date from raw data sources.

    Aggregates:
    - Token usage from instance/usage/{date}.jsonl (via cost_tracker)
    - Session outcomes from instance/session_outcomes.json

    Args:
        instance_dir: Path to instance directory.
        d: The date to snapshot.

    Returns:
        Snapshot dict with date, tokens, and missions sections.
    """
    instance_dir = Path(instance_dir)

    # Token usage from JSONL
    usage_summary = cost_tracker.summarize_day(instance_dir, d)

    # Session outcomes for this date
    outcomes_path = instance_dir / "session_outcomes.json"
    all_outcomes = session_tracker.load_outcomes(outcomes_path)
    date_str = d.isoformat()
    day_outcomes = [
        o for o in all_outcomes
        if o.get("timestamp", "").startswith(date_str)
    ]

    # Aggregate outcomes
    by_outcome = {}
    by_type = {}
    by_project_missions = {}
    total_duration = 0
    for o in day_outcomes:
        outcome = o.get("outcome", "unknown")
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

        mtype = o.get("mission_type", "unknown")
        by_type[mtype] = by_type.get(mtype, 0) + 1

        project = o.get("project", "_global")
        if project not in by_project_missions:
            by_project_missions[project] = {
                "total": 0, "productive": 0, "by_type": {},
            }
        by_project_missions[project]["total"] += 1
        if outcome == "productive":
            by_project_missions[project]["productive"] += 1
        ptype = by_project_missions[project]["by_type"]
        ptype[mtype] = ptype.get(mtype, 0) + 1

        total_duration += o.get("duration_minutes", 0)

    return {
        "date": date_str,
        "missions": {
            "total": len(day_outcomes),
            "by_outcome": by_outcome,
            "by_type": by_type,
            "by_project": by_project_missions,
            "total_duration_minutes": total_duration,
        },
        "tokens": {
            "total_input": usage_summary["total_input"],
            "total_output": usage_summary["total_output"],
            "total_cost_usd": round(usage_summary.get("total_cost_usd", 0.0), 6),
            "cache_creation_input_tokens": usage_summary.get(
                "cache_creation_input_tokens", 0
            ),
            "cache_read_input_tokens": usage_summary.get(
                "cache_read_input_tokens", 0
            ),
            "cache_hit_rate": round(
                usage_summary.get("cache_hit_rate", 0.0), 4
            ),
            "count": usage_summary["count"],
            "by_project": usage_summary.get("by_project", {}),
            "by_model": usage_summary.get("by_model", {}),
        },
    }


def update_daily_snapshot(instance_dir: Path, d: Optional[date] = None) -> bool:
    """Write or overwrite the daily snapshot for a given date.

    Called after each mission completes to keep the day's snapshot fresh.
    Rebuilds from raw data every time (idempotent).

    Args:
        instance_dir: Path to instance directory.
        d: Date to snapshot (defaults to today).

    Returns:
        True if the snapshot was written successfully.
    """
    if d is None:
        d = date.today()
    instance_dir = Path(instance_dir)

    snapshot = _build_snapshot(instance_dir, d)
    path = _snapshot_path(instance_dir, d)

    try:
        content = json.dumps(snapshot, indent=2, separators=(",", ": "))
        atomic_write(path, content)
        return True
    except OSError:
        return False


def read_daily_snapshot(
    instance_dir: Path, d: date, backfill: bool = True
) -> Optional[dict]:
    """Read the snapshot for a single day.

    Args:
        instance_dir: Path to instance directory.
        d: Date to read.
        backfill: If True and snapshot doesn't exist, build it from raw data.

    Returns:
        Snapshot dict, or None if no data exists for that day.
    """
    instance_dir = Path(instance_dir)
    path = _snapshot_path(instance_dir, d)

    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if backfill:
        # Check if there's any raw data for this day before building
        usage_dir = instance_dir / "usage"
        jsonl_path = usage_dir / f"{d.isoformat()}.jsonl"
        has_usage = jsonl_path.exists()

        # Check session outcomes for this day
        outcomes_path = instance_dir / "session_outcomes.json"
        has_outcomes = False
        if outcomes_path.exists():
            all_outcomes = session_tracker.load_outcomes(outcomes_path)
            date_str = d.isoformat()
            has_outcomes = any(
                o.get("timestamp", "").startswith(date_str) for o in all_outcomes
            )

        if has_usage or has_outcomes:
            snapshot = _build_snapshot(instance_dir, d)
            # Write it for next time
            try:
                path = _snapshot_path(instance_dir, d)
                content = json.dumps(snapshot, indent=2, separators=(",", ": "))
                atomic_write(path, content)
            except OSError:
                pass
            return snapshot

    return None


def read_metrics_range(
    instance_dir: Path,
    start: date,
    end: date,
    backfill: bool = True,
) -> dict:
    """Load and merge snapshots for a date range.

    O(days) reads instead of scanning all raw JSONL entries.

    Args:
        instance_dir: Path to instance directory.
        start: Start date (inclusive).
        end: End date (inclusive).
        backfill: If True, build missing snapshots from raw data on access.

    Returns:
        Merged dict with aggregated tokens and missions data.
    """
    merged = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": 0,
        "missions": {
            "total": 0,
            "by_outcome": {},
            "by_type": {},
            "by_project": {},
            "total_duration_minutes": 0,
        },
        "tokens": {
            "total_input": 0,
            "total_output": 0,
            "total_cost_usd": 0.0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "count": 0,
            "by_project": {},
            "by_model": {},
        },
        "daily": [],
    }

    current = start
    while current <= end:
        snapshot = read_daily_snapshot(instance_dir, current, backfill=backfill)
        if snapshot is None:
            current += timedelta(days=1)
            continue

        merged["days"] += 1

        # Merge missions
        m = snapshot.get("missions", {})
        merged["missions"]["total"] += m.get("total", 0)
        merged["missions"]["total_duration_minutes"] += m.get(
            "total_duration_minutes", 0
        )
        for k, v in m.get("by_outcome", {}).items():
            merged["missions"]["by_outcome"][k] = (
                merged["missions"]["by_outcome"].get(k, 0) + v
            )
        for k, v in m.get("by_type", {}).items():
            merged["missions"]["by_type"][k] = (
                merged["missions"]["by_type"].get(k, 0) + v
            )
        for proj, data in m.get("by_project", {}).items():
            if proj not in merged["missions"]["by_project"]:
                merged["missions"]["by_project"][proj] = {
                    "total": 0, "productive": 0, "by_type": {},
                }
            mp = merged["missions"]["by_project"][proj]
            mp["total"] += data.get("total", 0)
            mp["productive"] += data.get("productive", 0)
            for t, c in data.get("by_type", {}).items():
                mp["by_type"][t] = mp["by_type"].get(t, 0) + c

        # Merge tokens
        t = snapshot.get("tokens", {})
        merged["tokens"]["total_input"] += t.get("total_input", 0)
        merged["tokens"]["total_output"] += t.get("total_output", 0)
        merged["tokens"]["total_cost_usd"] += t.get("total_cost_usd", 0.0)
        merged["tokens"]["cache_creation_input_tokens"] += t.get(
            "cache_creation_input_tokens", 0
        )
        merged["tokens"]["cache_read_input_tokens"] += t.get(
            "cache_read_input_tokens", 0
        )
        merged["tokens"]["count"] += t.get("count", 0)

        # Merge by_project tokens
        for proj, data in t.get("by_project", {}).items():
            if proj not in merged["tokens"]["by_project"]:
                merged["tokens"]["by_project"][proj] = {
                    "input_tokens": 0, "output_tokens": 0, "count": 0,
                }
            tp = merged["tokens"]["by_project"][proj]
            tp["input_tokens"] += data.get("input_tokens", 0)
            tp["output_tokens"] += data.get("output_tokens", 0)
            tp["count"] += data.get("count", 0)

        # Merge by_model tokens
        for model, data in t.get("by_model", {}).items():
            if model not in merged["tokens"]["by_model"]:
                merged["tokens"]["by_model"][model] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "total_cost_usd": 0.0,
                    "count": 0,
                }
            tm = merged["tokens"]["by_model"][model]
            tm["input_tokens"] += data.get("input_tokens", 0)
            tm["output_tokens"] += data.get("output_tokens", 0)
            tm["cache_creation_input_tokens"] += data.get(
                "cache_creation_input_tokens", 0
            )
            tm["cache_read_input_tokens"] += data.get(
                "cache_read_input_tokens", 0
            )
            tm["total_cost_usd"] += data.get("total_cost_usd", 0.0)
            tm["count"] += data.get("count", 0)

        # Add daily summary for time-series views
        merged["daily"].append({
            "date": snapshot["date"],
            "mission_count": m.get("total", 0),
            "token_count": t.get("count", 0),
            "total_input": t.get("total_input", 0),
            "total_output": t.get("total_output", 0),
            "cost_usd": t.get("total_cost_usd", 0.0),
        })

        current += timedelta(days=1)

    # Compute aggregate cache hit rate
    total_cache = (
        merged["tokens"]["cache_read_input_tokens"]
        + merged["tokens"]["cache_creation_input_tokens"]
    )
    total_all = merged["tokens"]["total_input"] + total_cache
    if total_all > 0 and total_cache > 0:
        merged["tokens"]["cache_hit_rate"] = round(
            merged["tokens"]["cache_read_input_tokens"] / total_all, 4
        )
    else:
        merged["tokens"]["cache_hit_rate"] = 0.0

    merged["tokens"]["total_cost_usd"] = round(
        merged["tokens"]["total_cost_usd"], 6
    )

    return merged


def backfill_snapshots(
    instance_dir: Path,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> int:
    """Generate snapshots from existing raw data for days without one.

    Scans the usage/ directory to discover dates with JSONL data and
    creates snapshots for any that don't already have one.

    Args:
        instance_dir: Path to instance directory.
        start: Earliest date to backfill (defaults to earliest JSONL file).
        end: Latest date to backfill (defaults to today).

    Returns:
        Number of snapshots created.
    """
    instance_dir = Path(instance_dir)
    usage_dir = instance_dir / "usage"

    if not usage_dir.exists():
        return 0

    # Discover dates with data
    dates_with_data = set()
    for f in usage_dir.glob("*.jsonl"):
        try:
            d = date.fromisoformat(f.stem)
            dates_with_data.add(d)
        except ValueError:
            continue

    # Also check session_outcomes for dates
    outcomes_path = instance_dir / "session_outcomes.json"
    if outcomes_path.exists():
        all_outcomes = session_tracker.load_outcomes(outcomes_path)
        for o in all_outcomes:
            ts = o.get("timestamp", "")
            if len(ts) >= 10:
                try:
                    d = date.fromisoformat(ts[:10])
                    dates_with_data.add(d)
                except ValueError:
                    continue

    if not dates_with_data:
        return 0

    if start is None:
        start = min(dates_with_data)
    if end is None:
        end = date.today()

    created = 0
    for d in sorted(dates_with_data):
        if d < start or d > end:
            continue
        path = _snapshot_path(instance_dir, d)
        if path.exists():
            continue
        if update_daily_snapshot(instance_dir, d):
            created += 1

    return created
