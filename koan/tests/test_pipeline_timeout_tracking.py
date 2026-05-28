"""Tests for pipeline timeout tracking and alerting.

Covers:
- session_tracker.record_outcome records pipeline_timed_out flag
- daily_snapshot tallies pipeline_timeouts from session outcomes
- mission_runner._check_pipeline_timeout_rate emits outbox alert
- Alert cooldown deduplication
"""

import json
import time
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from app.session_tracker import record_outcome, load_outcomes
from app import daily_snapshot


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory structure."""
    (tmp_path / "usage").mkdir()
    (tmp_path / "metrics").mkdir()
    return tmp_path


class TestRecordOutcomePipelineTimeout:
    """record_outcome persists the pipeline_timed_out flag."""

    def test_default_false(self, instance_dir):
        entry = record_outcome(
            instance_dir=str(instance_dir),
            project="proj",
            mode="implement",
            duration_minutes=5,
            journal_content="branch pushed, PR #1",
            mission_title="/implement fix",
        )
        assert entry["pipeline_timed_out"] is False

    def test_true_when_timed_out(self, instance_dir):
        entry = record_outcome(
            instance_dir=str(instance_dir),
            project="proj",
            mode="implement",
            duration_minutes=5,
            journal_content="branch pushed",
            mission_title="/implement fix",
            pipeline_timed_out=True,
        )
        assert entry["pipeline_timed_out"] is True

        # Persisted in session_outcomes.json
        outcomes = load_outcomes(instance_dir / "session_outcomes.json")
        assert outcomes[-1]["pipeline_timed_out"] is True


class TestDailySnapshotPipelineTimeouts:
    """daily_snapshot tallies pipeline_timeouts from session outcomes."""

    def _seed_outcomes(self, instance_dir, timed_out_flags):
        """Record multiple outcomes with specified timeout flags."""
        for i, flag in enumerate(timed_out_flags):
            record_outcome(
                instance_dir=str(instance_dir),
                project="proj",
                mode="implement",
                duration_minutes=5,
                journal_content=f"session {i}",
                mission_title=f"/implement task-{i}",
                pipeline_timed_out=flag,
            )

    def test_counts_timed_out_sessions(self, instance_dir):
        self._seed_outcomes(instance_dir, [True, False, True, False, True])

        snapshot = daily_snapshot._build_snapshot(instance_dir, date.today())
        assert snapshot["missions"]["pipeline_timeouts"] == 3

    def test_zero_when_no_timeouts(self, instance_dir):
        self._seed_outcomes(instance_dir, [False, False, False])

        snapshot = daily_snapshot._build_snapshot(instance_dir, date.today())
        assert snapshot["missions"]["pipeline_timeouts"] == 0

    def test_merged_in_range(self, instance_dir):
        self._seed_outcomes(instance_dir, [True, False, True])

        today = date.today()
        daily_snapshot.update_daily_snapshot(instance_dir, today)

        merged = daily_snapshot.read_metrics_range(
            instance_dir, today, today, backfill=False,
        )
        assert merged["missions"]["pipeline_timeouts"] == 2


class TestCheckPipelineTimeoutRate:
    """_check_pipeline_timeout_rate emits outbox alert when rate > 50%."""

    def _seed_outcomes(self, instance_dir, timed_out_flags):
        for i, flag in enumerate(timed_out_flags):
            record_outcome(
                instance_dir=str(instance_dir),
                project="proj",
                mode="implement",
                duration_minutes=5,
                journal_content=f"session {i}",
                mission_title=f"/implement task-{i}",
                pipeline_timed_out=flag,
            )

    def test_alerts_when_above_threshold(self, instance_dir):
        from app.mission_runner import _check_pipeline_timeout_rate

        # 6/10 = 60% > 50%
        self._seed_outcomes(instance_dir, [True] * 6 + [False] * 4)

        outbox = instance_dir / "outbox.md"
        outbox.write_text("")

        _check_pipeline_timeout_rate(str(instance_dir))

        msg = outbox.read_text()
        assert "⏳ Pipeline timeout rate: 6/10" in msg
        assert "POST_MISSION_TIMEOUT" in msg

    def test_no_alert_when_below_threshold(self, instance_dir):
        from app.mission_runner import _check_pipeline_timeout_rate

        # 4/10 = 40% < 50%
        self._seed_outcomes(instance_dir, [True] * 4 + [False] * 6)

        outbox = instance_dir / "outbox.md"
        outbox.write_text("")

        _check_pipeline_timeout_rate(str(instance_dir))

        assert outbox.read_text() == ""

    def test_no_alert_with_too_few_outcomes(self, instance_dir):
        from app.mission_runner import _check_pipeline_timeout_rate

        # Only 2 outcomes — not enough data
        self._seed_outcomes(instance_dir, [True, True])

        outbox = instance_dir / "outbox.md"
        outbox.write_text("")

        _check_pipeline_timeout_rate(str(instance_dir))

        assert outbox.read_text() == ""

    def test_cooldown_prevents_duplicate_alerts(self, instance_dir):
        from app.mission_runner import _check_pipeline_timeout_rate

        self._seed_outcomes(instance_dir, [True] * 8 + [False] * 2)

        outbox = instance_dir / "outbox.md"
        outbox.write_text("")

        # First alert fires
        _check_pipeline_timeout_rate(str(instance_dir))
        first_msg = outbox.read_text()
        assert "⏳" in first_msg

        # Reset outbox, call again — cooldown should suppress
        outbox.write_text("")
        _check_pipeline_timeout_rate(str(instance_dir))
        assert outbox.read_text() == ""

    def test_alert_fires_after_cooldown_expires(self, instance_dir):
        from app.mission_runner import (
            _check_pipeline_timeout_rate,
            _TIMEOUT_ALERT_STATE_FILE,
        )

        self._seed_outcomes(instance_dir, [True] * 7 + [False] * 3)

        # Write an expired cooldown state
        state_path = instance_dir / _TIMEOUT_ALERT_STATE_FILE
        state_path.write_text(json.dumps({"last_alert_ts": time.time() - 7200}))

        outbox = instance_dir / "outbox.md"
        outbox.write_text("")

        _check_pipeline_timeout_rate(str(instance_dir))
        assert "⏳" in outbox.read_text()

    def test_does_not_raise_on_error(self, instance_dir):
        from app.mission_runner import _check_pipeline_timeout_rate

        # Corrupt session_outcomes.json
        (instance_dir / "session_outcomes.json").write_text("not json")

        # Should not raise
        _check_pipeline_timeout_rate(str(instance_dir))
