from unittest.mock import patch

from app.dashboard import app as dashboard_app


def _client():
    dashboard_app.config["TESTING"] = True
    return dashboard_app.test_client()


def test_config_sync_endpoint_returns_status():
    fake = {"synced": True, "restart_pending": False,
            "changed_safe_keys": [], "changed_unsafe_keys": []}
    with patch("app.config_sync.compute_status", return_value=fake):
        resp = _client().get("/api/config/sync")
    assert resp.status_code == 200
    assert resp.get_json()["restart_pending"] is False


def test_restart_if_idle_blocks_when_working():
    with patch("app.dashboard.config.stats_svc.get_agent_state",
               return_value={"state": "working"}), \
         patch("app.restart_manager.request_restart") as req:
        resp = _client().post("/api/config/restart-if-idle")
    assert resp.status_code == 409
    req.assert_not_called()


def test_restart_if_idle_restarts_when_idle():
    with patch("app.dashboard.config.stats_svc.get_agent_state",
               return_value={"state": "idle"}), \
         patch("app.restart_manager.request_restart") as req:
        resp = _client().post("/api/config/restart-if-idle")
    assert resp.status_code == 200
    req.assert_called_once()
