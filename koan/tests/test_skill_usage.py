"""Tests for skill usage tracking and hint history (app.skill_usage)."""

import json
from datetime import datetime, timedelta

import pytest

from app.skill_usage import (
    _HINT_HISTORY_FILE,
    _USAGE_FILE,
    get_recently_hinted,
    get_usage_counts,
    get_used_skills,
    record_hint_shown,
    record_usage,
)


class TestRecordUsage:
    def test_creates_file(self, tmp_path):
        record_usage(str(tmp_path), "review")
        path = tmp_path / _USAGE_FILE
        assert path.exists()
        data = json.loads(path.read_text())
        today = datetime.now().strftime("%Y-%m-%d")
        assert data["review"] == [today]

    def test_deduplicates_same_day(self, tmp_path):
        record_usage(str(tmp_path), "review")
        record_usage(str(tmp_path), "review")
        data = json.loads((tmp_path / _USAGE_FILE).read_text())
        assert len(data["review"]) == 1

    def test_multiple_skills(self, tmp_path):
        record_usage(str(tmp_path), "review")
        record_usage(str(tmp_path), "plan")
        data = json.loads((tmp_path / _USAGE_FILE).read_text())
        assert "review" in data
        assert "plan" in data

    def test_prunes_old_entries(self, tmp_path):
        path = tmp_path / _USAGE_FILE
        old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
        recent_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        path.write_text(json.dumps({"review": [old_date, recent_date]}))

        record_usage(str(tmp_path), "review")
        data = json.loads(path.read_text())
        assert old_date not in data["review"]
        assert recent_date in data["review"]


class TestGetUsedSkills:
    def test_empty(self, tmp_path):
        assert get_used_skills(str(tmp_path)) == set()

    def test_returns_recent(self, tmp_path):
        record_usage(str(tmp_path), "review")
        record_usage(str(tmp_path), "plan")
        assert get_used_skills(str(tmp_path)) == {"review", "plan"}

    def test_excludes_old(self, tmp_path):
        path = tmp_path / _USAGE_FILE
        old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
        path.write_text(json.dumps({"review": [old_date]}))
        assert get_used_skills(str(tmp_path)) == set()

    def test_custom_window(self, tmp_path):
        path = tmp_path / _USAGE_FILE
        date_40d = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
        path.write_text(json.dumps({"review": [date_40d]}))
        assert "review" in get_used_skills(str(tmp_path), days=90)
        assert "review" not in get_used_skills(str(tmp_path), days=30)


class TestGetUsageCounts:
    def test_empty(self, tmp_path):
        assert get_usage_counts(str(tmp_path)) == {}

    def test_counts(self, tmp_path):
        path = tmp_path / _USAGE_FILE
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        path.write_text(json.dumps({"review": [today, yesterday], "plan": [today]}))
        counts = get_usage_counts(str(tmp_path))
        assert counts["review"] == 2
        assert counts["plan"] == 1


class TestHintHistory:
    def test_record_and_retrieve(self, tmp_path):
        record_hint_shown(str(tmp_path), "status")
        hinted = get_recently_hinted(str(tmp_path))
        assert "status" in hinted

    def test_old_hints_excluded(self, tmp_path):
        path = tmp_path / _HINT_HISTORY_FILE
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        path.write_text(json.dumps({"status": old_date}))
        hinted = get_recently_hinted(str(tmp_path))
        assert "status" not in hinted

    def test_recent_hint_included(self, tmp_path):
        path = tmp_path / _HINT_HISTORY_FILE
        recent_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        path.write_text(json.dumps({"status": recent_date}))
        hinted = get_recently_hinted(str(tmp_path))
        assert "status" in hinted

    def test_empty_dir(self, tmp_path):
        assert get_recently_hinted(str(tmp_path)) == set()

    def test_corrupted_json_returns_empty(self, tmp_path):
        path = tmp_path / _HINT_HISTORY_FILE
        path.write_text("not json!")
        assert get_recently_hinted(str(tmp_path)) == set()
