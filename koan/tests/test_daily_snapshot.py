"""Tests for app.daily_snapshot — daily metrics snapshot system."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import daily_snapshot
from app.cost_tracker import record_usage
from app.session_tracker import record_outcome


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory structure."""
    (tmp_path / "usage").mkdir()
    (tmp_path / "metrics").mkdir()
    return tmp_path


def _record_usage(instance_dir, project="testproj", model="claude-sonnet-4-20250514",
                  input_tokens=1000, output_tokens=500, **kwargs):
    """Helper to record a usage event."""
    return record_usage(
        instance_dir=instance_dir,
        project=project,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        **kwargs,
    )


def _record_outcome(instance_dir, project="testproj", mode="implement",
                    duration_minutes=10, journal_content="branch pushed, PR #1",
                    mission_title="/implement fix"):
    """Helper to record a session outcome."""
    return record_outcome(
        instance_dir=str(instance_dir),
        project=project,
        mode=mode,
        duration_minutes=duration_minutes,
        journal_content=journal_content,
        mission_title=mission_title,
    )


class TestUpdateDailySnapshot:
    """Test writing daily snapshots."""

    def test_writes_snapshot_file(self, instance_dir):
        """Snapshot file is created in metrics/ directory."""
        _record_usage(instance_dir)
        today = date.today()

        result = daily_snapshot.update_daily_snapshot(instance_dir, today)

        assert result is True
        snapshot_path = instance_dir / "metrics" / f"{today.isoformat()}.json"
        assert snapshot_path.exists()

    def test_snapshot_contains_token_data(self, instance_dir):
        """Snapshot aggregates token usage from JSONL."""
        _record_usage(instance_dir, input_tokens=1500, output_tokens=700)
        _record_usage(instance_dir, input_tokens=2000, output_tokens=300)
        today = date.today()

        daily_snapshot.update_daily_snapshot(instance_dir, today)

        snapshot = json.loads(
            (instance_dir / "metrics" / f"{today.isoformat()}.json").read_text()
        )
        assert snapshot["tokens"]["total_input"] == 3500
        assert snapshot["tokens"]["total_output"] == 1000
        assert snapshot["tokens"]["count"] == 2

    def test_snapshot_contains_mission_data(self, instance_dir):
        """Snapshot aggregates session outcomes."""
        _record_outcome(instance_dir, journal_content="branch pushed, PR #42")
        _record_outcome(instance_dir, journal_content="verification session, no code",
                        mission_title="")
        today = date.today()

        daily_snapshot.update_daily_snapshot(instance_dir, today)

        snapshot = json.loads(
            (instance_dir / "metrics" / f"{today.isoformat()}.json").read_text()
        )
        assert snapshot["missions"]["total"] == 2
        assert snapshot["missions"]["by_outcome"]["productive"] == 1
        assert snapshot["missions"]["by_outcome"]["empty"] == 1

    def test_snapshot_by_project_missions(self, instance_dir):
        """Snapshot tracks per-project mission counts."""
        _record_outcome(instance_dir, project="alpha",
                        journal_content="branch pushed")
        _record_outcome(instance_dir, project="alpha",
                        journal_content="branch pushed")
        _record_outcome(instance_dir, project="beta",
                        journal_content="branch pushed")
        today = date.today()

        daily_snapshot.update_daily_snapshot(instance_dir, today)

        snapshot = json.loads(
            (instance_dir / "metrics" / f"{today.isoformat()}.json").read_text()
        )
        by_proj = snapshot["missions"]["by_project"]
        assert by_proj["alpha"]["total"] == 2
        assert by_proj["beta"]["total"] == 1

    def test_snapshot_by_type(self, instance_dir):
        """Snapshot tracks mission type breakdown."""
        _record_outcome(instance_dir, mission_title="/implement fix")
        _record_outcome(instance_dir, mission_title="")  # autonomous
        today = date.today()

        daily_snapshot.update_daily_snapshot(instance_dir, today)

        snapshot = json.loads(
            (instance_dir / "metrics" / f"{today.isoformat()}.json").read_text()
        )
        assert snapshot["missions"]["by_type"]["skill"] == 1
        assert snapshot["missions"]["by_type"]["autonomous"] == 1

    def test_snapshot_is_idempotent(self, instance_dir):
        """Calling update twice produces the same snapshot."""
        _record_usage(instance_dir, input_tokens=1000)
        today = date.today()

        daily_snapshot.update_daily_snapshot(instance_dir, today)
        daily_snapshot.update_daily_snapshot(instance_dir, today)

        snapshot = json.loads(
            (instance_dir / "metrics" / f"{today.isoformat()}.json").read_text()
        )
        # Should NOT double-count
        assert snapshot["tokens"]["total_input"] == 1000

    def test_snapshot_for_empty_day(self, instance_dir):
        """Snapshot for a day with no data is still valid."""
        yesterday = date.today() - timedelta(days=1)

        result = daily_snapshot.update_daily_snapshot(instance_dir, yesterday)

        assert result is True
        snapshot = json.loads(
            (instance_dir / "metrics" / f"{yesterday.isoformat()}.json").read_text()
        )
        assert snapshot["missions"]["total"] == 0
        assert snapshot["tokens"]["count"] == 0

    def test_snapshot_includes_cache_data(self, instance_dir):
        """Snapshot captures cache metrics."""
        _record_usage(
            instance_dir,
            input_tokens=1000,
            output_tokens=500,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=800,
        )
        today = date.today()

        daily_snapshot.update_daily_snapshot(instance_dir, today)

        snapshot = json.loads(
            (instance_dir / "metrics" / f"{today.isoformat()}.json").read_text()
        )
        assert snapshot["tokens"]["cache_creation_input_tokens"] == 200
        assert snapshot["tokens"]["cache_read_input_tokens"] == 800
        assert snapshot["tokens"]["cache_hit_rate"] > 0

    def test_snapshot_includes_cost_data(self, instance_dir):
        """Snapshot captures cost data."""
        _record_usage(instance_dir, cost_usd=0.0025)
        _record_usage(instance_dir, cost_usd=0.0015)
        today = date.today()

        daily_snapshot.update_daily_snapshot(instance_dir, today)

        snapshot = json.loads(
            (instance_dir / "metrics" / f"{today.isoformat()}.json").read_text()
        )
        assert snapshot["tokens"]["total_cost_usd"] == pytest.approx(0.004, abs=1e-6)

    def test_snapshot_duration_minutes(self, instance_dir):
        """Snapshot tracks total duration from session outcomes."""
        _record_outcome(instance_dir, duration_minutes=15,
                        journal_content="branch pushed")
        _record_outcome(instance_dir, duration_minutes=8,
                        journal_content="branch pushed")
        today = date.today()

        daily_snapshot.update_daily_snapshot(instance_dir, today)

        snapshot = json.loads(
            (instance_dir / "metrics" / f"{today.isoformat()}.json").read_text()
        )
        assert snapshot["missions"]["total_duration_minutes"] == 23


