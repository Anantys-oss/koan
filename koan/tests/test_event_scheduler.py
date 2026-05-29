"""Tests for event_scheduler.py — one-shot datetime-triggered mission injection."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(run_at: datetime, mission: str, type_: str = "once") -> dict:
    return {"type": type_, "run_at": run_at.isoformat(), "mission": mission}


def _write_event(events_dir: Path, name: str, data: dict) -> Path:
    path = events_dir / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# tick() — past-due events are enqueued and archived
# ---------------------------------------------------------------------------


class TestTick:
    def test_past_due_event_inserted(self, tmp_path):
        """An overdue event's mission is inserted into missions.md."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        past = datetime.now() - timedelta(hours=1)
        _write_event(events_dir, "evt.json", _make_event(past, "Check CI status"))

        missions_path = tmp_path / "missions.md"
        missions_path.write_text("## Pending\n\n## In Progress\n\n## Done\n")

        with patch("app.event_scheduler.insert_pending_mission", return_value=True) as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_called_once()
        call_args = mock_insert.call_args
        assert "Check CI status" in call_args[0][1]
        assert result == ["Check CI status"]

    def test_future_event_not_inserted(self, tmp_path):
        """A future event is not inserted."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        future = datetime.now() + timedelta(hours=2)
        _write_event(events_dir, "evt.json", _make_event(future, "Future mission"))

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []

    def test_processed_event_archived(self, tmp_path):
        """A processed event file is moved to events/archive/."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        past = datetime.now() - timedelta(minutes=5)
        event_file = _write_event(events_dir, "evt.json", _make_event(past, "Do something"))

        with patch("app.event_scheduler.insert_pending_mission", return_value=True):
            from app.event_scheduler import tick
            tick(str(tmp_path))

        archive_dir = events_dir / "archive"
        assert not event_file.exists(), "original file should be moved"
        assert (archive_dir / "evt.json").exists(), "file should be in archive"

    def test_no_events_dir_returns_empty(self, tmp_path):
        """Returns empty list when events/ directory doesn't exist."""
        from app.event_scheduler import tick
        result = tick(str(tmp_path))
        assert result == []

    def test_empty_events_dir_returns_empty(self, tmp_path):
        """Returns empty list when events/ directory is empty."""
        (tmp_path / "events").mkdir()
        from app.event_scheduler import tick
        result = tick(str(tmp_path))
        assert result == []

    def test_multiple_past_events_all_processed(self, tmp_path):
        """All overdue events in events/ are processed."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        past = datetime.now() - timedelta(hours=1)
        _write_event(events_dir, "a.json", _make_event(past, "Mission A"))
        _write_event(events_dir, "b.json", _make_event(past, "Mission B"))

        inserted = []

        def _fake_insert(path, entry, **kw):
            inserted.append(entry)
            return True

        with patch("app.event_scheduler.insert_pending_mission", side_effect=_fake_insert):
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        assert len(result) == 2
        mission_texts = " ".join(inserted)
        assert "Mission A" in mission_texts
        assert "Mission B" in mission_texts

    def test_malformed_json_skipped(self, tmp_path):
        """Malformed JSON files are skipped without crashing."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "bad.json").write_text("{not valid json", encoding="utf-8")

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []

    def test_missing_fields_skipped(self, tmp_path):
        """Events with missing run_at or mission fields are skipped."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        # Missing mission field
        (events_dir / "no_mission.json").write_text(
            json.dumps({"type": "once", "run_at": "2020-01-01T00:00:00"}),
            encoding="utf-8",
        )
        # Missing run_at field
        (events_dir / "no_run_at.json").write_text(
            json.dumps({"type": "once", "mission": "Do something"}),
            encoding="utf-8",
        )

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []

    def test_non_json_files_ignored(self, tmp_path):
        """Non-.json files in events/ are ignored."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "readme.txt").write_text("ignore me")
        (events_dir / ".gitkeep").write_text("")

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()

    def test_archive_files_not_reprocessed(self, tmp_path):
        """Files already in events/archive/ are not processed again."""
        events_dir = tmp_path / "events"
        archive_dir = events_dir / "archive"
        archive_dir.mkdir(parents=True)
        past = datetime.now() - timedelta(hours=1)
        _write_event(archive_dir, "old.json", _make_event(past, "Already done"))

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []


# ---------------------------------------------------------------------------
# parse_at_arg() — natural-language time parsing for /at command
# ---------------------------------------------------------------------------


class TestParseAtArg:
    def test_hhmm_today(self):
        """HH:MM resolves to today at that time (or tomorrow if past)."""
        from app.event_scheduler import parse_at_arg
        now = datetime(2026, 5, 23, 8, 0)
        result = parse_at_arg("09:00", now=now)
        assert result is not None
        assert result.hour == 9
        assert result.minute == 0
        assert result.date() == now.date()

    def test_hhmm_in_past_resolves_to_tomorrow(self):
        """HH:MM already past today → resolves to same time tomorrow."""
        from app.event_scheduler import parse_at_arg
        now = datetime(2026, 5, 23, 10, 0)
        result = parse_at_arg("09:00", now=now)
        assert result is not None
        assert result.date() > now.date()

    def test_iso_datetime(self):
        """ISO datetime string returned as-is."""
        from app.event_scheduler import parse_at_arg
        result = parse_at_arg("2026-04-25T09:00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 4

    def test_relative_minutes(self):
        """'30m' returns now + 30 minutes."""
        from app.event_scheduler import parse_at_arg
        now = datetime(2026, 5, 23, 8, 0)
        result = parse_at_arg("30m", now=now)
        assert result is not None
        assert result == datetime(2026, 5, 23, 8, 30)

    def test_relative_hours(self):
        """'2h' returns now + 2 hours."""
        from app.event_scheduler import parse_at_arg
        now = datetime(2026, 5, 23, 8, 0)
        result = parse_at_arg("2h", now=now)
        assert result == datetime(2026, 5, 23, 10, 0)

    def test_relative_hours_and_minutes(self):
        """'1h30m' returns now + 1h30m."""
        from app.event_scheduler import parse_at_arg
        now = datetime(2026, 5, 23, 8, 0)
        result = parse_at_arg("1h30m", now=now)
        assert result == datetime(2026, 5, 23, 9, 30)

    def test_invalid_returns_none(self):
        """Garbage input returns None."""
        from app.event_scheduler import parse_at_arg
        assert parse_at_arg("tomorrow morning") is None
        assert parse_at_arg("") is None
        assert parse_at_arg("99:99") is None


# ---------------------------------------------------------------------------
# write_event_file() — creates correctly formatted event JSON
# ---------------------------------------------------------------------------


class TestWriteEventFile:
    def test_creates_file_with_correct_fields(self, tmp_path):
        """write_event_file() creates a valid JSON event file."""
        from app.event_scheduler import write_event_file
        events_dir = tmp_path / "events"
        run_at = datetime(2026, 5, 24, 9, 0)
        path = write_event_file(events_dir, run_at, "Check deployment status")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["type"] == "once"
        assert data["run_at"] == "2026-05-24T09:00:00"
        assert data["mission"] == "Check deployment status"

    def test_creates_events_dir_if_missing(self, tmp_path):
        """events/ directory is created automatically."""
        from app.event_scheduler import write_event_file
        events_dir = tmp_path / "events"
        assert not events_dir.exists()
        write_event_file(events_dir, datetime(2026, 5, 24, 9, 0), "test")
        assert events_dir.exists()

    def test_unique_filenames(self, tmp_path):
        """Multiple calls produce distinct filenames."""
        from app.event_scheduler import write_event_file
        events_dir = tmp_path / "events"
        run_at = datetime(2026, 5, 24, 9, 0)
        p1 = write_event_file(events_dir, run_at, "Mission one")
        p2 = write_event_file(events_dir, run_at, "Mission two")
        assert p1 != p2

    def test_concurrent_writes_no_collision(self, tmp_path):
        """Concurrent write_event_file calls with same timestamp don't collide."""
        import threading
        from app.event_scheduler import write_event_file

        events_dir = tmp_path / "events"
        run_at = datetime(2026, 5, 24, 9, 0)
        results = []
        errors = []

        def write(idx):
            try:
                p = write_event_file(events_dir, run_at, f"Mission {idx}")
                results.append(p)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors: {errors}"
        assert len(set(results)) == 5, "Each call must produce a unique path"

    def test_first_filename_has_no_counter_suffix(self, tmp_path):
        """First event file uses clean name without counter suffix."""
        import re
        from app.event_scheduler import write_event_file
        events_dir = tmp_path / "events"
        run_at = datetime(2026, 5, 24, 9, 0)
        p = write_event_file(events_dir, run_at, "First")
        assert re.match(r"event_\d+\.json$", p.name), f"Unexpected name: {p.name}"
