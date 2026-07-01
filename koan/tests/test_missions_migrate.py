"""Tests for missions.md → SQLite migration (app.missions_db.migrate_md_to_sqlite)."""

from app import missions_db


def _write(inst, text):
    (inst / "missions.md").write_text(text)


def test_migrate_extracts_states_and_timestamps(tmp_path):
    inst = tmp_path
    _write(inst,
        "# Missions\n\n## Pending\n- queued one ⏳(2026-06-27T09:00)\n\n"
        "## In Progress\n- running two ⏳(2026-06-27T09:01) ▶(2026-06-27T09:05)\n\n"
        "## Done\n- done three ✅ (2026-06-26 18:00)\n\n"
        "## Failed\n- bad four ❌ (2026-06-26 19:00)\n"
    )
    report = missions_db.migrate_md_to_sqlite(str(inst))
    assert missions_db.mission_count_by_state(str(inst), "pending") == 1
    assert missions_db.mission_count_by_state(str(inst), "in_progress") == 1
    assert missions_db.mission_count_by_state(str(inst), "done") == 1
    assert missions_db.mission_count_by_state(str(inst), "failed") == 1
    assert report["unparseable"] == []
    assert report["inserted"] == 4
    ip = missions_db.list_by_state(str(inst), "in_progress")[0]
    assert ip["started_at"] == "2026-06-27T09:05"


def test_migrate_missing_file_returns_empty_report(tmp_path):
    report = missions_db.migrate_md_to_sqlite(str(tmp_path))
    assert report["inserted"] == 0
    assert report["unparseable"] == []


def test_reconcile_is_idempotent(tmp_path):
    inst = tmp_path
    _write(inst, "# Missions\n\n## Pending\n- a\n- b\n")
    missions_db.reconcile(str(inst))
    missions_db.reconcile(str(inst))
    assert missions_db.mission_count_by_state(str(inst), "pending") == 2


def test_migrate_handles_complex_block_missions(tmp_path):
    inst = tmp_path
    # Complex ### block missions must migrate to one row keyed on the title line.
    _write(inst,
        "# Missions\n\n## Pending\n"
        "### build the widget [project:koan] ⏳(2026-06-27T09:00)\n"
        "    some detail line\n"
        "    another detail\n"
    )
    report = missions_db.migrate_md_to_sqlite(str(inst))
    assert report["inserted"] == 1
    assert report["unparseable"] == []
    rows = missions_db.list_by_state(str(inst), "pending")
    assert rows[0]["text"] == "build the widget [project:koan]"
    assert rows[0]["project"] == "koan"


def test_dry_run_does_not_write_and_reports_by_state(tmp_path):
    inst = tmp_path
    _write(inst,
        "# Missions\n\n## Pending\n- a\n- b\n\n## Done\n- c ✅ (2026-06-26 18:00)\n")
    report = missions_db.migrate_md_to_sqlite(str(inst), dry_run=True)
    assert report["inserted"] == 3
    assert report["by_state"] == {"pending": 2, "done": 1}
    # Nothing was written.
    assert missions_db.mission_count_by_state(str(inst), "pending") == 0


def test_migrate_keys_on_canonical_text(tmp_path):
    inst = tmp_path
    _write(inst, "# Missions\n\n## Pending\n- fix [r:2] [complexity:high] ⏳(2026-06-27T09:00)\n")
    missions_db.migrate_md_to_sqlite(str(inst))
    rows = missions_db.list_by_state(str(inst), "pending")
    assert rows[0]["text"] == "fix"
