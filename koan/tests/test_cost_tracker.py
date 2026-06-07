"""Tests for cost_tracker.py — Structured per-model/project token tracking."""

import json
import pytest
from datetime import date, datetime, timedelta
from pathlib import Path

from app.cost_tracker import (
    record_usage,
    summarize_day,
    summarize_range,
    summarize_by_project,
    summarize_by_model,
    summarize_by_type,
    summarize_by_project_and_type,
    summarize_by_mode,
    summarize_by_project_and_mode,
    summarize_week,
    summarize_month,
    estimate_cost,
    estimate_cache_savings,
    daily_series,
    get_pricing_config,
    format_cache_summary,
    format_mission_cache_line,
    top_missions,
    compute_efficiency,
    _read_jsonl_for_date,
    _read_jsonl_range,
    _aggregate,
    _format_tokens,
)


@pytest.fixture
def instance_dir(tmp_path):
    """Create a temporary instance directory."""
    d = tmp_path / "instance"
    d.mkdir()
    return d


@pytest.fixture
def usage_dir(instance_dir):
    """Create the usage subdirectory."""
    d = instance_dir / "usage"
    d.mkdir()
    return d


@pytest.fixture
def sample_entries():
    """A set of sample usage entries for testing aggregation."""
    return [
        {"ts": "2026-03-08T10:00:00", "project": "koan", "model": "claude-sonnet-4-20250514",
         "input_tokens": 1000, "output_tokens": 500, "mode": "implement", "mission": "Fix bug"},
        {"ts": "2026-03-08T11:00:00", "project": "koan", "model": "claude-opus-4-20250514",
         "input_tokens": 3000, "output_tokens": 1500, "mode": "deep", "mission": "Refactor"},
        {"ts": "2026-03-08T12:00:00", "project": "other", "model": "claude-sonnet-4-20250514",
         "input_tokens": 800, "output_tokens": 200, "mode": "review", "mission": "Review PR"},
    ]


class TestRecordUsage:
    def test_creates_jsonl_file(self, instance_dir):
        result = record_usage(
            instance_dir, "koan", "claude-sonnet-4-20250514",
            1000, 500, "implement", "Fix bug",
        )
        assert result is True
        today = date.today().isoformat()
        jsonl_path = instance_dir / "usage" / f"{today}.jsonl"
        assert jsonl_path.exists()

    def test_appends_valid_json(self, instance_dir):
        record_usage(instance_dir, "koan", "sonnet", 100, 50)
        record_usage(instance_dir, "other", "opus", 200, 100)

        today = date.today().isoformat()
        lines = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2

        entry1 = json.loads(lines[0])
        assert entry1["project"] == "koan"
        assert entry1["input_tokens"] == 100

        entry2 = json.loads(lines[1])
        assert entry2["project"] == "other"

    def test_creates_usage_dir(self, instance_dir):
        """Usage dir should be auto-created if missing."""
        assert not (instance_dir / "usage").exists()
        record_usage(instance_dir, "p", "m", 1, 1)
        assert (instance_dir / "usage").exists()

    def test_empty_project_defaults_to_global(self, instance_dir):
        record_usage(instance_dir, "", "sonnet", 100, 50)
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert entry["project"] == "_global"

    def test_empty_model_defaults_to_unknown(self, instance_dir):
        record_usage(instance_dir, "p", "", 100, 50)
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert entry["model"] == "unknown"

    def test_zero_tokens_recorded(self, instance_dir):
        """Zero tokens should still be recorded (failed/aborted runs)."""
        result = record_usage(instance_dir, "p", "m", 0, 0)
        assert result is True
        today = date.today().isoformat()
        lines = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1

    def test_compact_json_format(self, instance_dir):
        """JSONL lines should be compact (no spaces after separators)."""
        record_usage(instance_dir, "p", "m", 100, 50)
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        assert ": " not in line  # No pretty-printing

    def test_mission_type_written_when_provided(self, instance_dir):
        """mission_type field appears in JSONL when explicitly passed."""
        record_usage(
            instance_dir, "koan", "sonnet", 1000, 500,
            mission_type="rebase",
        )
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert entry["mission_type"] == "rebase"

    def test_mission_type_omitted_when_empty(self, instance_dir):
        """mission_type field is absent when not passed (backwards compat)."""
        record_usage(instance_dir, "koan", "sonnet", 1000, 500)
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert "mission_type" not in entry

    def test_mission_type_omitted_for_empty_string(self, instance_dir):
        """Explicitly passing empty string for mission_type omits the field."""
        record_usage(instance_dir, "koan", "sonnet", 1000, 500, mission_type="")
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert "mission_type" not in entry


class TestReadJsonl:
    def test_reads_existing_file(self, usage_dir):
        today = date.today()
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text(
            json.dumps({"input_tokens": 100, "output_tokens": 50}) + "\n"
        )
        entries = _read_jsonl_for_date(usage_dir, today)
        assert len(entries) == 1
        assert entries[0]["input_tokens"] == 100

    def test_skips_invalid_lines(self, usage_dir):
        today = date.today()
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text(
            '{"input_tokens": 100}\n'
            'corrupt line\n'
            '{"input_tokens": 200}\n'
        )
        entries = _read_jsonl_for_date(usage_dir, today)
        assert len(entries) == 2

    def test_returns_empty_for_missing_file(self, usage_dir):
        entries = _read_jsonl_for_date(usage_dir, date(2020, 1, 1))
        assert entries == []

    def test_reads_date_range(self, usage_dir):
        d1 = date(2026, 3, 7)
        d2 = date(2026, 3, 8)
        (usage_dir / f"{d1.isoformat()}.jsonl").write_text(
            json.dumps({"input_tokens": 100}) + "\n"
        )
        (usage_dir / f"{d2.isoformat()}.jsonl").write_text(
            json.dumps({"input_tokens": 200}) + "\n"
        )
        entries = _read_jsonl_range(usage_dir, d1, d2)
        assert len(entries) == 2

    def test_skips_empty_lines(self, usage_dir):
        today = date.today()
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text('\n{"input_tokens": 100}\n\n')
        entries = _read_jsonl_for_date(usage_dir, today)
        assert len(entries) == 1


