"""Tests for the /autoreview and /noautoreview core skill — per-project autoreview toggle."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler
# ---------------------------------------------------------------------------

HANDLER_PATH = (
    Path(__file__).parent.parent / "skills" / "core" / "autoreview" / "handler.py"
)


def _load_handler():
    spec = importlib.util.spec_from_file_location("autoreview_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    """Create a SkillContext for autoreview tests."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="autoreview",
        args="",
        send_message=MagicMock(),
    )


def _make_config(*project_names, autoreview_overrides=None):
    """Build a minimal projects.yaml dict."""
    projects = {}
    for name in project_names:
        projects[name] = {"path": f"/workspace/{name}"}
    if autoreview_overrides:
        for name, val in autoreview_overrides.items():
            if name in projects:
                projects[name]["autoreview"] = val
    return {"projects": projects}


def _write_projects_yaml(tmp_path, content):
    (tmp_path / "projects.yaml").write_text(content)


# ===========================================================================
# _resolve_project_name
# ===========================================================================


class TestResolveProjectName:
    def test_exact_match(self, handler):
        projects = {"koan": {}, "webapp": {}}
        assert handler._resolve_project_name(projects, "koan") == "koan"

    def test_case_insensitive(self, handler):
        projects = {"Koan": {}, "WebApp": {}}
        assert handler._resolve_project_name(projects, "koan") == "Koan"
        assert handler._resolve_project_name(projects, "webapp") == "WebApp"

    def test_unknown_returns_none(self, handler):
        projects = {"koan": {}}
        assert handler._resolve_project_name(projects, "unknown") is None


# ===========================================================================
# handle — show status (no args)
# ===========================================================================


class TestShowStatus:
    def test_no_projects_yaml_returns_error(self, handler, ctx):
        with patch("app.projects_config.load_projects_config", side_effect=OSError):
            result = handler.handle(ctx)
        assert "No projects.yaml" in result

    def test_lists_projects_with_on_off(self, handler, ctx, tmp_path):
        config = _make_config("alpha", "beta", autoreview_overrides={"alpha": True})
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_merged.get_all_projects", return_value=[("alpha", "/w/alpha"), ("beta", "/w/beta")]),
        ):
            result = handler.handle(ctx)
        assert "alpha" in result
        assert "beta" in result
        assert "ON" in result
        assert "OFF" in result

    def test_shows_hint_lines(self, handler, ctx, tmp_path):
        config = _make_config("alpha")
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_merged.get_all_projects", return_value=[("alpha", "/w/alpha")]),
        ):
            result = handler.handle(ctx)
        assert "/autoreview" in result
        assert "/noautoreview" in result

    def test_no_projects_found_returns_error(self, handler, ctx):
        config = {"projects": {}}
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_merged.get_all_projects", return_value=[]),
        ):
            result = handler.handle(ctx)
        assert "No projects found" in result


# ===========================================================================
# handle — enable project
# ===========================================================================


class TestEnableProject:
    def test_enable_project(self, handler, ctx, tmp_path):
        config = _make_config("koan")
        ctx.args = "koan"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_config.save_projects_config") as mock_save,
        ):
            result = handler.handle(ctx)
        assert "enabled" in result.lower()
        assert "koan" in result
        mock_save.assert_called_once()
        assert config["projects"]["koan"]["autoreview"] is True

    def test_enable_already_enabled_is_idempotent(self, handler, ctx, tmp_path):
        config = _make_config("koan", autoreview_overrides={"koan": True})
        ctx.args = "koan"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_config.save_projects_config") as mock_save,
        ):
            result = handler.handle(ctx)
        assert "already enabled" in result.lower()
        mock_save.assert_not_called()

    def test_unknown_project_returns_error(self, handler, ctx, tmp_path):
        config = _make_config("koan")
        ctx.args = "nonexistent"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_merged.get_all_projects", return_value=[("koan", "/w/koan")]),
            patch("app.workspace_discovery.discover_workspace_projects", return_value=[]),
        ):
            result = handler.handle(ctx)
        assert "Unknown project" in result
        assert "koan" in result


# ===========================================================================
# handle — disable project (/noautoreview)
# ===========================================================================


class TestDisableProject:
    def test_disable_project(self, handler, ctx, tmp_path):
        config = _make_config("koan", autoreview_overrides={"koan": True})
        ctx.command_name = "noautoreview"
        ctx.args = "koan"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_config.save_projects_config") as mock_save,
        ):
            result = handler.handle(ctx)
        assert "disabled" in result.lower()
        assert "koan" in result
        mock_save.assert_called_once()
        assert config["projects"]["koan"]["autoreview"] is False

    def test_disable_already_disabled_is_idempotent(self, handler, ctx, tmp_path):
        config = _make_config("koan")  # autoreview defaults to False
        ctx.command_name = "noautoreview"
        ctx.args = "koan"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_config.save_projects_config") as mock_save,
        ):
            result = handler.handle(ctx)
        assert "already disabled" in result.lower()
        mock_save.assert_not_called()


# ===========================================================================
# handle — /autoreview all / /autoreview none
# ===========================================================================


class TestSetAll:
    def test_enable_all(self, handler, ctx, tmp_path):
        config = _make_config("alpha", "beta")
        ctx.args = "all"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_merged.get_all_projects", return_value=[("alpha", "/w/alpha"), ("beta", "/w/beta")]),
            patch("app.projects_config.save_projects_config") as mock_save,
        ):
            result = handler.handle(ctx)
        assert "2 project" in result
        mock_save.assert_called_once()

    def test_disable_all_via_none(self, handler, ctx, tmp_path):
        config = _make_config("alpha", "beta", autoreview_overrides={"alpha": True, "beta": True})
        ctx.args = "none"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_merged.get_all_projects", return_value=[("alpha", "/w/alpha"), ("beta", "/w/beta")]),
            patch("app.projects_config.save_projects_config") as mock_save,
        ):
            result = handler.handle(ctx)
        assert "disabled" in result.lower()
        mock_save.assert_called_once()

    def test_enable_all_already_enabled_returns_no_change(self, handler, ctx, tmp_path):
        config = _make_config("alpha", autoreview_overrides={"alpha": True})
        ctx.args = "all"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_merged.get_all_projects", return_value=[("alpha", "/w/alpha")]),
            patch("app.projects_config.save_projects_config") as mock_save,
        ):
            result = handler.handle(ctx)
        assert "already enabled" in result.lower()
        mock_save.assert_not_called()

    def test_no_projects_returns_error(self, handler, ctx, tmp_path):
        config = {"projects": {}}
        ctx.args = "all"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.projects_merged.get_all_projects", return_value=[]),
        ):
            result = handler.handle(ctx)
        assert "No projects found" in result


# ===========================================================================
# handle — workspace project auto-creates entry
# ===========================================================================


class TestWorkspaceProject:
    def test_workspace_project_auto_creates_entry(self, handler, ctx, tmp_path):
        config = {"projects": {}}
        ctx.args = "myrepo"
        with (
            patch("app.projects_config.load_projects_config", return_value=config),
            patch("app.workspace_discovery.discover_workspace_projects",
                  return_value=[("myrepo", "/workspace/myrepo")]),
            patch("app.projects_config.save_projects_config") as mock_save,
        ):
            result = handler.handle(ctx)
        assert "enabled" in result.lower()
        assert "myrepo" in config["projects"]
        mock_save.assert_called_once()
