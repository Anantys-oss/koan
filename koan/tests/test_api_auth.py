"""Tests for REST API authentication."""

import os
import pytest
from unittest.mock import patch

from app.api import create_app


@pytest.fixture
def instance_dir(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "missions.md").write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    return inst


@pytest.fixture
def api_client(tmp_path, instance_dir):
    """Flask test client with a valid token set."""
    with patch.dict(os.environ, {"KOAN_API_TOKEN": "test-secret", "KOAN_ROOT": str(tmp_path)}):
        app = create_app(koan_root=tmp_path, instance_dir=instance_dir)
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client


@pytest.fixture
def api_client_no_token(tmp_path, instance_dir):
    """Flask test client with NO token configured."""
    env = {"KOAN_ROOT": str(tmp_path)}
    # Remove KOAN_API_TOKEN if set
    env_clean = {k: v for k, v in os.environ.items() if k != "KOAN_API_TOKEN"}
    env_clean["KOAN_ROOT"] = str(tmp_path)
    with patch.dict(os.environ, env_clean, clear=True):
        with patch("app.config._load_config", return_value={}):
            app = create_app(koan_root=tmp_path, instance_dir=instance_dir)
            app.config["TESTING"] = True
            with app.test_client() as client:
                yield client


class TestHealth:
    def test_health_unauthenticated(self, api_client):
        resp = api_client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["name"] == "koan"

    def test_health_no_auth_header_still_200(self, api_client):
        """Health endpoint is public — no token needed."""
        resp = api_client.get("/v1/health")
        assert resp.status_code == 200


class TestTokenAuth:
    def test_missing_auth_header_returns_401(self, api_client):
        resp = api_client.get("/v1/status")
        assert resp.status_code == 401
        data = resp.get_json()
        assert data["error"]["code"] == "missing_token"

    def test_wrong_token_returns_403(self, api_client):
        resp = api_client.get("/v1/status", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error"]["code"] == "invalid_token"

    def test_correct_token_returns_200(self, api_client):
        resp = api_client.get("/v1/status", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200

    def test_malformed_bearer_returns_401(self, api_client):
        resp = api_client.get("/v1/status", headers={"Authorization": "Token test-secret"})
        assert resp.status_code == 401

    def test_empty_token_returns_401(self, api_client):
        resp = api_client.get("/v1/status", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_no_token_configured_returns_403(self, api_client_no_token):
        """When no token is configured, check_token returns False — 403."""
        resp = api_client_no_token.get(
            "/v1/status", headers={"Authorization": "Bearer anything"}
        )
        assert resp.status_code == 403


class TestIsApiEnabled:
    def test_disabled_by_default(self):
        with patch("app.config._load_config", return_value={}):
            from app.config import is_api_enabled
            assert is_api_enabled() is False

    def test_enabled_when_configured(self):
        with patch("app.config._load_config", return_value={"api": {"enabled": True}}):
            from app.config import is_api_enabled
            assert is_api_enabled() is True

    def test_api_port_default(self):
        with patch("app.config._load_config", return_value={}):
            from app.config import get_api_port
            assert get_api_port() == 8420

    def test_api_host_default(self):
        with patch("app.config._load_config", return_value={}):
            from app.config import get_api_host
            assert get_api_host() == "127.0.0.1"

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("KOAN_API_TOKEN", "env-token")
        with patch("app.config._load_config", return_value={}):
            from app.config import get_api_token
            assert get_api_token() == "env-token"

    def test_token_from_config(self, monkeypatch):
        monkeypatch.delenv("KOAN_API_TOKEN", raising=False)
        with patch("app.config._load_config", return_value={"api": {"token": "cfg-token"}}):
            from app.config import get_api_token
            assert get_api_token() == "cfg-token"
