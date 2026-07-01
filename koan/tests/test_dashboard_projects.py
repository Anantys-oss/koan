"""Tests for the dashboard /projects registry page and service layer."""
from unittest.mock import patch

import pytest


@pytest.fixture
def reg_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
    inst = tmp_path / "instance"
    (inst / "journal").mkdir(parents=True)
    (inst / "missions.md").write_text(
        "## Pending\n- [project:alpha] do a thing\n- [project:beta] other\n"
        "## In Progress\n- [project:alpha] ▶ running\n## Done\n"
    )
    return tmp_path


def test_registry_one_card_per_project_with_counts(reg_env):
    from app.dashboard import state
    from app.dashboard_service import projects as proj_svc
    with patch.object(state, "KOAN_ROOT", reg_env), \
         patch.object(state, "INSTANCE_DIR", reg_env / "instance"), \
         patch.object(state, "MISSIONS_FILE", reg_env / "instance" / "missions.md"), \
         patch("app.dashboard_service.projects.get_known_projects",
               return_value=[("alpha", str(reg_env / "alpha")),
                             ("beta", str(reg_env / "beta"))]), \
         patch("app.dashboard_service.projects.load_projects_config", return_value=None):
        reg = proj_svc.build_project_registry()
    by_name = {c["name"]: c for c in reg}
    assert by_name["alpha"]["pending"] == 1
    assert by_name["alpha"]["in_progress"] == 1
    assert by_name["beta"]["pending"] == 1
    # No projects.yaml → github_url missing → checklist flags it
    assert any(i["key"] == "github_url" for i in by_name["alpha"]["checklist"])


def test_status_for_unknown_project_is_empty_card(reg_env):
    from app.dashboard import state
    from app.dashboard_service import projects as proj_svc
    with patch.object(state, "KOAN_ROOT", reg_env), \
         patch.object(state, "INSTANCE_DIR", reg_env / "instance"), \
         patch.object(state, "MISSIONS_FILE", reg_env / "instance" / "missions.md"), \
         patch("app.dashboard_service.projects.get_known_projects", return_value=[]), \
         patch("app.dashboard_service.projects.load_projects_config", return_value=None):
        card = proj_svc.build_project_status("ghost")
    assert card["name"] == "ghost"
    assert card["pending"] == 0 and card["in_progress"] == 0
    assert card["last_activity"] is None


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

from pathlib import Path

from jinja2 import FileSystemLoader

_REAL_TEMPLATES = Path(__file__).parent.parent / "templates" / "dashboard"


def _client():
    from app.dashboard import app
    app.config.update(TESTING=True)
    app.jinja_loader = FileSystemLoader(str(_REAL_TEMPLATES))
    return app.test_client()


def test_index_redirects_when_two_or_more_projects():
    with patch("app.dashboard.core._configured_project_count", return_value=2):
        resp = _client().get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/projects" in resp.headers["Location"]


def test_index_single_project_renders_dashboard():
    with patch("app.dashboard.core._configured_project_count", return_value=1):
        resp = _client().get("/", follow_redirects=False)
    assert resp.status_code == 200


def test_status_endpoint_returns_card_json():
    card = {"name": "alpha", "pending": 2, "in_progress": 0,
            "checklist": [], "found": True}
    with patch("app.dashboard.projects.proj_svc.build_project_status", return_value=card):
        resp = _client().get("/api/projects/alpha/status")
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "alpha"


def test_status_endpoint_404_for_unknown_project():
    card = {"name": "ghost", "pending": 0, "in_progress": 0,
            "checklist": [], "found": False}
    with patch("app.dashboard.projects.proj_svc.build_project_status", return_value=card):
        resp = _client().get("/api/projects/ghost/status")
    assert resp.status_code == 404
    assert resp.get_json()["found"] is False


def test_add_project_rejects_blank_url():
    resp = _client().post("/projects/add", data={"github_url": ""})
    # Re-renders the page with an error, does not 500
    assert resp.status_code in (200, 400)
