"""Tests for /debug auto-escalation from failed /fix missions."""

import os

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")


class TestMaybeEscalateToDebug:

    def _setup_missions(self, tmp_path):
        """Create a missions.md at tmp_path (acting as instance dir)."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("## Pending\n\n## In Progress\n\n## Done\n")
        return missions_file

    def test_escalates_fix_failure_when_enabled(self, tmp_path, monkeypatch):
        from app.mission_executor import _maybe_escalate_to_debug

        missions_file = self._setup_missions(tmp_path)
        monkeypatch.setattr("app.config.is_debug_on_fix_failure", lambda: True)

        result = _maybe_escalate_to_debug(
            mission_title="/fix https://github.com/org/repo/issues/42",
            exit_code=1,
            instance=str(tmp_path),
        )
        assert result is True
        assert "/debug https://github.com/org/repo/issues/42" in missions_file.read_text()

    def test_no_escalation_when_disabled(self, tmp_path, monkeypatch):
        from app.mission_executor import _maybe_escalate_to_debug

        monkeypatch.setattr("app.config.is_debug_on_fix_failure", lambda: False)

        result = _maybe_escalate_to_debug(
            mission_title="/fix https://github.com/org/repo/issues/42",
            exit_code=1,
            instance=str(tmp_path),
        )
        assert result is False

    def test_no_escalation_on_success(self, tmp_path, monkeypatch):
        from app.mission_executor import _maybe_escalate_to_debug

        monkeypatch.setattr("app.config.is_debug_on_fix_failure", lambda: True)

        result = _maybe_escalate_to_debug(
            mission_title="/fix https://github.com/org/repo/issues/42",
            exit_code=0,
            instance=str(tmp_path),
        )
        assert result is False

    def test_no_escalation_for_debug_mission(self, tmp_path, monkeypatch):
        from app.mission_executor import _maybe_escalate_to_debug

        monkeypatch.setattr("app.config.is_debug_on_fix_failure", lambda: True)

        result = _maybe_escalate_to_debug(
            mission_title="/debug https://github.com/org/repo/issues/42",
            exit_code=1,
            instance=str(tmp_path),
        )
        assert result is False

    def test_no_escalation_for_non_fix_mission(self, tmp_path, monkeypatch):
        from app.mission_executor import _maybe_escalate_to_debug

        monkeypatch.setattr("app.config.is_debug_on_fix_failure", lambda: True)

        result = _maybe_escalate_to_debug(
            mission_title="/plan https://github.com/org/repo/issues/42",
            exit_code=1,
            instance=str(tmp_path),
        )
        assert result is False

    def test_extracts_issue_url_with_context(self, tmp_path, monkeypatch):
        from app.mission_executor import _maybe_escalate_to_debug

        missions_file = self._setup_missions(tmp_path)
        monkeypatch.setattr("app.config.is_debug_on_fix_failure", lambda: True)

        result = _maybe_escalate_to_debug(
            mission_title="/fix https://github.com/org/repo/issues/42 backend only",
            exit_code=1,
            instance=str(tmp_path),
        )
        assert result is True
        assert "/debug https://github.com/org/repo/issues/42 backend only" in missions_file.read_text()

    def test_handles_project_tag_prefix(self, tmp_path, monkeypatch):
        from app.mission_executor import _maybe_escalate_to_debug

        missions_file = self._setup_missions(tmp_path)
        monkeypatch.setattr("app.config.is_debug_on_fix_failure", lambda: True)

        result = _maybe_escalate_to_debug(
            mission_title="[project:foo] /fix https://github.com/org/repo/issues/42",
            exit_code=1,
            instance=str(tmp_path),
        )
        assert result is True
        # The store renders the project as a trailing [project:X] tag.
        assert "/debug https://github.com/org/repo/issues/42 [project:foo]" in missions_file.read_text()

    def test_recursion_guard_with_project_tag(self, tmp_path, monkeypatch):
        from app.mission_executor import _maybe_escalate_to_debug

        monkeypatch.setattr("app.config.is_debug_on_fix_failure", lambda: True)

        result = _maybe_escalate_to_debug(
            mission_title="[project:foo] /debug https://github.com/org/repo/issues/42",
            exit_code=1,
            instance=str(tmp_path),
        )
        assert result is False
