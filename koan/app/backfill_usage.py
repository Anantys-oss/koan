#!/usr/bin/env python3
"""Backfill historical usage activity from session outcomes.

The dashboard ``/usage`` page reads daily mission activity exclusively from
``instance/usage/YYYY-MM-DD.jsonl``. Skill-dispatch missions (e.g. ``/review``)
historically failed token extraction and wrote no usage row, so those days show
no activity even though ``instance/session_outcomes.json`` recorded the work.

This one-shot maintenance tool reconstructs the missing per-day activity by
writing synthetic, zero-cost usage rows derived from ``session_outcomes.json``.
Token and cost values are unrecoverable, so they are recorded as 0 / "unknown":
the goal is restoring the per-day activity *count* (and per-project / per-type
breakdowns), not historical spend.

Synthetic rows carry a ``"backfill": true`` marker. ``cost_tracker`` readers
ignore the marker (it is not a known field), but it makes synthetic rows
distinguishable from real ones — which is what keeps this tool idempotent and
prevents double-counting once the forward fix starts writing real rows.

Usage:
    python -m app.backfill_usage [--apply] [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Without --apply, runs in dry-run mode (shows what would change).
"""

import argparse
import fcntl
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from app import cost_tracker

# Marker key stamped on synthetic rows. Real usage rows never set this.
BACKFILL_MARKER = "backfill"

# Default window start — the first day the usage JSONL gap began.
DEFAULT_START = date(2026, 5, 30)


def get_koan_root() -> Path:
    root = os.environ.get("KOAN_ROOT")
    if not root:
        print("Error: KOAN_ROOT not set", file=sys.stderr)
        sys.exit(1)
    return Path(root)


