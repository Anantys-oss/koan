"""Tests for app.suggestion_engine — automation suggestion generation."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.suggestion_engine import (
    _dedup_against_existing,
    _format_outbox_message,
    _format_recurring_entries,
    _load_project_learnings,
    _parse_suggestions,
    _read_tracker,
    _record_suggestion,
    _seconds_since_last,
    _suggestions_today,
    is_eligible,
    maybe_suggest_automations,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory."""
    (tmp_path / "outbox.md").write_text("")
    (tmp_path / "recurring.json").write_text("[]")
    mem = tmp_path / "memory" / "projects" / "myproject"
    mem.mkdir(parents=True)
    (mem / "learnings.md").write_text("- Always run tests before pushing\n- Use ruff for linting\n")
    return str(tmp_path)


@pytest.fixture
def tracker_path(tmp_path):
    return tmp_path / ".suggestion-tracker.json"


# ---------------------------------------------------------------------------
# _parse_suggestions
# ---------------------------------------------------------------------------


class TestParseSuggestions:
    """Tests for JSON parsing from model output."""

    def test_clean_json_array(self):
        raw = '[{"command": "/weekly task", "rationale": "reason"}]'
        result = _parse_suggestions(raw)
        assert len(result) == 1
        assert result[0]["command"] == "/weekly task"

    def test_json_with_markdown_fences(self):
        raw = '```json\n[{"command": "/daily x", "rationale": "y"}]\n```'
        result = _parse_suggestions(raw)
        assert len(result) == 1

    def test_json_with_surrounding_text(self):
        raw = 'Here are suggestions:\n[{"command": "/weekly z", "rationale": "r"}]\nDone.'
        result = _parse_suggestions(raw)
        assert len(result) == 1

    def test_empty_array(self):
        assert _parse_suggestions("[]") == []

    def test_invalid_json(self):
        assert _parse_suggestions("not json at all") == []

    def test_missing_required_fields(self):
        raw = '[{"command": "/weekly x"}, {"rationale": "only rationale"}]'
        result = _parse_suggestions(raw)
        assert len(result) == 0  # both missing one required field

    def test_mixed_valid_invalid(self):
        raw = '[{"command": "/weekly x", "rationale": "r"}, {"bad": true}]'
        result = _parse_suggestions(raw)
        assert len(result) == 1

    def test_not_a_list(self):
        raw = '{"command": "/weekly x", "rationale": "r"}'
        assert _parse_suggestions(raw) == []


# ---------------------------------------------------------------------------
# Tracker functions
# ---------------------------------------------------------------------------


