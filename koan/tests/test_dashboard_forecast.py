"""Tests for dashboard forecast endpoint and _build_forecast helper."""

import json
import shutil
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock

from app import dashboard


def _make_test_instance(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "memory").mkdir()
    (inst / "journal").mkdir()
    (inst / "missions.md").write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n")

    tpl_src = Path(__file__).parent.parent / "templates"
    tpl_dest = tmp_path / "koan" / "templates"
    shutil.copytree(tpl_src, tpl_dest)

    return inst


class TestBuildForecast:
    """Unit tests for _build_forecast()."""

    def _patch_signals(self, paused=False, quota_paused=False):
        return {"paused": paused, "quota_paused": quota_paused,
                "stop_requested": False, "pause_reason": "", "reset_time": ""}

    def _make_snapshot(self, samples_count=5, rate=0.05):
        snap = MagicMock()
        snap.samples = [MagicMock()] * samples_count
        snap.burn_rate_pct_per_minute.return_value = rate
        snap.time_to_exhaustion.return_value = 120.0
        return snap

    def test_normal_case(self, tmp_path):
        snap = self._make_snapshot(samples_count=5, rate=0.05)
        with patch.object(dashboard, "INSTANCE_DIR", tmp_path), \
             patch.object(dashboard, "get_signal_status", return_value=self._patch_signals()), \
             patch("app.burn_rate.BurnRateSnapshot", return_value=snap), \
             patch("app.burn_rate.MIN_SAMPLES_FOR_ESTIMATE", 5), \
             patch("app.iteration_manager._read_session_pct_and_reset", return_value=(40.0, 300.0, {})), \
             patch.object(dashboard, "get_agent_state", return_value={"autonomous_mode": "DEEP"}):
            result = dashboard._build_forecast()

        assert result["status"] == "normal"
        assert result["burn_rate_pct_per_minute"] == 0.05
        assert result["time_to_exhaustion_minutes"] == 120.0
        assert result["session_pct"] == 40.0
        assert result["autonomous_mode"] == "DEEP"
        assert result["samples_count"] == 5
        # Verify mode passed as lowercase
        snap.time_to_exhaustion.assert_called_once_with(40.0, mode="deep")

    def test_warming_up_insufficient_samples(self, tmp_path):
        snap = self._make_snapshot(samples_count=3, rate=None)
        snap.burn_rate_pct_per_minute.return_value = None
        with patch.object(dashboard, "INSTANCE_DIR", tmp_path), \
             patch.object(dashboard, "get_signal_status", return_value=self._patch_signals()), \
             patch("app.burn_rate.BurnRateSnapshot", return_value=snap), \
             patch("app.burn_rate.MIN_SAMPLES_FOR_ESTIMATE", 5):
            result = dashboard._build_forecast()

        assert result["status"] == "warming_up"
        assert result["burn_rate_pct_per_minute"] is None
        assert result["time_to_exhaustion_minutes"] is None
        assert result["samples_count"] == 3

    def test_paused(self, tmp_path):
        with patch.object(dashboard, "INSTANCE_DIR", tmp_path), \
             patch.object(dashboard, "get_signal_status", return_value=self._patch_signals(paused=True)):
            result = dashboard._build_forecast()

        assert result["status"] == "paused"
        assert result["burn_rate_pct_per_minute"] is None
        assert result["time_to_exhaustion_minutes"] is None

    def test_quota_paused(self, tmp_path):
        with patch.object(dashboard, "INSTANCE_DIR", tmp_path), \
             patch.object(dashboard, "get_signal_status", return_value=self._patch_signals(quota_paused=True)):
            result = dashboard._build_forecast()

        assert result["status"] == "paused"

    def test_no_usage_state_json(self, tmp_path):
        snap = self._make_snapshot(samples_count=5, rate=0.05)
        with patch.object(dashboard, "INSTANCE_DIR", tmp_path), \
             patch.object(dashboard, "get_signal_status", return_value=self._patch_signals()), \
             patch("app.burn_rate.BurnRateSnapshot", return_value=snap), \
             patch("app.burn_rate.MIN_SAMPLES_FOR_ESTIMATE", 5), \
             patch("app.iteration_manager._read_session_pct_and_reset", return_value=(None, None, None)):
            result = dashboard._build_forecast()

        assert result["status"] == "warming_up"
        assert result["session_pct"] is None
        assert result["burn_rate_pct_per_minute"] == 0.05  # rate is available

    def test_empty_autonomous_mode(self, tmp_path):
        snap = self._make_snapshot(samples_count=5, rate=0.03)
        with patch.object(dashboard, "INSTANCE_DIR", tmp_path), \
             patch.object(dashboard, "get_signal_status", return_value=self._patch_signals()), \
             patch("app.burn_rate.BurnRateSnapshot", return_value=snap), \
             patch("app.burn_rate.MIN_SAMPLES_FOR_ESTIMATE", 5), \
             patch("app.iteration_manager._read_session_pct_and_reset", return_value=(60.0, 200.0, {})), \
             patch.object(dashboard, "get_agent_state", return_value={"autonomous_mode": ""}):
            result = dashboard._build_forecast()

        assert result["status"] == "normal"
        assert result["autonomous_mode"] is None
        # mode=None passed (no multiplier applied)
        snap.time_to_exhaustion.assert_called_once_with(60.0, mode=None)

    def test_wait_mode_returns_zero_tte(self, tmp_path):
        snap = self._make_snapshot(samples_count=5, rate=0.1)
        snap.time_to_exhaustion.return_value = 0.0
        with patch.object(dashboard, "INSTANCE_DIR", tmp_path), \
             patch.object(dashboard, "get_signal_status", return_value=self._patch_signals()), \
             patch("app.burn_rate.BurnRateSnapshot", return_value=snap), \
             patch("app.burn_rate.MIN_SAMPLES_FOR_ESTIMATE", 5), \
             patch("app.iteration_manager._read_session_pct_and_reset", return_value=(100.0, 0.0, {})), \
             patch.object(dashboard, "get_agent_state", return_value={"autonomous_mode": "WAIT"}):
            result = dashboard._build_forecast()

        assert result["status"] == "normal"
        assert result["time_to_exhaustion_minutes"] == 0.0

    def test_import_error_returns_warming_up(self, tmp_path):
        import sys
        # Setting module to None causes ImportError on 'from X import Y'
        with patch.object(dashboard, "INSTANCE_DIR", tmp_path), \
             patch.object(dashboard, "get_signal_status", return_value=self._patch_signals()), \
             patch.dict(sys.modules, {"app.burn_rate": None}):
            result = dashboard._build_forecast()

        assert result["status"] == "warming_up"


