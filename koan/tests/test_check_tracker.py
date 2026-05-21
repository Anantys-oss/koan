"""Tests for app.check_tracker — last-checked timestamp tracking."""

import json
from datetime import datetime, timezone

import pytest

from app.check_tracker import (
    get_last_checked,
    has_changed,
    mark_checked,
    _tracker_path,
)


@pytest.fixture
def instance_dir(tmp_path):
    d = tmp_path / "instance"
    d.mkdir()
    return d


def _write_tracker(instance_dir, data):
    """Test helper: write tracker data directly."""
    _tracker_path(instance_dir).write_text(json.dumps(data))


def _read_tracker(instance_dir):
    """Test helper: read tracker data directly."""
    p = _tracker_path(instance_dir)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# _tracker_path
# ---------------------------------------------------------------------------

class TestTrackerPath:
    def test_returns_json_file_in_instance(self, instance_dir):
        p = _tracker_path(instance_dir)
        assert p.name == ".check-tracker.json"
        assert p.parent == instance_dir


# ---------------------------------------------------------------------------
# Persistence roundtrip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_empty_when_no_file(self, instance_dir):
        assert get_last_checked(instance_dir, "any") is None

    def test_handles_corrupt_json(self, instance_dir):
        _tracker_path(instance_dir).write_text("not json{{{")
        assert get_last_checked(instance_dir, "any") is None


# ---------------------------------------------------------------------------
# get_last_checked
# ---------------------------------------------------------------------------

class TestGetLastChecked:
    def test_returns_none_when_never_checked(self, instance_dir):
        assert get_last_checked(instance_dir, "https://example.com") is None

    def test_returns_updated_at_when_exists(self, instance_dir):
        _write_tracker(instance_dir, {
            "https://github.com/o/r/pull/1": {
                "updated_at": "2026-02-01T12:00:00Z",
                "checked_at": "2026-02-01T12:01:00Z",
            }
        })
        result = get_last_checked(instance_dir, "https://github.com/o/r/pull/1")
        assert result == "2026-02-01T12:00:00Z"

    def test_returns_none_for_different_url(self, instance_dir):
        _write_tracker(instance_dir, {
            "https://github.com/o/r/pull/1": {"updated_at": "x", "checked_at": "y"}
        })
        assert get_last_checked(instance_dir, "https://github.com/o/r/pull/2") is None


# ---------------------------------------------------------------------------
# mark_checked
# ---------------------------------------------------------------------------

class TestMarkChecked:
    def test_creates_entry(self, instance_dir):
        mark_checked(instance_dir, "https://github.com/o/r/pull/1", "2026-02-01T12:00:00Z")
        data = _read_tracker(instance_dir)
        assert "https://github.com/o/r/pull/1" in data
        assert data["https://github.com/o/r/pull/1"]["updated_at"] == "2026-02-01T12:00:00Z"
        assert "checked_at" in data["https://github.com/o/r/pull/1"]

    def test_updates_existing_entry(self, instance_dir):
        mark_checked(instance_dir, "https://github.com/o/r/pull/1", "v1")
        mark_checked(instance_dir, "https://github.com/o/r/pull/1", "v2")
        data = _read_tracker(instance_dir)
        assert data["https://github.com/o/r/pull/1"]["updated_at"] == "v2"

    def test_preserves_other_entries(self, instance_dir):
        mark_checked(instance_dir, "url-a", "ts-a")
        mark_checked(instance_dir, "url-b", "ts-b")
        data = _read_tracker(instance_dir)
        assert data["url-a"]["updated_at"] == "ts-a"
        assert data["url-b"]["updated_at"] == "ts-b"

    def test_checked_at_is_utc_iso(self, instance_dir):
        mark_checked(instance_dir, "url-x", "2026-01-01T00:00:00Z")
        data = _read_tracker(instance_dir)
        checked_at = data["url-x"]["checked_at"]
        # Should parse as valid ISO timestamp
        dt = datetime.fromisoformat(checked_at)
        assert dt.tzinfo is not None  # timezone-aware


# ---------------------------------------------------------------------------
# has_changed
# ---------------------------------------------------------------------------

class TestHasChanged:
    def test_returns_true_when_never_checked(self, instance_dir):
        assert has_changed(instance_dir, "url-new", "any-ts") is True

    def test_returns_false_when_same_timestamp(self, instance_dir):
        mark_checked(instance_dir, "url-x", "2026-02-01T12:00:00Z")
        assert has_changed(instance_dir, "url-x", "2026-02-01T12:00:00Z") is False

    def test_returns_true_when_different_timestamp(self, instance_dir):
        mark_checked(instance_dir, "url-x", "2026-02-01T12:00:00Z")
        assert has_changed(instance_dir, "url-x", "2026-02-01T13:00:00Z") is True

    def test_different_urls_independent(self, instance_dir):
        mark_checked(instance_dir, "url-a", "ts-1")
        assert has_changed(instance_dir, "url-b", "ts-1") is True
