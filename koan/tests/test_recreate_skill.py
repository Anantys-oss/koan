"""Tests for the /recreate core skill -- handler, SKILL.md, and registry integration."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler functions
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "recreate" / "handler.py"


def _load_handler():
    """Load the recreate handler module."""
    spec = importlib.util.spec_from_file_location("recreate_handler", str(HANDLER_PATH))
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
    # Create a minimal missions.md so insert_pending_mission works
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="recreate",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# handle() -- usage / routing
# ---------------------------------------------------------------------------

class TestHandleRouting:
    def test_no_args_returns_usage(self, handler, ctx):
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "/recreate" in result

    def test_usage_mentions_diverged(self, handler, ctx):
        """Usage text should explain the recreate vs rebase distinction."""
        result = handler.handle(ctx)
        assert "diverged" in result.lower() or "scratch" in result.lower()

    def test_invalid_url_returns_error(self, handler, ctx):
        ctx.args = "not-a-url"
        result = handler.handle(ctx)
        assert "\u274c" in result
        assert "No valid" in result

    def test_non_pr_url_returns_error(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        result = handler.handle(ctx)
        assert "\u274c" in result

    def test_unknown_repo_returns_error(self, handler, ctx):
        ctx.args = "https://github.com/unknown/repo/pull/1"
        with patch("app.utils.resolve_project_path", return_value=None), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler.handle(ctx)
            assert "\u274c" in result
            assert "repo" in result.lower()


# ---------------------------------------------------------------------------
# handle() -- mission queuing
# ---------------------------------------------------------------------------

class TestMissionQueuing:
    def test_valid_url_queues_mission(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            assert "#42" in result
            mock_insert.assert_called_once()
            mission_entry = mock_insert.call_args[0][0]
            mission_project = mock_insert.call_args[0][1]
            assert mission_project == "koan"
            assert "/recreate https://github.com/sukria/koan/pull/42" in mission_entry

    def test_url_with_fragment_accepted(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42#discussion_r123"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            mock_insert.assert_called_once()

    def test_url_in_surrounding_text(self, handler, ctx):
        ctx.args = "please recreate https://github.com/sukria/koan/pull/99 thanks"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            assert "#99" in result
            mock_insert.assert_called_once()

    def test_returns_ack_message(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission"):
            result = handler.handle(ctx)
            assert result == "Recreate queued for PR #42 (sukria/koan)"

    def test_mission_entry_format(self, handler, ctx):
        """Verify mission text contains /recreate format and project is koan."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            handler.handle(ctx)
            entry = mock_insert.call_args[0][0]
            project = mock_insert.call_args[0][1]
            assert project == "koan"
            assert "/recreate https://github.com/sukria/koan/pull/42" in entry
            assert "run:" not in entry
            assert "python3 -m" not in entry

    def test_mission_uses_recreate_not_rebase(self, handler, ctx):
        """Verify the mission uses /recreate, not /rebase."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            handler.handle(ctx)
            entry = mock_insert.call_args[0][0]
            assert "/recreate " in entry
            assert "/rebase " not in entry

    def test_single_project_fallback(self, handler, ctx):
        """When resolve_project_path returns a path not in projects list,
        falls back to directory basename for the project tag."""
        ctx.args = "https://github.com/other/myrepo/pull/7"
        with patch("app.utils.resolve_project_path", return_value="/some/myrepo"), \
             patch("app.utils.get_known_projects", return_value=[("onlyone", "/other/path")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            project = mock_insert.call_args[0][1]
            # Falls back to directory basename when path doesn't match known projects
            assert project == "myrepo"

    def test_missions_path_uses_instance_dir(self, handler, ctx):
        """Verify insert_pending_mission is called with the mission text and project."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            handler.handle(ctx)
            mission_text = mock_insert.call_args[0][0]
            project = mock_insert.call_args[0][1]
            assert "/recreate https://github.com/sukria/koan/pull/42" in mission_text
            assert project == "koan"

    def test_recreate_ack_includes_repo_info(self, handler, ctx):
        """Ack message should include PR number and repo."""
        ctx.args = "https://github.com/org/myproject/pull/55"
        with patch("app.utils.resolve_project_path", return_value="/home/proj"), \
             patch("app.utils.get_known_projects", return_value=[("myproject", "/home/proj")]), \
             patch("app.utils.insert_pending_mission"):
            result = handler.handle(ctx)
            assert "#55" in result
            assert "org/myproject" in result


# ---------------------------------------------------------------------------
# SKILL.md -- structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "recreate" / "SKILL.md")
        assert skill is not None
        assert skill.name == "recreate"
        assert skill.scope == "core"
        assert skill.worker is False
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "recreate"

    def test_skill_has_alias(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "recreate" / "SKILL.md")
        assert "rc" in skill.commands[0].aliases

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("recreate")
        assert skill is not None
        assert skill.name == "recreate"

    def test_alias_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("rc")
        assert skill is not None
        assert skill.name == "recreate"

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()

    def test_prompt_template_exists(self):
        prompt_path = Path(__file__).parent.parent / "skills" / "core" / "recreate" / "prompts" / "recreate.md"
        assert prompt_path.exists()

    def test_prompt_has_placeholders(self):
        prompt_path = Path(__file__).parent.parent / "skills" / "core" / "recreate" / "prompts" / "recreate.md"
        content = prompt_path.read_text()
        assert "{TITLE}" in content
        assert "{BODY}" in content
        assert "{DIFF}" in content
        assert "{BASE}" in content
        assert "{BRANCH}" in content

    def test_prompt_mentions_scratch(self):
        """The recreate prompt should emphasize reimplementation from scratch."""
        prompt_path = Path(__file__).parent.parent / "skills" / "core" / "recreate" / "prompts" / "recreate.md"
        content = prompt_path.read_text().lower()
        assert "scratch" in content or "reimplement" in content