class TestAggregate:
    def test_empty_entries(self):
        result = _aggregate([])
        assert result["total_input"] == 0
        assert result["total_output"] == 0
        assert result["count"] == 0
        assert result["by_project"] == {}
        assert result["by_model"] == {}

    def test_single_entry(self):
        entries = [{"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m"}]
        result = _aggregate(entries)
        assert result["total_input"] == 100
        assert result["total_output"] == 50
        assert result["count"] == 1
        assert result["by_project"]["p"]["input_tokens"] == 100
        assert result["by_model"]["m"]["output_tokens"] == 50

    def test_multiple_projects(self, sample_entries):
        result = _aggregate(sample_entries)
        assert result["total_input"] == 4800
        assert result["total_output"] == 2200
        assert result["count"] == 3
        assert "koan" in result["by_project"]
        assert "other" in result["by_project"]
        assert result["by_project"]["koan"]["count"] == 2
        assert result["by_project"]["other"]["count"] == 1

    def test_multiple_models(self, sample_entries):
        result = _aggregate(sample_entries)
        sonnet = result["by_model"]["claude-sonnet-4-20250514"]
        opus = result["by_model"]["claude-opus-4-20250514"]
        assert sonnet["count"] == 2
        assert opus["count"] == 1
        assert sonnet["input_tokens"] == 1800  # 1000 + 800
        assert opus["input_tokens"] == 3000

    def test_missing_fields_default(self):
        entries = [{"project": "p"}]  # No token fields
        result = _aggregate(entries)
        assert result["total_input"] == 0
        assert result["total_output"] == 0
        assert result["count"] == 1


class TestSummarize:
    def test_summarize_day(self, instance_dir):
        record_usage(instance_dir, "koan", "sonnet", 1000, 500)
        record_usage(instance_dir, "other", "opus", 2000, 800)
        result = summarize_day(instance_dir)
        assert result["total_input"] == 3000
        assert result["total_output"] == 1300
        assert result["count"] == 2

    def test_summarize_day_empty(self, instance_dir):
        result = summarize_day(instance_dir, date(2020, 1, 1))
        assert result["count"] == 0

    def test_summarize_range(self, instance_dir):
        usage_dir = instance_dir / "usage"
        usage_dir.mkdir(exist_ok=True)
        d1 = date(2026, 3, 7)
        d2 = date(2026, 3, 8)
        (usage_dir / f"{d1.isoformat()}.jsonl").write_text(
            json.dumps({"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m"}) + "\n"
        )
        (usage_dir / f"{d2.isoformat()}.jsonl").write_text(
            json.dumps({"input_tokens": 200, "output_tokens": 100, "project": "p", "model": "m"}) + "\n"
        )
        result = summarize_range(instance_dir, d1, d2)
        assert result["total_input"] == 300
        assert result["count"] == 2

    def test_summarize_by_project(self, instance_dir):
        record_usage(instance_dir, "koan", "sonnet", 1000, 500)
        record_usage(instance_dir, "koan", "sonnet", 500, 200)
        record_usage(instance_dir, "other", "opus", 2000, 800)
        result = summarize_by_project(instance_dir, days=1)
        assert result["koan"]["input_tokens"] == 1500
        assert result["koan"]["count"] == 2
        assert result["other"]["count"] == 1

    def test_summarize_by_project_includes_cost_and_cache(self, instance_dir):
        record_usage(
            instance_dir, "koan", "sonnet", 1000, 500,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=800,
            cost_usd=0.15,
        )
        record_usage(
            instance_dir, "koan", "sonnet", 500, 200,
            cache_creation_input_tokens=100,
            cache_read_input_tokens=400,
            cost_usd=0.08,
        )
        result = summarize_by_project(instance_dir, days=1)
        koan = result["koan"]
        assert koan["total_cost_usd"] == pytest.approx(0.23, abs=0.001)
        assert koan["cache_creation_input_tokens"] == 300
        assert koan["cache_read_input_tokens"] == 1200

    def test_summarize_by_model(self, instance_dir):
        record_usage(instance_dir, "koan", "claude-sonnet-4-20250514", 1000, 500)
        record_usage(instance_dir, "koan", "claude-opus-4-20250514", 2000, 800)
        result = summarize_by_model(instance_dir, days=1)
        assert "claude-sonnet-4-20250514" in result
        assert "claude-opus-4-20250514" in result


