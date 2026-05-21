"""Tests for app.check_tracker — last-checked timestamp tracking."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.check_tracker import (
    get_last_checked,
    has_changed,
    mark_checked,
    _load,
    _tracker_path,
)


def _save(instance_dir, data):
    """Write tracker data directly (replaces removed check_tracker._save)."""
    import json
    path = _tracker_path(instance_dir)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def instance_dir(tmp_path):
    d = tmp_path / "instance"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# _tracker_path
# ---------------------------------------------------------------------------

class TestTrackerPath:
    def test_returns_json_file_in_instance(self, instance_dir):
        p = _tracker_path(instance_dir)
        assert p.name == ".check-tracker.json"
        assert p.parent == instance_dir


# ---------------------------------------------------------------------------
# _load / _save
# ---------------------------------------------------------------------------

class TestLoadSave:
    def test_load_returns_empty_dict_when_no_file(self, instance_dir):
        assert _load(instance_dir) == {}

    def test_save_creates_file(self, instance_dir):
        _save(instance_dir, {"key": "val"})
        assert _tracker_path(instance_dir).exists()

    def test_roundtrip(self, instance_dir):
        data = {
            "https://github.com/owner/repo/pull/1": {
                "updated_at": "2026-01-01T00:00:00Z",
                "checked_at": "2026-01-01T00:01:00Z",
            }
        }
        _save(instance_dir, data)
        loaded = _load(instance_dir)
        assert loaded == data

    def test_load_handles_corrupt_json(self, instance_dir):
        _tracker_path(instance_dir).write_text("not json{{{")
        assert _load(instance_dir) == {}

    def test_save_overwrites_existing(self, instance_dir):
        _save(instance_dir, {"a": 1})
        _save(instance_dir, {"b": 2})
        loaded = _load(instance_dir)
        assert "a" not in loaded
        assert loaded["b"] == 2


# ---------------------------------------------------------------------------
# get_last_checked
# ---------------------------------------------------------------------------

class TestGetLastChecked:
    def test_returns_none_when_never_checked(self, instance_dir):
        assert get_last_checked(instance_dir, "https://example.com") is None

    def test_returns_updated_at_when_exists(self, instance_dir):
        _save(instance_dir, {
            "https://github.com/o/r/pull/1": {
                "updated_at": "2026-02-01T12:00:00Z",
                "checked_at": "2026-02-01T12:01:00Z",
            }
        })
        result = get_last_checked(instance_dir, "https://github.com/o/r/pull/1")
        assert result == "2026-02-01T12:00:00Z"

    def test_returns_none_for_different_url(self, instance_dir):
        _save(instance_dir, {
            "https://github.com/o/r/pull/1": {"updated_at": "x", "checked_at": "y"}
        })
        assert get_last_checked(instance_dir, "https://github.com/o/r/pull/2") is None


# ---------------------------------------------------------------------------
# mark_checked
# ---------------------------------------------------------------------------

class TestMarkChecked:
    def test_creates_entry(self, instance_dir):
        mark_checked(instance_dir, "https://github.com/o/r/pull/1", "2026-02-01T12:00:00Z")
        data = _load(instance_dir)
        assert "https://github.com/o/r/pull/1" in data
        assert data["https://github.com/o/r/pull/1"]["updated_at"] == "2026-02-01T12:00:00Z"
        assert "checked_at" in data["https://github.com/o/r/pull/1"]

    def test_updates_existing_entry(self, instance_dir):
        mark_checked(instance_dir, "https://github.com/o/r/pull/1", "v1")
        mark_checked(instance_dir, "https://github.com/o/r/pull/1", "v2")
        data = _load(instance_dir)
        assert data["https://github.com/o/r/pull/1"]["updated_at"] == "v2"

    def test_preserves_other_entries(self, instance_dir):
        mark_checked(instance_dir, "url-a", "ts-a")
        mark_checked(instance_dir, "url-b", "ts-b")
        data = _load(instance_dir)
        assert data["url-a"]["updated_at"] == "ts-a"
        assert data["url-b"]["updated_at"] == "ts-b"

    def test_checked_at_is_utc_iso(self, instance_dir):
        mark_checked(instance_dir, "url-x", "2026-01-01T00:00:00Z")
        data = _load(instance_dir)
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
