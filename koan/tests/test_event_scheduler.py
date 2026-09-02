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

    def test_non_utf8_file_tick_does_not_crash_and_is_quarantined(self, tmp_path):
        """An event file with invalid UTF-8 bytes (byte 0xa3, the issue repro)
        must not crash tick(); it is moved to events/quarantine/ so it can't
        re-poison."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        bad = events_dir / "bad_enc.json"
        bad.write_bytes(
            b'{"type": "once", "run_at": "2020-01-01T00:00:00", "mission": "\xa3"}'
        )

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []
        assert not bad.exists(), "malformed file must leave the scan path"
        quarantine_dir = events_dir / "quarantine"
        assert (quarantine_dir / "bad_enc.json").exists()

    def test_corrupt_but_utf8_json_quarantined(self, tmp_path):
        """A valid-UTF-8 file that is invalid JSON is quarantined, not left to
        re-poison every iteration."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "bad.json").write_text("{not valid json", encoding="utf-8")

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []
        assert not (events_dir / "bad.json").exists()
        assert (events_dir / "quarantine" / "bad.json").exists()

    def test_quarantine_collision_suffixed(self, tmp_path):
        """Re-poisoning a same-named file must not clobber an earlier quarantine."""
        events_dir = tmp_path / "events"
        quarantine_dir = events_dir / "quarantine"
        quarantine_dir.mkdir(parents=True)
        (quarantine_dir / "bad.json").write_text("old corrupt", encoding="utf-8")

        events_dir.mkdir(exist_ok=True)
        (events_dir / "bad.json").write_text("{still bad", encoding="utf-8")

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []
        # The pre-existing quarantine entry is untouched.
        assert (quarantine_dir / "bad.json").read_text() == "old corrupt"
        # Exactly one suffixed collision copy now sits alongside it, and the
        # original scan-path file is gone.
        quarantined = list(quarantine_dir.glob("bad_*.json"))
        assert len(quarantined) == 1
        assert not (events_dir / "bad.json").exists()

    def test_missing_fields_quarantined(self, tmp_path):
        """Events missing run_at or mission are quarantined (fail-loud), so they
        stop re-tripping every iteration."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "no_mission.json").write_text(
            json.dumps({"type": "once", "run_at": "2020-01-01T00:00:00"}),
            encoding="utf-8",
        )
        (events_dir / "no_run_at.json").write_text(
            json.dumps({"type": "once", "mission": "Do something"}),
            encoding="utf-8",
        )

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []
        quarantine_dir = events_dir / "quarantine"
        assert (quarantine_dir / "no_mission.json").exists()
        assert (quarantine_dir / "no_run_at.json").exists()

    @pytest.mark.parametrize(
        "contents",
        ["[1, 2, 3]", "null", '{"mission": null, "run_at": 42}'],
    )
    def test_structurally_invalid_json_quarantined(self, tmp_path, contents):
        """Valid JSON with an invalid event shape cannot crash the scheduler."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "bad_shape.json").write_text(contents, encoding="utf-8")

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []
        assert (events_dir / "quarantine" / "bad_shape.json").exists()

    def test_invalid_run_at_quarantined(self, tmp_path):
        """An invalid ISO timestamp leaves the active scan path."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        _write_event(
            events_dir,
            "bad_time.json",
            {"type": "once", "run_at": "tomorrowish", "mission": "Do something"},
        )

        with patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []
        assert (events_dir / "quarantine" / "bad_time.json").exists()

    def test_transient_read_error_is_not_quarantined(self, tmp_path):
        """A read that fails transiently (mid-write, or archived by the other
        process between glob and read) is retried next tick, not destroyed."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        past = datetime.now() - timedelta(hours=1)
        _write_event(events_dir, "evt.json", _make_event(past, "Run smoke tests"))

        with patch.object(Path, "read_bytes", side_effect=OSError("EIO")), \
                patch("app.event_scheduler.insert_pending_mission") as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_not_called()
        assert result == []
        assert (events_dir / "evt.json").exists(), "valid event must survive"
        assert not (events_dir / "quarantine").exists()

    def test_failing_quarantine_does_not_abort_the_scan(self, tmp_path):
        """When quarantine itself fails, the file is skipped (old behavior) and
        later events still fire — no exception escapes tick()."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        # A regular file where the quarantine directory belongs: mkdir() raises
        # FileExistsError, i.e. an OSError from inside the except block.
        (events_dir / "quarantine").write_text("not a directory", encoding="utf-8")
        (events_dir / "aaa_bad.json").write_text("{not valid json", encoding="utf-8")
        past = datetime.now() - timedelta(hours=1)
        _write_event(events_dir, "zzz_good.json", _make_event(past, "Still fires"))

        with patch("app.event_scheduler.insert_pending_mission"):
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        assert result == ["Still fires"]
        assert (events_dir / "aaa_bad.json").exists()

    def test_tick_racing_an_in_flight_write_sees_no_partial_file(self, tmp_path):
        """write_event_file() publishes atomically: a tick() running while the
        bridge writes must not observe — and quarantine — a partial event."""
        import os as _os

        from app.event_scheduler import tick, write_event_file

        events_dir = tmp_path / "events"
        events_dir.mkdir()
        real_link = _os.link
        observed = {}

        def _tick_then_link(src, dst):
            # At this point the payload is staged but not yet published.
            observed["json_seen"] = sorted(p.name for p in events_dir.glob("*.json"))
            with patch("app.event_scheduler.insert_pending_mission"):
                observed["enqueued"] = tick(str(tmp_path))
            return real_link(src, dst)

        past = datetime.now() - timedelta(hours=1)
        with patch("app.event_scheduler.os.link", side_effect=_tick_then_link):
            path = write_event_file(events_dir, past, "Run smoke tests")

        assert observed["json_seen"] == []
        assert observed["enqueued"] == []
        assert not (events_dir / "quarantine").exists()

        # The published event is intact and fires on the next tick.
        with patch("app.event_scheduler.insert_pending_mission"):
            assert tick(str(tmp_path)) == ["Run smoke tests"]
        assert not path.exists(), "fired event is archived"
        assert not list(events_dir.glob(".koan-event-*")), "no staging leftovers"

    def test_dropped_scheduled_mission_is_reported_to_the_operator(self, tmp_path):
        """A quarantined event that still carries real mission text reaches the
        operator's outbox, not just a log line."""
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        _write_event(
            events_dir,
            "bad_time.json",
            {"type": "once", "run_at": "tomorrowish", "mission": "Run smoke tests"},
        )

        with patch("app.event_scheduler.insert_pending_mission"):
            from app.event_scheduler import tick
            tick(str(tmp_path))

        outbox = (tmp_path / "outbox.md").read_text(encoding="utf-8")
        assert "bad_time.json" in outbox
        assert "Run smoke tests" in outbox

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

    def test_past_due_event_with_utc_z_suffix_processed(self, tmp_path):
        """An overdue event whose run_at carries a 'Z' (UTC) suffix is processed,
        not crashed on. ``datetime.fromisoformat`` returns a tz-aware value for a
        'Z'-suffixed string; comparing it against a naive ``now`` raises TypeError.
        Such an event file would otherwise never be archived and poison every
        subsequent tick.
        """
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        # Far in the past, expressed as a UTC instant with explicit 'Z'.
        (events_dir / "z.json").write_text(
            json.dumps({"type": "once", "run_at": "2020-01-01T00:00:00Z",
                        "mission": "Z-suffixed mission"}),
            encoding="utf-8",
        )

        missions_path = tmp_path / "missions.md"
        missions_path.write_text("## Pending\n\n## In Progress\n\n## Done\n")

        with patch("app.event_scheduler.insert_pending_mission", return_value=True) as mock_insert:
            from app.event_scheduler import tick
            result = tick(str(tmp_path))

        mock_insert.assert_called_once()
        assert result == ["Z-suffixed mission"]
        # File must be archived so it is not reprocessed (poison-pill guard).
        assert not (events_dir / "z.json").exists()
        assert (events_dir / "archive" / "z.json").exists()

    def test_future_event_with_offset_not_inserted(self, tmp_path):
        """A future event with an explicit timezone offset is compared correctly
        and not inserted (no crash on naive-vs-aware comparison).
        """
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        # 2 hours in the future expressed in UTC; well ahead of local now regardless
        # of host offset, padded so even a +14h zone stays in the future.
        future = datetime.now() + timedelta(hours=20)
        (events_dir / "f.json").write_text(
            json.dumps({"type": "once",
                        "run_at": future.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00",
                        "mission": "Future offset mission"}),
            encoding="utf-8",
        )

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