class TestCacheTracking:
    """Tests for prompt cache observability in cost tracking."""

    def test_record_with_cache_fields(self, instance_dir):
        """Cache fields should be recorded in JSONL when non-zero."""
        record_usage(
            instance_dir, "koan", "claude-opus-4-6",
            24, 5916, "deep", "Fix bug",
            cache_creation_input_tokens=41777,
            cache_read_input_tokens=1036218,
            cost_usd=0.927,
        )
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert entry["cache_creation_input_tokens"] == 41777
        assert entry["cache_read_input_tokens"] == 1036218
        assert entry["cost_usd"] == 0.927

    def test_record_without_cache_fields_stays_compact(self, instance_dir):
        """Zero cache fields should NOT appear in JSONL (backward compat)."""
        record_usage(instance_dir, "koan", "sonnet", 100, 50)
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert "cache_creation_input_tokens" not in entry
        assert "cache_read_input_tokens" not in entry
        assert "cost_usd" not in entry

    def test_aggregate_cache_totals(self):
        """Aggregation should sum cache tokens across entries."""
        entries = [
            {"input_tokens": 24, "output_tokens": 500, "project": "p", "model": "m",
             "cache_creation_input_tokens": 40000, "cache_read_input_tokens": 100000,
             "cost_usd": 0.5},
            {"input_tokens": 50, "output_tokens": 300, "project": "p", "model": "m",
             "cache_read_input_tokens": 100000, "cost_usd": 0.3},
        ]
        result = _aggregate(entries)
        assert result["cache_creation_input_tokens"] == 40000
        assert result["cache_read_input_tokens"] == 200000
        assert result["total_cost_usd"] == pytest.approx(0.8)

    def test_aggregate_cache_hit_rate(self):
        """Cache hit rate = cache_read / total_all_input."""
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "cache_read_input_tokens": 900},
        ]
        result = _aggregate(entries)
        # total_all_input = 100 + 900 = 1000, cache_read = 900
        assert result["cache_hit_rate"] == pytest.approx(0.9)

    def test_aggregate_no_cache_hit_rate_zero(self):
        """No cache tokens should give 0% hit rate."""
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m"},
        ]
        result = _aggregate(entries)
        assert result["cache_hit_rate"] == 0.0

    def test_aggregate_empty_has_cache_fields(self):
        """Empty aggregation should include cache fields."""
        result = _aggregate([])
        assert result["cache_creation_input_tokens"] == 0
        assert result["cache_read_input_tokens"] == 0
        assert result["cache_hit_rate"] == 0.0
        assert result["total_cost_usd"] == 0.0

    def test_summarize_day_includes_cache(self, instance_dir):
        """Day summary should include cache metrics."""
        record_usage(
            instance_dir, "koan", "opus",
            24, 5000, cache_read_input_tokens=100000,
        )
        result = summarize_day(instance_dir)
        assert result["cache_read_input_tokens"] == 100000
        assert result["cache_hit_rate"] > 0

    def test_estimate_cache_savings_from_pricing(self):
        summary = {
            "by_model": {
                "claude-sonnet-4-20250514": {
                    "cache_read_input_tokens": 1_000_000,
                },
            }
        }
        pricing = {"sonnet": {"input": 3.0, "output": 15.0}}
        # 1M read tokens * $3/M input * 90% savings
        assert estimate_cache_savings(summary, pricing) == pytest.approx(2.7)

    def test_estimate_cache_savings_none_without_pricing(self):
        summary = {"by_model": {"m": {"cache_read_input_tokens": 1000}}}
        assert estimate_cache_savings(summary, None) is None

    def test_daily_series_includes_cache_fields(self, instance_dir):
        record_usage(
            instance_dir,
            "koan",
            "claude-sonnet-4-20250514",
            100,
            50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=800,
        )
        today = date.today()
        rows = daily_series(instance_dir, today, today)
        assert len(rows) == 1
        row = rows[0]
        assert row["cache_creation_input_tokens"] == 200
        assert row["cache_read_input_tokens"] == 800
        assert row["cache_hit_rate"] > 0

    def test_daily_series_has_exact_expected_keys(self, instance_dir):
        """Verify daily_series rows contain exactly the expected keys.

        Regression test: duplicate dict keys (cache_read_input_tokens,
        cache_creation_input_tokens, cache_hit_rate) were present in the
        dict literal, causing the per-day values to silently overwrite
        earlier assignments.
        """
        record_usage(
            instance_dir,
            "koan",
            "claude-sonnet-4-20250514",
            100,
            50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=800,
        )
        today = date.today()
        rows = daily_series(instance_dir, today, today)
        assert len(rows) == 1
        expected_keys = {
            "date", "total_input", "total_output",
            "cache_creation_input_tokens", "cache_read_input_tokens",
            "cache_hit_rate", "count", "cost",
        }
        assert set(rows[0].keys()) == expected_keys


class TestEstimateCost:
    def test_returns_none_without_pricing(self):
        tokens = {"input_tokens": 1000, "output_tokens": 500, "model": "sonnet"}
        assert estimate_cost(tokens) is None
        assert estimate_cost(tokens, None) is None

    def test_calculates_cost(self):
        pricing = {
            "sonnet": {"input": 3.0, "output": 15.0},
        }
        tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "model": "claude-sonnet-4"}
        cost = estimate_cost(tokens, pricing)
        assert cost == pytest.approx(18.0)

    def test_unknown_model_returns_none(self):
        pricing = {"sonnet": {"input": 3.0, "output": 15.0}}
        tokens = {"input_tokens": 1000, "output_tokens": 500, "model": "gpt-4"}
        assert estimate_cost(tokens, pricing) is None

    def test_opus_pricing(self):
        pricing = {
            "opus": {"input": 15.0, "output": 75.0},
        }
        tokens = {"input_tokens": 1_000_000, "output_tokens": 100_000, "model": "claude-opus-4-20250514"}
        cost = estimate_cost(tokens, pricing)
        assert cost == pytest.approx(15.0 + 7.5)

    def test_haiku_pricing(self):
        pricing = {"haiku": {"input": 0.25, "output": 1.25}}
        tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "model": "claude-haiku-4-5"}
        cost = estimate_cost(tokens, pricing)
        assert cost == pytest.approx(1.5)


class TestGetPricingConfig:
    def test_returns_none_without_config(self):
        assert get_pricing_config({}) is None

    def test_returns_pricing_from_config(self):
        config = {"usage": {"pricing": {"sonnet": {"input": 3.0, "output": 15.0}}}}
        pricing = get_pricing_config(config)
        assert pricing["sonnet"]["input"] == 3.0

    def test_returns_none_for_non_dict_pricing(self):
        config = {"usage": {"pricing": "invalid"}}
        assert get_pricing_config(config) is None


class TestFormatCacheSummary:
    def test_returns_empty_when_no_data(self, instance_dir):
        assert format_cache_summary(instance_dir) == ""

    def test_returns_summary_with_cache_data(self, instance_dir):
        record_usage(
            instance_dir, "koan", "opus",
            1000, 500,
            cache_read_input_tokens=9000,
            cache_creation_input_tokens=1000,
        )
        result = format_cache_summary(instance_dir)
        assert "hit rate" in result
        assert "read" in result
        assert "created" in result

    def test_summary_shows_zero_pct_for_creation_only(self, instance_dir):
        record_usage(
            instance_dir, "koan", "opus",
            1000, 500,
            cache_creation_input_tokens=5000,
        )
        result = format_cache_summary(instance_dir)
        assert "0% hit rate" in result