class TestApiForecastEndpoint:
    """Test /api/forecast HTTP endpoint."""

    def test_endpoint_returns_200(self, tmp_path):
        inst = _make_test_instance(tmp_path)
        with patch.object(dashboard, "KOAN_ROOT", tmp_path), \
             patch.object(dashboard, "INSTANCE_DIR", inst), \
             patch("app.dashboard._build_forecast", return_value={
                 "status": "warming_up",
                 "burn_rate_pct_per_minute": None,
                 "time_to_exhaustion_minutes": None,
                 "session_pct": None,
                 "autonomous_mode": None,
                 "samples_count": 0,
             }):
            resp = dashboard.app.test_client().get("/api/forecast")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "status" in data
        assert "samples_count" in data

    def test_endpoint_warming_up_on_fresh_instance(self, tmp_path):
        inst = _make_test_instance(tmp_path)
        with patch.object(dashboard, "KOAN_ROOT", tmp_path), \
             patch.object(dashboard, "INSTANCE_DIR", inst):
            resp = dashboard.app.test_client().get("/api/forecast")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] in ("warming_up", "paused")
        assert data["burn_rate_pct_per_minute"] is None
        assert data["time_to_exhaustion_minutes"] is None

    def test_endpoint_returns_all_expected_keys(self, tmp_path):
        inst = _make_test_instance(tmp_path)
        expected_keys = {
            "status", "burn_rate_pct_per_minute", "time_to_exhaustion_minutes",
            "session_pct", "autonomous_mode", "samples_count",
        }
        with patch.object(dashboard, "KOAN_ROOT", tmp_path), \
             patch.object(dashboard, "INSTANCE_DIR", inst):
            resp = dashboard.app.test_client().get("/api/forecast")

        data = json.loads(resp.data)
        assert expected_keys.issubset(data.keys())

    def test_endpoint_paused_when_pause_file_exists(self, tmp_path):
        inst = _make_test_instance(tmp_path)
        (tmp_path / ".koan-pause").write_text("manual\n")
        with patch.object(dashboard, "KOAN_ROOT", tmp_path), \
             patch.object(dashboard, "INSTANCE_DIR", inst):
            resp = dashboard.app.test_client().get("/api/forecast")

        data = json.loads(resp.data)
        assert data["status"] == "paused"


