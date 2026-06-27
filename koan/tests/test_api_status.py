"""Tests for GET /v1/status response enrichment."""

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.api import create_app


@pytest.fixture
def instance_dir(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "missions.md").write_text(
        "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
    )
    return inst


@pytest.fixture
def client(tmp_path, instance_dir):
    with patch.dict(
        os.environ,
        {"KOAN_API_TOKEN": "test-secret", "KOAN_ROOT": str(tmp_path)},
    ):
        app = create_app(koan_root=tmp_path, instance_dir=instance_dir)
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c


AUTH = {"Authorization": "Bearer test-secret"}


class TestStatusDefaults:
    def test_elapsed_seconds_zero_when_no_status_file(self, client):
        resp = client.get("/v1/status", headers=AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["agent"]["elapsed_seconds"] == 0

    def test_signals_all_false_when_no_signal_files(self, client):
        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert data["signals"] == {
            "stop_requested": False,
            "quota_paused": False,
            "paused": False,
        }

    def test_attention_count_zero_by_default(self, client):
        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert data["attention_count"] == 0


class TestElapsedSeconds:
    def test_reflects_file_age(self, client, tmp_path):
        status_file = tmp_path / ".koan-status"
        status_file.write_text("Run 1/10 — executing on my-project")
        past = time.time() - 120
        os.utime(status_file, (past, past))

        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert 118 <= data["agent"]["elapsed_seconds"] <= 122


class TestSignalFlags:
    def test_stop_requested(self, client, tmp_path):
        (tmp_path / ".koan-stop").touch()
        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert data["signals"]["stop_requested"] is True

    def test_quota_paused(self, client, tmp_path):
        (tmp_path / ".koan-quota-reset").touch()
        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert data["signals"]["quota_paused"] is True

    def test_paused(self, client, tmp_path):
        (tmp_path / ".koan-pause").touch()
        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert data["signals"]["paused"] is True


class TestExecutionTruth:
    def test_idle_execution_when_no_active_signal(self, client):
        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert data["execution"]["provider_state"] == "idle"
        assert data["execution"]["zombie"] is False
        assert data["agent"]["execution"]["state"] == "idle"

    def test_working_execution_with_live_pid(self, client, tmp_path):
        from app.active_mission import write_active

        write_active(tmp_path, pid=os.getpid(), project="koan")
        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert data["execution"]["provider_state"] == "working"
        assert data["execution"]["zombie"] is False

    def test_zombie_when_in_progress_but_run_loop_stale(
        self, client, instance_dir, tmp_path
    ):
        # In Progress line present, no .koan-active signal, and the run loop
        # heartbeat is stale → genuine orphan, flagged loudly.
        (instance_dir / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n- task\n\n## Done\n"
        )
        # The heartbeat age is the timestamp stored in the file, not its mtime.
        (tmp_path / ".koan-run-heartbeat").write_text(str(time.time() - 1200))

        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert data["execution"]["in_progress_lines"] == 1
        assert data["execution"]["zombie"] is True

    def test_no_zombie_during_start_stop_window(self, client, instance_dir, tmp_path):
        # In Progress with no provider signal but a FRESH run-loop heartbeat is
        # the normal start/stop window, not a zombie — must not flap true.
        (instance_dir / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n- task\n\n## Done\n"
        )
        (tmp_path / ".koan-run-heartbeat").write_text(str(time.time()))

        resp = client.get("/v1/status", headers=AUTH)
        data = resp.get_json()
        assert data["execution"]["in_progress_lines"] == 1
        assert data["execution"]["zombie"] is False


class TestAttentionCount:
    def test_returns_actual_count(self, client):
        with patch("app.attention.get_attention_count", return_value=5):
            resp = client.get("/v1/status", headers=AUTH)
            data = resp.get_json()
            assert data["attention_count"] == 5

    def test_defaults_to_zero_on_error(self, client):
        with patch(
            "app.attention.get_attention_count",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.get("/v1/status", headers=AUTH)
            data = resp.get_json()
            assert data["attention_count"] == 0
