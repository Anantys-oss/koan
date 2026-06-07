"""Tests for koan/diagnostics/permissions_check.py."""

import json
from pathlib import Path

import pytest

from diagnostics.permissions_check import fix, run


def _write_settings(root: Path, allow: list) -> None:
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"permissions": {"allow": allow}}))


class TestRun:
    def test_warns_when_file_missing(self, tmp_path):
        results = run(str(tmp_path), str(tmp_path / "instance"))
        assert len(results) == 1
        assert results[0].severity == "warn"
        assert results[0].fixable is True

    def test_ok_when_rules_present(self, tmp_path):
        _write_settings(tmp_path, ["Write(instance/**)", "Edit(instance/**)", "Bash(git*)"])
        results = run(str(tmp_path), str(tmp_path / "instance"))
        assert results[0].severity == "ok"

    def test_warns_when_rules_missing(self, tmp_path):
        _write_settings(tmp_path, ["Bash(git*)"])
        results = run(str(tmp_path), str(tmp_path / "instance"))
        assert results[0].severity == "warn"
        assert "Write(instance/**)" in results[0].message or "Edit(instance/**)" in results[0].message

    def test_handles_broken_json(self, tmp_path):
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{broken")
        results = run(str(tmp_path), str(tmp_path / "instance"))
        assert results[0].severity == "warn"


class TestFix:
    def test_creates_file_when_missing(self, tmp_path):
        results = fix(str(tmp_path), str(tmp_path / "instance"))
        assert len(results) == 1
        assert results[0].success is True
        assert (tmp_path / ".claude" / "settings.json").exists()

    def test_no_op_when_already_correct(self, tmp_path):
        _write_settings(tmp_path, ["Write(instance/**)", "Edit(instance/**)", "Bash(git*)"])
        results = fix(str(tmp_path), str(tmp_path / "instance"))
        assert results == []
