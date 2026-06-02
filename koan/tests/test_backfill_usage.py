"""Tests for backfill_usage.py — reconstruct historical usage activity."""

import json
from datetime import date
from pathlib import Path

import pytest

from app import backfill_usage, cost_tracker
from app.backfill_usage import (
    BACKFILL_MARKER,
    group_outcomes_by_date,
    load_outcomes,
    run_backfill,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def instance_dir(tmp_path: Path) -> Path:
    (tmp_path / "usage").mkdir(parents=True)
    return tmp_path


def _write_outcomes(instance_dir: Path, outcomes: list) -> None:
    (instance_dir / "session_outcomes.json").write_text(json.dumps(outcomes))


def _outcome(ts: str, project: str = "my-toolkit", mode: str = "implement", mtype: str = "review") -> dict:
    return {
        "timestamp": ts,
        "project": project,
        "mode": mode,
        "mission_type": mtype,
        "outcome": "productive",
        "summary": f"work at {ts}",
    }


def _write_real_row(usage_dir: Path, d: date, project: str = "my-toolkit") -> None:
    """Append a non-backfill (real) usage row for a date."""
    entry = {
        "ts": f"{d.isoformat()}T10:00:00",
        "project": project,
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 50,
        "mode": "deep",
        "mission": "real work",
    }
    path = usage_dir / f"{d.isoformat()}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------
# Grouping / loading
# ---------------------------------------------------------------------------

def test_load_outcomes_missing_returns_empty(instance_dir: Path):
    assert load_outcomes(instance_dir / "nope.json") == []


def test_group_outcomes_respects_range_and_orders_by_timestamp(instance_dir: Path):
    outcomes = [
        _outcome("2026-05-31T09:00:00"),
        _outcome("2026-06-01T15:00:00"),
        _outcome("2026-06-01T08:00:00"),
        _outcome("2026-06-05T08:00:00"),  # out of range
    ]
    grouped = group_outcomes_by_date(outcomes, date(2026, 6, 1), date(2026, 6, 2))
    assert set(grouped.keys()) == {date(2026, 6, 1)}
    ts = [o["timestamp"] for o in grouped[date(2026, 6, 1)]]
    assert ts == ["2026-06-01T08:00:00", "2026-06-01T15:00:00"]


def test_group_skips_unparseable_timestamps(instance_dir: Path):
    outcomes = [_outcome("not-a-date"), {"project": "x"}, _outcome("2026-06-01T08:00:00")]
    grouped = group_outcomes_by_date(outcomes, date(2026, 6, 1), date(2026, 6, 1))
    assert len(grouped[date(2026, 6, 1)]) == 1


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(instance_dir: Path):
    _write_outcomes(instance_dir, [_outcome("2026-06-01T08:00:00")])
    summary = run_backfill(instance_dir, date(2026, 6, 1), date(2026, 6, 1), dry_run=True)
    assert summary["total_to_write"] == 1
    assert summary["written"] == 0
    assert not (instance_dir / "usage" / "2026-06-01.jsonl").exists()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def test_apply_creates_rows_matching_outcome_count(instance_dir: Path):
    outcomes = [_outcome(f"2026-06-01T0{i}:00:00") for i in range(3)]
    _write_outcomes(instance_dir, outcomes)
    run_backfill(instance_dir, date(2026, 6, 1), date(2026, 6, 1), dry_run=False)

    rows = cost_tracker._read_jsonl_for_date(instance_dir / "usage", date(2026, 6, 1))
    assert len(rows) == 3
    assert all(r[BACKFILL_MARKER] is True for r in rows)
    assert all(r["input_tokens"] == 0 and r["output_tokens"] == 0 for r in rows)
    assert all(r["model"] == "unknown" for r in rows)


def test_apply_is_idempotent(instance_dir: Path):
    outcomes = [_outcome(f"2026-06-01T0{i}:00:00") for i in range(4)]
    _write_outcomes(instance_dir, outcomes)

    run_backfill(instance_dir, date(2026, 6, 1), date(2026, 6, 1), dry_run=False)
    second = run_backfill(instance_dir, date(2026, 6, 1), date(2026, 6, 1), dry_run=False)

    assert second["written"] == 0
    rows = cost_tracker._read_jsonl_for_date(instance_dir / "usage", date(2026, 6, 1))
    assert len(rows) == 4  # no duplicates on re-run


def test_real_rows_offset_synthetic_need(instance_dir: Path):
    # 5 outcomes, 2 real rows already present -> exactly 3 synthetic rows.
    outcomes = [_outcome(f"2026-06-01T0{i}:00:00") for i in range(5)]
    _write_outcomes(instance_dir, outcomes)
    usage_dir = instance_dir / "usage"
    _write_real_row(usage_dir, date(2026, 6, 1))
    _write_real_row(usage_dir, date(2026, 6, 1))

    run_backfill(instance_dir, date(2026, 6, 1), date(2026, 6, 1), dry_run=False)

    rows = cost_tracker._read_jsonl_for_date(usage_dir, date(2026, 6, 1))
    synthetic = [r for r in rows if r.get(BACKFILL_MARKER) is True]
    assert len(rows) == 5  # 2 real + 3 synthetic == outcome count
    assert len(synthetic) == 3


def test_over_backfilled_day_left_untouched(instance_dir: Path):
    # More existing backfill rows than outcomes: leave as-is, write nothing.
    outcomes = [_outcome("2026-06-01T01:00:00")]
    _write_outcomes(instance_dir, outcomes)
    # Simulate prior over-backfill by pre-writing 3 marked rows.
    usage_dir = instance_dir / "usage"
    path = usage_dir / "2026-06-01.jsonl"
    for i in range(3):
        backfill_usage.append_rows(path, [{"ts": f"2026-06-01T0{i}:00:00", BACKFILL_MARKER: True}])

    summary = run_backfill(instance_dir, date(2026, 6, 1), date(2026, 6, 1), dry_run=False)
    assert summary["over_backfilled_days"] == 1
    assert summary["written"] == 0
    rows = cost_tracker._read_jsonl_for_date(usage_dir, date(2026, 6, 1))
    assert len(rows) == 3  # nothing removed


# ---------------------------------------------------------------------------
# Integration with daily_series (what the dashboard reads)
# ---------------------------------------------------------------------------

def test_daily_series_count_reflects_backfill(instance_dir: Path):
    outcomes = (
        [_outcome("2026-05-31T08:00:00", project="my-toolkit")]
        + [_outcome(f"2026-06-01T0{i}:00:00", project="proj-b") for i in range(3)]
    )
    _write_outcomes(instance_dir, outcomes)
    run_backfill(instance_dir, date(2026, 5, 31), date(2026, 6, 1), dry_run=False)

    series = cost_tracker.daily_series(instance_dir, date(2026, 5, 31), date(2026, 6, 1))
    by_date = {e["date"]: e for e in series}
    assert by_date["2026-05-31"]["count"] == 1
    assert by_date["2026-06-01"]["count"] == 3
    # Backfilled days contribute activity but zero tokens.
    assert by_date["2026-06-01"]["total_input"] == 0
    assert by_date["2026-06-01"]["total_output"] == 0


def test_synthetic_rows_preserve_project_for_breakdown(instance_dir: Path):
    outcomes = [
        _outcome("2026-06-01T01:00:00", project="my-toolkit"),
        _outcome("2026-06-01T02:00:00", project="proj-b"),
    ]
    _write_outcomes(instance_dir, outcomes)
    run_backfill(instance_dir, date(2026, 6, 1), date(2026, 6, 1), dry_run=False)

    # One synthetic row per outcome, each carrying its originating project so
    # per-project dashboard breakdowns stay correct.
    rows = cost_tracker._read_jsonl_for_date(instance_dir / "usage", date(2026, 6, 1))
    assert {r["project"] for r in rows} == {"my-toolkit", "proj-b"}