class TestFormatMissionCacheLine:
    def test_empty_when_no_cache(self):
        assert format_mission_cache_line(0, 0, 1000) == ""

    def test_shows_hit_rate(self):
        result = format_mission_cache_line(
            cache_read=9000, cache_create=0, input_tokens=1000,
        )
        assert "90% hit" in result
        assert "9.0k read" in result

    def test_shows_creation(self):
        result = format_mission_cache_line(
            cache_read=0, cache_create=5000, input_tokens=1000,
        )
        assert "0% hit" in result
        assert "5.0k created" in result

    def test_mixed(self):
        result = format_mission_cache_line(
            cache_read=4000, cache_create=1000, input_tokens=5000,
        )
        assert "hit" in result
        assert "read" in result
        assert "created" in result


class TestFormatTokens:
    def test_small(self):
        assert _format_tokens(500) == "500"

    def test_thousands(self):
        assert _format_tokens(1500) == "1.5k"

    def test_millions(self):
        assert _format_tokens(1_500_000) == "1.5M"


class TestDailySeriesCacheFields:
    def test_includes_cache_fields(self, instance_dir):
        record_usage(
            instance_dir, "koan", "opus",
            1000, 500,
            cache_read_input_tokens=3000,
            cache_creation_input_tokens=1000,
        )
        from app.cost_tracker import daily_series
        series = daily_series(instance_dir, date.today(), date.today())
        assert len(series) == 1
        day = series[0]
        assert day["cache_read_input_tokens"] == 3000
        assert day["cache_creation_input_tokens"] == 1000

    def test_by_project_includes_cache_fields(self, instance_dir):
        record_usage(
            instance_dir, "proj-a", "sonnet",
            1000, 500,
            cache_read_input_tokens=2000,
            cache_creation_input_tokens=500,
        )
        from app.cost_tracker import daily_series
        series = daily_series(instance_dir, date.today(), date.today(), include_by_project=True)
        assert len(series) == 1
        bp = series[0]["by_project"]["proj-a"]
        assert bp["cache_read_input_tokens"] == 2000
        assert bp["cache_creation_input_tokens"] == 500
        # cache_hit_rate = 2000 / (1000 + 2000 + 500) = 2000/3500
        assert abs(bp["cache_hit_rate"] - 2000 / 3500) < 1e-9

    def test_by_project_zero_cache_returns_zero_not_nan(self, instance_dir):
        record_usage(instance_dir, "proj-b", "sonnet", 1000, 500)
        from app.cost_tracker import daily_series
        series = daily_series(instance_dir, date.today(), date.today(), include_by_project=True)
        assert len(series) == 1
        bp = series[0]["by_project"]["proj-b"]
        assert bp["cache_hit_rate"] == 0.0
        assert bp["cache_read_input_tokens"] == 0
        assert bp["cache_creation_input_tokens"] == 0

    def test_zero_cache_day_hit_rate_is_zero(self, instance_dir):
        record_usage(instance_dir, "proj-c", "sonnet", 500, 200)
        from app.cost_tracker import daily_series
        series = daily_series(instance_dir, date.today(), date.today())
        assert len(series) == 1
        assert series[0]["cache_hit_rate"] == 0.0


class TestBucketCacheFields:
    """Tests for cache hit rate recomputation in weekly/monthly bucket functions."""

    def test_bucket_by_week_recomputes_cache_hit_rate(self):
        from app.dashboard import _bucket_by_week
        from datetime import date

        monday = date(2026, 6, 1)  # A Monday
        tuesday = date(2026, 6, 2)
        series = [
            {
                "date": monday.isoformat(),
                "total_input": 1000,
                "total_output": 200,
                "cache_creation_input_tokens": 500,
                "cache_read_input_tokens": 1000,
                "cache_hit_rate": 1000 / 2500,
                "count": 1,
                "cost": None,
            },
            {
                "date": tuesday.isoformat(),
                "total_input": 2000,
                "total_output": 400,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 800,
                "cache_hit_rate": 800 / 3000,
                "count": 2,
                "cost": None,
            },
        ]
        buckets = _bucket_by_week(series)
        assert len(buckets) == 1
        b = buckets[0]
        # Summed: input=3000, cache_read=1800, cache_create=700 => rate = 1800/(3000+1800+700)
        expected = 1800 / (3000 + 1800 + 700)
        assert abs(b["cache_hit_rate"] - expected) < 1e-9

    def test_bucket_by_week_by_project_cache_fields(self):
        from app.dashboard import _bucket_by_week

        monday = date(2026, 6, 1)
        tuesday = date(2026, 6, 2)
        series = [
            {
                "date": monday.isoformat(),
                "total_input": 1000, "total_output": 200,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cache_hit_rate": 0.0, "count": 1, "cost": None,
                "by_project": {
                    "alpha": {
                        "total_input": 1000, "total_output": 200,
                        "cache_creation_input_tokens": 300, "cache_read_input_tokens": 600,
                        "cache_hit_rate": 600 / 1900, "count": 1,
                    }
                },
            },
            {
                "date": tuesday.isoformat(),
                "total_input": 500, "total_output": 100,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cache_hit_rate": 0.0, "count": 1, "cost": None,
                "by_project": {
                    "alpha": {
                        "total_input": 500, "total_output": 100,
                        "cache_creation_input_tokens": 100, "cache_read_input_tokens": 200,
                        "cache_hit_rate": 200 / 800, "count": 1,
                    }
                },
            },
        ]
        buckets = _bucket_by_week(series)
        assert len(buckets) == 1
        bp = buckets[0]["by_project"]["alpha"]
        assert bp["cache_read_input_tokens"] == 800
        assert bp["cache_creation_input_tokens"] == 400
        # rate = 800 / (1500 + 800 + 400)
        expected = 800 / (1500 + 800 + 400)
        assert abs(bp["cache_hit_rate"] - expected) < 1e-9

    def test_bucket_by_month_recomputes_cache_hit_rate(self):
        from app.dashboard import _bucket_by_month

        d1 = date(2026, 5, 1)
        d2 = date(2026, 5, 15)
        series = [
            {
                "date": d1.isoformat(),
                "total_input": 1000, "total_output": 200,
                "cache_creation_input_tokens": 400, "cache_read_input_tokens": 1600,
                "cache_hit_rate": 1600 / 3000, "count": 1, "cost": None,
            },
            {
                "date": d2.isoformat(),
                "total_input": 500, "total_output": 100,
                "cache_creation_input_tokens": 100, "cache_read_input_tokens": 400,
                "cache_hit_rate": 400 / 1000, "count": 1, "cost": None,
            },
        ]
        buckets = _bucket_by_month(series)
        assert len(buckets) == 1
        b = buckets[0]
        # Summed: input=1500, cache_read=2000, cache_create=500 => rate=2000/4000
        expected = 2000 / (1500 + 2000 + 500)
        assert abs(b["cache_hit_rate"] - expected) < 1e-9

    def test_bucket_zero_cache_returns_zero_hit_rate(self):
        from app.dashboard import _bucket_by_week

        series = [
            {
                "date": date(2026, 6, 1).isoformat(),
                "total_input": 1000, "total_output": 200,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cache_hit_rate": 0.0, "count": 1, "cost": None,
            },
        ]
        buckets = _bucket_by_week(series)
        assert len(buckets) == 1
        assert buckets[0]["cache_hit_rate"] == 0.0