class TestReadDailySnapshot:
    """Test reading snapshots."""

    def test_reads_existing_snapshot(self, instance_dir):
        """Reads a previously written snapshot."""
        _record_usage(instance_dir, input_tokens=2000)
        today = date.today()
        daily_snapshot.update_daily_snapshot(instance_dir, today)

        result = daily_snapshot.read_daily_snapshot(instance_dir, today)

        assert result is not None
        assert result["tokens"]["total_input"] == 2000

    def test_backfills_missing_snapshot(self, instance_dir):
        """Builds and caches snapshot from raw data when missing."""
        _record_usage(instance_dir, input_tokens=3000)
        today = date.today()

        # No snapshot exists yet
        result = daily_snapshot.read_daily_snapshot(
            instance_dir, today, backfill=True
        )

        assert result is not None
        assert result["tokens"]["total_input"] == 3000
        # Should have been cached
        snapshot_path = instance_dir / "metrics" / f"{today.isoformat()}.json"
        assert snapshot_path.exists()

    def test_no_backfill_returns_none(self, instance_dir):
        """Returns None when backfill=False and no snapshot exists."""
        yesterday = date.today() - timedelta(days=1)

        result = daily_snapshot.read_daily_snapshot(
            instance_dir, yesterday, backfill=False
        )

        assert result is None

    def test_no_data_no_backfill(self, instance_dir):
        """Returns None when no raw data exists for the day."""
        old_date = date(2020, 1, 1)

        result = daily_snapshot.read_daily_snapshot(
            instance_dir, old_date, backfill=True
        )

        assert result is None


