"""Tests for health_server.py — webhook and trigger-report endpoints."""

import json
import hmac
import hashlib
from datetime import date
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
    monkeypatch.setenv("INSTANCE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")

    config_dir = tmp_path / "watcher"
    config_dir.mkdir()
    (tmp_path / "config.yaml").write_text("governor:\n  autonomy:\n    watcher: notify\n")
    (config_dir / "user_registry.yaml").write_text("users: []\naliases: {}\n")

    import importlib
    import app.utils
    importlib.reload(app.utils)

    # Reset webhook secret cache
    import app.watcher.github_client as ghc
    ghc._webhook_secret_cache = (None, 0.0)

    import app.health_server
    importlib.reload(app.health_server)

    app.health_server.app.config["TESTING"] = True
    with app.health_server.app.test_client() as c:
        yield c


class TestWebhookGitHub:

    def test_get_returns_405(self, client):
        resp = client.get("/webhook/github")
        assert resp.status_code == 405

    def test_missing_signature_returns_401(self, client):
        resp = client.post(
            "/webhook/github",
            data=b'{}',
            content_type="application/json",
        )
        assert resp.status_code in (401, 403)

    def test_invalid_signature_returns_401(self, client):
        resp = client.post(
            "/webhook/github",
            data=b'{"action": "test"}',
            content_type="application/json",
            headers={
                "X-Hub-Signature-256": "sha256=invalid",
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "test-delivery-1",
            },
        )
        assert resp.status_code in (401, 403)

    @patch("app.watcher.github_client.get_webhook_secret", return_value="test-secret")
    @patch("app.watcher.github_client.is_duplicate", return_value=False)
    @patch("app.watcher.github_client.save_processed_delivery")
    @patch("app.watcher.journal.append_event")
    @patch("app.watcher.journal.count_events_today", return_value=1)
    @patch("app.watcher.journal.get_last_event", return_value=None)
    @patch("app.watcher.helpers.classify_author", return_value=("tech", "Test User"))
    @patch("app.watcher.helpers.update_repo_activity")
    def test_valid_push_returns_200(self, mock_ura, mock_ca, mock_gle, mock_cet,
                                    mock_ae, mock_spd, mock_dup, mock_secret,
                                    client):
        payload = json.dumps({
            "ref": "refs/heads/main",
            "commits": [{"id": "abc123", "message": "test", "author": {"username": "tester"}}],
            "repository": {"full_name": "YourArtOfficial/test-repo"},
            "sender": {"login": "tester"},
            "head_commit": {"id": "abc123", "message": "test commit", "timestamp": "2026-03-04T10:00:00Z"},
        }).encode()
        sig = "sha256=" + hmac.new(b"test-secret", payload, hashlib.sha256).hexdigest()
        resp = client.post(
            "/webhook/github",
            data=payload,
            content_type="application/json",
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "test-delivery-2",
            },
        )
        assert resp.status_code == 200


class TestTriggerReport:

    @patch("app.governor_daily_report.send_daily_report", return_value="Rapport du 2026-03-04\nActivité normale")
    def test_trigger_report_success(self, mock_report, client):
        resp = client.post("/api/trigger-report")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "sent"
        assert "date" in data

    @patch("app.governor_daily_report.send_daily_report", return_value="Aucune activité détectée")
    def test_trigger_report_no_activity(self, mock_report, client):
        resp = client.post("/api/trigger-report")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "no_activity"

    @patch("app.governor_daily_report.send_daily_report", side_effect=Exception("LLM error"))
    def test_trigger_report_error(self, mock_report, client):
        resp = client.post("/api/trigger-report")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["status"] == "error"

    def test_trigger_report_invalid_date(self, client):
        resp = client.post("/api/trigger-report?date=not-a-date")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["status"] == "error"

    @patch("app.governor_daily_report.send_daily_report", return_value="Rapport du 2026-03-03")
    def test_trigger_report_with_date(self, mock_report, client):
        resp = client.post("/api/trigger-report?date=2026-03-03")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["date"] == "2026-03-03"
        mock_report.assert_called_once_with(target_date=date(2026, 3, 3), notify=True)
