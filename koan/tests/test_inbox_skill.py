"""Tests for the /inbox skill handler."""

import os

import pytest

from app.skills import SkillContext


class TestInboxHandler:
    def _make_ctx(self, tmp_path, missions_content=None):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        if missions_content is not None:
            (instance_dir / "missions.md").write_text(missions_content)
        return SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name="inbox",
            args="",
        )

    def test_writes_signal_file(self, tmp_path):
        from skills.core.inbox.handler import handle

        ctx = self._make_ctx(tmp_path)
        handle(ctx)
        signal = tmp_path / ".koan-check-notifications"
        assert signal.exists()
        assert "requested at" in signal.read_text()

    def test_no_github_missions(self, tmp_path):
        from skills.core.inbox.handler import handle

        missions = "## Pending\n\n- Fix the widget\n- Update docs\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "📬" in result
        assert "No GitHub missions" in result

    def test_counts_github_missions(self, tmp_path):
        from skills.core.inbox.handler import handle

        missions = (
            "## Pending\n\n"
            "- [project:foo] /review https://github.com/o/r/pull/1 📬\n"
            "- Fix local bug\n"
            "- [project:bar] /implement https://github.com/o/r/issues/2 📬\n"
            "\n## Done\n"
        )
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "2 GitHub missions queued" in result

    def test_single_github_mission_singular(self, tmp_path):
        from skills.core.inbox.handler import handle

        missions = (
            "## Pending\n\n"
            "- [project:foo] /review https://github.com/o/r/pull/1 📬\n"
            "\n## Done\n"
        )
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "1 GitHub mission queued" in result

    def test_no_missions_file(self, tmp_path):
        from skills.core.inbox.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "No GitHub missions" in result

    def test_signal_write_failure(self, tmp_path):
        from skills.core.inbox.handler import handle

        ctx = self._make_ctx(tmp_path)
        os.chmod(tmp_path, 0o444)
        try:
            result = handle(ctx)
            assert "Failed" in result
        finally:
            os.chmod(tmp_path, 0o755)
