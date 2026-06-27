"""Dual-read consistency: DB and missions.md agree after transitions driven
through run.py / utils.py."""

import app.run as run
from app import missions_db
from app.utils import insert_pending_mission


def _seed(tmp_path, body):
    (tmp_path / "missions.md").write_text(body)


def test_mirror_keeps_db_in_sync_on_start_and_complete(tmp_path):
    inst = tmp_path
    _seed(inst,
        "# Missions\n\n## Pending\n- fix parser [project:koan] ⏳(2026-06-27T09:00)\n\n"
        "## In Progress\n\n## Done\n\n## Failed\n"
    )
    missions_db.reconcile(str(inst))
    assert missions_db.mission_count_by_state(str(inst), "pending") == 1

    run._start_mission_in_file(str(inst), "fix parser [project:koan]", "koan")
    assert missions_db.mission_count_by_state(str(inst), "in_progress") == 1
    assert missions_db.mission_count_by_state(str(inst), "pending") == 0

    run._update_mission_in_file(str(inst), "fix parser [project:koan]")  # complete
    assert missions_db.mission_count_by_state(str(inst), "done") == 1
    assert missions_db.mission_count_by_state(str(inst), "in_progress") == 0


def test_mirror_on_fail(tmp_path):
    inst = tmp_path
    _seed(inst,
        "# Missions\n\n## Pending\n\n"
        "## In Progress\n- broken task ⏳(2026-06-27T09:00) ▶(2026-06-27T09:05)\n\n"
        "## Done\n\n## Failed\n"
    )
    missions_db.reconcile(str(inst))
    assert missions_db.mission_count_by_state(str(inst), "in_progress") == 1

    run._update_mission_in_file(str(inst), "broken task", failed=True)
    assert missions_db.mission_count_by_state(str(inst), "failed") == 1
    assert missions_db.mission_count_by_state(str(inst), "in_progress") == 0


def test_insert_pending_mission_mirrors_to_db(tmp_path):
    inst = tmp_path
    _seed(inst, "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    missions_db.reconcile(str(inst))
    assert missions_db.mission_count_by_state(str(inst), "pending") == 0

    insert_pending_mission(inst / "missions.md", "brand new task [project:koan]")
    assert missions_db.mission_count_by_state(str(inst), "pending") == 1
    rows = missions_db.list_by_state(str(inst), "pending")
    assert rows[0]["project"] == "koan"


def test_db_error_does_not_abort_file_transition(tmp_path, monkeypatch):
    inst = tmp_path
    _seed(inst,
        "# Missions\n\n## Pending\n- fix parser [project:koan] ⏳(2026-06-27T09:00)\n\n"
        "## In Progress\n\n## Done\n\n## Failed\n"
    )
    # Force every mirror write to fail; the missions.md move must still commit.
    monkeypatch.setattr(missions_db, "ensure_db", lambda i: None)
    ok = run._start_mission_in_file(str(inst), "fix parser [project:koan]", "koan")
    assert ok is True
    content = (inst / "missions.md").read_text()
    assert "fix parser" in content.split("## In Progress")[1]


def test_requeue_mirrors_in_progress_back_to_pending(tmp_path):
    inst = tmp_path
    _seed(inst,
        "# Missions\n\n## Pending\n\n"
        "## In Progress\n- stuck task ⏳(2026-06-27T09:00) ▶(2026-06-27T09:05)\n\n"
        "## Done\n\n## Failed\n"
    )
    missions_db.reconcile(str(inst))
    assert missions_db.mission_count_by_state(str(inst), "in_progress") == 1

    run._requeue_mission_in_file(str(inst), "stuck task")
    assert missions_db.mission_count_by_state(str(inst), "pending") == 1
    assert missions_db.mission_count_by_state(str(inst), "in_progress") == 0


def test_batch_insert_mirrors_all_entries(tmp_path):
    from app.utils import insert_pending_missions
    inst = tmp_path
    _seed(inst, "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    missions_db.reconcile(str(inst))
    insert_pending_missions(
        inst / "missions.md",
        ["first task [project:koan]", "second task [project:web]"],
    )
    assert missions_db.mission_count_by_state(str(inst), "pending") == 2
