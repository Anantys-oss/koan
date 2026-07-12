"""Tests for the /brief skill handler."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from unittest.mock import patch

import pytest


@dataclass
class FakeCtx:
    koan_root: Path
    instance_dir: Path
    command_name: str = "brief"
    args: str = ""
    send_message: Optional[Callable] = None
    handle_chat: Optional[Callable] = None


@pytest.fixture
def instance(tmp_path):
    """Set up a minimal instance directory."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "events").mkdir()
    (inst / "journal").mkdir()
    return inst


class TestDigestAssembly:
    def test_basic_digest_with_pending_missions(self, tmp_path, instance):
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n"
            "- Fix the flaky test\n"
            "- Update docs\n\n"
            "## In Progress\n\n## Done\n\n## Failed\n"
        )
        (tmp_path / ".koan-status").write_text("IMPLEMENT")

        from skills.core.brief.handler import handle

        ctx = FakeCtx(koan_root=tmp_path, instance_dir=instance)
        result = handle(ctx)

        assert "2 pending" in result
        assert "Daily Brief" in result

    def test_done_last_24h_counts_recent_only(self, tmp_path, instance):
        now = datetime.now()
        recent_date = (now - timedelta(hours=2)).strftime("%Y-%m-%d")
        recent_time = (now - timedelta(hours=2)).strftime("%H:%M")
        old_date = (now - timedelta(hours=48)).strftime("%Y-%m-%d")
        old_time = (now - timedelta(hours=48)).strftime("%H:%M")
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n"
            f"## Done\n\n"
            f"- Recent task ✅({recent_date} {recent_time})\n"
            f"- Old task ✅({old_date} {old_time})\n\n"
            "## Failed\n"
        )

        from skills.core.brief.handler import handle

        ctx = FakeCtx(koan_root=tmp_path, instance_dir=instance)
        result = handle(ctx)

        assert "1 done (24h)" in result

    def test_loop_status_displayed(self, tmp_path, instance):
        (tmp_path / ".koan-status").write_text("REVIEW")
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n"
        )

        from skills.core.brief.handler import handle

        ctx = FakeCtx(koan_root=tmp_path, instance_dir=instance)
        result = handle(ctx)

        assert "Loop: REVIEW" in result

    def test_no_status_file(self, tmp_path, instance):
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n"
        )

        from skills.core.brief.handler import handle

        ctx = FakeCtx(koan_root=tmp_path, instance_dir=instance)
        result = handle(ctx)

        assert "Loop: unknown" in result

    def test_ci_section_displayed(self, tmp_path, instance):
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n## CI\n\n"
            "- [project:koan] https://github.com/o/r/pull/42 branch:b1 repo:o/r "
            "queued:2026-07-01T10:00 (attempt 0/5)\n"
            "- [project:koan] https://github.com/o/r/pull/43 branch:b2 repo:o/r "
            "queued:2026-07-01T10:01 (attempt 0/5)\n\n"
            "## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n"
        )

        from skills.core.brief.handler import handle

        ctx = FakeCtx(koan_root=tmp_path, instance_dir=instance)
        result = handle(ctx)

        assert "CI: 2 fixes queued" in result

    def test_journal_highlights_included(self, tmp_path, instance):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        journal_dir = instance / "journal" / yesterday
        journal_dir.mkdir(parents=True)
        (journal_dir / "myproject.md").write_text(
            "Refactored auth module\nFixed flaky test\n"
            "Updated docs\nExtra line\n"
        )

        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n"
        )

        from skills.core.brief.handler import handle

        ctx = FakeCtx(koan_root=tmp_path, instance_dir=instance)
        result = handle(ctx)

        assert "Journal:" in result
        assert "Refactored auth module" in result
        assert "Extra line" not in result

    def test_empty_instance_graceful(self, tmp_path, instance):
        from skills.core.brief.handler import handle

        ctx = FakeCtx(koan_root=tmp_path, instance_dir=instance)
        result = handle(ctx)

        assert "Daily Brief" in result
        assert "no data" in result

    def test_burn_rate_displayed(self, tmp_path, instance):
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n"
        )

        with patch("app.burn_rate.burn_rate_pct_per_minute", return_value=0.5):
            from skills.core.brief.handler import _add_quota_health

            parts = []
            _add_quota_health(parts, instance)

        assert any("30.0%/h" in p for p in parts)


class TestRescheduling:
    def test_reschedule_creates_event_file(self, tmp_path, instance):
        from skills.core.brief.handler import _maybe_reschedule

        _maybe_reschedule(instance)

        event_files = list((instance / "events").glob("*.json"))
        assert len(event_files) == 1
        data = json.loads(event_files[0].read_text())
        assert data["type"] == "once"
        assert "/brief" in data["mission"]
        assert "brief-" in data["mission"]

    def test_reschedule_idempotent(self, tmp_path, instance):
        from skills.core.brief.handler import _maybe_reschedule

        _maybe_reschedule(instance)
        _maybe_reschedule(instance)

        event_files = list((instance / "events").glob("*.json"))
        assert len(event_files) == 1

    def test_schedule_flag_returns_confirmation(self, tmp_path, instance):
        from skills.core.brief.handler import handle

        ctx = FakeCtx(
            koan_root=tmp_path, instance_dir=instance, args="--schedule"
        )
        result = handle(ctx)

        assert "scheduled" in result.lower()
        event_files = list((instance / "events").glob("*.json"))
        assert len(event_files) == 1

    def test_schedule_flag_idempotent(self, tmp_path, instance):
        from skills.core.brief.handler import handle

        ctx = FakeCtx(
            koan_root=tmp_path, instance_dir=instance, args="--schedule"
        )
        result1 = handle(ctx)
        result2 = handle(ctx)

        assert "already scheduled" in result2.lower()
        event_files = list((instance / "events").glob("*.json"))
        assert len(event_files) == 1