def load_outcomes(outcomes_path: Path) -> list:
    """Load the session outcomes list. Returns [] when missing or malformed."""
    if not outcomes_path.exists():
        return []
    try:
        data = json.loads(outcomes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _outcome_date(outcome: dict) -> Optional[date]:
    """Parse the calendar date from an outcome timestamp; None if unparseable."""
    ts = outcome.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        return date.fromisoformat(ts[:10])
    except ValueError:
        return None


def group_outcomes_by_date(
    outcomes: list, start: date, end: date
) -> "dict[date, list]":
    """Group outcomes by calendar date within [start, end] (inclusive).

    Each day's list is ordered by timestamp for deterministic backfill.
    """
    grouped: "dict[date, list]" = {}
    for o in outcomes:
        d = _outcome_date(o)
        if d is None or d < start or d > end:
            continue
        grouped.setdefault(d, []).append(o)
    for day_outcomes in grouped.values():
        day_outcomes.sort(key=lambda o: o.get("timestamp", ""))
    return grouped


def _synthetic_entry(outcome: dict) -> dict:
    """Build a synthetic usage row from an outcome, matching the real schema.

    Mirrors ``cost_tracker.record_usage``'s entry shape (omitting empty optional
    fields), with zero tokens/cost and the backfill marker appended.
    """
    entry = {
        "ts": outcome.get("timestamp"),
        "project": outcome.get("project") or "_global",
        "model": "unknown",
        "input_tokens": 0,
        "output_tokens": 0,
        "mode": outcome.get("mode", ""),
        "mission": outcome.get("summary", ""),
    }
    mission_type = outcome.get("mission_type")
    if mission_type:
        entry["mission_type"] = mission_type
    entry[BACKFILL_MARKER] = True
    return entry


def _serialize(entry: dict) -> str:
    """Match cost_tracker's compact JSONL serialization exactly."""
    return json.dumps(entry, separators=(",", ":")) + "\n"


def plan_day(usage_dir: Path, d: date, day_outcomes: list) -> dict:
    """Compute the backfill action for a single day.

    Credits existing real rows and previously written backfill rows so the tool
    is idempotent and never over-counts:

        target_synthetic = max(0, outcomes - real_rows)
        to_write         = target_synthetic - existing_backfill

    Returns a dict with counts and the list of synthetic entries to append
    (the outcomes already credited are skipped from the front, deterministically).
    """
    existing = cost_tracker._read_jsonl_for_date(usage_dir, d)
    existing_backfill = sum(1 for e in existing if e.get(BACKFILL_MARKER) is True)
    real_rows = len(existing) - existing_backfill

    outcomes_count = len(day_outcomes)
    target_synthetic = max(0, outcomes_count - real_rows)
    to_write = target_synthetic - existing_backfill

    entries = []
    if to_write > 0:
        # Skip outcomes already accounted for (real rows + prior backfill rows),
        # then materialize the remaining ones. Deterministic across re-runs.
        skip = real_rows + existing_backfill
        entries = [_synthetic_entry(o) for o in day_outcomes[skip:skip + to_write]]

    return {
        "date": d,
        "outcomes": outcomes_count,
        "real_rows": real_rows,
        "existing_backfill": existing_backfill,
        "to_write": to_write,
        "entries": entries,
    }


def append_rows(jsonl_path: Path, entries: list) -> bool:
    """Append synthetic rows under an exclusive lock, matching record_usage."""
    if not entries:
        return True
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(_serialize(e) for e in entries)
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        return True
    except OSError:
        return False


def run_backfill(
    instance_dir: Path,
    start: date,
    end: date,
    dry_run: bool = True,
) -> dict:
    """Plan (and optionally apply) the usage backfill for [start, end].

    Returns a summary dict: per-day plans plus totals. When dry_run is False,
    synthetic rows are appended to the per-day JSONL files.
    """
    usage_dir = Path(instance_dir) / "usage"
    outcomes = load_outcomes(Path(instance_dir) / "session_outcomes.json")
    grouped = group_outcomes_by_date(outcomes, start, end)

    plans = []
    current = start
    while current <= end:
        plans.append(plan_day(usage_dir, current, grouped.get(current, [])))
        current += timedelta(days=1)

    written = 0
    over = 0
    for p in plans:
        if p["to_write"] < 0:
            over += 1
            print(
                f"  WARNING {p['date']}: {-p['to_write']} more backfill row(s) "
                f"than outcomes — leaving as-is (no data removed)"
            )
        if not dry_run and p["entries"]:
            jsonl_path = usage_dir / f"{p['date'].isoformat()}.jsonl"
            if append_rows(jsonl_path, p["entries"]):
                written += len(p["entries"])

    total_to_write = sum(max(0, p["to_write"]) for p in plans)
    return {
        "plans": plans,
        "total_to_write": total_to_write,
        "written": written,
        "over_backfilled_days": over,
        "dry_run": dry_run,
    }


def _print_summary(summary: dict) -> None:
    print(f"{'DATE':<12} {'OUTCOMES':>9} {'REAL':>5} {'BACKFILL':>9} {'WOULD WRITE':>12}")
    print("-" * 52)
    for p in summary["plans"]:
        if p["outcomes"] == 0 and p["existing_backfill"] == 0 and p["real_rows"] == 0:
            continue
        print(
            f"{p['date'].isoformat():<12} {p['outcomes']:>9} {p['real_rows']:>5} "
            f"{p['existing_backfill']:>9} {max(0, p['to_write']):>12}"
        )
    print("-" * 52)
    if summary["dry_run"]:
        print(
            f"Total synthetic rows to write: {summary['total_to_write']}\n"
            "\nThis was a dry run. To apply, re-run with --apply"
        )
    else:
        print(f"Wrote {summary['written']} synthetic row(s).")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date (expected YYYY-MM-DD): {value}")


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Backfill historical usage activity from session outcomes."
    )
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument(
        "--start", type=_parse_date, default=DEFAULT_START,
        help=f"Start date YYYY-MM-DD (default: {DEFAULT_START.isoformat()})",
    )
    parser.add_argument(
        "--end", type=_parse_date, default=None,
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--instance-dir", type=Path, default=None,
        help="Instance directory (default: $KOAN_ROOT/instance)",
    )
    args = parser.parse_args(argv)

    instance_dir = args.instance_dir or (get_koan_root() / "instance")
    end = args.end or datetime.now().date()

    if args.start > end:
        print(f"Error: start {args.start} is after end {end}", file=sys.stderr)
        sys.exit(1)

    summary = run_backfill(instance_dir, args.start, end, dry_run=not args.apply)
    _print_summary(summary)


if __name__ == "__main__":
    main()
