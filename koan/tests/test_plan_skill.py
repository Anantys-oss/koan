"""Tests for the /plan core skill — mission-queuing handler."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler functions
# ---------------------------------------------------------------------------

import importlib.util

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "plan" / "handler.py"


def _load_handler():
    """Load the plan handler module."""
    spec = importlib.util.spec_from_file_location("plan_handler", str(HANDLER_PATH))
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
        command_name="plan",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# handle() — usage / routing
# ---------------------------------------------------------------------------

class TestHandleRouting:
    def test_no_args_returns_usage(self, handler, ctx):
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "/plan <idea>" in result
        assert "Queues" in result

    def test_routes_to_new_plan(self, handler, ctx):
        ctx.args = "Add dark mode"
        with patch.object(handler, "_queue_new_plan", return_value="queued") as mock:
            handler.handle(ctx)
            mock.assert_called_once()
            _, project, idea = mock.call_args[0]
            assert project is None
            assert idea == "Add dark mode"

    def test_routes_github_issue_url(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/64"
        with patch.object(handler, "_queue_issue_plan", return_value="queued") as mock:
            handler.handle(ctx)
            mock.assert_called_once()

    def test_routes_project_prefixed_idea(self, handler, ctx):
        ctx.args = "koan Add dark mode"
        with patch.object(handler, "_queue_new_plan", return_value="queued") as mock, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            handler.handle(ctx)
            mock.assert_called_once()
            _, project, idea = mock.call_args[0]
            assert project == "koan"
            assert idea == "Add dark mode"

    def test_routes_project_tag_idea(self, handler, ctx):
        ctx.args = "[project:koan] Add dark mode"
        with patch.object(handler, "_queue_new_plan", return_value="queued") as mock:
            handler.handle(ctx)
            mock.assert_called_once()
            _, project, idea = mock.call_args[0]
            assert project == "koan"
            assert idea == "Add dark mode"

    def test_github_url_with_fragment(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/64#issuecomment-123"
        with patch.object(handler, "_queue_issue_plan", return_value="queued") as mock:
            handler.handle(ctx)
            mock.assert_called_once()

    def test_empty_idea_returns_error(self, handler, ctx):
        ctx.args = "   "
        result = handler.handle(ctx)
        assert "Usage:" in result


# ---------------------------------------------------------------------------
# _parse_project_arg
# ---------------------------------------------------------------------------

class TestParseProjectArg:
    def test_no_project_prefix(self, handler):
        with patch("app.utils.get_known_projects", return_value=[]):
            project, idea = handler._parse_project_arg("Add dark mode")
            assert project is None
            assert idea == "Add dark mode"

    def test_project_tag_format(self, handler):
        project, idea = handler._parse_project_arg("[project:koan] Fix the bug")
        assert project == "koan"
        assert idea == "Fix the bug"

    def test_project_name_prefix(self, handler):
        with patch("app.utils.get_known_projects",
                    return_value=[("koan", "/path"), ("webapp", "/other")]):
            project, idea = handler._parse_project_arg("koan Fix the login")
            assert project == "koan"
            assert idea == "Fix the login"

    def test_unknown_project_name_treated_as_idea(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            project, idea = handler._parse_project_arg("webapp Fix the login")
            assert project is None
            assert idea == "webapp Fix the login"

    def test_single_word_no_project(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            project, idea = handler._parse_project_arg("refactor")
            assert project is None
            assert idea == "refactor"

    def test_case_insensitive_project_match(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("Koan", "/path")]):
            project, idea = handler._parse_project_arg("koan Fix bug")
            assert project == "Koan"
            assert idea == "Fix bug"


# ---------------------------------------------------------------------------
# _resolve_project_path
# ---------------------------------------------------------------------------

class TestResolveProjectPath:
    def test_named_project(self, handler):
        with patch("app.utils.get_known_projects",
                    return_value=[("koan", "/home/koan"), ("web", "/home/web")]):
            assert handler._resolve_project_path("koan") == "/home/koan"

    def test_named_project_case_insensitive(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("Koan", "/home/koan")]):
            assert handler._resolve_project_path("koan") == "/home/koan"

    def test_unknown_project_returns_none(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            assert handler._resolve_project_path("unknown") is None

    def test_no_project_defaults_to_first(self, handler):
        with patch("app.utils.get_known_projects",
                    return_value=[("koan", "/first"), ("web", "/second")]):
            assert handler._resolve_project_path(None) == "/first"

    def test_no_projects_returns_empty(self, handler):
        """Without projects, _resolve_project_path returns empty string."""
        with patch("app.utils.get_known_projects", return_value=[]):
            assert handler._resolve_project_path(None) == ""

    def test_directory_basename_match(self, handler):
        with patch("app.utils.get_known_projects",
                    return_value=[("myproject", "/home/koan")]):
            assert handler._resolve_project_path("koan") == "/home/koan"

    def test_fallback_mode(self, handler):
        with patch("app.utils.get_known_projects",
                    return_value=[("first", "/a"), ("second", "/b")]):
            assert handler._resolve_project_path("unknown", fallback=True) == "/a"


# ---------------------------------------------------------------------------
# _queue_new_plan — mission queuing
# ---------------------------------------------------------------------------

class TestQueueNewPlan:
    def test_queues_mission(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path/koan")]):
            result = handler._queue_new_plan(ctx, "koan", "Add dark mode")
            assert "queued" in result.lower() or "Plan queued" in result
            # Check mission was written
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "/plan Add dark mode" in missions
            assert "[project:koan]" in missions

    def test_unknown_project_returns_error(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler._queue_new_plan(ctx, "unknown", "idea")
            assert "not found" in result

    def test_mission_uses_clean_format(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/p")]):
            handler._queue_new_plan(ctx, "koan", "Add auth")
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "/plan Add auth" in missions
            assert "run:" not in missions
            assert "python3 -m" not in missions

    def test_default_project_when_none(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path/koan")]):
            result = handler._queue_new_plan(ctx, None, "Add feature")
            assert "queued" in result.lower() or "Plan queued" in result
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "/plan Add feature" in missions

    def test_idea_in_mission(self, handler, ctx):
        long_idea = "A" * 200
        with patch("app.utils.get_known_projects", return_value=[("koan", "/p")]):
            handler._queue_new_plan(ctx, "koan", long_idea)
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "/plan " in missions
            assert "A" * 50 in missions

    def test_idea_with_special_chars(self, handler, ctx):
        idea = "Add auth with 'quotes' and $vars"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/p")]):
            handler._queue_new_plan(ctx, "koan", idea)
            missions = (ctx.instance_dir / "missions.md").read_text()
            # Clean format preserves the idea text as-is
            assert "/plan Add auth with 'quotes' and $vars" in missions


# ---------------------------------------------------------------------------
# _queue_issue_plan — mission queuing for existing issues
# ---------------------------------------------------------------------------

class TestQueueIssuePlan:
    def test_queues_mission_for_issue(self, handler, ctx):
        match = handler._ISSUE_URL_RE.search(
            "https://github.com/sukria/koan/issues/64"
        )
        with patch("app.utils.get_known_projects", return_value=[("koan", "/p/koan")]):
            result = handler._queue_issue_plan(ctx, match)
            assert "#64" in result
            assert "queued" in result.lower()
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "/plan https://github.com/sukria/koan/issues/64" in missions

    def test_mission_contains_url(self, handler, ctx):
        match = handler._ISSUE_URL_RE.search(
            "https://github.com/sukria/koan/issues/42"
        )
        with patch("app.utils.get_known_projects", return_value=[("koan", "/p")]):
            handler._queue_issue_plan(ctx, match)
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "github.com/sukria/koan/issues/42" in missions

    def test_fallback_project_resolution(self, handler, ctx):
        match = handler._ISSUE_URL_RE.search(
            "https://github.com/other/repo/issues/1"
        )
        with patch("app.utils.get_known_projects", return_value=[("koan", "/p")]):
            result = handler._queue_issue_plan(ctx, match)
            assert "queued" in result.lower()


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(
            Path(__file__).parent.parent / "skills" / "core" / "plan" / "SKILL.md"
        )
        assert skill is not None
        assert skill.name == "plan"
        assert skill.scope == "core"
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "plan"

    def test_no_worker_flag(self):
        """Plan skill should NOT be a worker — it queues missions."""
        from app.skills import parse_skill_md
        skill = parse_skill_md(
            Path(__file__).parent.parent / "skills" / "core" / "plan" / "SKILL.md"
        )
        assert skill.worker is False

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("plan")
        assert skill is not None
        assert skill.name == "plan"

    def test_skill_handler_exists(self):
        handler_path = (
            Path(__file__).parent.parent / "skills" / "core" / "plan" / "handler.py"
        )
        assert handler_path.exists()


# ---------------------------------------------------------------------------
# System prompt — plan.md
# ---------------------------------------------------------------------------

PLAN_PROMPT_PATH = (
    Path(__file__).parent.parent / "skills" / "core" / "plan" / "prompts" / "plan.md"
)


class TestPlanPrompt:
    def test_prompt_file_exists(self):
        assert PLAN_PROMPT_PATH.exists()

    def test_prompt_has_placeholders(self):
        content = PLAN_PROMPT_PATH.read_text()
        assert "{IDEA}" in content
        assert "{CONTEXT}" in content

    def test_prompt_has_required_sections(self):
        content = PLAN_PROMPT_PATH.read_text()
        # Sections come via partials or inline
        assert "{@include plan-phases-format}" in content or "Implementation Phases" in content
        assert "{@include plan-tail-sections}" in content or "Corner Cases" in content
        assert "{@include plan-tail-sections}" in content or "Open Questions" in content
        assert "{@include plan-tail-sections}" in content or "Testing Strategy" in content


# ---------------------------------------------------------------------------
# Issue URL regex
# ---------------------------------------------------------------------------

class TestIssueUrlRegex:
    def test_standard_url(self, handler):
        m = handler._ISSUE_URL_RE.search(
            "https://github.com/sukria/koan/issues/64"
        )
        assert m is not None
        assert m.group("owner") == "sukria"
        assert m.group("repo") == "koan"
        assert m.group("number") == "64"

    def test_http_url(self, handler):
        m = handler._ISSUE_URL_RE.search("http://github.com/a/b/issues/1")
        assert m is not None

    def test_url_with_fragment(self, handler):
        m = handler._ISSUE_URL_RE.search(
            "https://github.com/owner/repo/issues/42#comment-123"
        )
        assert m is not None
        assert m.group("number") == "42"

    def test_url_in_text(self, handler):
        m = handler._ISSUE_URL_RE.search(
            "Check https://github.com/o/r/issues/5 please"
        )
        assert m is not None
        assert m.group("number") == "5"

    def test_pr_url_does_not_match(self, handler):
        m = handler._ISSUE_URL_RE.search("https://github.com/o/r/pull/5")
        assert m is None

    def test_no_url_returns_none(self, handler):
        m = handler._ISSUE_URL_RE.search("just some text")
        assert m is None
