"""Tests for the /tracker core skill."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import yaml

from app.projects_config import invalidate_projects_config_cache
from app.skills import SkillContext


HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "tracker" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("tracker_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ctx(tmp_path, args=""):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="tracker",
        args=args,
    )


def _write_projects(root: Path) -> None:
    (root / "projects.yaml").write_text(
        """
projects:
  myapp:
    path: /tmp/myapp
    issue_tracker:
      provider: jira
      jira_project: FOO
      jira_issue_type: Story
      default_branch: release/11.126
  web:
    path: /tmp/web
    issue_tracker:
      provider: github
      repo: acme/web
"""
    )
    invalidate_projects_config_cache()


class TestTrackerSkill:
    def test_no_args_lists_project_trackers(self, tmp_path, monkeypatch):
        handler = _load_handler()
        _write_projects(tmp_path)
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        with patch(
            "app.utils.get_known_projects",
            return_value=[("myapp", "/tmp/myapp"), ("web", "/tmp/web")],
        ):
            result = handler.handle(_ctx(tmp_path))

        assert "Issue trackers:" in result
        assert "myapp: jira:FOO type:Story branch:release/11.126" in result
        assert "web: github:acme/web" in result

    def test_set_jira_tracker_writes_projects_yaml(self, tmp_path):
        handler = _load_handler()
        _write_projects(tmp_path)

        with patch("app.utils.is_known_project", return_value=True):
            result = handler.handle(
                _ctx(
                    tmp_path,
                    "set web jira key:BAR type:Bug branch:release/12.0",
                )
            )

        assert "Tracker set for web: jira key:BAR type:Bug branch:release/12.0" in result
        data = yaml.safe_load((tmp_path / "projects.yaml").read_text())
        assert data["projects"]["web"]["issue_tracker"] == {
            "provider": "jira",
            "jira_project": "BAR",
            "jira_issue_type": "Bug",
            "default_branch": "release/12.0",
        }

    def test_set_github_tracker_normalizes_repo_url(self, tmp_path):
        handler = _load_handler()
        _write_projects(tmp_path)

        with patch("app.utils.is_known_project", return_value=True):
            result = handler.handle(
                _ctx(
                    tmp_path,
                    "set myapp github repo:https://github.com/acme/myapp.git branch:main",
                )
            )

        assert "github repo:acme/myapp branch:main" in result
        data = yaml.safe_load((tmp_path / "projects.yaml").read_text())
        assert data["projects"]["myapp"]["issue_tracker"] == {
            "provider": "github",
            "repo": "acme/myapp",
            "default_branch": "main",
        }

    def test_set_rejects_unknown_project(self, tmp_path):
        handler = _load_handler()

        with patch("app.utils.is_known_project", return_value=False):
            result = handler.handle(_ctx(tmp_path, "set missing jira key:FOO"))

        assert "Unknown project: missing" in result

    def test_set_jira_requires_project_key(self, tmp_path):
        handler = _load_handler()

        with patch("app.utils.is_known_project", return_value=True):
            result = handler.handle(_ctx(tmp_path, "set myapp jira"))

        assert "Jira tracker requires key:PROJ" in result

    def test_skill_registered(self):
        from app.skills import build_registry

        registry = build_registry()
        skill = registry.find_by_command("tracker")

        assert skill is not None
        assert skill.name == "tracker"
        assert skill.group == "config"