class TestAggregateByType:
    """Tests for mission_type aggregation in _aggregate."""

    def test_aggregate_includes_by_type(self):
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "mission_type": "review"},
            {"input_tokens": 200, "output_tokens": 100, "project": "p", "model": "m",
             "mission_type": "implement"},
            {"input_tokens": 300, "output_tokens": 150, "project": "p", "model": "m",
             "mission_type": "review"},
        ]
        result = _aggregate(entries)
        assert "by_type" in result
        assert result["by_type"]["review"]["count"] == 2
        assert result["by_type"]["review"]["input_tokens"] == 400
        assert result["by_type"]["review"]["output_tokens"] == 200
        assert result["by_type"]["implement"]["count"] == 1

    def test_missing_mission_type_reclassified_from_mission(self):
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "mission": "/review https://example.com/pr/1"},
        ]
        result = _aggregate(entries)
        assert "review" in result["by_type"]
        assert result["by_type"]["review"]["count"] == 1

    def test_missing_mission_type_no_mission_becomes_autonomous(self):
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m"},
        ]
        result = _aggregate(entries)
        assert "autonomous" in result["by_type"]
        assert result["by_type"]["autonomous"]["count"] == 1

    def test_empty_entries_has_by_type(self):
        result = _aggregate([])
        assert result["by_type"] == {}

    def test_cost_aggregated_by_type(self):
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "mission_type": "plan", "cost_usd": 0.5},
            {"input_tokens": 200, "output_tokens": 100, "project": "p", "model": "m",
             "mission_type": "plan", "cost_usd": 1.0},
        ]
        result = _aggregate(entries)
        assert result["by_type"]["plan"]["total_cost_usd"] == pytest.approx(1.5)


class TestSummarizeByType:
    def test_returns_type_breakdown(self, instance_dir, usage_dir):
        today = date.today()
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "mission_type": "review", "ts": today.isoformat()},
            {"input_tokens": 200, "output_tokens": 100, "project": "p", "model": "m",
             "mission_type": "implement", "ts": today.isoformat()},
            {"input_tokens": 150, "output_tokens": 75, "project": "p", "model": "m",
             "ts": today.isoformat()},
        ]
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = summarize_by_type(instance_dir, days=1)
        assert result["review"]["count"] == 1
        assert result["implement"]["count"] == 1
        assert result["autonomous"]["count"] == 1

    def test_empty_returns_empty(self, instance_dir):
        result = summarize_by_type(instance_dir, days=1)
        assert result == {}


class TestSummarizeByProjectAndType:
    def test_returns_nested_breakdown(self, instance_dir, usage_dir):
        today = date.today()
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "koan", "model": "m",
             "mission_type": "review", "ts": today.isoformat()},
            {"input_tokens": 200, "output_tokens": 100, "project": "koan", "model": "m",
             "mission_type": "implement", "ts": today.isoformat()},
            {"input_tokens": 300, "output_tokens": 150, "project": "other", "model": "m",
             "mission_type": "review", "ts": today.isoformat()},
        ]
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = summarize_by_project_and_type(instance_dir, days=1)
        assert result["koan"]["review"]["count"] == 1
        assert result["koan"]["implement"]["count"] == 1
        assert result["other"]["review"]["count"] == 1
        assert result["other"]["review"]["input_tokens"] == 300

    def test_missing_type_reclassified(self, instance_dir, usage_dir):
        today = date.today()
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "mission": "/rebase https://example.com/pr/1", "ts": today.isoformat()},
        ]
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text(json.dumps(entries[0]) + "\n")

        result = summarize_by_project_and_type(instance_dir, days=1)
        assert result["p"]["rebase"]["count"] == 1

    def test_empty_returns_empty(self, instance_dir):
        result = summarize_by_project_and_type(instance_dir, days=1)
        assert result == {}

    def test_cost_tracked_per_type(self, instance_dir, usage_dir):
        today = date.today()
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "mission_type": "review", "cost_usd": 0.5, "ts": today.isoformat()},
            {"input_tokens": 200, "output_tokens": 100, "project": "p", "model": "m",
             "mission_type": "review", "cost_usd": 1.0, "ts": today.isoformat()},
        ]
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = summarize_by_project_and_type(instance_dir, days=1)
        assert result["p"]["review"]["total_cost_usd"] == pytest.approx(1.5)