class TestSSEStreamForecast:
    """Test that the SSE stream includes forecast data."""

    def test_sse_includes_forecast_key(self, tmp_path):
        inst = _make_test_instance(tmp_path)
        with patch.object(dashboard, "KOAN_ROOT", tmp_path), \
             patch.object(dashboard, "INSTANCE_DIR", inst), \
             patch.object(dashboard, "MISSIONS_JSON_FILE", inst / "missions.md"), \
             patch("app.dashboard._build_forecast", return_value={
                 "status": "warming_up",
                 "burn_rate_pct_per_minute": None,
                 "time_to_exhaustion_minutes": None,
                 "session_pct": None,
                 "autonomous_mode": None,
                 "samples_count": 0,
             }), \
             patch("app.dashboard.time.sleep", side_effect=RuntimeError("break")):
            resp = dashboard.app.test_client().get("/api/state/stream")

        data_line = None
        for chunk in resp.response:
            if isinstance(chunk, bytes):
                chunk = chunk.decode()
            if chunk.startswith("data: "):
                data_line = chunk
                break

        assert data_line is not None
        payload = json.loads(data_line[6:].strip())
        assert "forecast" in payload
        assert payload["forecast"]["status"] == "warming_up"

    def test_sse_forecast_updates_on_burn_rate_mtime_change(self, tmp_path):
        inst = _make_test_instance(tmp_path)
        burn_rate_file = inst / ".burn-rate.json"
        burn_rate_file.write_text("{}")

        forecast_normal = {
            "status": "normal",
            "burn_rate_pct_per_minute": 0.05,
            "time_to_exhaustion_minutes": 120.0,
            "session_pct": 40.0,
            "autonomous_mode": "DEEP",
            "samples_count": 5,
        }

        with patch.object(dashboard, "KOAN_ROOT", tmp_path), \
             patch.object(dashboard, "INSTANCE_DIR", inst), \
             patch.object(dashboard, "MISSIONS_JSON_FILE", inst / "missions.md"), \
             patch("app.dashboard._build_forecast", return_value=forecast_normal), \
             patch("app.dashboard.time.sleep", side_effect=RuntimeError("break")):
            resp = dashboard.app.test_client().get("/api/state/stream")

        data_line = None
        for chunk in resp.response:
            if isinstance(chunk, bytes):
                chunk = chunk.decode()
            if chunk.startswith("data: "):
                data_line = chunk
                break

        assert data_line is not None
        payload = json.loads(data_line[6:].strip())
        assert payload["forecast"]["status"] == "normal"
        assert payload["forecast"]["autonomous_mode"] == "DEEP"
