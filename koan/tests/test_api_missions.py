"""Tests for REST API mission routes."""

import json
import os
import pytest
from unittest.mock import patch

from app.api import create_app

_TOKEN = "test-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture
def instance_dir(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "missions.md").write_text(
        "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
    )
    return inst


@pytest.fixture
def api_client(tmp_path, instance_dir):
    with patch.dict(os.environ, {"KOAN_API_TOKEN": _TOKEN, "KOAN_ROOT": str(tmp_path)}):
        app = create_app(koan_root=tmp_path, instance_dir=instance_dir)
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client


class TestCreateMission:
    def test_create_text_mission_returns_202(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions",
            json={"text": "Fix the bug"},
            headers=_AUTH,
        )
        assert resp.status_code == 202
        data = resp.get_json()
        assert "id" in data
        assert data["status"] == "pending"

    def test_create_command_mission(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions",
            json={"command": "/status"},
            headers=_AUTH,
        )
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "pending"

    def test_create_mission_writes_to_missions_md(self, api_client, instance_dir):
        api_client.post(
            "/v1/missions",
            json={"text": "Test mission content"},
            headers=_AUTH,
        )
        content = (instance_dir / "missions.md").read_text()
        assert "Test mission content" in content

    def test_create_mission_with_project_tag(self, api_client, instance_dir):
        api_client.post(
            "/v1/missions",
            json={"text": "Fix bug", "project": "my-project"},
            headers=_AUTH,
        )
        content = (instance_dir / "missions.md").read_text()
        assert "[project:my-project]" in content

    def test_create_mission_writes_sidecar(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions",
            json={"text": "Sidecar test"},
            headers=_AUTH,
        )
        mission_id = resp.get_json()["id"]
        sidecar = instance_dir / ".api-missions.json"
        assert sidecar.exists()
        records = json.loads(sidecar.read_text())
        assert any(r["id"] == mission_id for r in records)

    def test_create_mission_missing_body_returns_422(self, api_client):
        resp = api_client.post("/v1/missions", json={}, headers=_AUTH)
        assert resp.status_code == 422
        data = resp.get_json()
        assert data["error"]["code"] == "invalid_request"

    def test_create_mission_unauthenticated_returns_401(self, api_client):
        resp = api_client.post("/v1/missions", json={"text": "test"})
        assert resp.status_code == 401


