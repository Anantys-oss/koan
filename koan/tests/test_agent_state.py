"""Tests for the shared agent_state module."""

import json
import time

from app import agent_state


class TestGetSignalStatus:
    def test_no_files(self, tmp_path):
        result = agent_state.get_signal_status(tmp_path)
        assert result["stop_requested"] is False
        assert result["paused"] is False
        assert result["quota_paused"] is False
        assert result["loop_status"] == ""

    def test_stop_file(self, tmp_path):
        (tmp_path / ".koan-stop").write_text("1")
        result = agent_state.get_signal_status(tmp_path)
        assert result["stop_requested"] is True

    def test_pause_file(self, tmp_path):
        (tmp_path / ".koan-pause").write_text("manual\n")
        result = agent_state.get_signal_status(tmp_path)
        assert result["paused"] is True
        assert result["pause_reason"] == "manual"

    def test_status_file(self, tmp_path):
        (tmp_path / ".koan-status").write_text("Run 3/10 — executing")
        result = agent_state.get_signal_status(tmp_path)
        assert result["loop_status"] == "Run 3/10 — executing"


class TestGetAgentState:
    def test_idle_no_files(self, tmp_path):
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "idle"
        assert state["badge_color"] == "muted"
        assert state["label"] == "Idle"

    def test_stopped(self, tmp_path):
        (tmp_path / ".koan-stop").write_text("1")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "stopped"
        assert state["badge_color"] == "red"

    def test_paused_quota(self, tmp_path):
        (tmp_path / ".koan-pause").write_text("quota\n1740000000\nResets at 15:30\n")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "paused"
        assert "quota" in state["label"]
        assert state["pause_reason"] == "quota"

    def test_working_executing(self, tmp_path):
        (tmp_path / ".koan-status").write_text("Run 3/10 — executing mission on koan")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "working"
        assert state["run_info"] == "3/10"
        assert state["badge_color"] == "green"

    def test_working_skill_dispatch(self, tmp_path):
        (tmp_path / ".koan-status").write_text("Run 5/10 — skill dispatch on myproject")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "working"

    def test_working_review_mode(self, tmp_path):
        (tmp_path / ".koan-status").write_text("Run 2/10 — REVIEW on koan")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "working"
        assert state["autonomous_mode"] == "REVIEW"

    def test_working_implement_mode(self, tmp_path):
        (tmp_path / ".koan-status").write_text("Run 7/10 — IMPLEMENT on backend")
        state = agent_state.get_agent_state(tmp_path)
        assert state["autonomous_mode"] == "IMPLEMENT"

    def test_sleeping(self, tmp_path):
        (tmp_path / ".koan-status").write_text("Idle — sleeping 300s")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "sleeping"
        assert state["badge_color"] == "blue"

    def test_contemplating(self, tmp_path):
        (tmp_path / ".koan-status").write_text("Idle — post-contemplation sleep (14:30)")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "contemplating"

    def test_error_recovery(self, tmp_path):
        (tmp_path / ".koan-status").write_text("Error recovery (2/5)")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "error_recovery"
        assert state["badge_color"] == "red"

    def test_project_from_file(self, tmp_path):
        (tmp_path / ".koan-project").write_text("myproject")
        state = agent_state.get_agent_state(tmp_path)
        assert state["project"] == "myproject"

    def test_project_from_status_text(self, tmp_path):
        (tmp_path / ".koan-status").write_text("Run 3/10 — executing mission on koan")
        state = agent_state.get_agent_state(tmp_path)
        assert state["project"] == "koan"

    def test_stale_status(self, tmp_path):
        import os
        status_file = tmp_path / ".koan-status"
        status_file.write_text("Run 3/10 — executing mission")
        old_mtime = time.time() - 600
        os.utime(status_file, (old_mtime, old_mtime))
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "idle"
        assert "stale" in state["label"]

    def test_focus_state(self, tmp_path):
        focus_data = {
            "activated_at": int(time.time()),
            "duration": 18000,
            "reason": "missions",
        }
        (tmp_path / ".koan-focus").write_text(json.dumps(focus_data))
        state = agent_state.get_agent_state(tmp_path)
        assert state["focus"] is not None
        assert state["focus"]["reason"] == "missions"

    def test_stopped_overrides_paused(self, tmp_path):
        (tmp_path / ".koan-stop").write_text("1")
        (tmp_path / ".koan-pause").write_text("quota\n")
        (tmp_path / ".koan-status").write_text("Run 3/10 — executing")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "stopped"

    def test_paused_overrides_running(self, tmp_path):
        (tmp_path / ".koan-pause").write_text("manual\n")
        (tmp_path / ".koan-status").write_text("Run 3/10 — executing")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "paused"

    def test_quota_reset_file_without_pause(self, tmp_path):
        (tmp_path / ".koan-quota-reset").write_text("1")
        state = agent_state.get_agent_state(tmp_path)
        assert state["state"] == "paused"
        assert "quota" in state["label"]