class TestReadMetricsRange:
    """Test reading and merging snapshots over a date range."""

    def test_merges_multiple_days(self, instance_dir):
        """Merges snapshots across multiple days."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        # Write snapshots for two days
        # Today
        _record_usage(instance_dir, input_tokens=1000, output_tokens=500)
        _record_outcome(instance_dir, journal_content="branch pushed")
        daily_snapshot.update_daily_snapshot(instance_dir, today)

        # For yesterday, we write a fake snapshot directly
        yesterday_snapshot = {
            "date": yesterday.isoformat(),
            "missions": {
                "total": 3,
                "by_outcome": {"productive": 2, "empty": 1},
                "by_type": {"skill": 2, "autonomous": 1},
                "by_project": {
                    "testproj": {"total": 3, "productive": 2, "by_type": {"skill": 2, "autonomous": 1}},
                },
                "total_duration_minutes": 30,
            },
            "tokens": {
                "total_input": 5000,
                "total_output": 2000,
                "total_cost_usd": 0.01,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_hit_rate": 0.0,
                "count": 3,
                "by_project": {
                    "testproj": {"input_tokens": 5000, "output_tokens": 2000, "count": 3},
                },
                "by_model": {},
            },
        }
        (instance_dir / "metrics" / f"{yesterday.isoformat()}.json").write_text(
            json.dumps(yesterday_snapshot)
        )

        result = daily_snapshot.read_metrics_range(
            instance_dir, yesterday, today, backfill=False
        )

        assert result["days"] == 2
        assert result["tokens"]["total_input"] == 6000  # 5000 + 1000
        assert result["tokens"]["total_output"] == 2500  # 2000 + 500
        assert result["missions"]["total"] >= 4  # 3 + at least 1
        assert len(result["daily"]) == 2

    def test_empty_range(self, instance_dir):
        """Returns zero-value merged dict for range with no data."""
        old_start = date(2020, 1, 1)
        old_end = date(2020, 1, 7)

        result = daily_snapshot.read_metrics_range(
            instance_dir, old_start, old_end, backfill=False
        )

        assert result["days"] == 0
        assert result["tokens"]["total_input"] == 0
        assert result["missions"]["total"] == 0
        assert result["daily"] == []

    def test_single_day_range(self, instance_dir):
        """Range of one day returns that day's data."""
        today = date.today()
        _record_usage(instance_dir, input_tokens=4000)
        daily_snapshot.update_daily_snapshot(instance_dir, today)

        result = daily_snapshot.read_metrics_range(
            instance_dir, today, today, backfill=False
        )

        assert result["days"] == 1
        assert result["tokens"]["total_input"] == 4000

    def test_merges_by_project_tokens(self, instance_dir):
        """Per-project token totals are merged across days."""
        today = date.today()

        _record_usage(instance_dir, project="alpha", input_tokens=1000, output_tokens=400)
        _record_usage(instance_dir, project="beta", input_tokens=2000, output_tokens=600)
        daily_snapshot.update_daily_snapshot(instance_dir, today)

        result = daily_snapshot.read_metrics_range(
            instance_dir, today, today, backfill=False
        )

        by_proj = result["tokens"]["by_project"]
        assert by_proj["alpha"]["input_tokens"] == 1000
        assert by_proj["beta"]["input_tokens"] == 2000

    def test_daily_series_in_result(self, instance_dir):
        """Result includes per-day summary entries."""
        today = date.today()
        _record_usage(instance_dir, input_tokens=1500, output_tokens=700)
        daily_snapshot.update_daily_snapshot(instance_dir, today)

        result = daily_snapshot.read_metrics_range(
            instance_dir, today, today, backfill=False
        )

        assert len(result["daily"]) == 1
        day = result["daily"][0]
        assert day["date"] == today.isoformat()
        assert day["total_input"] == 1500
        assert day["total_output"] == 700


class TestBackfillSnapshots:
    """Test bulk backfill of snapshots from raw data."""

    def test_backfills_from_jsonl(self, instance_dir):
        """Creates snapshots for days with JSONL data."""
        today = date.today()
        _record_usage(instance_dir, input_tokens=2500)

        count = daily_snapshot.backfill_snapshots(instance_dir)

        assert count == 1
        snapshot_path = instance_dir / "metrics" / f"{today.isoformat()}.json"
        assert snapshot_path.exists()

    def test_skips_existing_snapshots(self, instance_dir):
        """Does not overwrite existing snapshot files."""
        today = date.today()
        _record_usage(instance_dir, input_tokens=2500)

        # Create snapshot first
        daily_snapshot.update_daily_snapshot(instance_dir, today)

        # Backfill should skip it
        count = daily_snapshot.backfill_snapshots(instance_dir)
        assert count == 0

    def test_empty_usage_dir(self, instance_dir):
        """Returns 0 when no JSONL files exist."""
        count = daily_snapshot.backfill_snapshots(instance_dir)
        assert count == 0

    def test_respects_date_range(self, instance_dir):
        """Only backfills within the specified date range."""
        today = date.today()
        _record_usage(instance_dir, input_tokens=1000)

        # Use a range that excludes today
        far_past = date(2020, 1, 1)
        yesterday = today - timedelta(days=1)
        count = daily_snapshot.backfill_snapshots(
            instance_dir, start=far_past, end=yesterday
        )

        assert count == 0


class TestMaxOutcomesRaised:
    """Verify MAX_OUTCOMES was raised to 2000."""

    def test_max_outcomes_is_2000(self):
        """MAX_OUTCOMES should be 2000 for ~1 year of daily missions."""
        from app.session_tracker import MAX_OUTCOMES
        assert MAX_OUTCOMES == 2000
