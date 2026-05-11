"""Tests for checkpoint_manager.py — structured mission progress checkpoints."""

import json
from pathlib import Path

import pytest

from app.checkpoint_manager import (
    create_checkpoint,
    delete_checkpoint,
    format_recovery_context,
    list_checkpoints,
    mission_hash,
    parse_checkpoint_markers,
    read_checkpoint,
    update_checkpoint,
    update_from_pending,
    update_from_stdout,
    _extract_steps_from_pending,
)


@pytest.fixture
def instance_dir(tmp_path):
    """Minimal instance dir with journal subdirectory."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "journal").mkdir()
    return inst


class TestMissionHash:
    def test_deterministic(self):
        assert mission_hash("fix the bug") == mission_hash("fix the bug")

    def test_strips_whitespace(self):
        assert mission_hash("  fix the bug  ") == mission_hash("fix the bug")

    def test_different_missions_different_hashes(self):
        assert mission_hash("fix the bug") != mission_hash("add a feature")

    def test_returns_12_chars(self):
        assert len(mission_hash("anything")) == 12


class TestCreateCheckpoint:
    def test_creates_file(self, instance_dir):
        path = create_checkpoint(str(instance_dir), "fix the bug", "myproject", 5)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["mission"] == "fix the bug"
        assert data["project"] == "myproject"
        assert data["run_num"] == 5
        assert data["branch"] == ""
        assert data["steps_done"] == []
        assert data["steps_remaining"] == []
        assert "started_at" in data
        assert "updated_at" in data

    def test_creates_checkpoints_dir(self, instance_dir):
        create_checkpoint(str(instance_dir), "test", "proj")
        assert (instance_dir / "journal" / "checkpoints").is_dir()


class TestReadCheckpoint:
    def test_read_existing(self, instance_dir):
        create_checkpoint(str(instance_dir), "fix the bug", "proj", 1)
        cp = read_checkpoint(str(instance_dir), "fix the bug")
        assert cp is not None
        assert cp["mission"] == "fix the bug"

    def test_read_nonexistent(self, instance_dir):
        assert read_checkpoint(str(instance_dir), "no such mission") is None

    def test_read_corrupt_json(self, instance_dir):
        h = mission_hash("corrupt")
        d = instance_dir / "journal" / "checkpoints"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{h}.json").write_text("not json!")
        assert read_checkpoint(str(instance_dir), "corrupt") is None


class TestUpdateCheckpoint:
    def test_update_branch(self, instance_dir):
        create_checkpoint(str(instance_dir), "task", "proj")
        ok = update_checkpoint(str(instance_dir), "task", branch="koan.atoomic/fix-it")
        assert ok
        cp = read_checkpoint(str(instance_dir), "task")
        assert cp["branch"] == "koan.atoomic/fix-it"

    def test_update_steps_done_appends(self, instance_dir):
        create_checkpoint(str(instance_dir), "task", "proj")
        update_checkpoint(str(instance_dir), "task", steps_done=["step1"])
        update_checkpoint(str(instance_dir), "task", steps_done=["step2", "step1"])  # step1 deduped
        cp = read_checkpoint(str(instance_dir), "task")
        assert cp["steps_done"] == ["step1", "step2"]

    def test_update_steps_remaining(self, instance_dir):
        create_checkpoint(str(instance_dir), "task", "proj")
        update_checkpoint(str(instance_dir), "task", steps_remaining=["todo1", "todo2"])
        cp = read_checkpoint(str(instance_dir), "task")
        assert cp["steps_remaining"] == ["todo1", "todo2"]

    def test_update_nonexistent_returns_false(self, instance_dir):
        assert update_checkpoint(str(instance_dir), "nope", branch="x") is False

    def test_update_refreshes_timestamp(self, instance_dir):
        create_checkpoint(str(instance_dir), "task", "proj")
        cp1 = read_checkpoint(str(instance_dir), "task")
        update_checkpoint(str(instance_dir), "task", branch="b")
        cp2 = read_checkpoint(str(instance_dir), "task")
        assert cp2["updated_at"] >= cp1["updated_at"]


class TestDeleteCheckpoint:
    def test_delete_existing(self, instance_dir):
        create_checkpoint(str(instance_dir), "task", "proj")
        assert delete_checkpoint(str(instance_dir), "task") is True
        assert read_checkpoint(str(instance_dir), "task") is None

    def test_delete_nonexistent(self, instance_dir):
        assert delete_checkpoint(str(instance_dir), "nope") is False


class TestListCheckpoints:
    def test_empty(self, instance_dir):
        assert list_checkpoints(str(instance_dir)) == []

    def test_lists_all(self, instance_dir):
        create_checkpoint(str(instance_dir), "task1", "proj")
        create_checkpoint(str(instance_dir), "task2", "proj")
        cps = list_checkpoints(str(instance_dir))
        assert len(cps) == 2
        missions = {cp["mission"] for cp in cps}
        assert missions == {"task1", "task2"}


class TestParseCheckpointMarkers:
    def test_single_marker(self):
        text = 'Some output\nCHECKPOINT: {"steps_done": ["read code"]}\nMore output'
        markers = parse_checkpoint_markers(text)
        assert len(markers) == 1
        assert markers[0]["steps_done"] == ["read code"]

    def test_multiple_markers(self):
        text = (
            'CHECKPOINT: {"steps_done": ["step1"]}\n'
            'work happening\n'
            'CHECKPOINT: {"steps_done": ["step2"], "branch": "koan/fix"}\n'
        )
        markers = parse_checkpoint_markers(text)
        assert len(markers) == 2

    def test_invalid_json_skipped(self):
        text = 'CHECKPOINT: {not valid json}\nCHECKPOINT: {"steps_done": ["ok"]}'
        markers = parse_checkpoint_markers(text)
        assert len(markers) == 1
        assert markers[0]["steps_done"] == ["ok"]

    def test_no_markers(self):
        assert parse_checkpoint_markers("just regular output") == []


class TestUpdateFromStdout:
    def test_merges_markers(self, instance_dir):
        create_checkpoint(str(instance_dir), "task", "proj")
        stdout = 'CHECKPOINT: {"steps_done": ["explored codebase"], "branch": "koan/fix"}'
        count = update_from_stdout(str(instance_dir), "task", stdout)
        assert count == 1
        cp = read_checkpoint(str(instance_dir), "task")
        assert "explored codebase" in cp["steps_done"]
        assert cp["branch"] == "koan/fix"

    def test_no_markers_returns_zero(self, instance_dir):
        create_checkpoint(str(instance_dir), "task", "proj")
        assert update_from_stdout(str(instance_dir), "task", "no markers here") == 0


class TestUpdateFromPending:
    def test_extracts_timestamped_steps(self, instance_dir):
        create_checkpoint(str(instance_dir), "task", "proj")
        pending_path = instance_dir / "journal" / "pending.md"
        pending_path.write_text(
            "# Mission\nProject: proj\n---\n"
            "09:12 — Reading migrations/ and models.py\n"
            "09:14 — Branch created\n"
            "09:17 — Migration written\n"
        )
        ok = update_from_pending(str(instance_dir), "task")
        assert ok
        cp = read_checkpoint(str(instance_dir), "task")
        assert len(cp["steps_done"]) == 3
        assert "Reading migrations/ and models.py" in cp["steps_done"]
        assert "Branch created" in cp["steps_done"]

    def test_no_pending_returns_false(self, instance_dir):
        create_checkpoint(str(instance_dir), "task", "proj")
        assert update_from_pending(str(instance_dir), "task") is False


class TestExtractStepsFromPending:
    def test_basic(self):
        content = "header\n---\n09:12 — step one\n09:14 — step two\n"
        steps = _extract_steps_from_pending(content)
        assert steps == ["step one", "step two"]

    def test_ignores_before_separator(self):
        content = "09:00 — not a step\n---\n10:00 — real step\n"
        steps = _extract_steps_from_pending(content)
        assert steps == ["real step"]

    def test_handles_dash_variants(self):
        content = "---\n09:12 - step with hyphen\n09:13 – step with en-dash\n"
        steps = _extract_steps_from_pending(content)
        assert len(steps) == 2

    def test_empty_returns_empty(self):
        assert _extract_steps_from_pending("") == []
        assert _extract_steps_from_pending("no separator here") == []


class TestFormatRecoveryContext:
    def test_basic_formatting(self):
        cp = {
            "mission": "fix the bug",
            "project": "myproject",
            "branch": "koan.atoomic/fix-bug",
            "started_at": "2026-05-11T09:00:00",
            "steps_done": ["read code", "created branch"],
            "steps_remaining": ["write tests"],
        }
        text = format_recovery_context(cp)
        assert "Recovery Context" in text
        assert "koan.atoomic/fix-bug" in text
        assert "read code" in text
        assert "created branch" in text
        assert "write tests" in text
        assert "Resume from where" in text

    def test_minimal_checkpoint(self):
        cp = {"mission": "task", "project": "proj"}
        text = format_recovery_context(cp)
        assert "Recovery Context" in text
        assert "proj" in text
