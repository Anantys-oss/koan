"""Tests for app.locked_file — reusable locked file operations."""

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp(tmp_path):
    """Provide a temp directory with KOAN_ROOT set."""
    return tmp_path


# ---------------------------------------------------------------------------
# locked_json_modify
# ---------------------------------------------------------------------------

class TestLockedJsonModify:

    def test_creates_file_from_default_dict(self, tmp):
        from app.locked_file import locked_json_modify

        path = tmp / "data.json"
        locked_json_modify(path, lambda d: d.update({"key": "val"}))

        assert json.loads(path.read_text()) == {"key": "val"}

    def test_returns_fn_result(self, tmp):
        from app.locked_file import locked_json_modify

        path = tmp / "data.json"
        result = locked_json_modify(path, lambda d: "hello")

        assert result == "hello"

    def test_modifies_existing_data(self, tmp):
        from app.locked_file import locked_json_modify

        path = tmp / "data.json"
        path.write_text(json.dumps({"a": 1}))

        locked_json_modify(path, lambda d: d.update({"b": 2}))

        assert json.loads(path.read_text()) == {"a": 1, "b": 2}

    def test_custom_default_factory_list(self, tmp):
        from app.locked_file import locked_json_modify

        path = tmp / "data.json"
        locked_json_modify(
            path,
            lambda d: d.append("item"),
            default_factory=list,
        )

        assert json.loads(path.read_text()) == ["item"]

    def test_handles_corrupt_json(self, tmp):
        from app.locked_file import locked_json_modify

        path = tmp / "data.json"
        path.write_text("not json{{{")

        locked_json_modify(path, lambda d: d.update({"fresh": True}))

        assert json.loads(path.read_text()) == {"fresh": True}

    def test_lock_file_created(self, tmp):
        from app.locked_file import locked_json_modify

        path = tmp / "tracker.json"
        locked_json_modify(path, lambda d: None)

        lock = tmp / ".tracker.lock"
        assert lock.exists()

    def test_custom_lock_path(self, tmp):
        from app.locked_file import locked_json_modify

        path = tmp / "data.json"
        lock = tmp / "custom.lock"
        locked_json_modify(path, lambda d: None, lock_path=lock)

        assert lock.exists()
        # Default lock should NOT exist
        assert not (tmp / ".data.lock").exists()

    def test_indent_formatting(self, tmp):
        from app.locked_file import locked_json_modify

        path = tmp / "data.json"
        locked_json_modify(path, lambda d: d.update({"k": "v"}), indent=2)

        content = path.read_text()
        assert "  " in content  # indented

    def test_atomic_write_on_error_in_fn(self, tmp):
        from app.locked_file import locked_json_modify

        path = tmp / "data.json"
        path.write_text(json.dumps({"original": True}))

        with pytest.raises(ValueError):
            locked_json_modify(path, lambda d: (_ for _ in ()).throw(ValueError("boom")))

        # Original file untouched
        assert json.loads(path.read_text()) == {"original": True}


# ---------------------------------------------------------------------------
# locked_json_read
# ---------------------------------------------------------------------------

class TestLockedJsonRead:

    def test_reads_existing_file(self, tmp):
        from app.locked_file import locked_json_read

        path = tmp / "data.json"
        path.write_text(json.dumps({"k": "v"}))

        assert locked_json_read(path) == {"k": "v"}

    def test_returns_default_when_missing(self, tmp):
        from app.locked_file import locked_json_read

        path = tmp / "nope.json"
        assert locked_json_read(path) is None
        assert locked_json_read(path, default={}) == {}

    def test_returns_default_on_corrupt(self, tmp):
        from app.locked_file import locked_json_read

        path = tmp / "bad.json"
        path.write_text("not-json")
        # Must also create the lock file for the open() call
        (tmp / ".bad.lock").touch()

        assert locked_json_read(path, default=[]) == []


# ---------------------------------------------------------------------------
# locked_jsonl_append
# ---------------------------------------------------------------------------

class TestLockedJsonlAppend:

    def test_creates_and_appends(self, tmp):
        from app.locked_file import locked_jsonl_append

        path = tmp / "log.jsonl"
        locked_jsonl_append(path, {"event": "start"})
        locked_jsonl_append(path, {"event": "end"})

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "start"
        assert json.loads(lines[1])["event"] == "end"

    def test_unicode_preserved(self, tmp):
        from app.locked_file import locked_jsonl_append

        path = tmp / "log.jsonl"
        locked_jsonl_append(path, {"msg": "café ☕"})

        record = json.loads(path.read_text().strip())
        assert record["msg"] == "café ☕"


