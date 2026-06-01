"""Tests for REST API project routes."""

import os
import pytest
from unittest.mock import patch, MagicMock

from app.api import create_app

_TOKEN = "proj-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture
def instance_dir(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "missions.md").write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    return inst


@pytest.fixture
def api_client(tmp_path, instance_dir):
    with patch.dict(os.environ, {"KOAN_API_TOKEN": _TOKEN, "KOAN_ROOT": str(tmp_path)}):
        app = create_app(koan_root=tmp_path, instance_dir=instance_dir)
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client


class TestListProjects:
    def test_list_projects_empty(self, api_client):
        with patch("app.utils.get_known_projects", return_value=[]):
            resp = api_client.get("/v1/projects", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_list_projects_returns_names(self, api_client):
        with patch("app.utils.get_known_projects", return_value=[("alpha", "/projects/alpha"), ("beta", "/projects/beta")]):
            with patch("app.projects_config.load_projects_config", return_value=None):
                resp = api_client.get("/v1/projects", headers=_AUTH)
        data = resp.get_json()
        assert len(data) == 2
        names = [p["name"] for p in data]
        assert "alpha" in names
        assert "beta" in names

    def test_list_projects_unauthenticated(self, api_client):
        resp = api_client.get("/v1/projects")
        assert resp.status_code == 401


class TestAddProject:
    def test_add_project_missing_github_url_returns_422(self, api_client):
        resp = api_client.post("/v1/projects", json={}, headers=_AUTH)
        assert resp.status_code == 422

    def test_add_project_calls_skill(self, api_client):
        with patch("app.api.routes_projects._run_skill", return_value=(True, "Project added")) as mock_skill:
            resp = api_client.post(
                "/v1/projects",
                json={"github_url": "https://github.com/org/repo"},
                headers=_AUTH,
            )
        assert resp.status_code == 201
        mock_skill.assert_called_once_with("add_project", "https://github.com/org/repo")
        data = resp.get_json()
        assert "result" in data

    def test_add_project_with_name(self, api_client):
        with patch("app.api.routes_projects._run_skill", return_value=(True, "ok")) as mock_skill:
            api_client.post(
                "/v1/projects",
                json={"github_url": "https://github.com/org/repo", "name": "myrepo"},
                headers=_AUTH,
            )
        mock_skill.assert_called_once_with("add_project", "https://github.com/org/repo myrepo")

    def test_add_project_skill_failure_returns_500(self, api_client):
        with patch("app.api.routes_projects._run_skill", return_value=(False, "Error running skill: boom")):
            resp = api_client.post(
                "/v1/projects",
                json={"github_url": "https://github.com/org/repo"},
                headers=_AUTH,
            )
        assert resp.status_code == 500
        assert "error" in resp.get_json()


class TestDeleteProject:
    def test_delete_project_calls_skill(self, api_client):
        with patch("app.api.routes_projects._run_skill", return_value=(True, "Deleted")) as mock_skill:
            resp = api_client.delete("/v1/projects/alpha", headers=_AUTH)
        assert resp.status_code == 200
        mock_skill.assert_called_once_with("delete_project", "alpha")
        data = resp.get_json()
        assert "result" in data

    def test_delete_project_skill_failure_returns_500(self, api_client):
        with patch("app.api.routes_projects._run_skill", return_value=(False, "Skill 'delete_project' not found")):
            resp = api_client.delete("/v1/projects/alpha", headers=_AUTH)
        assert resp.status_code == 500
        assert "error" in resp.get_json()
