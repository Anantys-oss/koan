"""Tests for advisor indexer — cross-platform repo discovery."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from app.advisor.indexer import _get_all_repos


@pytest.fixture
def tmp_instance(tmp_path, monkeypatch):
    """Setup a temporary instance dir with repos.yaml."""
    instance_dir = tmp_path / "instance"
    watcher_dir = instance_dir / "watcher"
    watcher_dir.mkdir(parents=True)

    repos_data = {
        "repos": [
            {
                "name": "ai-governor",
                "platform": "github",
                "url": "https://github.com/YourArtOfficial/ai-governor",
                "status": "active",
                "language": None,
                "last_activity": "2026-03-04T10:00:00Z",
                "contributors": ["stephaneyourart"],
                "webhook_active": True,
            }
        ]
    }
    (watcher_dir / "repos.yaml").write_text(
        yaml.dump(repos_data, default_flow_style=False)
    )

    config_data = {
        "watcher": {
            "github": {"org": "YourArtOfficial"},
            "gitlab": {"group": "yourart", "token_env": "GITLAB_TOKEN"},
        },
        "advisor": {"enabled": True},
    }
    (instance_dir / "config.yaml").write_text(
        yaml.dump(config_data, default_flow_style=False)
    )

    monkeypatch.setattr("app.utils.INSTANCE_DIR", instance_dir)
    monkeypatch.setattr("app.utils.KOAN_ROOT", tmp_path)
    monkeypatch.setattr("app.watcher.helpers.INSTANCE_DIR", instance_dir)

    # Reset cached config
    import app.advisor.indexer as idx
    idx._watcher_config_cache = None

    return instance_dir


def test_get_all_repos_without_gitlab_token(tmp_instance):
    """Without GITLAB_TOKEN, returns only repos.yaml repos."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GITLAB_TOKEN", None)
        repos = _get_all_repos()

    assert len(repos) == 1
    assert repos[0]["name"] == "ai-governor"
    assert repos[0]["platform"] == "github"


def test_get_all_repos_discovers_gitlab(tmp_instance):
    """With GITLAB_TOKEN, discovers GitLab repos and merges them."""
    mock_projects = [
        {"name": "iris-front", "id": 101, "web_url": "https://gitlab.com/yourart/iris-front",
         "last_activity_at": "2026-03-04T10:00:00Z"},
        {"name": "iris-api", "id": 102, "web_url": "https://gitlab.com/yourart/iris-api",
         "last_activity_at": "2026-03-03T10:00:00Z"},
    ]

    with patch.dict(os.environ, {"GITLAB_TOKEN": "fake-token"}):
        with patch("app.watcher.gitlab_client.GitLabClient") as MockGL:
            mock_client = MagicMock()
            mock_client.list_group_projects.return_value = mock_projects
            MockGL.from_config.return_value = mock_client

            repos = _get_all_repos()

    assert len(repos) == 3  # 1 github + 2 gitlab
    gitlab_repos = [r for r in repos if r["platform"] == "gitlab"]
    assert len(gitlab_repos) == 2
    assert {r["name"] for r in gitlab_repos} == {"iris-front", "iris-api"}

    # Check GitLab repo has project ID for fast lookup
    iris = next(r for r in gitlab_repos if r["name"] == "iris-front")
    assert iris["id"] == 101
    assert iris["url"] == "https://gitlab.com/yourart/iris-front"


def test_get_all_repos_no_duplicates(tmp_instance):
    """If a GitLab repo is already in repos.yaml, don't add it twice."""
    # Add a gitlab repo to repos.yaml
    repos_path = tmp_instance / "watcher" / "repos.yaml"
    data = yaml.safe_load(repos_path.read_text())
    data["repos"].append({
        "name": "iris-front",
        "platform": "gitlab",
        "url": "https://gitlab.com/yourart/iris-front",
        "status": "active",
        "language": None,
        "last_activity": "2026-03-04T10:00:00Z",
        "contributors": [],
        "webhook_active": False,
    })
    repos_path.write_text(yaml.dump(data, default_flow_style=False))

    mock_projects = [
        {"name": "iris-front", "id": 101, "web_url": "https://gitlab.com/yourart/iris-front",
         "last_activity_at": "2026-03-04T10:00:00Z"},
        {"name": "iris-api", "id": 102, "web_url": "https://gitlab.com/yourart/iris-api",
         "last_activity_at": "2026-03-03T10:00:00Z"},
    ]

    with patch.dict(os.environ, {"GITLAB_TOKEN": "fake-token"}):
        with patch("app.watcher.gitlab_client.GitLabClient") as MockGL:
            mock_client = MagicMock()
            mock_client.list_group_projects.return_value = mock_projects
            MockGL.from_config.return_value = mock_client

            repos = _get_all_repos()

    assert len(repos) == 3  # 1 github + 1 existing gitlab + 1 new gitlab
    gitlab_repos = [r for r in repos if r["platform"] == "gitlab"]
    assert len(gitlab_repos) == 2


def test_get_all_repos_persists_discovered(tmp_instance):
    """Discovered GitLab repos are saved to repos.yaml for persistence."""
    mock_projects = [
        {"name": "new-repo", "id": 200, "web_url": "https://gitlab.com/yourart/new-repo",
         "last_activity_at": "2026-03-04T10:00:00Z"},
    ]

    with patch.dict(os.environ, {"GITLAB_TOKEN": "fake-token"}):
        with patch("app.watcher.gitlab_client.GitLabClient") as MockGL:
            mock_client = MagicMock()
            mock_client.list_group_projects.return_value = mock_projects
            MockGL.from_config.return_value = mock_client

            _get_all_repos()

    # Check repos.yaml was updated
    repos_path = tmp_instance / "watcher" / "repos.yaml"
    saved = yaml.safe_load(repos_path.read_text())
    names = [r["name"] for r in saved["repos"]]
    assert "new-repo" in names
    assert "ai-governor" in names
