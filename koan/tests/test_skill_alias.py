"""Tests for the alias skill — project alias management and dispatch."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def koan_root(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    missions = instance / "missions.md"
    missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    return tmp_path


@pytest.fixture
def ctx(koan_root):
    ctx = MagicMock()
    ctx.koan_root = koan_root
    ctx.instance_dir = koan_root / "instance"
    ctx.send_message = MagicMock()
    ctx.handle_chat = None
    return ctx


@pytest.fixture
def patch_bridge_state(koan_root):
    instance = koan_root / "instance"
    missions_file = instance / "missions.md"
    with patch("app.command_handlers.KOAN_ROOT", koan_root), \
         patch("app.command_handlers.INSTANCE_DIR", instance), \
         patch("app.command_handlers.MISSIONS_FILE", missions_file):
        yield koan_root


@pytest.fixture
def mock_send():
    with patch("app.command_handlers.send_telegram") as m:
        yield m


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.find_by_command.return_value = None
    registry.resolve_scoped_command.return_value = None
    registry.suggest_command.return_value = None
    registry.list_all.return_value = []
    with patch("app.command_handlers._get_registry", return_value=registry):
        yield registry


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------

class TestAliasHandler:
    """Tests for the alias skill handler."""

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2"), ("koan", "/path/k")])
    def test_create_alias(self, _mock, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = "Template2 tt"
        result = handle(ctx)
        assert "Alias created" in result
        assert "/tt" in result
        assert "Template2" in result

        aliases_path = ctx.instance_dir / ".project-aliases.json"
        assert aliases_path.exists()
        aliases = json.loads(aliases_path.read_text())
        assert aliases["tt"] == "Template2"

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2")])
    def test_create_alias_lowercases_shortcut(self, _mock, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = "Template2 TT"
        handle(ctx)

        aliases = json.loads((ctx.instance_dir / ".project-aliases.json").read_text())
        assert "tt" in aliases

    @patch("app.utils.get_known_projects", return_value=[])
    def test_create_alias_unknown_project(self, _mock, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = "nonexistent tt"
        result = handle(ctx)
        assert "Unknown project" in result

    def test_create_alias_missing_args(self, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = "Template2"
        result = handle(ctx)
        assert "Usage" in result

    def test_create_alias_too_many_args(self, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = "Template2 tt extra"
        result = handle(ctx)
        assert "Too many arguments" in result

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/p")])
    def test_create_alias_conflicts_with_skill(self, _mock, ctx):
        import skills.core.alias.handler as handler_mod
        ctx.command_name = "alias"
        ctx.args = "Template2 status"
        mock_reg = MagicMock()
        mock_reg.find_by_command.return_value = MagicMock()
        with patch("app.bridge_state._get_registry", return_value=mock_reg):
            result = handler_mod.handle(ctx)
        assert "conflicts" in result

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/p")])
    def test_create_alias_conflicts_with_core_command(self, _mock, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = "Template2 stop"
        result = handle(ctx)
        assert "conflicts" in result

    def test_list_aliases_empty(self, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = ""
        result = handle(ctx)
        assert "No project aliases" in result

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/p"), ("koan", "/k")])
    def test_list_aliases_shows_all(self, _mock, ctx):
        aliases_path = ctx.instance_dir / ".project-aliases.json"
        aliases_path.write_text(json.dumps({"tt": "Template2", "k": "koan"}))

        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = ""
        result = handle(ctx)
        assert "/tt" in result
        assert "Template2" in result
        assert "/k" in result
        assert "koan" in result

    def test_unalias(self, ctx):
        aliases_path = ctx.instance_dir / ".project-aliases.json"
        aliases_path.write_text(json.dumps({"tt": "Template2"}))

        from skills.core.alias.handler import handle
        ctx.command_name = "unalias"
        ctx.args = "tt"
        result = handle(ctx)
        assert "removed" in result

        aliases = json.loads(aliases_path.read_text())
        assert "tt" not in aliases

    def test_unalias_nonexistent(self, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "unalias"
        ctx.args = "nope"
        result = handle(ctx)
        assert "No alias" in result

    def test_unalias_no_args(self, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "unalias"
        ctx.args = ""
        result = handle(ctx)
        assert "Usage" in result


# ---------------------------------------------------------------------------
# Dispatch integration tests
# ---------------------------------------------------------------------------

class TestAliasDispatch:
    """Tests for alias resolution in handle_command."""

    def _write_aliases(self, root, aliases):
        path = root / "instance" / ".project-aliases.json"
        path.write_text(json.dumps(aliases))

    @patch("app.command_handlers.is_known_project", return_value=False)
    @patch("app.command_handlers.handle_mission")
    def test_alias_with_args_queues_mission(
        self, mock_mission, _mock_proj,
        patch_bridge_state, mock_send, mock_registry
    ):
        self._write_aliases(patch_bridge_state, {"tt": "Template2"})
        from app.command_handlers import handle_command
        handle_command("/tt fix the build")
        mock_mission.assert_called_once_with("Template2 fix the build")
        mock_send.assert_not_called()

    @patch("app.command_handlers.is_known_project", return_value=False)
    @patch("app.command_handlers.handle_mission")
    def test_alias_without_args_shows_info(
        self, mock_mission, _mock_proj,
        patch_bridge_state, mock_send, mock_registry
    ):
        self._write_aliases(patch_bridge_state, {"tt": "Template2"})
        from app.command_handlers import handle_command
        handle_command("/tt")
        mock_mission.assert_not_called()
        mock_send.assert_called_once()
        assert "Template2" in mock_send.call_args[0][0]

    @patch("app.command_handlers.is_known_project", return_value=False)
    @patch("app.command_handlers.handle_mission")
    def test_no_alias_file_falls_through(
        self, mock_mission, _mock_proj,
        patch_bridge_state, mock_send, mock_registry
    ):
        from app.command_handlers import handle_command
        handle_command("/tt fix stuff")
        mock_mission.assert_not_called()
        assert "Unknown command" in mock_send.call_args[0][0]

    @patch("app.command_handlers.is_known_project", return_value=False)
    @patch("app.command_handlers.handle_mission")
    def test_skill_takes_priority_over_alias(
        self, mock_mission, _mock_proj,
        patch_bridge_state, mock_send, mock_registry
    ):
        """If a skill matches, alias is never checked."""
        self._write_aliases(patch_bridge_state, {"status": "Template2"})
        from app.command_handlers import handle_command
        from app.skills import Skill

        skill = MagicMock(spec=Skill)
        skill.worker = False
        mock_registry.find_by_command.return_value = skill

        with patch("app.command_handlers.execute_skill", return_value="ok"):
            handle_command("/status")
        mock_mission.assert_not_called()

    @patch("app.command_handlers.is_known_project", return_value=False)
    @patch("app.command_handlers.handle_mission")
    def test_alias_case_insensitive_lookup(
        self, mock_mission, _mock_proj,
        patch_bridge_state, mock_send, mock_registry
    ):
        self._write_aliases(patch_bridge_state, {"tt": "Template2"})
        from app.command_handlers import handle_command
        handle_command("/TT fix it")
        mock_mission.assert_called_once_with("Template2 fix it")
