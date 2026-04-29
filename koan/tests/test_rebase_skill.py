"""Tests for the /rebase core skill — handler, SKILL.md, and registry integration."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler functions
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "rebase" / "handler.py"


def _load_handler():
    """Load the rebase handler module."""
    spec = importlib.util.spec_from_file_location("rebase_handler", str(HANDLER_PATH))
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
        command_name="rebase",
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
        assert "/rebase" in result

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
# handle() — mission queuing
# ---------------------------------------------------------------------------

class TestMissionQueuing:
    def _own_pr_patch(self, handler_mod):
        """Patch is_own_pr on the helper module used by the handler."""
        return patch(
            "app.github_skill_helpers.is_own_pr",
            return_value=(True, "koan/some-branch"),
        )

    def test_valid_url_queues_mission(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch(handler):
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            assert "#42" in result
            mock_insert.assert_called_once()
            mission_entry = mock_insert.call_args[0][1]
            assert "[project:koan]" in mission_entry
            assert "/rebase https://github.com/sukria/koan/pull/42" in mission_entry

    def test_url_with_fragment_accepted(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42#discussion_r123"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch(handler):
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            mock_insert.assert_called_once()

    def test_url_in_surrounding_text(self, handler, ctx):
        ctx.args = "please rebase https://github.com/sukria/koan/pull/99 thanks"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch(handler):
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            assert "#99" in result
            mock_insert.assert_called_once()

    def test_returns_ack_message(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission"), \
             self._own_pr_patch(handler):
            result = handler.handle(ctx)
            assert result == "Rebase queued for PR #42 (sukria/koan)"

    def test_mission_entry_format(self, handler, ctx):
        """Verify mission text contains project tag and clean /rebase format."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch(handler):
            handler.handle(ctx)
            entry = mock_insert.call_args[0][1]
            assert entry.startswith("- [project:koan]")
            assert "/rebase https://github.com/sukria/koan/pull/42" in entry
            assert "run:" not in entry
            assert "python3 -m" not in entry

    def test_single_project_fallback(self, handler, ctx):
        """When resolve_project_path returns a path not in projects list,
        falls back to directory basename for the project tag."""
        ctx.args = "https://github.com/other/myrepo/pull/7"
        with patch("app.utils.resolve_project_path", return_value="/some/myrepo"), \
             patch("app.utils.get_known_projects", return_value=[("onlyone", "/other/path")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch(handler):
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            entry = mock_insert.call_args[0][1]
            # Falls back to directory basename when path doesn't match known projects
            assert "[project:myrepo]" in entry

    def test_missions_path_uses_instance_dir(self, handler, ctx):
        """Verify insert_pending_mission is called with the correct missions path."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch(handler):
            handler.handle(ctx)
            missions_path = mock_insert.call_args[0][0]
            assert missions_path == ctx.instance_dir / "missions.md"


# ---------------------------------------------------------------------------
# handle() — PR ownership check
# ---------------------------------------------------------------------------

class TestPROwnership:
    def test_rejects_pr_from_other_instance(self, handler, ctx):
        """Refuse to rebase a PR whose branch wasn't created by this instance."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.github_skill_helpers.is_own_pr", return_value=(False, "other-bot/fix-thing")), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "Not my PR" in result
            assert "other-bot/fix-thing" in result
            mock_insert.assert_not_called()

    def test_accepts_pr_from_own_instance(self, handler, ctx):
        """Allow rebase when the PR branch matches our prefix."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.github_skill_helpers.is_own_pr", return_value=(True, "koan/fix-thing")), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            mock_insert.assert_called_once()

    def test_ownership_check_failure_returns_error(self, handler, ctx):
        """If the GitHub API call fails, return an error instead of crashing."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.github_skill_helpers.is_own_pr", side_effect=Exception("API timeout")):
            result = handler.handle(ctx)
            assert "\u274c" in result
            assert "ownership" in result.lower()


# ---------------------------------------------------------------------------
# handle() — --now priority flag
# ---------------------------------------------------------------------------

class TestNowFlag:
    def _own_pr_patch(self):
        return patch(
            "app.github_skill_helpers.is_own_pr",
            return_value=(True, "koan/some-branch"),
        )

    def test_now_flag_queues_as_urgent(self, handler, ctx):
        """--now flag causes the mission to be queued with urgent=True."""
        ctx.args = "--now https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch():
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            assert "(priority)" in result
            mock_insert.assert_called_once()
            assert mock_insert.call_args[1]["urgent"] is True

    def test_now_flag_after_url(self, handler, ctx):
        """--now after URL is also recognized (within first 5 words)."""
        ctx.args = "https://github.com/sukria/koan/pull/42 --now"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch():
            result = handler.handle(ctx)
            assert "(priority)" in result
            assert mock_insert.call_args[1]["urgent"] is True

    def test_without_now_flag_not_urgent(self, handler, ctx):
        """Without --now, mission is queued normally (not urgent)."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch():
            result = handler.handle(ctx)
            assert "(priority)" not in result
            assert mock_insert.call_args[1].get("urgent", False) is False

    def test_now_flag_stripped_from_mission_text(self, handler, ctx):
        """--now should not appear in the queued mission text."""
        ctx.args = "--now https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             self._own_pr_patch():
            handler.handle(ctx)
            mission_entry = mock_insert.call_args[0][1]
            assert "--now" not in mission_entry

    def test_now_flag_usage_documented(self, handler, ctx):
        """Empty args help text mentions --now."""
        ctx.args = ""
        result = handler.handle(ctx)
        assert "--now" in result


# ---------------------------------------------------------------------------
# Stale module cache guard (issue #1235)
# ---------------------------------------------------------------------------

class TestStaleModuleReload:
    """Verify _execute_handler refreshes stale app modules before loading."""

    def test_execute_handler_reloads_stale_modules(self):
        """_refresh_stale_app_modules reloads the module in-place so
        stale sys.modules entries are refreshed after auto-update."""
        import app.github_skill_helpers as gh_mod
        from app.skills import _refresh_stale_app_modules

        original = gh_mod.queue_github_mission
        del gh_mod.queue_github_mission
        assert not hasattr(gh_mod, "queue_github_mission")

        try:
            _refresh_stale_app_modules()
            assert hasattr(gh_mod, "queue_github_mission")
        finally:
            if not hasattr(gh_mod, "queue_github_mission"):
                gh_mod.queue_github_mission = original

    def test_stale_urgent_param_restored_after_reload(self):
        """The exact scenario from #1235: queue_github_mission exists but
        lacks the 'urgent' keyword argument.  After reload, the correct
        signature is available."""
        import inspect
        import sys as _sys
        import app.github_skill_helpers as gh_mod
        from app.skills import _refresh_stale_app_modules

        original = gh_mod.queue_github_mission

        def stale(ctx, command, url, project_name, context=None):
            pass

        gh_mod.queue_github_mission = stale

        try:
            _refresh_stale_app_modules()
            sig = inspect.signature(gh_mod.queue_github_mission)
            assert "urgent" in sig.parameters
        finally:
            if gh_mod.queue_github_mission is stale:
                gh_mod.queue_github_mission = original

    def test_evicts_module_on_reload_failure(self):
        """If importlib.reload raises, the stale entry is removed from
        sys.modules so the handler's own import loads a fresh copy."""
        import sys as _sys
        from unittest.mock import patch as _patch

        from app.skills import _refresh_stale_app_modules, _MODULES_TO_REFRESH

        target = _MODULES_TO_REFRESH[0]
        saved = {name: _sys.modules.get(name) for name in _MODULES_TO_REFRESH}
        sentinel = type("StaleModule", (), {"__name__": target, "__spec__": None})()
        _sys.modules[target] = sentinel

        try:
            with _patch("importlib.reload", side_effect=ImportError("boom")):
                _refresh_stale_app_modules()
            assert target not in _sys.modules or _sys.modules[target] is not sentinel
        finally:
            for name, orig in saved.items():
                if orig is not None:
                    _sys.modules[name] = orig
                else:
                    _sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# resolve_project_path (shared helper in utils)
# ---------------------------------------------------------------------------

class TestResolveProjectPath:
    def test_exact_name_match(self):
        from app.utils import resolve_project_path
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan"), ("web", "/home/web")]):
            assert resolve_project_path("koan") == "/home/koan"

    def test_case_insensitive_match(self):
        from app.utils import resolve_project_path
        with patch("app.utils.get_known_projects", return_value=[("Koan", "/home/koan")]):
            assert resolve_project_path("koan") == "/home/koan"

    def test_directory_basename_match(self):
        from app.utils import resolve_project_path
        with patch("app.utils.get_known_projects", return_value=[("myproject", "/home/koan")]):
            assert resolve_project_path("koan") == "/home/koan"

    def test_single_project_fallback(self):
        from app.utils import resolve_project_path
        with patch("app.utils.get_known_projects", return_value=[("onlyone", "/only")]):
            assert resolve_project_path("anything") == "/only"

    def test_no_match_returns_none(self):
        from app.utils import resolve_project_path
        with patch("app.utils.get_known_projects", return_value=[("a", "/a"), ("b", "/b")]):
            assert resolve_project_path("xyz") is None

    def test_no_env_fallback(self):
        """KOAN_PROJECT_PATH env var is no longer used as fallback."""
        from app.utils import resolve_project_path
        with patch("app.utils.get_known_projects", return_value=[("a", "/a"), ("b", "/b")]), \
             patch.dict("os.environ", {"KOAN_PROJECT_PATH": "/from/env"}):
            assert resolve_project_path("xyz") is None


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "rebase" / "SKILL.md")
        assert skill is not None
        assert skill.name == "rebase"
        assert skill.scope == "core"
        assert skill.worker is False
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "rebase"

    def test_skill_has_alias(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "rebase" / "SKILL.md")
        assert "rb" in skill.commands[0].aliases

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("rebase")
        assert skill is not None
        assert skill.name == "rebase"

    def test_alias_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("rb")
        assert skill is not None
        assert skill.name == "rebase"

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()
