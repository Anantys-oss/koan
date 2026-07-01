"""Coverage tests for app.run notification + mission-finalization helpers.

Targets behaviours not exercised by test_run.py / test_session_orchestration.py:
- _notify_mission_normal branch selection (failure / tracked-skill / autonomous / titled)
- _notify_stagnation and _notify_stagnation_retry message shape + failure isolation
- _finalize_mission stagnation retry/cap state machine and success counter cleanup
"""

from unittest.mock import MagicMock, patch

import pytest

import app.run as run

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# _notify_mission_normal
# ---------------------------------------------------------------------------

class TestNotifyMissionNormal:
    def test_failure_surfaces_short_form(self):
        with patch.object(run, "_notify") as mock_notify:
            run._notify_mission_normal(
                "/inst", "proj", 1, 60, 1, "do the thing", "",
            )
        msg = mock_notify.call_args[0][1]
        assert msg.startswith("❌ [proj]")
        assert "Failed: do the thing" in msg

    def test_failure_ci_mission_uses_traffic_light(self):
        with patch.object(run, "_notify") as mock_notify:
            run._notify_mission_normal(
                "/inst", "proj", 1, 60, 1, "/ci_check https://x/pull/1", "",
            )
        assert mock_notify.call_args[0][1].startswith("🚦 [proj]")

    def test_tracked_skill_success_uses_provided_url(self):
        with patch.object(run, "_notify") as mock_notify:
            run._notify_mission_normal(
                "/inst", "proj", 1, 60, 0, "/review https://x/pull/9", "https://x/pull/9",
            )
        msg = mock_notify.call_args[0][1]
        assert msg == "✅ [proj] 🔍 Reviewed https://x/pull/9"

    def test_tracked_skill_success_falls_back_to_completion_url(self):
        with patch.object(run, "_notify") as mock_notify, \
             patch.object(run, "_completion_pr_url", return_value="https://x/pull/7"):
            run._notify_mission_normal(
                "/inst", "proj", 1, 60, 0, "/fix something", "",
            )
        assert mock_notify.call_args[0][1] == "✅ [proj] 🐞 Fixed https://x/pull/7"

    def test_plan_success_logged_not_pushed(self):
        """/plan is tracked but the canonical line cannot carry its issue/Jira URL
        or inline body. The runner already emitted that content (not suppressed),
        so the loop logs only — no bare '🧠 Planned' row duplicating it (#2153)."""
        with patch.object(run, "_notify") as mock_notify, \
             patch("app.run_log.log_safe") as mock_log:
            run._notify_mission_normal(
                "/inst", "proj", 1, 60, 0, "/plan add dark mode", "",
            )
        mock_notify.assert_not_called()
        mock_log.assert_called_once()

    def test_autonomous_run_no_title_is_logged_not_pushed(self):
        with patch.object(run, "_notify") as mock_notify, \
             patch("app.run_log.log_safe") as mock_log:
            run._notify_mission_normal("/inst", "proj", 3, 60, 0, "", "")
        mock_notify.assert_not_called()
        mock_log.assert_called_once()

    def test_titled_mission_gets_minimal_success(self):
        with patch.object(run, "_notify") as mock_notify:
            run._notify_mission_normal(
                "/inst", "proj", 1, 60, 0, "improve docs", "",
            )
        assert mock_notify.call_args[0][1] == "✅ [proj] Done: improve docs"


# ---------------------------------------------------------------------------
# _notify_stagnation / _notify_stagnation_retry
# ---------------------------------------------------------------------------

class TestNotifyStagnation:
    def test_includes_project_pattern_and_excerpt(self):
        with patch("app.notify.send_telegram") as mock_send:
            run._notify_stagnation(
                "stuck mission", "proj", pattern_type="repeat",
                pattern_excerpt="loop loop loop",
            )
        msg = mock_send.call_args[0][0]
        assert "[proj]" in msg
        assert "(repeat)" in msg
        assert "Context: loop loop loop" in msg

    def test_no_project_no_pattern_no_excerpt(self):
        with patch("app.notify.send_telegram") as mock_send:
            run._notify_stagnation("stuck mission", "")
        msg = mock_send.call_args[0][0]
        assert "Context:" not in msg
        assert "()" not in msg

    def test_send_failure_is_swallowed(self):
        with patch("app.notify.send_telegram", side_effect=RuntimeError("boom")), \
             patch.object(run, "log") as mock_log:
            run._notify_stagnation("m", "proj")
        assert mock_log.call_args[0][0] == "error"

    def test_retry_message_shows_attempt_count(self):
        with patch("app.notify.send_telegram") as mock_send:
            run._notify_stagnation_retry(
                "stuck", "proj", 2, 3, pattern_type="repeat",
                pattern_excerpt="abc",
            )
        msg = mock_send.call_args[0][0]
        assert "retry 2/3" in msg
        assert "(repeat)" in msg
        assert "Context: abc" in msg

    def test_retry_send_failure_is_swallowed(self):
        with patch("app.notify.send_telegram", side_effect=RuntimeError("x")), \
             patch.object(run, "log") as mock_log:
            run._notify_stagnation_retry("m", "proj", 1, 3)
        assert mock_log.call_args[0][0] == "error"


