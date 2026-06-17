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
         patch("app.command_handlers.MISSIONS_FILE", missions_file), \
         patch("app.utils.KOAN_ROOT", koan_root):
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

    def test_alias_rm_removes_alias(self, ctx):
        aliases_path = ctx.instance_dir / ".project-aliases.json"
        aliases_path.write_text(json.dumps({"tt": "Template2"}))

        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = "--rm tt"
        result = handle(ctx)
        assert "removed" in result
        assert "tt" in result

        aliases = json.loads(aliases_path.read_text())
        assert "tt" not in aliases

    def test_alias_rm_nonexistent(self, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = "--rm nope"
        result = handle(ctx)
        assert "No alias" in result

    def test_alias_rm_no_shortcut(self, ctx):
        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = "--rm"
        result = handle(ctx)
        assert "Usage" in result

    def test_list_aliases_shows_rm_hint(self, ctx):
        aliases_path = ctx.instance_dir / ".project-aliases.json"
        aliases_path.write_text(json.dumps({"tt": "Template2"}))

        from skills.core.alias.handler import handle
        ctx.command_name = "alias"
        ctx.args = ""
        result = handle(ctx)
        assert "--rm" in result


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


# ---------------------------------------------------------------------------
# Alias resolution in skill arguments (the main fix)
# ---------------------------------------------------------------------------

class TestAliasInSkillArgs:
    """Tests that aliases are resolved when used as project arguments in skills."""

    def _write_aliases(self, root, aliases):
        path = root / "instance" / ".project-aliases.json"
        path.write_text(json.dumps(aliases))

    @patch("app.command_handlers.insert_pending_mission")
    @patch("app.utils.get_known_projects", return_value=[("Template2", "/path/t2")])
    @patch("app.utils.resolve_project_alias", return_value="Template2")
    def test_queue_cli_skill_resolves_alias_as_project(
        self, _mock_alias, _mock_proj, mock_insert,
        patch_bridge_state, mock_send, mock_registry,
    ):
        """When first arg is an alias, it should resolve to the project name."""
        from app.command_handlers import _queue_cli_skill_mission
        from app.skills import Skill, SkillCommand

        skill = Skill(
            name="ai",
            scope="core",
            description="AI exploration",
            audience="agent",
            cli_skill="ai-tool",
            commands=[SkillCommand(name="ai", description="AI")],
        )

        _queue_cli_skill_mission(skill, "tt explore auth")
        entry = mock_insert.call_args[0][1]
        assert "[project:Template2]" in entry
        assert "explore auth" in entry

    @patch("app.command_handlers.insert_pending_mission")
    @patch("app.utils.get_known_projects", return_value=[("koan", "/path/k")])
    def test_queue_cli_skill_prefers_project_over_alias(
        self, _mock_proj, mock_insert, patch_bridge_state, mock_send, mock_registry
    ):
        """Known project names take priority over aliases."""
        self._write_aliases(patch_bridge_state, {"koan": "ShouldNotUse"})
        from app.command_handlers import _queue_cli_skill_mission
        from app.skills import Skill, SkillCommand

        skill = Skill(
            name="audit",
            scope="core",
            description="Audit",
            audience="agent",
            cli_skill="audit-tool",
            commands=[SkillCommand(name="audit", description="Audit")],
        )

        _queue_cli_skill_mission(skill, "koan check deps")
        entry = mock_insert.call_args[0][1]
        assert "[project:koan]" in entry

    def test_strip_project_prefix_resolves_alias(self):
        """_strip_project_prefix should recognize aliases as project prefixes."""
        from app.skill_dispatch import _strip_project_prefix

        with patch("app.skill_dispatch.is_known_project", return_value=False), \
             patch("app.utils.resolve_project_alias", return_value="Template2"):
            project, remainder = _strip_project_prefix("tt /plan add dark mode")
        assert project == "Template2"
        assert remainder == "/plan add dark mode"

    def test_strip_project_prefix_prefers_known_project(self):
        """Known projects take priority over aliases in _strip_project_prefix."""
        from app.skill_dispatch import _strip_project_prefix

        with patch("app.skill_dispatch.is_known_project", return_value=True):
            project, remainder = _strip_project_prefix("koan /plan add dark mode")
        assert project == "koan"
        assert remainder == "/plan add dark mode"

    def test_strip_project_prefix_no_alias_match(self):
        """When first word is neither a project nor an alias, no prefix is extracted."""
        from app.skill_dispatch import _strip_project_prefix

        with patch("app.skill_dispatch.is_known_project", return_value=False), \
             patch("app.utils.resolve_project_alias", return_value=None):
            project, remainder = _strip_project_prefix("unknown /plan add mode")
        assert project == ""
        assert remainder == "unknown /plan add mode"

    def test_detect_project_from_text_resolves_alias(self):
        """detect_project_from_text should resolve aliases."""
        from app.utils import detect_project_from_text

        with patch("app.utils.get_known_projects", return_value=[]), \
             patch("app.utils.load_project_aliases", return_value={"tt": "Template2"}):
            project, text = detect_project_from_text("tt explore auth")
        assert project == "Template2"
        assert text == "explore auth"


# ---------------------------------------------------------------------------
# Alias resolution in skill handler _resolve_project functions
# ---------------------------------------------------------------------------

class TestAliasInSkillHandlers:
    """Tests that skill handlers resolve aliases in their project resolution."""

    @patch("app.project_explorer.get_projects",
           return_value=[("Template2", "/path/t2"), ("koan", "/path/k")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_ai_resolve_project_alias(self, _aliases, _projects):
        from skills.core.ai.handler import _resolve_project
        name, path = _resolve_project(
            [("Template2", "/path/t2"), ("koan", "/path/k")], "tt"
        )
        assert name == "Template2"
        assert path == "/path/t2"

    @patch("app.project_explorer.get_projects",
           return_value=[("Template2", "/path/t2")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_ai_prefers_exact_name_over_alias(self, _aliases, _projects):
        from skills.core.ai.handler import _resolve_project
        projects = [("tt", "/path/tt"), ("Template2", "/path/t2")]
        name, path = _resolve_project(projects, "tt")
        assert name == "tt"
        assert path == "/path/tt"

    @patch("app.utils.load_project_aliases", return_value={})
    def test_ai_unknown_project_returns_none(self, _aliases):
        from skills.core.ai.handler import _resolve_project
        name, path = _resolve_project([("koan", "/path/k")], "nope")
        assert name is None
        assert path is None

    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_deep_resolve_project_alias(self, _aliases):
        from skills.core.deep.handler import _resolve_project
        name, path = _resolve_project(
            [("Template2", "/path/t2"), ("koan", "/path/k")], "tt"
        )
        assert name == "Template2"
        assert path == "/path/t2"

    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_magic_resolve_project_alias(self, _aliases):
        from skills.core.magic.handler import _resolve_project
        name, path = _resolve_project(
            [("Template2", "/path/t2"), ("koan", "/path/k")], "tt"
        )
        assert name == "Template2"
        assert path == "/path/t2"

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2"), ("koan", "/path/k")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_branches_resolve_project_alias(self, _aliases, _projects):
        from skills.core.branches.handler import _resolve_project
        ctx = MagicMock()
        name, path = _resolve_project("tt", ctx)
        assert name == "Template2"
        assert path == "/path/t2"

    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_explore_resolve_project_alias(self, _aliases):
        from skills.core.explore.handler import _resolve_project_name
        projects = {"Template2": {"exploration": True}, "koan": {}}
        result = _resolve_project_name(projects, "tt")
        assert result == "Template2"

    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_autoreview_resolve_project_alias(self, _aliases):
        from skills.core.autoreview.handler import _resolve_project_name
        projects = {"Template2": {"autoreview": True}, "koan": {}}
        result = _resolve_project_name(projects, "tt")
        assert result == "Template2"

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_claudemd_resolve_alias(self, _aliases, _projects, koan_root):
        from skills.core.claudemd.handler import handle
        ctx = MagicMock()
        ctx.args = "tt"
        ctx.instance_dir = koan_root / "instance"
        with patch("app.utils.insert_pending_mission"):
            result = handle(ctx)
        assert "Template2" in result
        assert "queued" in result.lower()

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_changelog_resolve_alias(self, _aliases, _projects):
        from skills.core.changelog.handler import _resolve_project
        result = _resolve_project(None, "tt")
        assert result == "/path/t2"

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_done_resolve_alias(self, _aliases, _projects):
        from skills.core.done.handler import handle
        ctx = MagicMock()
        ctx.args = "tt"
        with patch("app.github.get_gh_username", return_value="bot"), \
             patch("skills.core.done.handler._get_repo_slug", return_value=None):
            result = handle(ctx)
        assert "No activity" in result

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_plan_parse_project_alias(self, _aliases, _projects):
        from skills.core.plan.handler import _parse_project_arg
        project, remainder = _parse_project_arg("tt add dark mode")
        assert project == "Template2"
        assert remainder == "add dark mode"

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_brainstorm_parse_project_alias(self, _aliases, _projects):
        from skills.core.brainstorm.handler import _parse_project_arg
        project, remainder = _parse_project_arg("tt improve search")
        assert project == "Template2"
        assert remainder == "improve search"

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_incident_parse_project_alias(self, _aliases, _projects):
        from skills.core.incident.handler import _parse_project_arg
        project, remainder = _parse_project_arg("tt TypeError: bad value")
        assert project == "Template2"
        assert remainder == "TypeError: bad value"

    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_stats_resolve_alias(self, _aliases, koan_root):
        """Stats handler filters by alias — call handle() directly."""
        import json
        from datetime import datetime

        instance_dir = koan_root / "instance"
        outcomes_path = instance_dir / "session_outcomes.json"
        ts = datetime.now().isoformat()
        outcomes_path.write_text(json.dumps([
            {"project": "Template2", "mode": "implement",
             "timestamp": ts, "duration_minutes": 5, "outcome": "productive"},
        ]))

        from skills.core.stats.handler import handle
        ctx = MagicMock()
        ctx.args = "tt"
        ctx.instance_dir = instance_dir
        result = handle(ctx)
        assert "Template2" in result

    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_stats_perf_resolve_alias(self, _aliases, koan_root):
        """Stats --perf mode resolves alias before filtering outcomes."""
        import json
        from datetime import datetime

        instance_dir = koan_root / "instance"
        outcomes_path = instance_dir / "session_outcomes.json"
        ts = datetime.now().isoformat()
        outcomes_path.write_text(json.dumps([
            {"project": "Template2", "mode": "implement", "provider": "claude",
             "timestamp": ts, "duration_minutes": 5, "outcome": "productive"},
        ]))

        from skills.core.stats.handler import handle
        ctx = MagicMock()
        ctx.args = "tt --perf"
        ctx.instance_dir = instance_dir
        result = handle(ctx)
        assert "Template2" in result
        assert "No session data" not in result

    @patch("app.utils.get_known_projects",
           return_value=[("Template2", "/path/t2")])
    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_deepplan_parse_project_alias(self, _aliases, _projects):
        from skills.core.deepplan.handler import _parse_project_arg
        project, remainder = _parse_project_arg("tt refactor auth module")
        assert project == "Template2"
        assert remainder == "refactor auth module"

    @patch("app.utils.load_project_aliases", return_value={"tt": "Template2"})
    def test_diagnose_resolve_alias(self, _aliases, koan_root):
        """Diagnose handler resolves alias for project filter."""
        instance_dir = koan_root / "instance"
        missions = instance_dir / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n"
            "## Failed\n\n"
            "- [project:Template2] fix bug "
            "❌ (2026-06-15 10:00)\n"
        )
        from skills.core.diagnose.handler import handle
        ctx = MagicMock()
        ctx.args = "tt"
        ctx.instance_dir = instance_dir
        result = handle(ctx)
        assert "queued" in result.lower() or "fix bug" in result.lower() or "Diagnosis" in result

    def test_gha_audit_resolve_alias(self):
        """gha_audit _resolve_project_path resolves via alias."""
        from skills.core.gha_audit.handler import _resolve_project_path
        with patch("app.utils.resolve_project_name_and_path",
                   return_value=("Template2", "/path/t2")):
            result = _resolve_project_path("tt")
        assert result == "/path/t2"
