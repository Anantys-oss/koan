"""Tests for external_skill_dispatch — in-process handler invocation
from GitHub / Jira bridges.
"""

import os
from pathlib import Path

import pytest

from app.external_skill_dispatch import (
    augment_args_with_issue_key,
    should_dispatch_in_process,
    try_dispatch_custom_handler,
)
from app.skills import Skill, SkillCommand


@pytest.fixture(autouse=True)
def _koan_root(tmp_path, monkeypatch):
    """Point KOAN_ROOT at a throwaway directory with an instance/ folder."""
    instance = tmp_path / "instance"
    instance.mkdir()
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
    return tmp_path


def _make_custom_skill(tmp_path: Path, handler_src: str) -> Skill:
    """Create a custom skill backed by a real handler.py on disk."""
    skill_dir = tmp_path / "custom_skill"
    skill_dir.mkdir()
    handler_path = skill_dir / "handler.py"
    handler_path.write_text(handler_src)
    return Skill(
        name="my_fix",
        scope="my_team",
        description="Test custom skill",
        handler_path=handler_path,
        skill_dir=skill_dir,
        github_enabled=True,
        github_context_aware=True,
        commands=[SkillCommand(name="my_fix", aliases=["myfix"])],
    )


def _make_core_skill() -> Skill:
    return Skill(
        name="rebase",
        scope="core",
        description="Core skill",
        github_enabled=True,
        commands=[SkillCommand(name="rebase")],
    )


# ---------------------------------------------------------------------------
# augment_args_with_issue_key
# ---------------------------------------------------------------------------


class TestAugmentArgs:
    def test_returns_context_unchanged_when_jira_key_already_present(self):
        out = augment_args_with_issue_key(
            "focus on race PROJ-999",
            jira_issue_key="PROJ-1",
        )
        assert out == "focus on race PROJ-999"

    def test_appends_jira_source_key_when_missing(self):
        out = augment_args_with_issue_key(
            "focus on the race",
            jira_issue_key="PROJ-456",
        )
        assert out == "focus on the race PROJ-456"

    def test_uses_jira_key_even_when_github_sources_also_present(self):
        # Jira source wins over GitHub title/body fallbacks.
        out = augment_args_with_issue_key(
            "",
            jira_issue_key="PROJ-10",
            github_title="references PROJ-99",
            github_body="and PROJ-88",
        )
        assert out == "PROJ-10"

    def test_falls_back_to_github_title(self):
        out = augment_args_with_issue_key(
            "please fix",
            github_title="Bug: PROJ-321 breaks login",
        )
        assert out == "please fix PROJ-321"

    def test_falls_back_to_github_body_when_title_has_none(self):
        out = augment_args_with_issue_key(
            "",
            github_title="just a bug",
            github_body="tracked as PROJ-77 in jira",
        )
        assert out == "PROJ-77"

    def test_leaves_context_alone_when_nothing_found(self):
        out = augment_args_with_issue_key(
            "no key anywhere",
            github_title="nothing",
            github_body="also nothing",
        )
        assert out == "no key anywhere"

    def test_empty_context_and_no_sources_returns_empty(self):
        assert augment_args_with_issue_key("") == ""


# ---------------------------------------------------------------------------
# should_dispatch_in_process
# ---------------------------------------------------------------------------


class TestShouldDispatchInProcess:
    def test_true_for_custom_skill_with_handler(self, tmp_path):
        skill = _make_custom_skill(tmp_path, "def handle(ctx):\n    return 'ok'\n")
        assert should_dispatch_in_process(skill) is True

    def test_false_for_core_skill_even_with_handler(self, tmp_path):
        # Core skills keep the slash-mission path even if they have a handler.
        handler = tmp_path / "h.py"
        handler.write_text("def handle(ctx):\n    return 'ok'\n")
        skill = Skill(
            name="plan", scope="core", handler_path=handler,
            github_enabled=True, commands=[SkillCommand(name="plan")],
        )
        assert should_dispatch_in_process(skill) is False

    def test_false_for_custom_skill_without_handler(self, tmp_path):
        # Prompt-only skill — nothing to invoke inline.
        skill = Skill(
            name="thoughts", scope="custom",
            github_enabled=True, commands=[SkillCommand(name="thoughts")],
        )
        assert should_dispatch_in_process(skill) is False


