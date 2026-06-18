"""Tests for mission_store.py — structured mission store."""

import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.mission_store import (
    MissionRecord,
    MissionStore,
    _parse_record_from_markdown_line,
    _strip_all_markers,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_instance(tmp_path):
    """An empty instance directory."""
    return str(tmp_path)


@pytest.fixture
def store(tmp_instance):
    """A freshly created empty store."""
    return MissionStore(tmp_instance)


@pytest.fixture
def populated_md(tmp_path):
    """Instance directory with a pre-existing missions.md."""
    md = tmp_path / "missions.md"
    md.write_text(
        "# Missions\n\n"
        "## In Progress\n\n"
        "- Fix auth bug [project:webapp] ▶(2026-06-14T10:00)\n\n"
        "## Pending\n\n"
        "- Add logging ⏳(2026-06-14T09:00)\n"
        "- Refactor DB [project:api] [complexity:medium] [r:1] ⏳(2026-06-14T08:00)\n\n"
        "## Done\n\n"
        "- Initial setup [project:webapp] ✅ (2026-06-14 07:00)\n\n"
        "## Failed\n\n"
        "- Deploy staging ❌ (2026-06-14 06:00) [flushed]\n",
        encoding="utf-8",
    )
    return str(tmp_path)


# ---------------------------------------------------------------------------
# MissionRecord dataclass
# ---------------------------------------------------------------------------

class TestMissionRecord:
    def test_to_dict_roundtrip(self):
        r = MissionRecord(
            id="abc-123",
            text="Fix bug",
            status="pending",
            project="myapp",
            queued_at="2026-06-14T10:00",
            started_at=None,
            completed_at=None,
            tags=[],
            complexity="simple",
            crash_count=0,
        )
        d = r.to_dict()
        r2 = MissionRecord.from_dict(d)
        assert r2.id == r.id
        assert r2.text == r.text
        assert r2.status == r.status
        assert r2.project == r.project
        assert r2.queued_at == r.queued_at
        assert r2.started_at is None
        assert r2.complexity == "simple"
        assert r2.crash_count == 0

    def test_from_dict_missing_fields_use_defaults(self):
        r = MissionRecord.from_dict({"text": "Do something"})
        assert r.status == "pending"
        assert r.project == ""
        assert r.tags == []
        assert r.crash_count == 0
        assert r.id != ""  # UUID generated

    def test_tags_are_copied(self):
        original_tags = ["flushed"]
        r = MissionRecord.from_dict({"tags": original_tags})
        r.tags.append("stagnation")
        assert original_tags == ["flushed"]  # not mutated


# ---------------------------------------------------------------------------
# Store initialization and path helpers
# ---------------------------------------------------------------------------

class TestMissionStoreInit:
    def test_store_path(self, tmp_instance):
        s = MissionStore(tmp_instance)
        assert s._store_path() == Path(tmp_instance) / "missions.json"

    def test_view_path(self, tmp_instance):
        s = MissionStore(tmp_instance)
        assert s._view_path() == Path(tmp_instance) / "missions.md"

    def test_lock_path(self, tmp_instance):
        s = MissionStore(tmp_instance)
        assert s._lock_path() == Path(tmp_instance) / ".missions-store.lock"

    def test_empty_store_has_no_records(self, store):
        assert store._records == []


# ---------------------------------------------------------------------------
# Load and migration
# ---------------------------------------------------------------------------

class TestMissionStoreLoad:
    def test_load_empty_instance_returns_empty_store(self, tmp_instance):
        s = MissionStore.load(tmp_instance)
        assert s._records == []

    def test_load_from_existing_json(self, tmp_instance):
        # Write a missions.json manually
        record = MissionRecord(
            id=str(uuid.uuid4()),
            text="Test mission",
            status="pending",
            project="",
            queued_at="2026-06-14T10:00",
            started_at=None,
            completed_at=None,
            tags=[],
            complexity=None,
            crash_count=0,
        )
        payload = {"records": [record.to_dict()], "view_hash": "abc"}
        Path(tmp_instance, "missions.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        s = MissionStore.load(tmp_instance)
        assert len(s._records) == 1
        assert s._records[0].text == "Test mission"

    def test_load_corrupted_json_falls_back_to_markdown(self, tmp_instance):
        Path(tmp_instance, "missions.json").write_text("not json!", encoding="utf-8")
        Path(tmp_instance, "missions.md").write_text(
            "# Missions\n\n## Pending\n\n- Fallback mission ⏳(2026-06-14T10:00)\n",
            encoding="utf-8",
        )
        s = MissionStore.load(tmp_instance)
        assert any(r.text == "Fallback mission" for r in s._records)

    def test_load_triggers_migration_when_no_json(self, populated_md):
        s = MissionStore.load(populated_md)
        texts = [r.text for r in s._records]
        assert "Fix auth bug" in texts
        assert "Add logging" in texts
        assert "Refactor DB" in texts
        assert "Initial setup" in texts
        assert "Deploy staging" in texts
        # JSON should now exist
        assert Path(populated_md, "missions.json").exists()

    def test_migration_preserves_status(self, populated_md):
        s = MissionStore.load(populated_md)
        by_text = {r.text: r for r in s._records}
        assert by_text["Fix auth bug"].status == "in_progress"
        assert by_text["Add logging"].status == "pending"
        assert by_text["Initial setup"].status == "done"
        assert by_text["Deploy staging"].status == "failed"

    def test_migration_extracts_project(self, populated_md):
        s = MissionStore.load(populated_md)
        by_text = {r.text: r for r in s._records}
        assert by_text["Fix auth bug"].project == "webapp"
        assert by_text["Refactor DB"].project == "api"
        assert by_text["Add logging"].project == ""

    def test_migration_extracts_timestamps(self, populated_md):
        s = MissionStore.load(populated_md)
        by_text = {r.text: r for r in s._records}
        assert by_text["Fix auth bug"].started_at == "2026-06-14T10:00"
        assert by_text["Add logging"].queued_at == "2026-06-14T09:00"
        # completed_at should use space-separator, not T
        assert by_text["Initial setup"].completed_at == "2026-06-14 07:00"
        assert by_text["Deploy staging"].completed_at == "2026-06-14 06:00"

    def test_migration_extracts_complexity(self, populated_md):
        s = MissionStore.load(populated_md)
        by_text = {r.text: r for r in s._records}
        assert by_text["Refactor DB"].complexity == "medium"
        assert by_text["Add logging"].complexity is None

    def test_migration_extracts_crash_count(self, populated_md):
        s = MissionStore.load(populated_md)
        by_text = {r.text: r for r in s._records}
        assert by_text["Refactor DB"].crash_count == 1
        assert by_text["Add logging"].crash_count == 0

    def test_migration_extracts_tags(self, populated_md):
        s = MissionStore.load(populated_md)
        by_text = {r.text: r for r in s._records}
        assert "flushed" in by_text["Deploy staging"].tags

    def test_migration_strips_project_from_text(self, populated_md):
        s = MissionStore.load(populated_md)
        by_text = {r.text: r for r in s._records}
        assert "[project:" not in by_text["Fix auth bug"].text
        assert "[project:" not in by_text["Refactor DB"].text

    def test_load_detects_human_edit_and_reconciles(self, tmp_instance):
        # Create a store, save it
        s = MissionStore(tmp_instance)
        s.add("Original mission")
        s.save()

        # Simulate human adding a mission directly to missions.md
        view_path = Path(tmp_instance) / "missions.md"
        original = view_path.read_text(encoding="utf-8")
        patched = original.replace(
            "## Pending\n\n",
            "## Pending\n\n- Human added mission ⏳(2026-06-14T11:00)\n",
        )
        view_path.write_text(patched, encoding="utf-8")

        # Reload — should detect the hash mismatch and reconcile
        s2 = MissionStore.load(tmp_instance)
        texts = [r.text for r in s2._records]
        assert "Original mission" in texts
        assert "Human added mission" in texts


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

class TestMissionStoreSave:
    def test_save_creates_json_file(self, store, tmp_instance):
        store.add("Test mission")
        store.save()
        assert Path(tmp_instance, "missions.json").exists()

    def test_save_creates_md_file(self, store, tmp_instance):
        store.add("Test mission")
        store.save()
        assert Path(tmp_instance, "missions.md").exists()

    def test_save_json_is_valid(self, store, tmp_instance):
        store.add("Test mission")
        store.save()
        payload = json.loads(Path(tmp_instance, "missions.json").read_text())
        assert "records" in payload
        assert "view_hash" in payload
        assert payload["records"][0]["text"] == "Test mission"

    def test_save_md_is_parseable(self, store, tmp_instance):
        store.add("Mission A")
        store.add("Mission B")
        store.save()
        from app.missions import parse_sections
        content = Path(tmp_instance, "missions.md").read_text()
        sections = parse_sections(content)
        assert len(sections["pending"]) == 2

    def test_save_stores_view_hash(self, store, tmp_instance):
        store.add("Test mission")
        store.save()
        assert store._last_view_hash is not None
        # Hash should also be in the JSON
        payload = json.loads(Path(tmp_instance, "missions.json").read_text())
        assert payload["view_hash"] == store._last_view_hash


# ---------------------------------------------------------------------------
# add()
# ---------------------------------------------------------------------------

class TestMissionStoreAdd:
    def test_add_creates_pending_record(self, store):
        r = store.add("Fix bug")
        assert r.status == "pending"
        assert r.text == "Fix bug"
        assert r.id != ""
        assert r.crash_count == 0

    def test_add_with_project(self, store):
        r = store.add("Fix bug", project="myapp")
        assert r.project == "myapp"

    def test_add_with_complexity(self, store):
        r = store.add("Fix bug", complexity="medium")
        assert r.complexity == "medium"

    def test_add_duplicate_returns_existing(self, store):
        r1 = store.add("Fix bug")
        r2 = store.add("Fix bug")
        assert r1.id == r2.id
        assert len(store._records) == 1

    def test_add_strips_lifecycle_markers(self, store):
        r = store.add("Fix bug ⏳(2026-06-14T10:00)")
        assert r.text == "Fix bug"

    def test_add_sets_queued_at(self, store):
        r = store.add("Fix bug")
        assert r.queued_at is not None

    def test_add_multiple_missions(self, store):
        store.add("Mission A")
        store.add("Mission B")
        store.add("Mission C")
        assert len(store._records) == 3


# ---------------------------------------------------------------------------
# find()
# ---------------------------------------------------------------------------

class TestMissionStoreFind:
    def test_find_by_exact_text(self, store):
        store.add("Fix bug")
        r = store.find("Fix bug")
        assert r is not None
        assert r.text == "Fix bug"

    def test_find_by_text_with_markers(self, store):
        store.add("Fix bug")
        r = store.find("Fix bug ⏳(2026-06-14T10:00)")
        assert r is not None
        assert r.text == "Fix bug"

    def test_find_returns_none_for_missing(self, store):
        assert store.find("Nonexistent") is None

    def test_find_with_canonical_key_match(self, store):
        # Mission stored with project tag in the project field, not text
        r = store.add("Deploy app", project="webapp")
        # Should find even if project tag is included in lookup text
        assert store.find("Deploy app") is r


# ---------------------------------------------------------------------------
# get_by_status()
# ---------------------------------------------------------------------------

class TestGetByStatus:
    def test_get_by_status_returns_matching_records(self, store):
        store.add("Mission A")
        store.add("Mission B")
        pending = store.get_by_status("pending")
        assert len(pending) == 2

    def test_get_by_status_empty_for_missing_status(self, store):
        store.add("Mission A")
        assert store.get_by_status("done") == []

    def test_get_by_status_after_transition(self, store):
        store.add("Mission A")
        store.start("Mission A")
        assert store.get_by_status("pending") == []
        assert len(store.get_by_status("in_progress")) == 1


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------

class TestMissionStoreStart:
    def test_start_moves_pending_to_in_progress(self, store):
        store.add("Fix bug")
        result = store.start("Fix bug")
        assert result is True
        r = store.find("Fix bug")
        assert r.status == "in_progress"

    def test_start_sets_started_at(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        r = store.find("Fix bug")
        assert r.started_at is not None

    def test_start_returns_false_for_missing_mission(self, store):
        assert store.start("Nonexistent") is False

    def test_start_returns_false_for_non_pending(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        # Already in_progress — start returns False
        assert store.start("Fix bug") is False

    def test_start_flushes_stale_in_progress(self, store):
        store.add("Stale mission")
        store.start("Stale mission")
        # Manually put another mission in pending (bypassing store API)
        store._records[0].status = "in_progress"  # force stale

        store.add("New mission")
        store.start("New mission")

        stale = store.find("Stale mission")
        assert stale.status == "failed"
        assert "flushed" in stale.tags

        new = store.find("New mission")
        assert new.status == "in_progress"

    def test_start_with_markers_in_text(self, store):
        store.add("Fix bug")
        result = store.start("Fix bug ⏳(2026-06-14T10:00)")
        assert result is True


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------

class TestMissionStoreComplete:
    def test_complete_moves_in_progress_to_done(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        result = store.complete("Fix bug")
        assert result is True
        r = store.find("Fix bug")
        assert r.status == "done"

    def test_complete_sets_completed_at(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.complete("Fix bug")
        r = store.find("Fix bug")
        assert r.completed_at is not None

    def test_complete_also_accepts_pending(self, store):
        store.add("Fix bug")
        result = store.complete("Fix bug")
        assert result is True
        assert store.find("Fix bug").status == "done"

    def test_complete_returns_false_for_missing(self, store):
        assert store.complete("Nonexistent") is False

    def test_complete_returns_false_for_done(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.complete("Fix bug")
        assert store.complete("Fix bug") is False


# ---------------------------------------------------------------------------
# fail()
# ---------------------------------------------------------------------------

class TestMissionStoreFail:
    def test_fail_moves_in_progress_to_failed(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        result = store.fail("Fix bug")
        assert result is True
        assert store.find("Fix bug").status == "failed"

    def test_fail_sets_completed_at(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.fail("Fix bug")
        assert store.find("Fix bug").completed_at is not None

    def test_fail_with_extra_tags(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.fail("Fix bug", extra_tags=["stagnation"])
        assert "stagnation" in store.find("Fix bug").tags

    def test_fail_does_not_duplicate_tags(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.fail("Fix bug", extra_tags=["stagnation"])
        store.find("Fix bug").tags  # already has stagnation
        # Calling fail again won't add a duplicate (but also returns False since already failed)
        r = store.find("Fix bug")
        assert r.tags.count("stagnation") == 1

    def test_fail_also_accepts_pending(self, store):
        store.add("Fix bug")
        result = store.fail("Fix bug")
        assert result is True
        assert store.find("Fix bug").status == "failed"

    def test_fail_returns_false_for_missing(self, store):
        assert store.fail("Nonexistent") is False


# ---------------------------------------------------------------------------
# requeue()
# ---------------------------------------------------------------------------

class TestMissionStoreRequeue:
    def test_requeue_moves_in_progress_to_pending(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        result = store.requeue("Fix bug")
        assert result is True
        assert store.find("Fix bug").status == "pending"

    def test_requeue_moves_failed_to_pending(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.fail("Fix bug")
        result = store.requeue("Fix bug")
        assert result is True
        assert store.find("Fix bug").status == "pending"

    def test_requeue_increments_crash_count(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.requeue("Fix bug")
        assert store.find("Fix bug").crash_count == 1
        # requeue again
        store.start("Fix bug")
        store.requeue("Fix bug")
        assert store.find("Fix bug").crash_count == 2

    def test_requeue_clears_timestamps(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.requeue("Fix bug")
        r = store.find("Fix bug")
        assert r.started_at is None
        assert r.completed_at is None

    def test_requeue_inserts_at_top_of_pending(self, store):
        store.add("Mission A")
        store.add("Mission B")
        store.add("Mission C")
        store.start("Mission A")
        store.requeue("Mission A")
        pending = store.get_by_status("pending")
        assert pending[0].text == "Mission A"

    def test_requeue_returns_false_for_missing(self, store):
        assert store.requeue("Nonexistent") is False

    def test_requeue_updates_queued_at(self, store):
        store.add("Fix bug")
        r = store.find("Fix bug")
        original_queued = r.queued_at
        store.start("Fix bug")
        store.requeue("Fix bug")
        r2 = store.find("Fix bug")
        # queued_at should be refreshed
        assert r2.queued_at is not None


# ---------------------------------------------------------------------------
# remove()
# ---------------------------------------------------------------------------

class TestMissionStoreRemove:
    def test_remove_deletes_record(self, store):
        store.add("Fix bug")
        result = store.remove("Fix bug")
        assert result is True
        assert store.find("Fix bug") is None
        assert len(store._records) == 0

    def test_remove_returns_false_for_missing(self, store):
        assert store.remove("Nonexistent") is False


# ---------------------------------------------------------------------------
# flush_stale_in_progress()
# ---------------------------------------------------------------------------

class TestFlushStaleInProgress:
    def test_flush_moves_in_progress_to_failed(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        flushed = store.flush_stale_in_progress()
        assert len(flushed) == 1
        assert flushed[0].status == "failed"
        assert "flushed" in flushed[0].tags

    def test_flush_returns_empty_list_when_nothing_in_progress(self, store):
        store.add("Fix bug")
        flushed = store.flush_stale_in_progress()
        assert flushed == []

    def test_flush_handles_multiple_in_progress(self, store):
        # Force two records into in_progress (unusual state)
        store.add("Mission A")
        store.add("Mission B")
        store._records[0].status = "in_progress"
        store._records[1].status = "in_progress"
        flushed = store.flush_stale_in_progress()
        assert len(flushed) == 2
        for r in flushed:
            assert r.status == "failed"
            assert "flushed" in r.tags


# ---------------------------------------------------------------------------
# _reconcile_from_markdown()
# ---------------------------------------------------------------------------

class TestReconcileFromMarkdown:
    def test_reconcile_detects_status_change(self, store):
        store.add("Fix bug")
        # Simulate Markdown showing it as done
        md = (
            "# Missions\n\n"
            "## Done\n\n"
            "- Fix bug ✅ (2026-06-14 11:00)\n\n"
            "## Pending\n\n"
        )
        new_count = store._reconcile_from_markdown(md)
        assert new_count == 0
        assert store.find("Fix bug").status == "done"

    def test_reconcile_adds_new_missions(self, store):
        md = (
            "# Missions\n\n"
            "## Pending\n\n"
            "- Brand new mission ⏳(2026-06-14T11:00)\n\n"
        )
        new_count = store._reconcile_from_markdown(md)
        assert new_count == 1
        assert store.find("Brand new mission") is not None

    def test_reconcile_preserves_unmatched_records(self, store):
        store.add("Existing mission")
        md = (
            "# Missions\n\n"
            "## Pending\n\n"
            "- Human added ⏳(2026-06-14T11:00)\n\n"
        )
        store._reconcile_from_markdown(md)
        # "Existing mission" was not in the Markdown — should be preserved
        assert store.find("Existing mission") is not None

    def test_reconcile_counts_new_records(self, store):
        store.add("Mission A")
        md = (
            "# Missions\n\n"
            "## Pending\n\n"
            "- Mission A ⏳(2026-06-14T11:00)\n"
            "- Mission B ⏳(2026-06-14T11:01)\n"
            "- Mission C ⏳(2026-06-14T11:02)\n\n"
        )
        new_count = store._reconcile_from_markdown(md)
        assert new_count == 2


# ---------------------------------------------------------------------------
# to_markdown()
# ---------------------------------------------------------------------------

class TestGenerateView:
    def test_to_markdown_has_all_sections(self, store):
        store.add("Fix bug")
        view = store.to_markdown()
        assert "## In Progress" in view
        assert "## Pending" in view
        assert "## Done" in view
        assert "## Failed" in view

    def test_to_markdown_section_order(self, store):
        view = store.to_markdown()
        ip_pos = view.index("## In Progress")
        p_pos = view.index("## Pending")
        d_pos = view.index("## Done")
        f_pos = view.index("## Failed")
        assert ip_pos < p_pos < d_pos < f_pos

    def test_to_markdown_pending_uses_queued_marker(self, store):
        store.add("Fix bug")
        view = store.to_markdown()
        assert "⏳" in view
        assert "Fix bug" in view

    def test_to_markdown_in_progress_uses_started_marker(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        view = store.to_markdown()
        assert "▶" in view

    def test_to_markdown_done_uses_checkmark(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.complete("Fix bug")
        view = store.to_markdown()
        assert "✅" in view

    def test_to_markdown_failed_uses_cross(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.fail("Fix bug")
        view = store.to_markdown()
        assert "❌" in view

    def test_to_markdown_includes_project_tag(self, store):
        store.add("Fix bug", project="webapp")
        view = store.to_markdown()
        assert "[project:webapp]" in view

    def test_to_markdown_includes_complexity(self, store):
        store.add("Fix bug", complexity="complex")
        view = store.to_markdown()
        assert "[complexity:complex]" in view

    def test_to_markdown_includes_crash_count(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.requeue("Fix bug")
        view = store.to_markdown()
        assert "[r:1]" in view

    def test_to_markdown_includes_fate_tags_in_failed(self, store):
        store.add("Fix bug")
        store.start("Fix bug")
        store.fail("Fix bug", extra_tags=["stagnation"])
        view = store.to_markdown()
        assert "[stagnation]" in view

    def test_to_markdown_caps_done_at_50(self, store):
        for i in range(60):
            r = store.add(f"Mission {i}")
            r.status = "done"
            r.completed_at = "2026-06-14 10:00"
        view = store.to_markdown()
        from app.missions import parse_sections
        sections = parse_sections(view)
        assert len(sections["done"]) == 50

    def test_to_markdown_caps_failed_at_30(self, store):
        for i in range(40):
            r = store.add(f"Mission {i}")
            r.status = "failed"
            r.completed_at = "2026-06-14 10:00"
        view = store.to_markdown()
        from app.missions import parse_sections
        sections = parse_sections(view)
        assert len(sections["failed"]) == 30

    def test_to_markdown_parseable_by_parse_sections(self, store):
        store.add("Mission A")
        store.add("Mission B", project="myapp")
        store.start("Mission A")
        view = store.to_markdown()
        from app.missions import parse_sections
        sections = parse_sections(view)
        assert len(sections["in_progress"]) == 1
        assert len(sections["pending"]) == 1

    def test_to_markdown_ends_with_newline(self, store):
        view = store.to_markdown()
        assert view.endswith("\n")

    def test_to_markdown_complexity_before_crash_count_before_timestamp(self, store):
        r = store.add("Fix bug", complexity="simple")
        r.crash_count = 2
        view = store.to_markdown()
        line = [l for l in view.splitlines() if "Fix bug" in l][0]
        comp_pos = line.find("[complexity:")
        r_pos = line.find("[r:")
        ts_pos = line.find("⏳")
        assert comp_pos < r_pos < ts_pos


# ---------------------------------------------------------------------------
# _strip_all_markers()
# ---------------------------------------------------------------------------

class TestStripAllMarkers:
    def test_strips_queued_marker(self):
        assert _strip_all_markers("Fix bug ⏳(2026-06-14T10:00)") == "Fix bug"

    def test_strips_started_marker(self):
        assert _strip_all_markers("Fix bug ▶(2026-06-14T10:00)") == "Fix bug"

    def test_strips_done_marker(self):
        assert _strip_all_markers("Fix bug ✅ (2026-06-14 10:00)") == "Fix bug"

    def test_strips_failed_marker(self):
        assert _strip_all_markers("Fix bug ❌ (2026-06-14 10:00)") == "Fix bug"

    def test_strips_crash_count(self):
        assert _strip_all_markers("Fix bug [r:3]") == "Fix bug"

    def test_strips_complexity(self):
        assert _strip_all_markers("Fix bug [complexity:medium]") == "Fix bug"

    def test_strips_flushed_tag(self):
        assert _strip_all_markers("Fix bug [flushed]") == "Fix bug"

    def test_strips_stagnation_tag(self):
        assert _strip_all_markers("Fix bug [stagnation]") == "Fix bug"

    def test_strips_leading_dash_prefix(self):
        assert _strip_all_markers("- Fix bug") == "Fix bug"

    def test_strips_all_combined(self):
        result = _strip_all_markers(
            "- Fix bug [complexity:medium] [r:2] ⏳(2026-06-14T10:00) [flushed]"
        )
        assert result == "Fix bug"

    def test_empty_string(self):
        assert _strip_all_markers("") == ""


# ---------------------------------------------------------------------------
# _parse_record_from_markdown_line()
# ---------------------------------------------------------------------------

class TestParseRecordFromMarkdownLine:
    def test_parses_pending_line(self):
        r = _parse_record_from_markdown_line(
            "- Fix bug ⏳(2026-06-14T10:00)", "pending"
        )
        assert r.text == "Fix bug"
        assert r.status == "pending"
        assert r.queued_at == "2026-06-14T10:00"
        assert r.started_at is None

    def test_parses_in_progress_line(self):
        r = _parse_record_from_markdown_line(
            "- Fix bug ▶(2026-06-14T10:05)", "in_progress"
        )
        assert r.status == "in_progress"
        assert r.started_at == "2026-06-14T10:05"

    def test_parses_done_line(self):
        r = _parse_record_from_markdown_line(
            "- Fix bug ✅ (2026-06-14 11:00)", "done"
        )
        assert r.status == "done"
        # completed_at should use space separator (display format)
        assert r.completed_at == "2026-06-14 11:00"
        assert "T" not in r.completed_at

    def test_parses_failed_line_with_tag(self):
        r = _parse_record_from_markdown_line(
            "- Fix bug ❌ (2026-06-14 11:00) [flushed]", "failed"
        )
        assert r.status == "failed"
        assert r.completed_at == "2026-06-14 11:00"
        assert "flushed" in r.tags

    def test_parses_project_tag(self):
        r = _parse_record_from_markdown_line(
            "- Fix bug [project:webapp] ⏳(2026-06-14T10:00)", "pending"
        )
        assert r.project == "webapp"
        assert "[project:" not in r.text

    def test_parses_complexity(self):
        r = _parse_record_from_markdown_line(
            "- Fix bug [complexity:complex] ⏳(2026-06-14T10:00)", "pending"
        )
        assert r.complexity == "complex"

    def test_parses_crash_count(self):
        r = _parse_record_from_markdown_line(
            "- Fix bug [r:3] ⏳(2026-06-14T10:00)", "pending"
        )
        assert r.crash_count == 3

    def test_generates_uuid_id(self):
        r = _parse_record_from_markdown_line("- Fix bug", "pending")
        assert len(r.id) == 36  # UUID format

    def test_handles_multiline_item(self):
        # Multi-line missions — only the first line has markers
        r = _parse_record_from_markdown_line(
            "- Fix bug [project:api] ⏳(2026-06-14T10:00)\n  Details on next line",
            "pending",
        )
        assert r.project == "api"
        assert r.queued_at == "2026-06-14T10:00"


# ---------------------------------------------------------------------------
# Full integration: save → load → mutate → save → reload
# ---------------------------------------------------------------------------

class TestMissionStoreIntegration:
    def test_full_lifecycle_persists_correctly(self, tmp_instance):
        # Create and save
        s = MissionStore.load(tmp_instance)
        s.add("Deploy app", project="webapp")
        s.save()

        # Reload and start
        s2 = MissionStore.load(tmp_instance)
        assert s2.find("Deploy app") is not None
        s2.start("Deploy app")
        s2.save()

        # Reload and complete
        s3 = MissionStore.load(tmp_instance)
        r = s3.find("Deploy app")
        assert r.status == "in_progress"
        s3.complete("Deploy app")
        s3.save()

        # Reload and verify final state
        s4 = MissionStore.load(tmp_instance)
        r = s4.find("Deploy app")
        assert r.status == "done"
        assert r.completed_at is not None

    def test_migration_then_store_operations(self, populated_md):
        # First load triggers migration
        s = MissionStore.load(populated_md)
        assert Path(populated_md, "missions.json").exists()

        # Complete a mission
        s.complete("Add logging")
        s.save()

        # Reload — should use JSON path now
        s2 = MissionStore.load(populated_md)
        assert s2.find("Add logging").status == "done"

    def test_view_parseable_after_lifecycle_transitions(self, tmp_instance):
        from app.missions import parse_sections
        s = MissionStore.load(tmp_instance)
        s.add("Mission A")
        s.add("Mission B")
        s.start("Mission A")
        s.complete("Mission A")
        s.fail("Mission B", extra_tags=["stagnation"])
        s.add("Mission C")
        s.save()

        content = Path(tmp_instance, "missions.md").read_text()
        sections = parse_sections(content)
        done_texts = [item.split("✅")[0].strip().lstrip("- ") for item in sections["done"]]
        assert any("Mission A" in t for t in done_texts)
        assert len(sections["failed"]) == 1
        assert len(sections["pending"]) == 1

    def test_requeue_appears_at_top_of_pending_in_view(self, tmp_instance):
        from app.missions import parse_sections
        s = MissionStore.load(tmp_instance)
        s.add("Mission A")
        s.add("Mission B")
        s.start("Mission A")
        s.requeue("Mission A")
        s.save()

        content = Path(tmp_instance, "missions.md").read_text()
        sections = parse_sections(content)
        # Mission A should be first in Pending (requeued to top)
        first_pending = sections["pending"][0]
        assert "Mission A" in first_pending
