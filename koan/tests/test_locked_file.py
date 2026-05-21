"""Tests for app.locked_file — centralised file-locking helpers."""

import json

import pytest

from app.locked_file import locked_json_modify, locked_json_read, locked_jsonl_append


@pytest.fixture
def data_file(tmp_path):
    return tmp_path / "data.json"


# ---------------------------------------------------------------------------
# locked_json_modify
# ---------------------------------------------------------------------------

class TestLockedJsonModify:
    def test_creates_file_when_missing(self, data_file):
        locked_json_modify(data_file, lambda d: d.update({"k": 1}))
        assert data_file.exists()
        assert json.loads(data_file.read_text()) == {"k": 1}

    def test_reads_existing_data(self, data_file):
        data_file.write_text(json.dumps({"existing": True}))
        locked_json_modify(data_file, lambda d: d.update({"new": True}))
        result = json.loads(data_file.read_text())
        assert result == {"existing": True, "new": True}

    def test_returns_fn_result(self, data_file):
        result = locked_json_modify(data_file, lambda d: 42)
        assert result == 42

    def test_returns_none_when_fn_has_no_return(self, data_file):
        result = locked_json_modify(data_file, lambda d: d.update({"k": 1}))
        assert result is None

    def test_default_factory_list(self, data_file):
        def append(lst):
            lst.append("item")
        locked_json_modify(data_file, append, default_factory=list)
        assert json.loads(data_file.read_text()) == ["item"]

    def test_handles_corrupt_json(self, data_file):
        data_file.write_text("not valid json{{{")
        locked_json_modify(data_file, lambda d: d.update({"fixed": True}))
        assert json.loads(data_file.read_text()) == {"fixed": True}

    def test_handles_type_mismatch(self, data_file):
        # File contains a list but caller expects a dict
        data_file.write_text(json.dumps([1, 2, 3]))
        locked_json_modify(data_file, lambda d: d.update({"replaced": True}))
        assert json.loads(data_file.read_text()) == {"replaced": True}

    def test_indent_parameter(self, data_file):
        locked_json_modify(data_file, lambda d: d.update({"k": 1}), indent=2)
        content = data_file.read_text()
        assert "  " in content  # indented

    def test_custom_lock_path(self, data_file):
        lock = data_file.parent / "custom.lock"
        locked_json_modify(data_file, lambda d: d.update({"k": 1}), lock_path=lock)
        assert data_file.exists()

    def test_list_slice_assignment(self, data_file):
        """fn can replace list contents via slice assignment."""
        data_file.write_text(json.dumps([1, 2, 3]))
        def keep_even(lst):
            lst[:] = [x for x in lst if x % 2 == 0]
            return len(lst)
        result = locked_json_modify(data_file, keep_even, default_factory=list)
        assert result == 1
        assert json.loads(data_file.read_text()) == [2]


# ---------------------------------------------------------------------------
# locked_json_read
# ---------------------------------------------------------------------------

class TestLockedJsonRead:
    def test_returns_default_when_missing(self, data_file):
        result = locked_json_read(data_file)
        assert result == {}

    def test_returns_default_list_when_missing(self, data_file):
        result = locked_json_read(data_file, default_factory=list)
        assert result == []

    def test_reads_existing_data(self, data_file):
        data_file.write_text(json.dumps({"k": "v"}))
        result = locked_json_read(data_file)
        assert result == {"k": "v"}

    def test_handles_corrupt_json(self, data_file):
        data_file.write_text("broken{")
        result = locked_json_read(data_file)
        assert result == {}


# ---------------------------------------------------------------------------
# locked_jsonl_append
# ---------------------------------------------------------------------------

class TestLockedJsonlAppend:
    def test_creates_file_and_appends(self, tmp_path):
        path = tmp_path / "log.jsonl"
        locked_jsonl_append(path, {"event": "first"})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"event": "first"}

    def test_appends_multiple_records(self, tmp_path):
        path = tmp_path / "log.jsonl"
        locked_jsonl_append(path, {"n": 1})
        locked_jsonl_append(path, {"n": 2})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["n"] == 1
        assert json.loads(lines[1])["n"] == 2

    def test_fsync_option(self, tmp_path):
        path = tmp_path / "log.jsonl"
        # Just verify it doesn't crash with fsync=True
        locked_jsonl_append(path, {"event": "test"}, fsync=True)
        assert path.exists()
