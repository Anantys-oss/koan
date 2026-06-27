"""Unit tests for the SQLite mission mirror (app.missions_db)."""

import sqlite3

import pytest

from app import missions_db


def test_ensure_db_creates_missions_table(tmp_path):
    conn = missions_db.ensure_db(str(tmp_path))
    assert conn is not None
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(missions)")}
    finally:
        conn.close()
    assert {"id", "text", "state", "project", "created_at",
            "started_at", "completed_at", "queued_at", "complexity"} <= cols


def test_state_check_constraint_rejects_unknown(tmp_path):
    conn = missions_db.ensure_db(str(tmp_path))
    assert conn is not None
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO missions(text, state, project) VALUES (?, ?, ?)",
                ("hello", "bogus", "default"),
            )
    finally:
        conn.close()


def test_insert_and_transition(tmp_path):
    inst = str(tmp_path)
    missions_db.insert_mission_row(inst, "fix the parser", project="koan",
                                   queued_at="2026-06-27T10:00")
    assert missions_db.mission_count_by_state(inst, "pending") == 1
    missions_db.set_mission_state(inst, "fix the parser", "in_progress",
                                  started_at="2026-06-27T10:05")
    assert missions_db.mission_count_by_state(inst, "pending") == 0
    assert missions_db.mission_count_by_state(inst, "in_progress") == 1
    rows = missions_db.list_by_state(inst, "in_progress")
    assert rows[0]["project"] == "koan"
    assert rows[0]["started_at"] == "2026-06-27T10:05"


def test_count_missing_db_returns_zero(tmp_path):
    assert missions_db.mission_count_by_state(str(tmp_path / "nope"), "pending") == 0


def test_set_mission_state_rejects_invalid_state(tmp_path):
    inst = str(tmp_path)
    missions_db.insert_mission_row(inst, "a task")
    missions_db.set_mission_state(inst, "a task", "garbage")
    # Unchanged — still pending.
    assert missions_db.mission_count_by_state(inst, "pending") == 1


def test_graceful_degradation_when_ensure_db_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(missions_db, "ensure_db", lambda inst: None)
    inst = str(tmp_path)
    # No raises, safe defaults.
    missions_db.insert_mission_row(inst, "x")
    missions_db.set_mission_state(inst, "x", "done")
    assert missions_db.mission_count_by_state(inst, "pending") == 0
    assert missions_db.list_by_state(inst, "pending") == []


def test_mirror_transition_pending_is_idempotent(tmp_path):
    inst = str(tmp_path)
    missions_db.mirror_transition(inst, "do thing [project:koan]", "pending",
                                  project="koan")
    missions_db.mirror_transition(inst, "do thing [project:koan]", "pending",
                                  project="koan")
    assert missions_db.mission_count_by_state(inst, "pending") == 1


def test_set_mission_state_returns_false_when_no_live_row(tmp_path):
    inst = str(tmp_path)
    # No row at all.
    assert missions_db.set_mission_state(inst, "ghost", "done") is False


def test_set_mission_state_does_not_touch_terminal_rows(tmp_path):
    inst = str(tmp_path)
    # A historical Done row and a fresh Pending row share a canonical key.
    missions_db._insert_row(inst, "repeat task", "done")
    missions_db.insert_mission_row(inst, "repeat task")
    # Starting the pending one must not flip the historical done row.
    missions_db.set_mission_state(inst, "repeat task", "in_progress")
    assert missions_db.mission_count_by_state(inst, "done") == 1
    assert missions_db.mission_count_by_state(inst, "in_progress") == 1


def test_mirror_transition_inserts_on_miss_for_terminal_state(tmp_path):
    inst = str(tmp_path)
    # Mission was never inserted (bypassed the mirror); finalizing must still
    # create a row so the DB learns of it instead of silently dropping it.
    missions_db.mirror_transition(inst, "bypassed task", "done",
                                  completed_at="2026-06-27T10:00")
    assert missions_db.mission_count_by_state(inst, "done") == 1


def test_mirror_transition_requeue_moves_in_progress_back_to_pending(tmp_path):
    inst = str(tmp_path)
    missions_db.insert_mission_row(inst, "work")
    missions_db.set_mission_state(inst, "work", "in_progress")
    assert missions_db.mission_count_by_state(inst, "in_progress") == 1
    # Requeue: In Progress -> Pending without duplicating the row.
    missions_db.mirror_transition(inst, "work", "pending")
    assert missions_db.mission_count_by_state(inst, "pending") == 1
    assert missions_db.mission_count_by_state(inst, "in_progress") == 0


def test_mirror_transition_pending_rerun_after_done_creates_new_row(tmp_path):
    inst = str(tmp_path)
    missions_db._insert_row(inst, "again", "done")
    # A brand-new identical mission queued later gets its own pending row.
    missions_db.mirror_transition(inst, "again", "pending")
    assert missions_db.mission_count_by_state(inst, "done") == 1
    assert missions_db.mission_count_by_state(inst, "pending") == 1