class TestSummarizeWeekMonth:
    def test_summarize_week_covers_7_days(self, instance_dir, usage_dir):
        today = date.today()
        # Write data for today and 6 days ago
        for d in [today, today - timedelta(days=6)]:
            jsonl_path = usage_dir / f"{d.isoformat()}.jsonl"
            jsonl_path.write_text(
                json.dumps({"input_tokens": 100, "output_tokens": 50,
                            "project": "p", "model": "m"}) + "\n"
            )
        result = summarize_week(instance_dir)
        assert result["count"] == 2
        assert result["total_input"] == 200

    def test_summarize_week_excludes_8th_day(self, instance_dir, usage_dir):
        today = date.today()
        # Only 8 days ago — outside the 7-day window
        d = today - timedelta(days=7)
        jsonl_path = usage_dir / f"{d.isoformat()}.jsonl"
        jsonl_path.write_text(
            json.dumps({"input_tokens": 100, "output_tokens": 50,
                        "project": "p", "model": "m"}) + "\n"
        )
        result = summarize_week(instance_dir)
        assert result["count"] == 0

    def test_summarize_month_covers_30_days(self, instance_dir, usage_dir):
        today = date.today()
        for d in [today, today - timedelta(days=29)]:
            jsonl_path = usage_dir / f"{d.isoformat()}.jsonl"
            jsonl_path.write_text(
                json.dumps({"input_tokens": 100, "output_tokens": 50,
                            "project": "p", "model": "m"}) + "\n"
            )
        result = summarize_month(instance_dir)
        assert result["count"] == 2

    def test_summarize_month_excludes_31st_day(self, instance_dir, usage_dir):
        today = date.today()
        d = today - timedelta(days=30)
        jsonl_path = usage_dir / f"{d.isoformat()}.jsonl"
        jsonl_path.write_text(
            json.dumps({"input_tokens": 100, "output_tokens": 50,
                        "project": "p", "model": "m"}) + "\n"
        )
        result = summarize_month(instance_dir)
        assert result["count"] == 0

    def test_week_and_month_include_by_type(self, instance_dir, usage_dir):
        today = date.today()
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text(
            json.dumps({"input_tokens": 100, "output_tokens": 50,
                        "project": "p", "model": "m", "mission_type": "plan"}) + "\n"
        )
        week = summarize_week(instance_dir)
        month = summarize_month(instance_dir)
        assert "plan" in week["by_type"]
        assert "plan" in month["by_type"]


class TestByMode:
    """Tests for autonomous-mode aggregation in _aggregate."""

    def test_aggregate_by_mode(self, sample_entries):
        result = _aggregate(sample_entries)
        assert "by_mode" in result
        assert "implement" in result["by_mode"]
        assert "deep" in result["by_mode"]
        assert "review" in result["by_mode"]
        assert result["by_mode"]["implement"]["count"] == 1
        assert result["by_mode"]["implement"]["input_tokens"] == 1000
        assert result["by_mode"]["implement"]["output_tokens"] == 500
        assert result["by_mode"]["deep"]["count"] == 1
        assert result["by_mode"]["review"]["count"] == 1

    def test_empty_mode_bucketed_as_unknown(self):
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "mode": ""},
        ]
        result = _aggregate(entries)
        assert "unknown" in result["by_mode"]
        assert result["by_mode"]["unknown"]["count"] == 1
        assert result["by_mode"]["unknown"]["input_tokens"] == 100
        assert result["by_mode"]["unknown"]["output_tokens"] == 50

    def test_missing_mode_bucketed_as_unknown(self):
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m"},
        ]
        result = _aggregate(entries)
        assert "unknown" in result["by_mode"]
        assert result["by_mode"]["unknown"]["count"] == 1
        assert result["by_mode"]["unknown"]["input_tokens"] == 100
        assert result["by_mode"]["unknown"]["output_tokens"] == 50

    def test_empty_entries_has_by_mode(self):
        result = _aggregate([])
        assert result["by_mode"] == {}
        assert result["by_project_and_mode"] == {}

    def test_cost_aggregated_by_mode(self):
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "mode": "implement", "cost_usd": 0.5},
            {"input_tokens": 200, "output_tokens": 100, "project": "p", "model": "m",
             "mode": "implement", "cost_usd": 1.0},
        ]
        result = _aggregate(entries)
        assert result["by_mode"]["implement"]["total_cost_usd"] == pytest.approx(1.5)

    def test_by_project_and_mode(self, sample_entries):
        result = _aggregate(sample_entries)
        assert "by_project_and_mode" in result
        assert "koan" in result["by_project_and_mode"]
        assert "other" in result["by_project_and_mode"]
        assert result["by_project_and_mode"]["koan"]["implement"]["count"] == 1
        assert result["by_project_and_mode"]["koan"]["deep"]["count"] == 1
        assert result["by_project_and_mode"]["other"]["review"]["count"] == 1

    def test_summarize_by_mode(self, instance_dir, usage_dir):
        today = date.today()
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "p", "model": "m",
             "mode": "implement", "ts": today.isoformat()},
            {"input_tokens": 200, "output_tokens": 100, "project": "p", "model": "m",
             "mode": "deep", "ts": today.isoformat()},
            {"input_tokens": 50, "output_tokens": 25, "project": "p", "model": "m",
             "ts": today.isoformat()},
        ]
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = summarize_by_mode(instance_dir, days=1)
        assert result["implement"]["count"] == 1
        assert result["deep"]["count"] == 1
        assert result["unknown"]["count"] == 1

    def test_summarize_by_mode_empty(self, instance_dir):
        result = summarize_by_mode(instance_dir, days=1)
        assert result == {}

    def test_summarize_by_project_and_mode(self, instance_dir, usage_dir):
        today = date.today()
        entries = [
            {"input_tokens": 100, "output_tokens": 50, "project": "koan", "model": "m",
             "mode": "implement", "ts": today.isoformat()},
            {"input_tokens": 200, "output_tokens": 100, "project": "other", "model": "m",
             "mode": "review", "ts": today.isoformat()},
        ]
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        jsonl_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = summarize_by_project_and_mode(instance_dir, days=1)
        assert result["koan"]["implement"]["count"] == 1
        assert result["other"]["review"]["count"] == 1


