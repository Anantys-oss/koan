"""Tests for the /planimplement (/planit) combo skill — handler, SKILL.md, and registry."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "plan_implement" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("plan_implement_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="planimplement",
        args="",
        send_message=MagicMock(),
    )


class TestHandleRouting:
    def test_no_args_returns_usage(self, handler, ctx):
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "/planit" in result

    def test_invalid_url_returns_error(self, handler, ctx):
        ctx.args = "not-a-url"
        result = handler.handle(ctx)
        assert "❌" in result
        assert "No valid" in result

    def test_unknown_repo_returns_error(self, handler, ctx):
        ctx.args = "https://github.com/unknown/repo/issues/1"
        with patch("app.utils.resolve_project_path", return_value=None), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler.handle(ctx)
            assert "❌" in result
            assert "repo" in result.lower()


class TestComboQueuing:
    def test_queues_plan_then_implement(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)

            assert mock_insert.call_count == 2

            first_entry = mock_insert.call_args_list[0][0][1]
            assert "/plan https://github.com/sukria/koan/issues/42" in first_entry
            assert "[project:koan]" in first_entry

            second_entry = mock_insert.call_args_list[1][0][1]
            assert "/implement https://github.com/sukria/koan/issues/42" in second_entry
            assert "[project:koan]" in second_entry

    def test_returns_combo_ack(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission"):
            result = handler.handle(ctx)
            assert "Plan + implement combo queued" in result
            assert "#42" in result
            assert "sukria/koan" in result

    def test_context_passed_to_both(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42 phase 1 only"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            handler.handle(ctx)

            plan_entry = mock_insert.call_args_list[0][0][1]
            impl_entry = mock_insert.call_args_list[1][0][1]
            assert "phase 1 only" in plan_entry
            assert "phase 1 only" in impl_entry

    def test_accepts_pr_urls_too(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert mock_insert.call_count == 2
            assert "combo queued" in result.lower()

    def test_url_with_fragment_stripped(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42#issuecomment-123"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert mock_insert.call_count == 2
            assert "combo queued" in result.lower()

    def test_missions_path_uses_instance_dir(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            handler.handle(ctx)
            for c in mock_insert.call_args_list:
                assert c[0][0] == ctx.instance_dir / "missions.md"

    def test_duplicate_both_returns_warning(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission", return_value=False):
            result = handler.handle(ctx)
            assert "⚠️" in result
            assert "already queued" in result

    def test_duplicate_plan_only(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission", side_effect=[False, True]):
            result = handler.handle(ctx)
            assert "Implement queued" in result
            assert "plan already" in result

    def test_duplicate_implement_only(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission", side_effect=[True, False]):
            result = handler.handle(ctx)
            assert "Plan queued" in result
            assert "implement already" in result


class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "plan_implement" / "SKILL.md")
        assert skill is not None
        assert skill.name == "plan_implement"
        assert skill.scope == "core"
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "planimplement"

    def test_skill_has_aliases(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "plan_implement" / "SKILL.md")
        aliases = skill.commands[0].aliases
        assert "planimp" in aliases
        assert "planimpl" in aliases
        assert "planit" in aliases
        assert "plandoit" in aliases

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("planimplement")
        assert skill is not None
        assert skill.name == "plan_implement"

    def test_aliases_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        for alias in ("planimp", "planimpl", "planit", "plandoit"):
            skill = registry.find_by_command(alias)
            assert skill is not None, f"Alias '{alias}' not found in registry"
            assert skill.name == "plan_implement"

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()

    def test_skill_has_group(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "plan_implement" / "SKILL.md")
        assert skill.group == "code"

    def test_sub_commands_defined(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "plan_implement" / "SKILL.md")
        assert skill.sub_commands == ["plan", "implement"]
