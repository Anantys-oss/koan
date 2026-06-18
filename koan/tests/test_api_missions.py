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
    with patch.dict(os.environ, {"KOAN_API_TOKEN": _TOKEN, "KOAN_ROOT": str(tmp_path)}), \
         patch("app.utils.KOAN_ROOT", tmp_path):
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

    def test_cancel_does_not_remove_substring_match(self, api_client, instance_dir):
        """DELETE must use exact matching — not substring. Deleting 'Fix bug'
        must not remove 'Fix bug in auth module'."""
        resp_short = api_client.post(
            "/v1/missions", json={"text": "Fix bug"}, headers=_AUTH
        )
        resp_long = api_client.post(
            "/v1/missions", json={"text": "Fix bug in auth module"}, headers=_AUTH
        )
        short_id = resp_short.get_json()["id"]

        api_client.delete(f"/v1/missions/{short_id}", headers=_AUTH)

        content = (instance_dir / "missions.md").read_text()
        assert "Fix bug in auth module" in content


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

    def test_cancel_by_text_exact_match_after_strip(self, instance_dir):
        from app.api.mission_index import record_mission, cancel_by_text, get_mission
        mid = record_mission(instance_dir, "- [project:koan] Fix something", "koan")
        result = cancel_by_text(instance_dir, "- [project:koan] Fix something")
        assert result is True
        assert get_mission(instance_dir, mid)["status"] == "removed"

    def test_cancel_by_text_rejects_substring(self, instance_dir):
        from app.api.mission_index import record_mission, cancel_by_text
        record_mission(instance_dir, "- [project:koan] Fix something", "koan")
        result = cancel_by_text(instance_dir, "Fix")
        assert result is False


class TestReconcileSubstringMatch:
    """Reconcile must not confuse missions that share a common prefix."""

    def test_reconcile_rejects_substring_match(self, api_client, instance_dir):
        """A mission 'Fix bug' reconciled against missions.md containing only
        'Fix bug in auth module' must NOT report as present."""
        from app.api.mission_index import record_mission, reconcile

        mid = record_mission(instance_dir, "- Fix bug", None)

        missions_file = instance_dir / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n"
            "- Fix bug in auth module\n\n"
            "## In Progress\n\n## Done\n"
        )

        rec = reconcile(instance_dir, missions_file, mid)
        assert rec["status"] == "removed"


class TestRecordMissionDedup:
    def test_record_mission_dedup_returns_same_id(self, instance_dir):
        from app.api.mission_index import record_mission, list_missions
        id1 = record_mission(instance_dir, "- Fix bug", None)
        id2 = record_mission(instance_dir, "- Fix bug", None)
        assert id1 == id2
        records = list_missions(instance_dir)
        assert len(records) == 1

    def test_record_mission_dedup_different_project_creates_new(self, instance_dir):
        from app.api.mission_index import record_mission, list_missions
        id1 = record_mission(instance_dir, "- Fix bug", "alpha")
        id2 = record_mission(instance_dir, "- Fix bug", "beta")
        assert id1 != id2
        records = list_missions(instance_dir)
        assert len(records) == 2

    def test_record_mission_no_dedup_across_status(self, instance_dir):
        from app.api.mission_index import record_mission, cancel_mission, list_missions
        id1 = record_mission(instance_dir, "- Repeat task", None)
        cancel_mission(instance_dir, id1)
        id2 = record_mission(instance_dir, "- Repeat task", None)
        assert id1 != id2
        records = list_missions(instance_dir)
        assert len(records) == 2


class TestUpdateMissionText:
    def test_update_text_changes_record(self, instance_dir):
        from app.api.mission_index import record_mission, update_mission_text, get_mission
        mid = record_mission(instance_dir, "- Old text", None)
        result = update_mission_text(instance_dir, mid, "- New text")
        assert result is True
        rec = get_mission(instance_dir, mid)
        assert rec["text"] == "- New text"

    def test_update_text_nonexistent_returns_false(self, instance_dir):
        from app.api.mission_index import update_mission_text
        result = update_mission_text(instance_dir, "no-such-id", "- New")
        assert result is False

    def test_update_text_only_updates_pending(self, instance_dir):
        from app.api.mission_index import record_mission, cancel_mission, update_mission_text, get_mission
        mid = record_mission(instance_dir, "- Done mission", None)
        cancel_mission(instance_dir, mid)
        result = update_mission_text(instance_dir, mid, "- Updated")
        assert result is False
        rec = get_mission(instance_dir, mid)
        assert rec["text"] == "- Done mission"


