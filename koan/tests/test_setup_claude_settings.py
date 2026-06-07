"""Tests for app.setup_claude_settings — permission allowlist installer."""

import json
from pathlib import Path

import pytest

from app.setup_claude_settings import KOAN_ALLOWLIST, install


class TestInstall:
    def test_creates_file_when_missing(self, tmp_path):
        result = install(project_root=tmp_path)
        assert result["created"] is True
        assert result["updated"] is True
        assert result["dry_run"] is False
        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()

    def test_writes_correct_allowlist(self, tmp_path):
        install(project_root=tmp_path)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data["permissions"]["allow"] == KOAN_ALLOWLIST

    def test_idempotent(self, tmp_path):
        install(project_root=tmp_path)
        result2 = install(project_root=tmp_path)
        assert result2["created"] is False
        assert result2["updated"] is False

    def test_dry_run_does_not_create_file(self, tmp_path):
        result = install(project_root=tmp_path, dry_run=True)
        assert result["dry_run"] is True
        assert not (tmp_path / ".claude" / "settings.json").exists()

    def test_preserves_extra_keys(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"theme": "dark"}))
        install(project_root=tmp_path)
        data = json.loads(settings_path.read_text())
        assert data["theme"] == "dark"
        assert "permissions" in data

    def test_updates_stale_allowlist(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"permissions": {"allow": ["Write(instance/*)"]}}))
        result = install(project_root=tmp_path)
        assert result["updated"] is True
        data = json.loads(settings_path.read_text())
        assert data["permissions"]["allow"] == KOAN_ALLOWLIST

    def test_creates_parent_dirs(self, tmp_path):
        deep_root = tmp_path / "a" / "b" / "c"
        deep_root.mkdir(parents=True)
        install(project_root=deep_root)
        assert (deep_root / ".claude" / "settings.json").exists()


class TestAllowlistContent:
    def test_has_write_rule(self):
        assert "Write(instance/**)" in KOAN_ALLOWLIST

    def test_has_edit_rule(self):
        assert "Edit(instance/**)" in KOAN_ALLOWLIST

    def test_has_git_rules(self):
        assert any("git" in rule for rule in KOAN_ALLOWLIST)

    def test_has_gh_rules(self):
        assert any("gh" in rule for rule in KOAN_ALLOWLIST)
