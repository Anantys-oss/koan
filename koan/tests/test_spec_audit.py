"""Tests for the /spec_audit skill handler."""

import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext


HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "spec_audit" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("spec_audit_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    ctx = MagicMock(spec=SkillContext)
    ctx.instance_dir = tmp_path
    (tmp_path / "missions.md").write_text("# Missions\n\n## Pending\n\n## In Progress\n")
    return ctx


class TestSpecAuditHandler:
    @patch("app.utils.resolve_project_name_and_path", return_value=("backend", "/path/backend"))
    @patch("app.utils.insert_pending_mission")
    def test_alias_resolves_to_canonical(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "be"
        result = handler.handle(ctx)

        assert "queued" in result.lower()
        assert "backend" in result
        mission_entry = mock_insert.call_args[0][1]
        assert "[project:backend]" in mission_entry

    @patch("app.utils.resolve_project_name_and_path", return_value=("koan", "/path/koan"))
    @patch("app.utils.insert_pending_mission")
    def test_named_project(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan"
        result = handler.handle(ctx)

        assert "queued" in result.lower()
        assert "koan" in result
        mission_entry = mock_insert.call_args[0][1]
        assert "[project:koan]" in mission_entry
        assert "/spec_audit" in mission_entry

    @patch("app.utils.resolve_project_name_and_path", return_value=("nonexistent", None))
    @patch("app.utils.get_known_projects", return_value=[("web", "/path/web")])
    def test_unknown_project(self, mock_projects, mock_resolve, handler, ctx):
        ctx.args = "nonexistent"
        result = handler.handle(ctx)

        assert "❌" in result
        assert "nonexistent" in result
        assert "web" in result

    @patch("app.utils.get_known_projects", return_value=[("myproject", "/path/myproject")])
    @patch("app.utils.insert_pending_mission")
    def test_default_project(self, mock_insert, mock_projects, handler, ctx):
        ctx.args = ""
        result = handler.handle(ctx)

        assert "queued" in result.lower()
        assert "myproject" in result

    @patch("app.utils.get_known_projects", return_value=[])
    def test_no_projects_configured(self, mock_projects, handler, ctx):
        ctx.args = ""
        result = handler.handle(ctx)
        assert "❌" in result

    def test_help_flag(self, handler, ctx):
        ctx.args = "--help"
        result = handler.handle(ctx)
        assert "Usage" in result