class TestRecordUsageDurationProvider:
    def test_duration_seconds_written_when_nonzero(self, instance_dir):
        """duration_seconds appears in JSONL when non-zero."""
        record_usage(
            instance_dir, "koan", "sonnet", 1000, 500,
            duration_seconds=300,
        )
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert entry["duration_seconds"] == 300

    def test_duration_seconds_omitted_when_zero(self, instance_dir):
        """duration_seconds absent from JSONL when zero (backwards compat)."""
        record_usage(instance_dir, "koan", "sonnet", 1000, 500)
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert "duration_seconds" not in entry

    def test_provider_written_when_nonempty(self, instance_dir):
        """provider field appears in JSONL when non-empty string passed."""
        record_usage(
            instance_dir, "koan", "sonnet", 1000, 500,
            provider="claude",
        )
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert entry["provider"] == "claude"

    def test_provider_omitted_when_empty(self, instance_dir):
        """provider absent from JSONL when empty or not passed."""
        record_usage(instance_dir, "koan", "sonnet", 1000, 500)
        today = date.today().isoformat()
        line = (instance_dir / "usage" / f"{today}.jsonl").read_text().strip()
        entry = json.loads(line)
        assert "provider" not in entry


class TestTopMissions:
    def _write_entries(self, usage_dir, entries, d=None):
        if d is None:
            d = date.today()
        path = usage_dir / f"{d.isoformat()}.jsonl"
        lines = [json.dumps(e) for e in entries]
        path.write_text("\n".join(lines) + "\n")

    def test_top_missions_returns_sorted_by_total(self, instance_dir, usage_dir):
        today = date.today()
        self._write_entries(usage_dir, [
            {"ts": "2026-01-01T10:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 100, "output_tokens": 50, "mode": "implement",
             "mission": "small", "mission_type": "implement"},
            {"ts": "2026-01-01T11:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 5000, "output_tokens": 2000, "mode": "deep",
             "mission": "large", "mission_type": "implement"},
            {"ts": "2026-01-01T12:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 1000, "output_tokens": 500, "mode": "review",
             "mission": "medium", "mission_type": "review"},
        ], today)
        result = top_missions(instance_dir, today, today)
        assert result[0]["mission"] == "large"
        assert result[1]["mission"] == "medium"
        assert result[2]["mission"] == "small"

    def test_top_missions_project_filter(self, instance_dir, usage_dir):
        today = date.today()
        self._write_entries(usage_dir, [
            {"ts": "2026-01-01T10:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 1000, "output_tokens": 500, "mode": "implement",
             "mission": "koan mission", "mission_type": "implement"},
            {"ts": "2026-01-01T11:00:00", "project": "other", "model": "sonnet",
             "input_tokens": 2000, "output_tokens": 1000, "mode": "implement",
             "mission": "other mission", "mission_type": "implement"},
        ], today)
        result = top_missions(instance_dir, today, today, project="koan")
        assert len(result) == 1
        assert result[0]["project"] == "koan"

    def test_top_missions_limit(self, instance_dir, usage_dir):
        today = date.today()
        self._write_entries(usage_dir, [
            {"ts": f"2026-01-01T{i:02d}:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": (i + 1) * 100, "output_tokens": 50, "mode": "implement",
             "mission": f"mission {i}", "mission_type": "implement"}
            for i in range(5)
        ], today)
        result = top_missions(instance_dir, today, today, limit=2)
        assert len(result) == 2

    def test_top_missions_truncates_mission_text(self, instance_dir, usage_dir):
        today = date.today()
        long_text = "x" * 400
        self._write_entries(usage_dir, [
            {"ts": "2026-01-01T10:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 100, "output_tokens": 50, "mode": "implement",
             "mission": long_text, "mission_type": "implement"},
        ], today)
        result = top_missions(instance_dir, today, today)
        assert len(result) == 1
        assert result[0]["mission"].endswith("...")
        assert len(result[0]["mission"]) == 303  # 300 + len("...")

    def test_top_missions_empty_range(self, instance_dir, usage_dir):
        future = date.today() + timedelta(days=365)
        result = top_missions(instance_dir, future, future)
        assert result == []

    def test_top_missions_missing_cost_usd_fallback(self, instance_dir, usage_dir):
        today = date.today()
        self._write_entries(usage_dir, [
            {"ts": "2026-01-01T10:00:00", "project": "koan",
             "model": "claude-sonnet-4-20250514",
             "input_tokens": 1000, "output_tokens": 500, "mode": "implement",
             "mission": "no cost field", "mission_type": "implement"},
        ], today)
        pricing = {"sonnet": {"input": 3.0, "output": 15.0}}
        from unittest.mock import patch
        with patch("app.cost_tracker.get_pricing_config", return_value=pricing):
            result = top_missions(instance_dir, today, today)
        assert len(result) == 1
        assert result[0]["cost_usd"] > 0

    def test_top_missions_missing_mission_type_reclassified(self, instance_dir, usage_dir):
        today = date.today()
        self._write_entries(usage_dir, [
            {"ts": "2026-01-01T10:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 100, "output_tokens": 50, "mode": "implement",
             "mission": "/review fix something"},
        ], today)
        result = top_missions(instance_dir, today, today)
        assert len(result) == 1
        assert result[0]["mission_type"] != ""

    def test_top_missions_newlines_replaced_in_mission(self, instance_dir, usage_dir):
        today = date.today()
        self._write_entries(usage_dir, [
            {"ts": "2026-01-01T10:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 100, "output_tokens": 50, "mode": "implement",
             "mission": "line1\nline2\nline3", "mission_type": "implement"},
        ], today)
        result = top_missions(instance_dir, today, today)
        assert "\n" not in result[0]["mission"]

    def test_top_missions_zero_tokens_included(self, instance_dir, usage_dir):
        today = date.today()
        self._write_entries(usage_dir, [
            {"ts": "2026-01-01T10:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 0, "output_tokens": 0, "mode": "implement",
             "mission": "zero", "mission_type": "implement"},
            {"ts": "2026-01-01T11:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 100, "output_tokens": 50, "mode": "implement",
             "mission": "nonzero", "mission_type": "implement"},
        ], today)
        result = top_missions(instance_dir, today, today)
        assert len(result) == 2
        assert result[-1]["mission"] == "zero"

    def test_top_missions_cost_zero_not_fallback(self, instance_dir, usage_dir):
        today = date.today()
        self._write_entries(usage_dir, [
            {"ts": "2026-01-01T10:00:00", "project": "koan", "model": "sonnet",
             "input_tokens": 100, "output_tokens": 50, "mode": "implement",
             "mission": "cached", "mission_type": "implement", "cost_usd": 0.0},
        ], today)
        from unittest.mock import patch
        call_count = []
        def tracking_estimate_cost(tokens, pricing):
            call_count.append(1)
            return 0.005
        with patch("app.cost_tracker.get_pricing_config", return_value={"sonnet": {"input": 3.0, "output": 15.0}}):
            with patch("app.cost_tracker.estimate_cost", side_effect=tracking_estimate_cost):
                result = top_missions(instance_dir, today, today)
        assert result[0]["cost_usd"] == 0.0
        assert len(call_count) == 0


