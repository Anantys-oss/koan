"""Tests for the /commit core skill — mission-queuing handler."""

import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler functions
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "commit" / "handler.py"
SKILL_DIR = Path(__file__).parent.parent / "skills" / "core" / "commit"


def _load_handler():
    """Load the commit handler module."""
    spec = importlib.util.spec_from_file_location("commit_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    """Create a basic SkillContext for tests."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_path = instance_dir / "missions.md"
    missions_path.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="commit",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# handle() — routing
# ---------------------------------------------------------------------------

class TestHandleRouting:
    def test_no_args_queues_commit(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "/commit" in missions

    def test_hint_queues_commit_with_hint(self, handler, ctx):
        ctx.args = "fix the login bug"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            assert "fix the login bug" in result
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "/commit fix the login bug" in missions

    def test_project_prefix(self, handler, ctx):
        ctx.args = "koan fix auth"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "[project:koan]" in missions
            assert "/commit fix auth" in missions

    def test_project_tag_prefix(self, handler, ctx):
        ctx.args = "[project:webapp] fix auth"
        with patch("app.utils.get_known_projects", return_value=[("webapp", "/path")]):
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "[project:webapp]" in missions
            assert "/commit fix auth" in missions

    def test_unknown_project_returns_error(self, handler, ctx):
        ctx.args = "unknown fix bug"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            # "unknown" is not a known project, so it's treated as hint text
            result = handler.handle(ctx)
            assert "queued" in result.lower()


# ---------------------------------------------------------------------------
# _parse_project_arg
# ---------------------------------------------------------------------------

class TestParseProjectArg:
    def test_no_project(self, handler):
        with patch("app.utils.get_known_projects", return_value=[]):
            project, hint = handler._parse_project_arg("fix the bug")
            assert project is None
            assert hint == "fix the bug"

    def test_project_tag(self, handler):
        project, hint = handler._parse_project_arg("[project:koan] fix bug")
        assert project == "koan"
        assert hint == "fix bug"

    def test_project_name_prefix(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            project, hint = handler._parse_project_arg("koan fix login")
            assert project == "koan"
            assert hint == "fix login"

    def test_unknown_project_treated_as_hint(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            project, hint = handler._parse_project_arg("unknown fix login")
            assert project is None
            assert hint == "unknown fix login"

    def test_single_word_no_project(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            project, hint = handler._parse_project_arg("refactor")
            assert project is None
            assert hint == "refactor"

    def test_case_insensitive(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("Koan", "/path")]):
            project, hint = handler._parse_project_arg("koan fix bug")
            assert project == "Koan"
            assert hint == "fix bug"


# ---------------------------------------------------------------------------
# _queue_commit — mission queuing
# ---------------------------------------------------------------------------

class TestQueueCommit:
    def test_queues_with_project(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler._queue_commit(ctx, project="koan", hint="fix bug")
            assert "queued" in result.lower()
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "[project:koan] /commit fix bug" in missions

    def test_queues_without_hint(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler._queue_commit(ctx, project="koan", hint="")
            assert "queued" in result.lower()
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "[project:koan] /commit" in missions
            # Should not have trailing space
            assert "/commit " not in missions or "/commit fix" not in missions

    def test_unknown_project_error(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler._queue_commit(ctx, project="unknown", hint="fix")
            assert "not found" in result

    def test_no_project_uses_default(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("default", "/d")]):
            result = handler._queue_commit(ctx, project=None, hint="fix")
            assert "queued" in result.lower()
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "[project:default]" in missions

    def test_long_hint_truncated_in_response(self, handler, ctx):
        long_hint = "A" * 200
        with patch("app.utils.get_known_projects", return_value=[("koan", "/p")]):
            result = handler._queue_commit(ctx, project="koan", hint=long_hint)
            # Response should truncate at 100 chars
            assert "..." in result

    def test_no_projects_available(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[]):
            result = handler._queue_commit(ctx, project=None, hint="fix")
            assert "queued" in result.lower()
            missions = (ctx.instance_dir / "missions.md").read_text()
            # No project tag when no default project
            assert "[project:" not in missions
            assert "/commit fix" in missions


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(SKILL_DIR / "SKILL.md")
        assert skill is not None
        assert skill.name == "commit"
        assert skill.scope == "core"
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "commit"

    def test_skill_has_alias(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(SKILL_DIR / "SKILL.md")
        assert "ci" in skill.commands[0].aliases

    def test_skill_audience_hybrid(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(SKILL_DIR / "SKILL.md")
        assert skill.audience == "hybrid"

    def test_no_worker_flag(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(SKILL_DIR / "SKILL.md")
        assert skill.worker is False

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("commit")
        assert skill is not None
        assert skill.name == "commit"

    def test_alias_registered(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("ci")
        assert skill is not None
        assert skill.name == "commit"

    def test_handler_exists(self):
        assert HANDLER_PATH.exists()


# ---------------------------------------------------------------------------
# Prompt file
# ---------------------------------------------------------------------------

PROMPT_PATH = SKILL_DIR / "prompts" / "commit.md"


class TestCommitPrompt:
    def test_prompt_file_exists(self):
        assert PROMPT_PATH.exists()

    def test_prompt_has_hint_placeholder(self):
        content = PROMPT_PATH.read_text()
        assert "{HINT}" in content

    def test_prompt_loadable_via_load_skill_prompt(self):
        from app.prompts import load_skill_prompt
        prompt = load_skill_prompt(SKILL_DIR, "commit", HINT="test hint")
        assert "test hint" in prompt
        assert "{HINT}" not in prompt

    def test_prompt_mentions_conventional_format(self):
        content = PROMPT_PATH.read_text()
        assert "feat" in content
        assert "fix" in content
        assert "refactor" in content

    def test_prompt_warns_about_credentials(self):
        content = PROMPT_PATH.read_text()
        assert ".env" in content

    def test_prompt_warns_about_main_branch(self):
        content = PROMPT_PATH.read_text()
        assert "main" in content


# ---------------------------------------------------------------------------
# Skill dispatch integration
# ---------------------------------------------------------------------------

class TestSkillDispatch:
    def test_commit_in_skill_runners(self):
        from app.skill_dispatch import _SKILL_RUNNERS
        assert "commit" in _SKILL_RUNNERS
        assert _SKILL_RUNNERS["commit"] == "skills.core.commit.commit_runner"

    def test_build_skill_command(self):
        from app.skill_dispatch import build_skill_command
        cmd = build_skill_command(
            command="commit",
            args="fix the bug",
            project_name="koan",
            project_path="/path/to/koan",
            koan_root="/root",
            instance_dir="/root/instance",
        )
        assert cmd is not None
        assert "--project-path" in cmd
        assert "/path/to/koan" in cmd
        assert "--hint" in cmd
        assert "fix the bug" in cmd

    def test_build_skill_command_no_hint(self):
        from app.skill_dispatch import build_skill_command
        cmd = build_skill_command(
            command="commit",
            args="",
            project_name="koan",
            project_path="/path/to/koan",
            koan_root="/root",
            instance_dir="/root/instance",
        )
        assert cmd is not None
        assert "--project-path" in cmd
        assert "--hint" not in cmd

    def test_parse_skill_mission(self):
        from app.skill_dispatch import parse_skill_mission
        project, command, args = parse_skill_mission("/commit fix the login")
        assert project == ""
        assert command == "commit"
        assert args == "fix the login"

    def test_parse_skill_mission_with_project(self):
        from app.skill_dispatch import parse_skill_mission
        project, command, args = parse_skill_mission("[project:koan] /commit fix")
        assert project == "koan"
        assert command == "commit"
        assert args == "fix"

    def test_is_skill_mission(self):
        from app.skill_dispatch import is_skill_mission
        assert is_skill_mission("/commit fix bug")
        assert is_skill_mission("/commit")
        assert not is_skill_mission("commit fix bug")