# ---------------------------------------------------------------------------
# try_dispatch_custom_handler
# ---------------------------------------------------------------------------


class TestTryDispatchCustomHandler:
    def test_returns_none_for_core_skill(self):
        skill = _make_core_skill()
        assert try_dispatch_custom_handler(
            skill, "rebase", "context", source="github",
        ) is None

    def test_returns_none_when_koan_root_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        skill = _make_custom_skill(tmp_path, "def handle(ctx):\n    return 'ok'\n")
        assert try_dispatch_custom_handler(
            skill, "my_fix", "", source="github",
        ) is None

    def test_invokes_custom_handler_and_returns_reply(self, tmp_path):
        handler_src = (
            "def handle(ctx):\n"
            "    return f'args={ctx.args!r} cmd={ctx.command_name!r}'\n"
        )
        skill = _make_custom_skill(tmp_path, handler_src)

        reply = try_dispatch_custom_handler(
            skill, "my_fix", "do the thing",
            source="github",
            github_body="nothing",
        )

        assert reply == "args='do the thing' cmd='my_fix'"

    def test_jira_key_auto_fed_from_jira_source(self, tmp_path):
        handler_src = (
            "def handle(ctx):\n"
            "    return f'got:{ctx.args}'\n"
        )
        skill = _make_custom_skill(tmp_path, handler_src)

        reply = try_dispatch_custom_handler(
            skill, "my_fix", "",
            source="jira",
            jira_issue_key="PROJ-42",
        )

        assert reply == "got:PROJ-42"

    def test_jira_key_auto_fed_from_github_title(self, tmp_path):
        handler_src = (
            "def handle(ctx):\n"
            "    return f'got:{ctx.args}'\n"
        )
        skill = _make_custom_skill(tmp_path, handler_src)

        reply = try_dispatch_custom_handler(
            skill, "my_fix", "",
            source="github",
            github_title="PROJ-789 breaks",
            github_body="body text",
        )

        assert reply == "got:PROJ-789"

    def test_user_context_with_key_preserved(self, tmp_path):
        handler_src = (
            "def handle(ctx):\n"
            "    return f'got:{ctx.args}'\n"
        )
        skill = _make_custom_skill(tmp_path, handler_src)

        # Author typed PROJ-1; source issue is PROJ-999. Author wins.
        reply = try_dispatch_custom_handler(
            skill, "my_fix", "PROJ-1 please",
            source="jira",
            jira_issue_key="PROJ-999",
        )

        assert reply == "got:PROJ-1 please"

    def test_returns_empty_string_when_handler_returns_none(self, tmp_path):
        # Handler returning None means "no user-visible reply" — caller should
        # still see "dispatched" (empty string) rather than None (fallthrough).
        handler_src = "def handle(ctx):\n    return None\n"
        skill = _make_custom_skill(tmp_path, handler_src)

        reply = try_dispatch_custom_handler(
            skill, "my_fix", "context",
            source="github",
        )

        assert reply == ""

    def test_returns_error_message_when_handler_raises(self, tmp_path):
        handler_src = (
            "def handle(ctx):\n"
            "    raise RuntimeError('boom')\n"
        )
        skill = _make_custom_skill(tmp_path, handler_src)

        reply = try_dispatch_custom_handler(
            skill, "my_fix", "ctx", source="github",
        )

        # SkillError is surfaced as its message string, not None.
        assert reply is not None
        assert "boom" in reply

    def test_ctx_has_expected_paths(self, tmp_path):
        # The handler should receive an instance_dir pointing at
        # KOAN_ROOT/instance so it can write into missions.md.
        handler_src = (
            "def handle(ctx):\n"
            "    return f'{ctx.instance_dir}|{ctx.koan_root}'\n"
        )
        skill = _make_custom_skill(tmp_path, handler_src)

        reply = try_dispatch_custom_handler(
            skill, "my_fix", "", source="github",
            jira_issue_key=None,
        )

        assert reply is not None
        instance_part, koan_part = reply.split("|", 1)
        assert instance_part == str(tmp_path / "instance")
        assert koan_part == str(tmp_path)
