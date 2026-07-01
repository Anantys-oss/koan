"""Unit tests for app.dashboard_service.stats (no Flask client)."""
from unittest.mock import patch

from app.dashboard_service import stats as svc


def test_get_signal_status_delegates(tmp_path):
    with patch.object(svc.state, "KOAN_ROOT", tmp_path):
        status = svc.get_signal_status()
    assert isinstance(status, dict)


def test_get_agent_state_delegates(tmp_path):
    with patch.object(svc.state, "KOAN_ROOT", tmp_path):
        state = svc.get_agent_state()
    assert "state" in state


def test_build_forecast_paused():
    with patch.object(svc, "get_signal_status", return_value={"paused": True}):
        result = svc.build_forecast()
    assert result["status"] == "paused"


def test_build_forecast_warming_up_few_samples(tmp_path):
    with patch.object(svc.state, "INSTANCE_DIR", tmp_path), \
         patch.object(svc, "get_signal_status", return_value={}):
        result = svc.build_forecast()
    assert result["status"] == "warming_up"


def test_compute_dashboard_skill_metrics_no_dir(tmp_path):
    with patch.object(svc.state, "INSTANCE_DIR", tmp_path):
        assert svc.compute_dashboard_skill_metrics() == {}