class TestEditMission:
    def test_edit_pending_mission_returns_200(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Original text"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.patch(
            f"/v1/missions/{mission_id}",
            json={"text": "Updated text"},
            headers=_AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == mission_id
        assert data["status"] == "pending"

    def test_edit_updates_missions_md(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Before edit"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        api_client.patch(
            f"/v1/missions/{mission_id}",
            json={"text": "After edit"},
            headers=_AUTH,
        )
        content = (instance_dir / "missions.md").read_text()
        assert "After edit" in content
        assert "Before edit" not in content

    def test_edit_updates_sidecar_index(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Sidecar before"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        api_client.patch(
            f"/v1/missions/{mission_id}",
            json={"text": "Sidecar after"},
            headers=_AUTH,
        )
        resp = api_client.get(f"/v1/missions/{mission_id}", headers=_AUTH)
        data = resp.get_json()
        assert "Sidecar after" in data["text"]

    def test_edit_nonexistent_returns_404(self, api_client):
        resp = api_client.patch(
            "/v1/missions/no-such-id",
            json={"text": "New text"},
            headers=_AUTH,
        )
        assert resp.status_code == 404

    def test_edit_in_progress_returns_409(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Moving mission"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        content = (instance_dir / "missions.md").read_text()
        lines = content.splitlines(keepends=True)
        pending_line = next(ln for ln in lines if "Moving mission" in ln)
        content = content.replace(pending_line, "")
        content = content.replace(
            "## In Progress\n\n", f"## In Progress\n\n{pending_line}"
        )
        (instance_dir / "missions.md").write_text(content)

        resp = api_client.patch(
            f"/v1/missions/{mission_id}",
            json={"text": "Try to edit"},
            headers=_AUTH,
        )
        assert resp.status_code == 409

    def test_edit_missing_text_returns_422(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Some mission"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.patch(
            f"/v1/missions/{mission_id}", json={}, headers=_AUTH
        )
        assert resp.status_code == 422

    def test_edit_empty_text_returns_422(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Some mission"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.patch(
            f"/v1/missions/{mission_id}",
            json={"text": "   "},
            headers=_AUTH,
        )
        assert resp.status_code == 422

    def test_edit_non_string_text_returns_422(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Some mission"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.patch(
            f"/v1/missions/{mission_id}",
            json={"text": 12345},
            headers=_AUTH,
        )
        assert resp.status_code == 422

    def test_edit_project_mission_preserves_tag(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions",
            json={"text": "Project task", "project": "my-toolkit"},
            headers=_AUTH,
        )
        mission_id = resp.get_json()["id"]

        api_client.patch(
            f"/v1/missions/{mission_id}",
            json={"text": "Updated project task"},
            headers=_AUTH,
        )

        content = (instance_dir / "missions.md").read_text()
        assert "[project:my-toolkit]" in content
        assert "Updated project task" in content

        resp = api_client.get(f"/v1/missions/{mission_id}", headers=_AUTH)
        data = resp.get_json()
        assert data["status"] == "pending"
        assert "Updated project task" in data["text"]

    def test_edit_invalid_json_returns_422(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Some mission"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.patch(
            f"/v1/missions/{mission_id}",
            data="not json",
            content_type="application/json",
            headers=_AUTH,
        )
        assert resp.status_code == 422

    def test_edit_duplicate_pending_returns_409(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Duplicate task"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        # Inject a second identical pending entry directly into the Pending
        # section to create an ambiguous match.
        content = (instance_dir / "missions.md").read_text()
        content = content.replace(
            "## Pending\n", "## Pending\n\n- Duplicate task\n", 1
        )
        (instance_dir / "missions.md").write_text(content)

        resp = api_client.patch(
            f"/v1/missions/{mission_id}",
            json={"text": "New text"},
            headers=_AUTH,
        )
        assert resp.status_code == 409
        assert "Ambiguous" in resp.get_json()["error"]["message"]


class TestReorderMission:
    def test_reorder_returns_200(self, api_client, instance_dir):
        api_client.post("/v1/missions", json={"text": "First"}, headers=_AUTH)
        resp_second = api_client.post(
            "/v1/missions", json={"text": "Second"}, headers=_AUTH
        )
        mission_id = resp_second.get_json()["id"]

        resp = api_client.post(
            "/v1/missions/reorder",
            json={"mission_id": mission_id, "target_position": 1},
            headers=_AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == mission_id

    def test_reorder_changes_order_in_missions_md(self, api_client, instance_dir):
        api_client.post("/v1/missions", json={"text": "Alpha"}, headers=_AUTH)
        resp_beta = api_client.post(
            "/v1/missions", json={"text": "Beta"}, headers=_AUTH
        )
        beta_id = resp_beta.get_json()["id"]

        api_client.post(
            "/v1/missions/reorder",
            json={"mission_id": beta_id, "target_position": 1},
            headers=_AUTH,
        )
        content = (instance_dir / "missions.md").read_text()
        alpha_pos = content.find("Alpha")
        beta_pos = content.find("Beta")
        assert beta_pos < alpha_pos

    def test_reorder_nonexistent_returns_404(self, api_client):
        resp = api_client.post(
            "/v1/missions/reorder",
            json={"mission_id": "no-such-id", "target_position": 1},
            headers=_AUTH,
        )
        assert resp.status_code == 404

    def test_reorder_in_progress_returns_409(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Will move"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        content = (instance_dir / "missions.md").read_text()
        lines = content.splitlines(keepends=True)
        pending_line = next(ln for ln in lines if "Will move" in ln)
        content = content.replace(pending_line, "")
        content = content.replace(
            "## In Progress\n\n", f"## In Progress\n\n{pending_line}"
        )
        (instance_dir / "missions.md").write_text(content)

        resp = api_client.post(
            "/v1/missions/reorder",
            json={"mission_id": mission_id, "target_position": 1},
            headers=_AUTH,
        )
        assert resp.status_code == 409

    def test_reorder_missing_fields_returns_422(self, api_client):
        resp = api_client.post(
            "/v1/missions/reorder", json={}, headers=_AUTH
        )
        assert resp.status_code == 422

    def test_reorder_invalid_json_returns_422(self, api_client):
        resp = api_client.post(
            "/v1/missions/reorder",
            data="not json",
            content_type="application/json",
            headers=_AUTH,
        )
        assert resp.status_code == 422

    def test_reorder_invalid_target_returns_422(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Only one"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.post(
            "/v1/missions/reorder",
            json={"mission_id": mission_id, "target_position": 99},
            headers=_AUTH,
        )
        assert resp.status_code == 422

    def test_reorder_boolean_target_returns_422(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Bool test"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.post(
            "/v1/missions/reorder",
            json={"mission_id": mission_id, "target_position": True},
            headers=_AUTH,
        )
        assert resp.status_code == 422

    def test_reorder_float_target_returns_422(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Float test"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        resp = api_client.post(
            "/v1/missions/reorder",
            json={"mission_id": mission_id, "target_position": 1.9},
            headers=_AUTH,
        )
        assert resp.status_code == 422

    def test_reorder_duplicate_pending_returns_409(self, api_client, instance_dir):
        resp = api_client.post(
            "/v1/missions", json={"text": "Dup reorder"}, headers=_AUTH
        )
        mission_id = resp.get_json()["id"]

        # Inject a second identical pending entry directly into the Pending
        # section to create an ambiguous match.
        content = (instance_dir / "missions.md").read_text()
        content = content.replace(
            "## Pending\n", "## Pending\n\n- Dup reorder\n", 1
        )
        (instance_dir / "missions.md").write_text(content)

        resp = api_client.post(
            "/v1/missions/reorder",
            json={"mission_id": mission_id, "target_position": 1},
            headers=_AUTH,
        )
        assert resp.status_code == 409
        assert "Ambiguous" in resp.get_json()["error"]["message"]