# ---------------------------------------------------------------------------
# _run_preflight_check
# ---------------------------------------------------------------------------

class TestRunPreflightCheck:
    _PLAN = {
        "project_path": "/p", "project_name": "proj", "mission_title": "do thing",
    }

    def test_quota_ok_proceeds(self):
        with patch("app.preflight.preflight_quota_check", return_value=(True, "")):
            result = run._run_preflight_check(dict(self._PLAN), "/root", "/inst", 5)
        assert result is False

    def test_quota_exhausted_pauses_and_aborts(self):
        with patch("app.preflight.preflight_quota_check", return_value=(False, "limit")), \
             patch.object(run, "_compute_preflight_reset_ts", return_value=(123, "noon")), \
             patch("app.pause_manager.create_pause") as mock_pause, \
             patch.object(run, "_notify") as mock_notify:
            result = run._run_preflight_check(dict(self._PLAN), "/root", "/inst", 5)
        assert result is True
        mock_pause.assert_called_once()
        assert "Pre-flight quota check failed" in mock_notify.call_args[0][1]

    def test_exhausted_autonomous_uses_default_label(self):
        plan = {"project_path": "/p", "project_name": "proj", "mission_title": ""}
        with patch("app.preflight.preflight_quota_check", return_value=(False, "limit")), \
             patch.object(run, "_compute_preflight_reset_ts", return_value=(0, "")), \
             patch("app.pause_manager.create_pause"), \
             patch.object(run, "_notify") as mock_notify:
            run._run_preflight_check(plan, "/root", "/inst", 5)
        assert "autonomous run" in mock_notify.call_args[0][1]

    def test_probe_exception_is_swallowed(self):
        with patch("app.preflight.preflight_quota_check", side_effect=RuntimeError("x")), \
             patch.object(run, "log") as mock_log:
            result = run._run_preflight_check(dict(self._PLAN), "/root", "/inst", 5)
        assert result is False
        assert mock_log.call_args[0][0] == "error"


# ---------------------------------------------------------------------------
# _handle_update_release
# ---------------------------------------------------------------------------

class TestHandleUpdateRelease:
    def _result(self, success=True, changed=True, error=""):
        r = MagicMock()
        r.success, r.changed, r.error = success, changed, error
        r.summary.return_value = "v1.2.3"
        return r

    def test_changed_notifies_and_restarts(self):
        with patch("app.update_manager.checkout_latest_tag", return_value=self._result()), \
             patch("app.restart_manager.request_restart") as mock_restart, \
             patch("app.pause_manager.remove_pause") as mock_remove, \
             patch.object(run, "_notify") as mock_notify:
            assert run._handle_update_release("/root", "/inst", 4) is True
        mock_restart.assert_called_once()
        mock_remove.assert_called_once()
        assert "Release update complete" in mock_notify.call_args[0][1]

    def test_no_change_already_latest(self):
        with patch("app.update_manager.checkout_latest_tag",
                   return_value=self._result(changed=False)), \
             patch("app.restart_manager.request_restart"), \
             patch("app.pause_manager.remove_pause"), \
             patch.object(run, "_notify") as mock_notify:
            run._handle_update_release("/root", "/inst", 4)
        assert "Already on latest" in mock_notify.call_args[0][1]

    def test_failure_restarts_anyway(self):
        with patch("app.update_manager.checkout_latest_tag",
                   return_value=self._result(success=False, error="net")), \
             patch("app.restart_manager.request_restart") as mock_restart, \
             patch("app.pause_manager.remove_pause"), \
             patch.object(run, "_notify") as mock_notify:
            assert run._handle_update_release("/root", "/inst", 4) is True
        mock_restart.assert_called_once()
        assert "Release update failed" in mock_notify.call_args[0][1]


