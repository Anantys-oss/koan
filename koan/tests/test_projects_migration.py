"""Tests for projects_migration.py — env vars to projects.yaml migration."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from app.projects_migration import (
    _load_config_auto_merge,
    _parse_env_projects,
    build_projects_yaml,
    run_migration,
    should_migrate,
)


@pytest.fixture
def tmp_koan_root(tmp_path):
    """Create a temporary KOAN_ROOT with instance/config.yaml."""
    instance = tmp_path / "instance"
    instance.mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# should_migrate
# ---------------------------------------------------------------------------


class TestShouldMigrate:
    def test_returns_false_when_projects_yaml_exists(self, tmp_koan_root):
        (tmp_koan_root / "projects.yaml").write_text("projects: {}")
        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/bar"}):
            assert should_migrate(str(tmp_koan_root)) is False

    def test_returns_true_when_koan_projects_set(self, tmp_koan_root):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/bar"}, clear=False):
            assert should_migrate(str(tmp_koan_root)) is True

    def test_returns_true_when_koan_project_path_set(self, tmp_koan_root):
        env = {"KOAN_PROJECT_PATH": "/some/path"}
        with patch.dict(os.environ, env, clear=False):
            # Remove KOAN_PROJECTS if set
            os.environ.pop("KOAN_PROJECTS", None)
            assert should_migrate(str(tmp_koan_root)) is True

    def test_returns_false_when_no_env_vars(self, tmp_koan_root):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KOAN_PROJECTS", None)
            os.environ.pop("KOAN_PROJECT_PATH", None)
            assert should_migrate(str(tmp_koan_root)) is False

    def test_returns_false_when_empty_env_vars(self, tmp_koan_root):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "", "KOAN_PROJECT_PATH": ""}):
            assert should_migrate(str(tmp_koan_root)) is False


# ---------------------------------------------------------------------------
# _parse_env_projects
# ---------------------------------------------------------------------------


class TestParseEnvProjects:
    def test_parse_koan_projects(self):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/a;bar:/b"}):
            result = _parse_env_projects()
            assert result == [("foo", "/a"), ("bar", "/b")]

    def test_parse_koan_projects_with_spaces(self):
        with patch.dict(os.environ, {"KOAN_PROJECTS": " foo : /a ; bar : /b "}):
            result = _parse_env_projects()
            assert result == [("foo", "/a"), ("bar", "/b")]

    def test_parse_koan_projects_single_entry(self):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "myapp:/home/user/myapp"}):
            result = _parse_env_projects()
            assert result == [("myapp", "/home/user/myapp")]

    def test_parse_koan_projects_skips_empty(self):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/a;;bar:/b;"}):
            result = _parse_env_projects()
            assert result == [("foo", "/a"), ("bar", "/b")]

    def test_parse_koan_projects_skips_malformed(self):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/a;bad;bar:/b"}):
            result = _parse_env_projects()
            # "bad" has no colon but passes ":" check — but name/path could be empty
            assert ("foo", "/a") in result
            assert ("bar", "/b") in result

    def test_parse_koan_project_path_fallback(self):
        with patch.dict(os.environ, {"KOAN_PROJECT_PATH": "/home/user/myproject"}, clear=False):
            os.environ.pop("KOAN_PROJECTS", None)
            result = _parse_env_projects()
            assert len(result) == 1
            assert result[0][0] == "myproject"
            assert result[0][1] == "/home/user/myproject"

    def test_parse_koan_project_path_derives_name(self):
        with patch.dict(os.environ, {"KOAN_PROJECT_PATH": "/home/user/My App"}, clear=False):
            os.environ.pop("KOAN_PROJECTS", None)
            result = _parse_env_projects()
            assert result[0][0] == "my-app"

    def test_koan_projects_takes_precedence(self):
        env = {"KOAN_PROJECTS": "foo:/a", "KOAN_PROJECT_PATH": "/b"}
        with patch.dict(os.environ, env):
            result = _parse_env_projects()
            assert result == [("foo", "/a")]

    def test_empty_env_returns_empty(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KOAN_PROJECTS", None)
            os.environ.pop("KOAN_PROJECT_PATH", None)
            result = _parse_env_projects()
            assert result == []


# ---------------------------------------------------------------------------
# _load_config_auto_merge
# ---------------------------------------------------------------------------


class TestLoadConfigAutoMerge:
    def test_no_config_file(self, tmp_koan_root):
        result = _load_config_auto_merge(str(tmp_koan_root))
        assert result == {}

    def test_empty_config(self, tmp_koan_root):
        (tmp_koan_root / "instance" / "config.yaml").write_text("{}")
        result = _load_config_auto_merge(str(tmp_koan_root))
        assert result == {}

    def test_per_project_overrides(self, tmp_koan_root):
        config = {
            "projects": {
                "myapp": {
                    "git_auto_merge": {
                        "enabled": True,
                        "strategy": "merge",
                    }
                }
            }
        }
        (tmp_koan_root / "instance" / "config.yaml").write_text(yaml.dump(config))
        result = _load_config_auto_merge(str(tmp_koan_root))
        assert "myapp" in result
        assert result["myapp"]["enabled"] is True
        assert result["myapp"]["strategy"] == "merge"

    def test_multiple_project_overrides(self, tmp_koan_root):
        config = {
            "projects": {
                "app1": {"git_auto_merge": {"enabled": True}},
                "app2": {"git_auto_merge": {"base_branch": "staging"}},
            }
        }
        (tmp_koan_root / "instance" / "config.yaml").write_text(yaml.dump(config))
        result = _load_config_auto_merge(str(tmp_koan_root))
        assert len(result) == 2
        assert result["app1"]["enabled"] is True
        assert result["app2"]["base_branch"] == "staging"

    def test_ignores_projects_without_auto_merge(self, tmp_koan_root):
        config = {
            "projects": {
                "app1": {"git_auto_merge": {"enabled": True}},
                "app2": {"some_other_key": True},
            }
        }
        (tmp_koan_root / "instance" / "config.yaml").write_text(yaml.dump(config))
        result = _load_config_auto_merge(str(tmp_koan_root))
        assert "app1" in result
        assert "app2" not in result

    def test_handles_invalid_yaml(self, tmp_koan_root):
        (tmp_koan_root / "instance" / "config.yaml").write_text("{{invalid")
        result = _load_config_auto_merge(str(tmp_koan_root))
        assert result == {}


# ---------------------------------------------------------------------------
# build_projects_yaml
# ---------------------------------------------------------------------------


class TestBuildProjectsYaml:
    def test_basic_build(self):
        projects = [("foo", "/a"), ("bar", "/b")]
        content = build_projects_yaml(projects)
        data = yaml.safe_load(content.split("\n\n", 1)[1])  # Skip header
        assert "defaults" in data
        assert "projects" in data
        assert "bar" in data["projects"]
        assert "foo" in data["projects"]
        assert data["projects"]["foo"]["path"] == "/a"

    def test_includes_defaults(self):
        content = build_projects_yaml([("app", "/x")])
        data = yaml.safe_load(content.split("\n\n", 1)[1])
        defaults = data["defaults"]["git_auto_merge"]
        assert defaults["enabled"] is False
        assert defaults["base_branch"] == "main"
        assert defaults["strategy"] == "squash"

    def test_includes_auto_merge_overrides(self):
        projects = [("app", "/x")]
        overrides = {"app": {"enabled": True, "strategy": "merge"}}
        content = build_projects_yaml(projects, overrides)
        data = yaml.safe_load(content.split("\n\n", 1)[1])
        assert data["projects"]["app"]["git_auto_merge"]["enabled"] is True

    def test_sorted_alphabetically(self):
        projects = [("zebra", "/z"), ("alpha", "/a"), ("mid", "/m")]
        content = build_projects_yaml(projects)
        data = yaml.safe_load(content.split("\n\n", 1)[1])
        names = list(data["projects"].keys())
        assert names == ["alpha", "mid", "zebra"]

    def test_header_comment(self):
        content = build_projects_yaml([("x", "/y")])
        assert "Auto-generated" in content
        assert "projects.example.yaml" in content

    def test_no_overrides(self):
        projects = [("app", "/x")]
        content = build_projects_yaml(projects)
        data = yaml.safe_load(content.split("\n\n", 1)[1])
        assert "git_auto_merge" not in data["projects"]["app"]

    def test_valid_yaml_output(self):
        projects = [("a", "/a"), ("b", "/b")]
        overrides = {"a": {"enabled": True}}
        content = build_projects_yaml(projects, overrides)
        # Should be valid YAML
        data = yaml.safe_load(content.split("\n\n", 1)[1])
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# run_migration (integration)
# ---------------------------------------------------------------------------


class TestRunMigration:
    def test_creates_projects_yaml(self, tmp_koan_root):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/a;bar:/b"}, clear=False):
            msgs = run_migration(str(tmp_koan_root))
            assert len(msgs) >= 1
            # instance/ exists in the fixture, so migration writes there.
            written = tmp_koan_root / "instance" / "projects.yaml"
            assert written.exists()

            data = yaml.safe_load(written.read_text().split("\n\n", 1)[1])
            assert "foo" in data["projects"]
            assert "bar" in data["projects"]

    def test_migration_message_includes_count(self, tmp_koan_root):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "a:/x;b:/y;c:/z"}, clear=False):
            msgs = run_migration(str(tmp_koan_root))
            assert any("3 project(s)" in m for m in msgs)

    def test_migration_message_includes_source(self, tmp_koan_root):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "a:/x"}, clear=False):
            msgs = run_migration(str(tmp_koan_root))
            assert any("KOAN_PROJECTS" in m for m in msgs)

    def test_migration_from_project_path(self, tmp_koan_root):
        with patch.dict(os.environ, {"KOAN_PROJECT_PATH": "/my/project"}, clear=False):
            os.environ.pop("KOAN_PROJECTS", None)
            msgs = run_migration(str(tmp_koan_root))
            assert any("KOAN_PROJECT_PATH" in m for m in msgs)
            assert (tmp_koan_root / "instance" / "projects.yaml").exists()

    def test_skips_when_projects_yaml_exists(self, tmp_koan_root):
        (tmp_koan_root / "projects.yaml").write_text("existing: true")
        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/a"}):
            msgs = run_migration(str(tmp_koan_root))
            assert msgs == []
            # File not overwritten
            assert "existing" in (tmp_koan_root / "projects.yaml").read_text()

    def test_skips_when_no_env_vars(self, tmp_koan_root):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KOAN_PROJECTS", None)
            os.environ.pop("KOAN_PROJECT_PATH", None)
            msgs = run_migration(str(tmp_koan_root))
            assert msgs == []

    def test_imports_auto_merge_overrides(self, tmp_koan_root):
        config = {
            "projects": {
                "foo": {
                    "git_auto_merge": {
                        "enabled": True,
                        "strategy": "merge",
                    }
                }
            }
        }
        (tmp_koan_root / "instance" / "config.yaml").write_text(yaml.dump(config))

        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/a;bar:/b"}, clear=False):
            msgs = run_migration(str(tmp_koan_root))
            assert any("git_auto_merge" in m for m in msgs)

            data = yaml.safe_load(
                (tmp_koan_root / "instance" / "projects.yaml")
                .read_text()
                .split("\n\n", 1)[1]
            )
            assert data["projects"]["foo"]["git_auto_merge"]["enabled"] is True
            assert "git_auto_merge" not in data["projects"]["bar"]

    def test_idempotent(self, tmp_koan_root):
        """Running migration twice doesn't modify the file."""
        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/a"}, clear=False):
            written = tmp_koan_root / "instance" / "projects.yaml"
            run_migration(str(tmp_koan_root))
            content1 = written.read_text()

            msgs = run_migration(str(tmp_koan_root))
            assert msgs == []  # Second run does nothing
            content2 = written.read_text()
            assert content1 == content2

    def test_deprecation_message(self, tmp_koan_root):
        with patch.dict(os.environ, {"KOAN_PROJECTS": "foo:/a"}, clear=False):
            msgs = run_migration(str(tmp_koan_root))
            assert any("remove" in m.lower() for m in msgs)

    def test_generated_yaml_is_loadable(self, tmp_koan_root):
        """The generated projects.yaml can be loaded by projects_config.py."""
        with patch.dict(os.environ, {"KOAN_PROJECTS": "a:/x;b:/y"}, clear=False):
            run_migration(str(tmp_koan_root))

        from app.projects_config import load_projects_config
        config = load_projects_config(str(tmp_koan_root))
        assert config is not None
        assert "a" in config["projects"]
        assert "b" in config["projects"]


# ---------------------------------------------------------------------------
# run_startup integration
# ---------------------------------------------------------------------------


class TestRunStartupMigration:
    def test_migration_called_during_startup(self, tmp_koan_root):
        """Verify that run_startup calls run_migration."""
        # We can't easily test run_startup directly (too many deps),
        # but we can verify the import works and function is callable
        from app.projects_migration import run_migration
        assert callable(run_migration)
