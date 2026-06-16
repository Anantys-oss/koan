"""Tests for REST API admin routes (pause/resume, config, restart/shutdown/update)."""

import os
import pytest
from unittest.mock import patch, MagicMock

from app.api import create_app

_TOKEN = "admin-token"
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


class TestPauseResume:
    def test_pause_creates_koan_pause(self, api_client, tmp_path):
        resp = api_client.post("/v1/pause", json={}, headers=_AUTH)
        assert resp.status_code == 200
        assert (tmp_path / ".koan-pause").exists()
        data = resp.get_json()
        assert data["status"] == "paused"

    def test_pause_with_duration(self, api_client, tmp_path):
        resp = api_client.post("/v1/pause", json={"duration": "2h"}, headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["duration"] == "2h"

    def test_pause_invalid_duration_returns_422(self, api_client):
        resp = api_client.post("/v1/pause", json={"duration": "not-valid"}, headers=_AUTH)
        assert resp.status_code == 422

    def test_resume_removes_koan_pause(self, api_client, tmp_path):
        (tmp_path / ".koan-pause").write_text("manual\n0\n\n")
        resp = api_client.post("/v1/resume", headers=_AUTH)
        assert resp.status_code == 200
        assert not (tmp_path / ".koan-pause").exists()
        data = resp.get_json()
        assert data["status"] == "resumed"

    def test_resume_ok_when_not_paused(self, api_client):
        resp = api_client.post("/v1/resume", headers=_AUTH)
        assert resp.status_code == 200


class TestConfig:
    def test_config_returns_structure(self, api_client):
        with patch("app.utils.load_config", return_value={"foo": "bar", "api": {"token": "secret"}}):
            resp = api_client.get("/v1/config", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "config" in data
        assert "projects" in data

    def test_config_masks_token(self, api_client):
        with patch("app.utils.load_config", return_value={"api": {"token": "my-secret"}}):
            resp = api_client.get("/v1/config", headers=_AUTH)
        data = resp.get_json()
        api_cfg = data["config"].get("api", {})
        assert api_cfg.get("token") == "***"

    def test_config_masks_bot_token(self, api_client):
        cfg = {"telegram": {"bot_token": "real-secret", "chat_id": "123"}}
        with patch("app.utils.load_config", return_value=cfg):
            resp = api_client.get("/v1/config", headers=_AUTH)
        data = resp.get_json()
        assert data["config"]["telegram"]["bot_token"] == "***"
        assert data["config"]["telegram"]["chat_id"] == "123"

    @pytest.mark.parametrize("key", [
        "api_key", "private_key", "access_key", "signing_key", "encryption_key",
        "credential", "credentials", "service_credential",
        "passphrase", "ssh_passphrase",
        "password", "db_password",
        "secret", "webhook_secret",
        "token", "access_token", "refresh_token",
    ])
    def test_config_masks_secret_patterns(self, api_client, key):
        cfg = {"section": {key: "should-be-masked", "enabled": True}}
        with patch("app.utils.load_config", return_value=cfg):
            resp = api_client.get("/v1/config", headers=_AUTH)
        data = resp.get_json()
        assert data["config"]["section"][key] == "***"
        assert data["config"]["section"]["enabled"] is True

    @pytest.mark.parametrize("key", [
        "enabled", "host", "port", "chat_id", "name", "description",
        "check_interval", "cooldown_minutes",
    ])
    def test_config_does_not_mask_non_secrets(self, api_client, key):
        cfg = {"section": {key: "visible-value"}}
        with patch("app.utils.load_config", return_value=cfg):
            resp = api_client.get("/v1/config", headers=_AUTH)
        data = resp.get_json()
        assert data["config"]["section"][key] == "visible-value"


class TestRestart:
    def test_restart_creates_signal_file(self, api_client, tmp_path):
        resp = api_client.post("/v1/restart", headers=_AUTH)
        assert resp.status_code == 200
        # writes both per-consumer markers so the restart actually fires,
        # not the deprecated (no-op) legacy .koan-restart.
        assert (tmp_path / ".koan-restart-run").exists()
        assert (tmp_path / ".koan-restart-bridge").exists()
        assert not (tmp_path / ".koan-restart").exists()
        data = resp.get_json()
        assert data["status"] == "restart_signaled"


class TestShutdown:
    def test_shutdown_creates_stop_file(self, api_client, tmp_path):
        resp = api_client.post("/v1/shutdown", headers=_AUTH)
        assert resp.status_code == 200
        assert (tmp_path / ".koan-stop").exists()
        data = resp.get_json()
        assert data["status"] == "shutdown_signaled"


class TestUpdate:
    def test_update_calls_pull_upstream(self, api_client, tmp_path):
        mock_result = MagicMock()
        mock_result.__str__ = MagicMock(return_value="updated")
        with patch("app.update_manager.pull_upstream", return_value=mock_result):
            resp = api_client.post("/v1/update", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "updated"
        # Restart should also be signaled — via the per-consumer markers.
        assert (tmp_path / ".koan-restart-run").exists()
        assert (tmp_path / ".koan-restart-bridge").exists()

    def test_update_returns_500_on_error(self, api_client):
        with patch("app.update_manager.pull_upstream", side_effect=RuntimeError("git error")):
            resp = api_client.post("/v1/update", headers=_AUTH)
        assert resp.status_code == 500