class TestTracker:
    """Tests for tracker read/write and time calculations."""

    def test_read_nonexistent_tracker(self, instance_dir):
        tracker = _read_tracker(instance_dir)
        assert tracker == {}

    def test_seconds_since_last_none_when_empty(self):
        assert _seconds_since_last({}, "proj") is None

    def test_seconds_since_last_returns_positive(self):
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        tracker = {"proj": {"last_suggested_at": one_hour_ago}}
        secs = _seconds_since_last(tracker, "proj")
        assert secs is not None
        assert 3500 < secs < 3700  # ~1 hour

    def test_seconds_since_last_handles_naive_datetime(self):
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        tracker = {"proj": {"last_suggested_at": one_hour_ago}}
        secs = _seconds_since_last(tracker, "proj")
        assert secs is not None

    def test_suggestions_today_zero_when_empty(self):
        assert _suggestions_today({}, "proj") == 0

    def test_suggestions_today_counts_correctly(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tracker = {"proj": {"last_date": today, "count_today": 3}}
        assert _suggestions_today(tracker, "proj") == 3

    def test_suggestions_today_resets_on_new_day(self):
        tracker = {"proj": {"last_date": "2020-01-01", "count_today": 5}}
        assert _suggestions_today(tracker, "proj") == 0

    def test_record_suggestion(self, instance_dir):
        _record_suggestion(instance_dir, "myproject")
        tracker = _read_tracker(instance_dir)
        assert tracker["myproject"]["count_today"] == 1
        assert "last_suggested_at" in tracker["myproject"]

    def test_record_suggestion_increments(self, instance_dir):
        _record_suggestion(instance_dir, "myproject")
        _record_suggestion(instance_dir, "myproject")
        tracker = _read_tracker(instance_dir)
        assert tracker["myproject"]["count_today"] == 2


# ---------------------------------------------------------------------------
# is_eligible
# ---------------------------------------------------------------------------


class TestIsEligible:
    """Tests for eligibility checks."""

    def test_eligible_when_all_conditions_met(self, instance_dir):
        assert is_eligible(instance_dir, "myproject", "deep") is True

    def test_not_eligible_when_disabled(self, instance_dir):
        with patch("app.suggestion_engine._load_suggestion_config",
                   return_value={"enabled": False}):
            assert is_eligible(instance_dir, "myproject", "deep") is False

    def test_not_eligible_in_review_mode(self, instance_dir):
        assert is_eligible(instance_dir, "myproject", "review") is False

    def test_not_eligible_in_wait_mode(self, instance_dir):
        assert is_eligible(instance_dir, "myproject", "wait") is False

    def test_not_eligible_when_focus_active(self, instance_dir):
        assert is_eligible(instance_dir, "myproject", "deep", focus_active=True) is False

    def test_not_eligible_when_cooldown_not_elapsed(self, instance_dir):
        _record_suggestion(instance_dir, "myproject")
        # Default cooldown is 24h, so this should fail
        assert is_eligible(instance_dir, "myproject", "deep") is False

    def test_not_eligible_when_daily_cap_hit(self, instance_dir):
        with patch("app.suggestion_engine._load_suggestion_config",
                   return_value={"enabled": True, "min_interval_hours": 0, "max_per_day": 1}):
            _record_suggestion(instance_dir, "myproject")
            assert is_eligible(instance_dir, "myproject", "deep") is False

    def test_eligible_in_implement_mode(self, instance_dir):
        assert is_eligible(instance_dir, "myproject", "implement") is True


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


class TestContextAssembly:
    """Tests for learnings loading and recurring formatting."""

    def test_load_project_learnings(self, instance_dir):
        result = _load_project_learnings(instance_dir, "myproject")
        assert "Always run tests" in result

    def test_load_project_learnings_missing(self, instance_dir):
        result = _load_project_learnings(instance_dir, "nonexistent")
        assert "no learnings" in result

    def test_format_recurring_entries_empty(self):
        assert _format_recurring_entries([]) == "(none configured)"

    def test_format_recurring_entries(self):
        entries = [
            {"frequency": "daily", "text": "check status", "project": "myproj"},
            {"frequency": "weekly", "text": "audit deps", "at": "09:00"},
        ]
        result = _format_recurring_entries(entries)
        assert "/daily [project:myproj] check status" in result
        assert "/weekly at 09:00 audit deps" in result


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Tests for keyword-overlap deduplication."""

    def test_no_duplicates_when_no_existing(self, instance_dir):
        suggestions = [{"command": "/weekly do something new", "rationale": "r"}]
        result = _dedup_against_existing(suggestions, instance_dir, "myproject")
        assert len(result) == 1

    def test_removes_overlapping_suggestion(self, instance_dir):
        # Write an existing recurring task
        recurring_path = Path(instance_dir) / "recurring.json"
        recurring_path.write_text(json.dumps([
            {"frequency": "daily", "text": "check security vulnerabilities", "project": "myproject", "enabled": True}
        ]))
        suggestions = [
            {"command": "/weekly [project:myproject] check security vulnerabilities weekly", "rationale": "r"},
            {"command": "/weekly [project:myproject] update documentation freshness", "rationale": "r"},
        ]
        result = _dedup_against_existing(suggestions, instance_dir, "myproject")
        # First should be filtered (overlaps with existing), second should remain
        assert len(result) == 1
        assert "documentation" in result[0]["command"]

    def test_keeps_non_overlapping(self, instance_dir):
        recurring_path = Path(instance_dir) / "recurring.json"
        recurring_path.write_text(json.dumps([
            {"frequency": "daily", "text": "run linter", "project": "myproject", "enabled": True}
        ]))
        suggestions = [
            {"command": "/weekly [project:myproject] audit API security posture", "rationale": "r"}
        ]
        result = _dedup_against_existing(suggestions, instance_dir, "myproject")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Outbox formatting
# ---------------------------------------------------------------------------


class TestFormatOutbox:
    """Tests for outbox message formatting."""

    def test_format_basic_message(self):
        suggestions = [
            {
                "command": "/weekly [project:foo] run security audit",
                "rationale": "Catches vulnerabilities early",
                "category": "security",
                "confidence": "high",
            }
        ]
        msg = _format_outbox_message("foo", suggestions)
        assert "foo" in msg
        assert "/weekly" in msg
        assert "security" in msg
        assert "Copy any command" in msg

    def test_format_multiple_suggestions(self):
        suggestions = [
            {"command": "/weekly x", "rationale": "r1", "category": "security", "confidence": "high"},
            {"command": "/daily y", "rationale": "r2", "category": "quality", "confidence": "medium"},
        ]
        msg = _format_outbox_message("proj", suggestions)
        assert "/weekly x" in msg
        assert "/daily y" in msg


# ---------------------------------------------------------------------------
# maybe_suggest_automations (integration)
# ---------------------------------------------------------------------------


class TestMaybeSuggestAutomations:
    """Integration tests for the main entry point."""

    def test_writes_to_outbox_on_success(self, instance_dir):
        fake_response = json.dumps([
            {
                "command": "/weekly [project:myproject] scan for dead code",
                "rationale": "Keeps codebase clean",
                "category": "quality",
                "confidence": "high",
            }
        ])
        with patch("app.suggestion_engine.is_eligible", return_value=True), \
             patch("app.provider.run_command", return_value=fake_response):
            result = maybe_suggest_automations(
                instance_dir, "myproject", "/fake/path", "deep"
            )
        assert result is True
        outbox = Path(instance_dir) / "outbox.md"
        content = outbox.read_text()
        assert "dead code" in content

    def test_returns_false_when_not_eligible(self, instance_dir):
        result = maybe_suggest_automations(
            instance_dir, "myproject", "/fake/path", "review"
        )
        assert result is False

    def test_returns_false_on_empty_suggestions(self, instance_dir):
        with patch("app.suggestion_engine.is_eligible", return_value=True), \
             patch("app.provider.run_command", return_value="[]"):
            result = maybe_suggest_automations(
                instance_dir, "myproject", "/fake/path", "deep"
            )
        assert result is False

    def test_returns_false_on_model_failure(self, instance_dir):
        with patch("app.suggestion_engine.is_eligible", return_value=True), \
             patch("app.provider.run_command", side_effect=RuntimeError("API error")):
            result = maybe_suggest_automations(
                instance_dir, "myproject", "/fake/path", "deep"
            )
        assert result is False

    def test_records_tracker_on_success(self, instance_dir):
        fake_response = json.dumps([
            {
                "command": "/weekly [project:myproject] run audit",
                "rationale": "Keeps things tidy",
                "category": "quality",
                "confidence": "medium",
            }
        ])
        with patch("app.suggestion_engine.is_eligible", return_value=True), \
             patch("app.provider.run_command", return_value=fake_response):
            maybe_suggest_automations(
                instance_dir, "myproject", "/fake/path", "deep"
            )
        tracker = _read_tracker(instance_dir)
        assert "myproject" in tracker
        assert tracker["myproject"]["count_today"] >= 1