class TestComputeEfficiency:
    def _write_outcomes(self, instance_dir, outcomes):
        (instance_dir / "session_outcomes.json").write_text(json.dumps(outcomes))

    def _write_usage(self, instance_dir, entries):
        today = date.today()
        usage_dir = instance_dir / "usage"
        usage_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = usage_dir / f"{today.isoformat()}.jsonl"
        with open(jsonl_path, "a") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def _make_outcome(self, project, outcome):
        return {
            "timestamp": datetime.now().isoformat(),
            "project": project,
            "mode": "implement",
            "outcome": outcome,
            "mission_type": "implement",
            "has_pr": False,
            "has_branch": False,
            "duration_minutes": 10,
            "pipeline_timed_out": False,
            "summary": "",
        }

    def test_basic_join(self, instance_dir):
        self._write_usage(instance_dir, [
            {"ts": datetime.now().isoformat(), "project": "alpha", "model": "sonnet",
             "input_tokens": 9000, "output_tokens": 1000, "mode": "implement", "mission": "x"},
        ])
        self._write_outcomes(instance_dir, [
            self._make_outcome("alpha", "productive"),
            self._make_outcome("alpha", "empty"),
        ])
        result = compute_efficiency(instance_dir, days=7)
        assert "alpha" in result["by_project"]
        p = result["by_project"]["alpha"]
        assert p["total_tokens"] == 10000
        assert p["productive_count"] == 1
        assert p["empty_count"] == 1
        assert p["total_sessions"] == 2
        assert p["tokens_per_productive_outcome"] == 10000.0
        assert p["waste_pct"] == 50.0

    def test_zero_productive_sessions(self, instance_dir):
        self._write_usage(instance_dir, [
            {"ts": datetime.now().isoformat(), "project": "beta", "model": "sonnet",
             "input_tokens": 5000, "output_tokens": 1000, "mode": "review", "mission": "y"},
        ])
        self._write_outcomes(instance_dir, [
            self._make_outcome("beta", "empty"),
            self._make_outcome("beta", "blocked"),
        ])
        result = compute_efficiency(instance_dir, days=7)
        p = result["by_project"]["beta"]
        assert p["tokens_per_productive_outcome"] is None
        assert p["waste_pct"] == 100.0

    def test_project_only_in_cost_data(self, instance_dir):
        self._write_usage(instance_dir, [
            {"ts": datetime.now().isoformat(), "project": "gamma", "model": "sonnet",
             "input_tokens": 1000, "output_tokens": 500, "mode": "implement", "mission": "z"},
        ])
        self._write_outcomes(instance_dir, [])
        result = compute_efficiency(instance_dir, days=7)
        assert "gamma" in result["by_project"]
        p = result["by_project"]["gamma"]
        assert p["total_tokens"] == 1500
        assert p["tokens_per_productive_outcome"] is None
        assert p["waste_pct"] == 0.0

    def test_project_only_in_outcome_data(self, instance_dir):
        self._write_outcomes(instance_dir, [
            self._make_outcome("delta", "productive"),
        ])
        result = compute_efficiency(instance_dir, days=7)
        assert "delta" in result["by_project"]
        p = result["by_project"]["delta"]
        assert p["total_tokens"] == 0
        assert p["tokens_per_productive_outcome"] is None

    def test_project_filter(self, instance_dir):
        self._write_usage(instance_dir, [
            {"ts": datetime.now().isoformat(), "project": "alpha", "model": "sonnet",
             "input_tokens": 1000, "output_tokens": 500, "mode": "implement", "mission": "x"},
            {"ts": datetime.now().isoformat(), "project": "beta", "model": "sonnet",
             "input_tokens": 2000, "output_tokens": 1000, "mode": "deep", "mission": "y"},
        ])
        self._write_outcomes(instance_dir, [
            self._make_outcome("alpha", "productive"),
            self._make_outcome("beta", "productive"),
        ])
        result = compute_efficiency(instance_dir, days=7, project="alpha")
        assert "alpha" in result["by_project"]
        assert "beta" not in result["by_project"]

    def test_no_data_returns_empty(self, instance_dir):
        result = compute_efficiency(instance_dir, days=7)
        assert result["by_project"] == {}
        assert result["days"] == 7
