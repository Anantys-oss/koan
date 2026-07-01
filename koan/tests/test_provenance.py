"""Tests for app.provenance — per-mission file-touch provenance."""

import json
from unittest.mock import patch

import app.provenance as provenance


def _patch_git(changed, sha):
    return (
        patch.object(provenance, "get_changed_files", return_value=changed),
        patch.object(provenance, "_head_sha", return_value=sha),
    )


def test_record_writes_expected_fields(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    p_files, p_sha = _patch_git(["a/b.py", "c.py"], "deadbeef")
    with p_files, p_sha:
        provenance.record_provenance(str(inst), "my-toolkit", "/repo", "Add widget")

    path = inst / ".mission-provenance.jsonl"
    rec = json.loads(path.read_text().splitlines()[0])
    assert rec["mission"] == "Add widget"
    assert rec["project"] == "my-toolkit"
    assert rec["commit_sha"] == "deadbeef"
    assert rec["files"] == ["a/b.py", "c.py"]
    assert rec["ts"]  # non-empty ISO timestamp


def test_record_tolerates_empty_file_list(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    p_files, p_sha = _patch_git([], "")
    with p_files, p_sha:
        provenance.record_provenance(str(inst), "my-toolkit", "/repo", "First commit")

    rec = json.loads((inst / ".mission-provenance.jsonl").read_text().splitlines()[0])
    assert rec["files"] == []
    assert rec["commit_sha"] == ""


def test_record_flags_incomplete_on_failed_diff(tmp_path):
    # Empty file list, valid HEAD, base ref resolves -- but the diff command
    # itself fails (rc != 0). The empty list is a broken read, not a no-op.
    inst = tmp_path / "instance"
    inst.mkdir()
    with patch.object(provenance, "get_changed_files", return_value=[]), \
         patch.object(provenance, "_head_sha", return_value="sha"), \
         patch.object(provenance, "_run_git", return_value="commit"), \
         patch.object(provenance, "_run_git_rc", return_value=(1, "")):
        provenance.record_provenance(str(inst), "proj", "/repo", "broken diff")

    rec = json.loads((inst / ".mission-provenance.jsonl").read_text().splitlines()[0])
    assert rec["incomplete"] is True


def test_record_genuine_noop_not_incomplete(tmp_path):
    # Empty file list, valid HEAD, base ref resolves, diff exits cleanly:
    # a real no-op must NOT be flagged incomplete.
    inst = tmp_path / "instance"
    inst.mkdir()
    with patch.object(provenance, "get_changed_files", return_value=[]), \
         patch.object(provenance, "_head_sha", return_value="sha"), \
         patch.object(provenance, "_run_git", return_value="commit"), \
         patch.object(provenance, "_run_git_rc", return_value=(0, "")):
        provenance.record_provenance(str(inst), "proj", "/repo", "noop")

    rec = json.loads((inst / ".mission-provenance.jsonl").read_text().splitlines()[0])
    assert rec["incomplete"] is False


def test_record_rotation_caps_entries(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    p_files, p_sha = _patch_git(["x.py"], "sha")
    with p_files, p_sha, patch.object(provenance, "_MAX_PROVENANCE_ENTRIES", 3):
        for i in range(5):
            provenance.record_provenance(str(inst), "proj", "/repo", f"m{i}")

    lines = (inst / ".mission-provenance.jsonl").read_text().splitlines()
    missions = [json.loads(line)["mission"] for line in lines if line.strip()]
    assert missions == ["m2", "m3", "m4"]


def test_read_provenance_filters_by_file_and_project(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    p_files, p_sha = _patch_git(["src/core.py"], "s1")
    with p_files, p_sha:
        provenance.record_provenance(str(inst), "proj", "/repo", "touch core")
    p_files2, p_sha2 = _patch_git(["src/other.py"], "s2")
    with p_files2, p_sha2:
        provenance.record_provenance(str(inst), "proj", "/repo", "touch other")

    hits = provenance.read_provenance(str(inst), "proj", "src/core.py")
    assert len(hits) == 1
    assert hits[0]["mission"] == "touch core"
    # Wrong project yields nothing
    assert provenance.read_provenance(str(inst), "elsewhere", "src/core.py") == []