class TestGetMission:
    def test_get_existing_mission(self, api_client, instance_dir):
        # Create a mission first
        resp = api_client.post(
            "/v1/missions", json={"text": "Mission to get"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.get(f"/v1/missions/{mission_id}", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == mission_id

    def test_get_nonexistent_mission_returns_404(self, api_client):
        resp = api_client.get("/v1/missions/nonexistent-id", headers=_AUTH)
        assert resp.status_code == 404

    def test_get_mission_reconciles_status(self, api_client, instance_dir):
        """When mission moves to in_progress in missions.md, GET reflects it."""
        resp = api_client.post(
            "/v1/missions", json={"text": "Reconcile me"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        # Read actual content (missions get timestamp-stamped on insert)
        content = (instance_dir / "missions.md").read_text()
        lines = content.splitlines(keepends=True)
        # Find the line containing our mission text
        pending_line = next(
            (ln for ln in lines if "Reconcile me" in ln), None
        )
        assert pending_line is not None, "Mission not found in missions.md"

        # Move it: remove from pending section, add to in_progress section
        content = content.replace(pending_line, "")
        content = content.replace(
            "## In Progress\n\n",
            f"## In Progress\n\n{pending_line}",
        )
        (instance_dir / "missions.md").write_text(content)

        resp = api_client.get(f"/v1/missions/{mission_id}", headers=_AUTH)
        data = resp.get_json()
        assert data["status"] == "in_progress"


class TestDeleteMission:
    def test_cancel_pending_mission(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Cancel me"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.delete(f"/v1/missions/{mission_id}", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "removed"

    def test_cancel_removes_from_missions_md(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Remove from file"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        api_client.delete(f"/v1/missions/{mission_id}", headers=_AUTH)
        content = (instance_dir / "missions.md").read_text()
        assert "Remove from file" not in content

    def test_cancel_in_progress_returns_409(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "In progress one"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        # Move to in_progress (missions have timestamps, read actual line)
        content = (instance_dir / "missions.md").read_text()
        lines = content.splitlines(keepends=True)
        pending_line = next(
            (ln for ln in lines if "In progress one" in ln), None
        )
        assert pending_line is not None, "Mission not found in missions.md"

        content = content.replace(pending_line, "")
        content = content.replace(
            "## In Progress\n\n",
            f"## In Progress\n\n{pending_line}",
        )
        (instance_dir / "missions.md").write_text(content)

        resp = api_client.delete(f"/v1/missions/{mission_id}", headers=_AUTH)
        assert resp.status_code == 409

    def test_cancel_nonexistent_returns_404(self, api_client):
        resp = api_client.delete("/v1/missions/no-such-id", headers=_AUTH)
        assert resp.status_code == 404


class TestListMissions:
    def test_list_empty(self, api_client):
        resp = api_client.get("/v1/missions", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_list_returns_created_missions(self, api_client):
        api_client.post("/v1/missions", json={"text": "Mission A"}, headers=_AUTH)
        api_client.post("/v1/missions", json={"text": "Mission B"}, headers=_AUTH)

        resp = api_client.get("/v1/missions", headers=_AUTH)
        data = resp.get_json()
        assert len(data) == 2

    def test_list_filter_by_status(self, api_client):
        api_client.post("/v1/missions", json={"text": "Pending one"}, headers=_AUTH)

        resp = api_client.get("/v1/missions?status=pending", headers=_AUTH)
        data = resp.get_json()
        assert len(data) == 1

        resp = api_client.get("/v1/missions?status=done", headers=_AUTH)
        assert resp.get_json() == []

    def test_list_filter_by_project(self, api_client):
        api_client.post(
            "/v1/missions",
            json={"text": "For proj", "project": "alpha"},
            headers=_AUTH,
        )
        api_client.post("/v1/missions", json={"text": "No project"}, headers=_AUTH)

        resp = api_client.get("/v1/missions?project=alpha", headers=_AUTH)
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["project"] == "alpha"


class TestCancelByText:
    def test_cancel_by_text_marks_removed(self, instance_dir):
        from app.api.mission_index import record_mission, cancel_by_text, get_mission
        mid = record_mission(instance_dir, "- Fix bug", None)
        result = cancel_by_text(instance_dir, "- Fix bug")
        assert result is True
        rec = get_mission(instance_dir, mid)
        assert rec["status"] == "removed"

    def test_cancel_by_text_no_match_returns_false(self, instance_dir):
        from app.api.mission_index import cancel_by_text
        result = cancel_by_text(instance_dir, "- Nonexistent mission")
        assert result is False

    def test_cancel_by_text_only_matches_pending(self, instance_dir):
        from app.api.mission_index import record_mission, cancel_mission, cancel_by_text, get_mission
        mid = record_mission(instance_dir, "- Already done", None)
        cancel_mission(instance_dir, mid)
        result = cancel_by_text(instance_dir, "- Already done")
        assert result is False

    def test_cancel_by_text_substring_match(self, instance_dir):
        from app.api.mission_index import record_mission, cancel_by_text, get_mission
        mid = record_mission(instance_dir, "- [project:koan] Fix something", "koan")
        result = cancel_by_text(instance_dir, "Fix something")
        assert result is True
        assert get_mission(instance_dir, mid)["status"] == "removed"


class TestRecordMissionDedup:
    def test_record_mission_dedup_returns_same_id(self, instance_dir):
        from app.api.mission_index import record_mission, list_missions
        id1 = record_mission(instance_dir, "- Fix bug", None)
        id2 = record_mission(instance_dir, "- Fix bug", None)
        assert id1 == id2
        records = list_missions(instance_dir)
        assert len(records) == 1

    def test_record_mission_no_dedup_across_status(self, instance_dir):
        from app.api.mission_index import record_mission, cancel_mission, list_missions
        id1 = record_mission(instance_dir, "- Repeat task", None)
        cancel_mission(instance_dir, id1)
        id2 = record_mission(instance_dir, "- Repeat task", None)
        assert id1 != id2
        records = list_missions(instance_dir)
        assert len(records) == 2
