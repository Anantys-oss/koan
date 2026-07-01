"""Tests for the /reviewrebase (/rr) combo skill — handler, SKILL.md, and registry."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "review_rebase" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("review_rebase_handler", str(HANDLER_PATH))
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
        command_name="reviewrebase",
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
        assert "/rr" in result

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
# handle() — mission queuing (the combo)
# ---------------------------------------------------------------------------

class TestComboQueuing:
    def test_queues_review_then_rebase(self, handler, ctx):
        """The core behavior: two missions queued in review-first order."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_missions", return_value=[True, True]) as mock_insert:
            result = handler.handle(ctx)

            assert mock_insert.call_count == 1
            entries = mock_insert.call_args[0][1]
            assert len(entries) == 2

            # First entry: /review
            assert "/review https://github.com/sukria/koan/pull/42" in entries[0]
            assert "[project:koan]" in entries[0]

            # Second entry: /rebase
            assert "/rebase https://github.com/sukria/koan/pull/42" in entries[1]
            assert "[project:koan]" in entries[1]

    def test_default_queued_at_end_of_pending(self, handler, ctx):
        """By default the combo appends at the end of the queue, not the top."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_missions", return_value=[True, True]) as mock_insert:
            handler.handle(ctx)
            assert mock_insert.call_args.kwargs.get("urgent") is False

    def test_now_flag_queues_at_top_of_pending(self, handler, ctx):
        """--now jumps the queue: missions land at the top."""
        ctx.args = "--now https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_missions", return_value=[True, True]) as mock_insert:
            handler.handle(ctx)
            assert mock_insert.call_args.kwargs.get("urgent") is True
            # The --now flag must not leak into the queued mission entries.
            entries = mock_insert.call_args[0][1]
            assert all("--now" not in e for e in entries)

    def test_default_review_above_rebase_at_end_of_file(self, handler, ctx):
        """Observable: review above rebase, both after the pre-existing task."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        missions_path = ctx.instance_dir / "missions.md"
        missions_path.write_text(
            "## Pending\n\n- [project:koan] /existing earlier task\n\n"
            "## In Progress\n\n## Done\n"
        )
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)

        content = missions_path.read_text()
        review_idx = content.index("/review https://github.com/sukria/koan/pull/42")
        rebase_idx = content.index("/rebase https://github.com/sukria/koan/pull/42")
        existing_idx = content.index("/existing earlier task")
        # pre-existing task first, then review, then rebase
        assert existing_idx < review_idx < rebase_idx

    def test_now_review_above_rebase_at_top_of_file(self, handler, ctx):
        """--now: review above rebase, both ahead of the pre-existing task."""
        ctx.args = "--now https://github.com/sukria/koan/pull/42"
        missions_path = ctx.instance_dir / "missions.md"
        missions_path.write_text(
            "## Pending\n\n- [project:koan] /existing earlier task\n\n"
            "## In Progress\n\n## Done\n"
        )
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)

        content = missions_path.read_text()
        review_idx = content.index("/review https://github.com/sukria/koan/pull/42")
        rebase_idx = content.index("/rebase https://github.com/sukria/koan/pull/42")
        existing_idx = content.index("/existing earlier task")
        # review first, then rebase, both ahead of the pre-existing task
        assert review_idx < rebase_idx < existing_idx

    def test_returns_combo_ack(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_missions", return_value=[True, True]):
            result = handler.handle(ctx)
            assert "Review + rebase combo queued" in result
            assert "#42" in result
            assert "sukria/koan" in result

    def test_context_passed_to_review_only(self, handler, ctx):
        """Extra context after URL goes to review, not rebase."""
        ctx.args = "https://github.com/sukria/koan/pull/42 focus on security"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_missions", return_value=[True, True]) as mock_insert:
            handler.handle(ctx)

            entries = mock_insert.call_args[0][1]
            review_entry, rebase_entry = entries
            assert "focus on security" in review_entry
            assert "focus on security" not in rebase_entry

    def test_url_with_fragment_stripped(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42#discussion_r123"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_missions", return_value=[True, True]) as mock_insert:
            result = handler.handle(ctx)
            assert len(mock_insert.call_args[0][1]) == 2
            assert "combo queued" in result.lower()

    def test_missions_path_uses_instance_dir(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_missions", return_value=[True, True]) as mock_insert:
            handler.handle(ctx)
            assert mock_insert.call_args[0][0] == ctx.instance_dir / "missions.md"


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "review_rebase" / "SKILL.md")
        assert skill is not None
        assert skill.name == "review_rebase"
        assert skill.scope == "core"
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "reviewrebase"

    def test_skill_has_rr_alias(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "review_rebase" / "SKILL.md")
        assert "rr" in skill.commands[0].aliases

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("reviewrebase")
        assert skill is not None
        assert skill.name == "review_rebase"

    def test_alias_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("rr")
        assert skill is not None
        assert skill.name == "review_rebase"

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()

    def test_skill_has_group(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "review_rebase" / "SKILL.md")
        assert skill.group == "pr"
