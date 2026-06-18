"""Tests for the /check core skill — handler (thin queuer)."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "check" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("check_handler", str(HANDLER_PATH))
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
        command_name="check",
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
        assert "/check" in result

    def test_invalid_url_returns_error(self, handler, ctx):
        ctx.args = "not-a-url"
        result = handler.handle(ctx)
        assert "\u274c" in result
        assert "No valid" in result

    def test_random_github_url_returns_error(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan"
        result = handler.handle(ctx)
        assert "\u274c" in result


# ---------------------------------------------------------------------------
# handle() — PR URL queuing
# ---------------------------------------------------------------------------

class TestHandlePrQueuing:
    def test_pr_url_queues_mission(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "Check queued" in result
            assert "PR #42" in result
            mock_insert.assert_called_once()

    def test_pr_mission_entry_format(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            text = mock_insert.call_args[0][0]
            project = mock_insert.call_args[0][1]
            assert project == "koan"
            assert "/check https://github.com/sukria/koan/pull/42" in text
            assert "run:" not in text

    def test_pr_mission_uses_clean_format(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            text = mock_insert.call_args[0][0]
            project = mock_insert.call_args[0][1]
            assert project == "koan"
            assert "/check " in text
            assert "python3 -m" not in text

    def test_pr_mission_contains_url(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            text = mock_insert.call_args[0][0]
            assert "github.com/sukria/koan/pull/42" in text

    def test_pr_url_in_surrounding_text(self, handler, ctx):
        ctx.args = "please check https://github.com/sukria/koan/pull/99 thanks"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "Check queued" in result
            assert "PR #99" in result


# ---------------------------------------------------------------------------
# handle() — Issue URL queuing
# ---------------------------------------------------------------------------

class TestHandleIssueQueuing:
    def test_issue_url_queues_mission(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "Check queued" in result
            assert "issue #42" in result
            mock_insert.assert_called_once()

    def test_issue_mission_entry_format(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            text = mock_insert.call_args[0][0]
            project = mock_insert.call_args[0][1]
            assert project == "koan"
            assert "/check https://github.com/sukria/koan/issues/42" in text
            assert "run:" not in text

    def test_issue_mission_has_correct_url(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            text = mock_insert.call_args[0][0]
            assert "github.com/sukria/koan/issues/42" in text


# ---------------------------------------------------------------------------
# handle() — PR takes priority
# ---------------------------------------------------------------------------

class TestUrlPriority:
    def test_pr_url_takes_priority(self, handler, ctx):
        """PR URL matches before issue URL."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "PR #42" in result
            text = mock_insert.call_args[0][0]
            assert "pull/42" in text


# ---------------------------------------------------------------------------
# handle() — project resolution
# ---------------------------------------------------------------------------

class TestProjectResolution:
    def test_known_project_resolved(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            project = mock_insert.call_args[0][1]
            assert project == "koan"

    def test_unknown_project_uses_repo_name(self, handler, ctx):
        ctx.args = "https://github.com/unknown/myrepo/pull/10"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            project = mock_insert.call_args[0][1]
            assert project == "myrepo"

    def test_case_insensitive_project_match(self, handler, ctx):
        ctx.args = "https://github.com/sukria/Koan/pull/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            project = mock_insert.call_args[0][1]
            assert project == "koan"


# ---------------------------------------------------------------------------
# handle() — http (not https)
# ---------------------------------------------------------------------------

class TestHttpUrls:
    def test_http_pr_url_accepted(self, handler, ctx):
        ctx.args = "http://github.com/sukria/koan/pull/5"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "Check queued" in result

    def test_http_issue_url_accepted(self, handler, ctx):
        ctx.args = "http://github.com/sukria/koan/issues/5"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "Check queued" in result


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "check" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        assert skill is not None
        assert skill.name == "check"
        assert skill.scope == "core"
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "check"

    def test_skill_no_longer_worker(self):
        """After conversion to mission-queuing, worker flag should be absent."""
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "check" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        assert skill.worker is False

    def test_skill_has_alias(self):
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "check" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        assert "inspect" in skill.commands[0].aliases

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("check")
        assert skill is not None
        assert skill.name == "check"

    def test_alias_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("inspect")
        assert skill is not None
        assert skill.name == "check"

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()

    def test_skill_version_bumped(self):
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "check" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        assert skill.version == "2.0.0"
