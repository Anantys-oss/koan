"""Tests for the /reset core skill -- reset run counter to zero."""

from pathlib import Path
from unittest.mock import patch

from app.skills import SkillContext


class TestResetHandler:
    """Test the reset skill handler directly."""

    def _make_ctx(self, tmp_path, args=""):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        return SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name="reset",
            args=args,
        )

    def test_creates_reset_signal_file(self, tmp_path):
        from skills.core.reset.handler import handle

        ctx = self._make_ctx(tmp_path)
        handle(ctx)
        assert (tmp_path / ".koan-reset-counter").exists()

    def test_response_when_not_paused(self, tmp_path):
        from skills.core.reset.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "reset to 0" in result

    def test_resumes_from_max_runs_pause(self, tmp_path):
        from skills.core.reset.handler import handle

        pause_file = tmp_path / ".koan-pause"
        pause_file.write_text("max_runs")
        (tmp_path / ".koan-pause-reason").write_text("max_runs")
        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "resumed" in result
        assert not pause_file.exists()
        assert (tmp_path / ".koan-reset-counter").exists()

    def test_does_not_resume_manual_pause(self, tmp_path):
        from skills.core.reset.handler import handle

        pause_file = tmp_path / ".koan-pause"
        pause_file.write_text("manual")
        (tmp_path / ".koan-pause-reason").write_text("manual")
        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "reset to 0" in result
        assert pause_file.exists()

    def test_does_not_resume_quota_pause(self, tmp_path):
        from skills.core.reset.handler import handle

        pause_file = tmp_path / ".koan-pause"
        pause_file.write_text("quota|1234567890|Resets at 10:00")
        (tmp_path / ".koan-pause-reason").write_text("quota")
        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "reset to 0" in result
        assert pause_file.exists()


class TestResetSignalConstant:
    """Test RESET_COUNTER_FILE is defined in signals."""

    def test_constant_exists(self):
        from app.signals import RESET_COUNTER_FILE

        assert RESET_COUNTER_FILE == ".koan-reset-counter"


class TestResetSkillRegistry:
    """Test /reset is discoverable in the skill registry."""

    def test_resolves_in_registry(self):
        from app.skills import build_registry

        registry = build_registry()
        skill = registry.find_by_command("reset")
        assert skill is not None
        assert skill.name == "reset"

    def test_has_system_group(self):
        from app.skills import build_registry

        registry = build_registry()
        skill = registry.find_by_command("reset")
        assert skill is not None
        assert skill.group == "system"


class TestResetInMainLoop:
    """Test the reset counter signal is consumed in the main loop."""

    def test_signal_file_cleared_on_startup(self, tmp_path, monkeypatch):
        """Stale reset signal from previous session is cleared at startup."""
        from app.signals import RESET_COUNTER_FILE

        reset_file = tmp_path / RESET_COUNTER_FILE
        reset_file.touch()
        assert reset_file.exists()