# ---------------------------------------------------------------------------
# locked_jsonl_read
# ---------------------------------------------------------------------------

class TestLockedJsonlRead:

    def test_reads_lines(self, tmp):
        from app.locked_file import locked_jsonl_read

        path = tmp / "log.jsonl"
        path.write_text('{"a":1}\n{"b":2}\n')

        lines = locked_jsonl_read(path)
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}

    def test_returns_empty_when_missing(self, tmp):
        from app.locked_file import locked_jsonl_read

        path = tmp / "nope.jsonl"
        assert locked_jsonl_read(path) == []

    def test_raw_lines_returned(self, tmp):
        from app.locked_file import locked_jsonl_read

        path = tmp / "log.jsonl"
        path.write_text('{"a":1}\n')

        lines = locked_jsonl_read(path)
        # Lines include trailing newline
        assert lines[0].endswith("\n")


# ---------------------------------------------------------------------------
# locked_jsonl_tail  (#2354)
# ---------------------------------------------------------------------------

class TestLockedJsonlTail:
    def _write_jsonl(self, path, n):
        with open(path, "w", encoding="utf-8") as f:
            for i in range(n):
                f.write(json.dumps({"i": i}) + "\n")

    def test_tail_returns_last_n(self, tmp_path):
        from app.locked_file import locked_jsonl_tail
        p = tmp_path / "h.jsonl"
        self._write_jsonl(p, 100)
        lines = locked_jsonl_tail(p, 10)
        assert len(lines) == 10
        assert json.loads(lines[0])["i"] == 90
        assert json.loads(lines[-1])["i"] == 99

    def test_tail_file_shorter_than_n(self, tmp_path):
        from app.locked_file import locked_jsonl_tail
        p = tmp_path / "h.jsonl"
        self._write_jsonl(p, 3)
        assert len(locked_jsonl_tail(p, 10)) == 3

    def test_tail_missing_file(self, tmp_path):
        from app.locked_file import locked_jsonl_tail
        assert locked_jsonl_tail(tmp_path / "nope.jsonl", 10) == []

    def test_tail_zero_lines(self, tmp_path):
        from app.locked_file import locked_jsonl_tail
        p = tmp_path / "h.jsonl"
        self._write_jsonl(p, 5)
        assert locked_jsonl_tail(p, 0) == []

    def test_tail_multi_chunk(self, tmp_path):
        from app.locked_file import locked_jsonl_tail
        # Each line ~200 bytes so 100 lines span several 4096-byte chunks.
        p = tmp_path / "h.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            for i in range(100):
                f.write(json.dumps({"i": i, "pad": "x" * 180}) + "\n")
        lines = locked_jsonl_tail(p, 10)
        assert len(lines) == 10
        assert json.loads(lines[0])["i"] == 90


# ---------------------------------------------------------------------------
# signal_lock
# ---------------------------------------------------------------------------

class TestSignalLock:
    """signal_lock serializes read-check-act on marker files."""

    def test_yields_and_creates_sidecar(self, tmp):
        from app.locked_file import signal_lock

        sig = tmp / ".koan-pause"
        with signal_lock(sig):
            # Body runs; sidecar lock lives next to the signal file.
            assert (tmp / ".koan-pause.lock").exists()

    def test_does_not_touch_signal_file(self, tmp):
        from app.locked_file import signal_lock

        sig = tmp / ".koan-stop"
        with signal_lock(sig):
            pass
        # The lock is a sidecar -- the signal file itself is never created.
        assert not sig.exists()

    def test_serializes_across_threads(self, tmp):
        """Two threads entering signal_lock never overlap the critical section."""
        import threading
        import time

        from app.locked_file import signal_lock

        sig = tmp / ".koan-stop"
        order = []

        def worker(tag):
            with signal_lock(sig):
                order.append(f"enter-{tag}")
                time.sleep(0.05)
                order.append(f"exit-{tag}")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Each enter is immediately followed by its own exit (no interleaving).
        assert order[0].startswith("enter-")
        assert order[1] == order[0].replace("enter-", "exit-")
        assert order[2].startswith("enter-")
        assert order[3] == order[2].replace("enter-", "exit-")