# ---------------------------------------------------------------------------
# _finalize_mission
# ---------------------------------------------------------------------------

@pytest.fixture
def _clean_stagnation_state():
    """Reset the module-level stagnation flags around each test."""
    run._last_mission_stagnated.clear()
    run._stagnation_pattern_type = ""
    run._stagnation_pattern_excerpt = ""
    yield
    run._last_mission_stagnated.clear()
    run._stagnation_pattern_type = ""
    run._stagnation_pattern_excerpt = ""


_STAG_CFG = {"max_retry_on_stagnation": 3, "max_total_retries": 10}


class TestFinalizeMission:
    def test_success_clears_retry_counters(self, _clean_stagnation_state):
        with patch.object(run, "_update_mission_in_file") as mock_update, \
             patch("app.stagnation_monitor.clear_retry_count") as mock_clear, \
             patch("app.mission_history.record_execution") as mock_record:
            run._finalize_mission("/inst", "do thing", "proj", 0)
        mock_clear.assert_called_once_with("/inst", "do thing")
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs["failed"] is False
        mock_record.assert_called_once()

    def test_plain_failure_no_stagnation(self, _clean_stagnation_state):
        with patch.object(run, "_update_mission_in_file") as mock_update, \
             patch("app.mission_history.record_execution"):
            run._finalize_mission("/inst", "do thing", "proj", 1)
        assert mock_update.call_args.kwargs["failed"] is True
        assert mock_update.call_args.kwargs["cause_tag"] == ""

    def test_stagnation_under_cap_requeues(self, _clean_stagnation_state):
        run._last_mission_stagnated.set()
        run._stagnation_pattern_type = "repeat"
        with patch("app.config.get_stagnation_config", return_value=_STAG_CFG), \
             patch("app.stagnation_monitor.get_retry_count", return_value=0), \
             patch("app.stagnation_monitor.get_total_attempts", return_value=1), \
             patch("app.stagnation_monitor.increment_retry_count", return_value=1) as mock_inc, \
             patch.object(run, "_requeue_mission_in_file") as mock_requeue, \
             patch.object(run, "_notify_stagnation_retry") as mock_retry_notify, \
             patch.object(run, "_update_mission_in_file") as mock_update, \
             patch("app.mission_history.record_execution") as mock_record:
            run._finalize_mission("/inst", "stuck", "proj", 1)
        mock_inc.assert_called_once()
        mock_requeue.assert_called_once_with("/inst", "stuck")
        mock_retry_notify.assert_called_once()
        # Requeue path returns before marking Failed.
        mock_update.assert_not_called()
        mock_record.assert_called_once()
        # Flag consumed.
        assert not run._last_mission_stagnated.is_set()

    def test_stagnation_cap_reached_marks_failed_with_tag(self, _clean_stagnation_state):
        run._last_mission_stagnated.set()
        run._stagnation_pattern_type = "repeat"
        with patch("app.config.get_stagnation_config", return_value=_STAG_CFG), \
             patch("app.stagnation_monitor.get_retry_count", return_value=3), \
             patch("app.stagnation_monitor.get_total_attempts", return_value=3), \
             patch.object(run, "_notify_stagnation") as mock_notify, \
             patch.object(run, "_update_mission_in_file") as mock_update, \
             patch("app.mission_history.record_execution"):
            run._finalize_mission("/inst", "stuck", "proj", 1)
        mock_notify.assert_called_once()
        assert mock_update.call_args.kwargs["cause_tag"] == "stagnation:repeat"

    def test_stagnation_total_cap_tag(self, _clean_stagnation_state):
        run._last_mission_stagnated.set()
        run._stagnation_pattern_type = "repeat"
        with patch("app.config.get_stagnation_config", return_value=_STAG_CFG), \
             patch("app.stagnation_monitor.get_retry_count", return_value=0), \
             patch("app.stagnation_monitor.get_total_attempts", return_value=10), \
             patch.object(run, "_notify_stagnation"), \
             patch.object(run, "_update_mission_in_file") as mock_update, \
             patch("app.mission_history.record_execution"):
            run._finalize_mission("/inst", "stuck", "proj", 1)
        tag = mock_update.call_args.kwargs["cause_tag"]
        assert tag.startswith("stagnation:repeat:total_cap(10/10)")
