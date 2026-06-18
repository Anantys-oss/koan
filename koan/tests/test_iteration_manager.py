"""Tests for app.iteration_manager — per-iteration planning."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")

from app.iteration_manager import (
    AutonomousDecision,
    FilterResult,
    _MODE_DOWNGRADE,
    _MODE_RANK,
    _check_focus,
    _check_schedule,
    _decide_autonomous_action,
    _downgrade_if_burning_fast,
    _downgrade_if_unaffordable,
    _get_tier_cost_multiplier,
    _fallback_mission_extract,
    _filter_exploration_projects,
    _get_known_project_names,
    _get_usage_decision,
    _inject_recurring,
    _is_diagnostic_on_cooldown,
    _load_diagnostic_cooldowns,
    _log_selection_audit,
    _make_result,
    _maybe_inject_diagnostic_mission,
    _maybe_warn_burn_rate,
    _pick_mission,
    _read_session_pct_and_reset,
    _refresh_usage,
    _resolve_project_path,
    _save_diagnostic_cooldown,
    _select_diagnostic_type,
    _select_random_exploration_project,
    _should_contemplate,
    plan_iteration,
)
from app.loop_manager import resolve_focus_area


# === Helper fixtures ===


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "journal").mkdir()
    (inst / "memory" / "global").mkdir(parents=True)
    (inst / "memory" / "projects").mkdir(parents=True)
    return inst


@pytest.fixture
def koan_root(tmp_path):
    """Create a KOAN_ROOT directory."""
    root = tmp_path / "koan-root"
    root.mkdir()
    return root


@pytest.fixture
def usage_state(tmp_path):
    """Create a usage state file path."""
    return tmp_path / "usage_state.json"


PROJECTS_STR = "koan:/path/to/koan;backend:/path/to/backend;webapp:/path/to/webapp"
PROJECTS_LIST = [("koan", "/path/to/koan"), ("backend", "/path/to/backend"), ("webapp", "/path/to/webapp")]


# === Tests: _resolve_project_path ===


class TestResolveProjectPath:

    def test_finds_existing_project(self):
        assert _resolve_project_path("koan", PROJECTS_LIST) == ("koan", "/path/to/koan")
        assert _resolve_project_path("backend", PROJECTS_LIST) == ("backend", "/path/to/backend")
        assert _resolve_project_path("webapp", PROJECTS_LIST) == ("webapp", "/path/to/webapp")

    def test_returns_none_for_unknown(self):
        assert _resolve_project_path("unknown", PROJECTS_LIST) is None

    def test_empty_projects_list(self):
        assert _resolve_project_path("koan", []) is None

    def test_single_project(self):
        assert _resolve_project_path("only", [("only", "/single/path")]) == ("only", "/single/path")

    def test_case_insensitive_match(self):
        """Project name matching should be case-insensitive."""
        assert _resolve_project_path("Koan", PROJECTS_LIST) == ("koan", "/path/to/koan")
        assert _resolve_project_path("BACKEND", PROJECTS_LIST) == ("backend", "/path/to/backend")
        assert _resolve_project_path("WebApp", PROJECTS_LIST) == ("webapp", "/path/to/webapp")

    def test_resolves_user_alias(self, monkeypatch):
        """A mission tagged with a user alias resolves to its canonical project."""
        import app.utils as utils
        monkeypatch.setattr(
            utils, "resolve_project_alias",
            lambda n: {"be": "backend", "kn": "koan"}.get(n.lower()),
        )
        assert _resolve_project_path("be", PROJECTS_LIST) == ("backend", "/path/to/backend")
        assert _resolve_project_path("KN", PROJECTS_LIST) == ("koan", "/path/to/koan")

    def test_unknown_alias_still_none(self, monkeypatch):
        """A non-alias, non-project name still resolves to None."""
        import app.utils as utils
        monkeypatch.setattr(utils, "resolve_project_alias", lambda n: None)
        assert _resolve_project_path("ghost", PROJECTS_LIST) is None


class TestResolveProjectPathOrgWide:
    """The org-wide sentinel ([project:all]) resolves to the workspace root."""

    def test_all_resolves_to_workspace_root(self, tmp_path, monkeypatch):
        """`all` with no matching project resolves to <KOAN_ROOT>/workspace."""
        import app.utils as utils
        monkeypatch.setattr(utils, "resolve_project_alias", lambda n: None)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        result = _resolve_project_path("all", PROJECTS_LIST, str(tmp_path))
        assert result == ("all", str(workspace))

    def test_all_case_insensitive(self, tmp_path, monkeypatch):
        """The sentinel is matched case-insensitively."""
        import app.utils as utils
        monkeypatch.setattr(utils, "resolve_project_alias", lambda n: None)
        (tmp_path / "workspace").mkdir()
        result = _resolve_project_path("ALL", PROJECTS_LIST, str(tmp_path))
        assert result == ("all", str(tmp_path / "workspace"))

    def test_all_falls_back_to_env_koan_root(self, tmp_path, monkeypatch):
        """When koan_root is omitted, KOAN_ROOT env is used."""
        import app.utils as utils
        monkeypatch.setattr(utils, "resolve_project_alias", lambda n: None)
        (tmp_path / "workspace").mkdir()
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        result = _resolve_project_path("all", PROJECTS_LIST)
        assert result == ("all", str(tmp_path / "workspace"))

    def test_all_none_when_no_workspace_dir(self, tmp_path, monkeypatch):
        """No workspace/ directory means the sentinel cannot resolve."""
        import app.utils as utils
        monkeypatch.setattr(utils, "resolve_project_alias", lambda n: None)
        # tmp_path has no "workspace" subdirectory
        assert _resolve_project_path("all", PROJECTS_LIST, str(tmp_path)) is None

    def test_real_project_named_all_takes_precedence(self, tmp_path, monkeypatch):
        """A real project literally named 'all' wins over the sentinel."""
        import app.utils as utils
        monkeypatch.setattr(utils, "resolve_project_alias", lambda n: None)
        (tmp_path / "workspace").mkdir()
        projects = [("all", "/path/to/real-all"), ("backend", "/path/to/backend")]
        result = _resolve_project_path("all", projects, str(tmp_path))
        assert result == ("all", "/path/to/real-all")

    def test_named_project_unaffected_by_workspace(self, tmp_path, monkeypatch):
        """A named project still resolves to its own path, not the workspace."""
        import app.utils as utils
        monkeypatch.setattr(utils, "resolve_project_alias", lambda n: None)
        (tmp_path / "workspace").mkdir()
        result = _resolve_project_path("backend", PROJECTS_LIST, str(tmp_path))
        assert result == ("backend", "/path/to/backend")


class TestGetKnownProjectNames:

    def test_extracts_sorted_names(self):
        names = _get_known_project_names(PROJECTS_LIST)
        assert names == ["backend", "koan", "webapp"]

    def test_single_project(self):
        names = _get_known_project_names([("solo", "/path")])
        assert names == ["solo"]

    def test_empty_list(self):
        names = _get_known_project_names([])
        assert names == []


# === Tests: resolve_focus_area ===


class TestResolveFocusArea:

    def test_mission_mode(self):
        assert resolve_focus_area("deep", has_mission=True) == "Execute assigned mission"

    def test_review_mode(self):
        result = resolve_focus_area("review", has_mission=False)
        assert "review" in result.lower() or "READ-ONLY" in result

    def test_implement_mode(self):
        result = resolve_focus_area("implement", has_mission=False)
        assert "implementation" in result.lower() or "implement" in result.lower()

    def test_deep_mode(self):
        result = resolve_focus_area("deep", has_mission=False)
        assert "deep" in result.lower() or "refactoring" in result.lower()

    def test_wait_mode(self):
        result = resolve_focus_area("wait", has_mission=False)
        assert "pause" in result.lower() or "exhausted" in result.lower()

    def test_unknown_mode(self):
        result = resolve_focus_area("unknown", has_mission=False)
        assert "General" in result


# === Tests: _refresh_usage ===


class TestRefreshUsage:

    @patch("app.usage_estimator.cmd_refresh")
    def test_refreshes_on_first_run(self, mock_refresh, tmp_path):
        """Count=0 (first run or after auto-resume) must still refresh.

        Critical for the budget exhaustion fix: after auto-resume, count
        resets to 0 but stale usage.md must be cleared.
        """
        state = tmp_path / "usage_state.json"
        usage_md = tmp_path / "usage.md"
        _refresh_usage(state, usage_md, count=0)
        mock_refresh.assert_called_once_with(state, usage_md)

    @patch("app.usage_estimator.cmd_refresh")
    def test_calls_refresh_after_first_run(self, mock_refresh, tmp_path):
        state = tmp_path / "usage_state.json"
        usage_md = tmp_path / "usage.md"
        _refresh_usage(state, usage_md, count=1)
        mock_refresh.assert_called_once_with(state, usage_md)

    def test_handles_refresh_error_gracefully(self, tmp_path):
        """Errors in refresh don't crash the iteration."""
        with patch("app.usage_estimator.cmd_refresh", side_effect=OSError("boom")):
            # Should not raise
            _refresh_usage(tmp_path / "state", tmp_path / "usage.md", count=1)


class TestReadSessionPctAndReset:
    def test_reads_tokens_and_minutes_until_reset(self, tmp_path):
        from datetime import datetime, timedelta

        state = tmp_path / "usage_state.json"
        state.write_text(json.dumps({
            "session_tokens": 250,
            "session_start": (datetime.now() - timedelta(minutes=30)).isoformat(),
        }))

        with (
            patch("app.usage_estimator._get_limits", return_value=(1000, 10000)),
            patch("app.utils.load_config", return_value={}),
        ):
            pct, minutes, parsed_state = _read_session_pct_and_reset(state)

        assert pct == 25.0
        assert minutes is not None
        assert 0 < minutes <= 270
        assert parsed_state is not None
        assert "session_tokens" in parsed_state

    def test_invalid_json_returns_none_triple(self, tmp_path):
        state = tmp_path / "usage_state.json"
        state.write_text("not-json")

        assert _read_session_pct_and_reset(state) == (None, None, None)

    def test_missing_session_start_returns_pct_without_reset(self, tmp_path):
        state = tmp_path / "usage_state.json"
        state.write_text(json.dumps({"session_tokens": 1500}))

        with (
            patch("app.usage_estimator._get_limits", return_value=(1000, 10000)),
            patch("app.utils.load_config", return_value={}),
        ):
            pct, minutes, parsed_state = _read_session_pct_and_reset(state)
            assert pct == 100.0
            assert minutes is None
            assert parsed_state == {"session_tokens": 1500}

    def test_non_positive_session_limit_returns_none_triple(self, tmp_path):
        state = tmp_path / "usage_state.json"
        state.write_text(json.dumps({"session_tokens": 100}))

        with (
            patch("app.usage_estimator._get_limits", return_value=(0, 10000)),
            patch("app.utils.load_config", return_value={}),
        ):
            assert _read_session_pct_and_reset(state) == (None, None, None)


# === Tests: _downgrade_if_unaffordable ===


class TestDowngradeIfUnaffordable:

    def _make_tracker(self, tmp_path, session_pct, runs):
        """Create a UsageTracker with known session usage."""
        from app.usage_tracker import UsageTracker
        usage_md = tmp_path / "usage.md"
        usage_md.write_text(
            f"Session (5hr) : {session_pct}% (reset in 2h)\n"
            f"Weekly (7 day) : 10% (Resets in 5d)\n"
        )
        return UsageTracker(usage_md, runs)

    def test_no_downgrade_when_affordable(self, tmp_path):
        """Deep mode stays deep when budget is ample."""
        tracker = self._make_tracker(tmp_path, session_pct=20, runs=5)
        assert _downgrade_if_unaffordable(tracker, "deep") == "deep"

    def test_deep_downgrades_to_implement(self, tmp_path):
        """Deep is too expensive but implement fits."""
        # 80% used, 10% safety → 10% remaining
        # 10 runs at 80% → avg cost 8%/run → deep=16% > 10%, implement=8% ≤ 10%
        tracker = self._make_tracker(tmp_path, session_pct=80, runs=10)
        assert _downgrade_if_unaffordable(tracker, "deep") == "implement"

    def test_deep_downgrades_to_review(self, tmp_path):
        """Both deep and implement too expensive, review fits."""
        # 87% used → 3% remaining, 20 runs → avg 4.35%/run
        # deep=8.7%, implement=4.35% > 3%, review=2.175% ≤ 3%
        tracker = self._make_tracker(tmp_path, session_pct=87, runs=20)
        assert _downgrade_if_unaffordable(tracker, "deep") == "review"

    def test_all_unaffordable_falls_to_wait(self, tmp_path):
        """When nothing is affordable, mode becomes wait."""
        # 95% used → -5% remaining (clamped to 0)
        tracker = self._make_tracker(tmp_path, session_pct=95, runs=5)
        assert _downgrade_if_unaffordable(tracker, "deep") == "wait"

    def test_review_stays_review(self, tmp_path):
        """Review mode with enough budget stays review."""
        tracker = self._make_tracker(tmp_path, session_pct=50, runs=10)
        assert _downgrade_if_unaffordable(tracker, "review") == "review"

    def test_wait_passthrough(self, tmp_path):
        """Wait mode is not in downgrade chain — passes through unchanged."""
        tracker = self._make_tracker(tmp_path, session_pct=95, runs=5)
        assert _downgrade_if_unaffordable(tracker, "wait") == "wait"

    def test_mode_downgrade_chain(self):
        """Verify the downgrade chain is complete."""
        assert _MODE_DOWNGRADE == {
            "deep": "implement",
            "implement": "review",
            "review": "wait",
        }

    def test_tier_multiplier_triggers_downgrade(self, tmp_path):
        """A high tier multiplier forces downgrade even when base mode fits."""
        # 50% used → 40% remaining, 10 runs → avg 5%/run
        # implement = 5*1.0 = 5% ≤ 40% (affordable without tier)
        # implement with tier_mult=2.0 = 5*1.0*2.0 = 10% ≤ 40% (still fits)
        # deep = 5*2.0 = 10% ≤ 40% (affordable without tier)
        # deep with tier_mult=2.0 = 5*2.0*2.0 = 20% ≤ 40% (still fits)
        tracker = self._make_tracker(tmp_path, session_pct=50, runs=10)
        assert _downgrade_if_unaffordable(tracker, "deep") == "deep"
        assert _downgrade_if_unaffordable(tracker, "deep", tier_multiplier=2.0) == "deep"

        # 80% used → 10% remaining, 10 runs → avg 8%/run
        # deep = 8*2.0 = 16% > 10% → downgrade
        # deep with tier_mult=1.5 = 8*2.0*1.5 = 24% > 10% → downgrade
        # implement = 8*1.0 = 8% ≤ 10% (fits without tier)
        # implement with tier_mult=1.5 = 8*1.0*1.5 = 12% > 10% → downgrade further
        tracker2 = self._make_tracker(tmp_path, session_pct=80, runs=10)
        assert _downgrade_if_unaffordable(tracker2, "deep") == "implement"
        assert _downgrade_if_unaffordable(tracker2, "deep", tier_multiplier=1.5) == "review"

    def test_tier_multiplier_one_is_noop(self, tmp_path):
        """tier_multiplier=1.0 behaves identically to no multiplier."""
        tracker = self._make_tracker(tmp_path, session_pct=80, runs=10)
        assert (_downgrade_if_unaffordable(tracker, "deep", tier_multiplier=1.0)
                == _downgrade_if_unaffordable(tracker, "deep"))


# === Tests: _get_tier_cost_multiplier ===


class TestGetTierCostMultiplier:

    def test_none_tier_returns_one(self):
        assert _get_tier_cost_multiplier(None) == 1.0

    def test_empty_tier_returns_one(self):
        assert _get_tier_cost_multiplier("") == 1.0

    def test_returns_timeout_multiplier_from_config(self):
        routing = {
            "enabled": True,
            "tiers": {
                "complex": {"model": "opus", "max_turns": 500, "timeout_multiplier": 1.5},
                "critical": {"model": "opus", "max_turns": 500, "timeout_multiplier": 2.0},
            },
        }
        with patch(
            "app.config.get_complexity_routing_config", return_value=routing,
        ):
            assert _get_tier_cost_multiplier("complex", "myproject") == 1.5
            assert _get_tier_cost_multiplier("critical", "myproject") == 2.0

    def test_missing_tier_falls_back_to_one(self):
        routing = {"enabled": True, "tiers": {"trivial": {"timeout_multiplier": 0.5}}}
        with patch(
            "app.config.get_complexity_routing_config", return_value=routing,
        ):
            assert _get_tier_cost_multiplier("unknown_tier", "myproject") == 1.0

    def test_routing_disabled_returns_one(self):
        with patch(
            "app.config.get_complexity_routing_config", return_value=None,
        ):
            assert _get_tier_cost_multiplier("complex", "myproject") == 1.0


# === Tests: _get_usage_decision ===


class TestGetUsageDecision:

    def test_returns_fallback_on_missing_file(self, tmp_path):
        result = _get_usage_decision(tmp_path / "nonexistent.md", 0, PROJECTS_STR)
        assert result["mode"] in ("wait", "review", "implement", "deep")
        assert isinstance(result["available_pct"], int)
        assert isinstance(result["display_lines"], list)

    def test_parses_usage_file(self, tmp_path):
        usage_md = tmp_path / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 2h30m)\n"
            "Weekly (7 day) : 20% (Resets in 5d)\n"
        )
        result = _get_usage_decision(usage_md, 3, PROJECTS_STR)
        assert result["mode"] == "deep"  # 70% available (100-30-safety)
        assert result["available_pct"] >= 50
        assert len(result["display_lines"]) == 2
        assert "Session" in result["display_lines"][0]
        assert "Weekly" in result["display_lines"][1]
        assert result.get("tracker_error") is None

    def test_returns_tracker_for_tier_recheck(self, tmp_path):
        """Decision dict includes tracker for post-tier affordability recheck."""
        usage_md = tmp_path / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 2h30m)\n"
            "Weekly (7 day) : 20% (Resets in 5d)\n"
        )
        result = _get_usage_decision(usage_md, 3, PROJECTS_STR)
        assert result.get("tracker") is not None
        assert hasattr(result["tracker"], "can_afford_run")

    def test_tracker_error_has_no_tracker(self, tmp_path):
        """On tracker error, no tracker is returned."""
        with patch("app.usage_tracker.UsageTracker", side_effect=ValueError("boom")):
            result = _get_usage_decision(tmp_path / "x.md", 0, PROJECTS_STR)
        assert result.get("tracker") is None

    def test_high_usage_returns_wait(self, tmp_path):
        usage_md = tmp_path / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 97% (reset in 1h)\n"
            "Weekly (7 day) : 50% (Resets in 3d)\n"
        )
        result = _get_usage_decision(usage_md, 5, PROJECTS_STR)
        assert result["mode"] == "wait"

    @patch("app.usage_tracker.UsageTracker", side_effect=ValueError("tracker crash"))
    def test_tracker_error_falls_back_to_review_mode(self, mock_tracker, tmp_path):
        """When the usage tracker crashes, fallback to 'review' (read-only) not 'implement'."""
        usage_md = tmp_path / "usage.md"
        usage_md.write_text("Session (5hr) : 50%\n")
        result = _get_usage_decision(usage_md, 3, PROJECTS_STR)
        assert result["mode"] == "review"
        assert result["available_pct"] == 0
        assert "safe fallback" in result["reason"].lower() or "tracker error" in result["reason"].lower()
        assert result["tracker_error"] == "tracker crash"

    @patch("app.usage_tracker.UsageTracker", side_effect=ImportError("missing module"))
    def test_tracker_error_surfaces_import_error(self, mock_tracker, tmp_path):
        """ImportError in tracker also populates tracker_error for operator notification."""
        usage_md = tmp_path / "usage.md"
        usage_md.write_text("Session (5hr) : 50%\n")
        result = _get_usage_decision(usage_md, 3, PROJECTS_STR)
        assert result["mode"] == "review"
        assert result["tracker_error"] == "missing module"

    def test_medium_usage_returns_implement(self, tmp_path):
        usage_md = tmp_path / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 60% (reset in 2h)\n"
            "Weekly (7 day) : 40% (Resets in 4d)\n"
        )
        result = _get_usage_decision(usage_md, 3, PROJECTS_STR)
        assert result["mode"] == "implement"  # 30% available

    def test_can_afford_run_downgrades_mode(self, tmp_path):
        """When decide_mode picks deep but can_afford_run says no, mode is downgraded."""
        usage_md = tmp_path / "usage.md"
        # 50% used, 2 runs → avg cost 25%/run → deep=50% > 40% available → downgrade
        # decide_mode returns "deep" (40% available ≥ 40 threshold)
        # but can_afford_run("deep") = 25*2.0=50 > 40 → downgrade to implement
        # can_afford_run("implement") = 25*1.0=25 ≤ 40 → implement fits
        usage_md.write_text(
            "Session (5hr) : 50% (reset in 3h)\n"
            "Weekly (7 day) : 10% (Resets in 5d)\n"
        )
        result = _get_usage_decision(usage_md, 2, PROJECTS_STR)
        assert result["mode"] == "implement"

    def test_disabled_budget_skips_burn_rate_downgrade(self, tmp_path):
        """When budget_mode is disabled, burn-rate downgrade is skipped entirely."""
        usage_md = tmp_path / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 80% (reset in 1h)\n"
            "Weekly (7 day) : 50% (Resets in 3d)\n"
        )
        with patch("app.usage_tracker._get_budget_mode", return_value="disabled"), \
             patch("app.iteration_manager._downgrade_if_burning_fast") as mock_burn:
            result = _get_usage_decision(usage_md, 5, PROJECTS_STR)
            mock_burn.assert_not_called()
            assert result["mode"] == "deep"


# === Tests: _inject_recurring ===


class TestInjectRecurring:

    def test_returns_empty_when_no_recurring_file(self, instance_dir):
        result = _inject_recurring(instance_dir)
        assert result == []

    @patch("app.recurring.check_and_inject", return_value=["test daily task"])
    def test_returns_injected_descriptions(self, mock_inject, instance_dir):
        (instance_dir / "recurring.json").write_text("{}")
        result = _inject_recurring(instance_dir)
        assert result == ["test daily task"]

    def test_handles_error_gracefully(self, instance_dir):
        (instance_dir / "recurring.json").write_text("{}")
        with patch("app.recurring.check_and_inject", side_effect=OSError("boom")):
            result = _inject_recurring(instance_dir)
            assert result == []


# === Tests: _fallback_mission_extract ===


class TestFallbackMissionExtract:

    def test_no_missions_file(self, tmp_path):
        """Returns (None, None) when no store exists."""
        inst = tmp_path / "instance"
        inst.mkdir()
        project, title = _fallback_mission_extract(inst, PROJECTS_STR, "test context")
        assert project is None
        assert title is None

    def test_no_pending_missions(self, tmp_path):
        """Returns (None, None) when no pending missions."""
        inst = tmp_path / "instance"
        inst.mkdir()
        (inst / "missions.md").write_text("# Missions\n\n## Pending\n\n## Done\n")
        project, title = _fallback_mission_extract(inst, PROJECTS_STR, "test context")
        assert project is None
        assert title is None

    def test_extracts_pending_mission(self, tmp_path):
        """Extracts mission from the store when pending missions exist."""
        inst = tmp_path / "instance"
        inst.mkdir()
        (inst / "missions.md").write_text(
            "# Missions\n\n## Pending\n- [project:koan] Fix bug\n\n## Done\n"
        )
        project, title = _fallback_mission_extract(inst, PROJECTS_STR, "test context")
        assert project == "koan"
        assert title == "Fix bug"

    def test_handles_store_error(self, tmp_path, monkeypatch):
        """Returns (None, None) when MissionStore raises an OSError."""
        inst = tmp_path / "instance"
        inst.mkdir()
        (inst / "missions.md").write_text(
            "# Missions\n\n## Pending\n- [project:koan] Fix bug\n\n## Done\n"
        )
        monkeypatch.setattr(
            "app.mission_store.MissionStore.load",
            lambda *_: (_ for _ in ()).throw(OSError("boom")),
        )
        project, title = _fallback_mission_extract(inst, PROJECTS_STR, "test context")
        assert project is None
        assert title is None


# === Tests: _make_result ===


class TestMakeResult:

    def test_returns_all_keys(self):
        """Result dict contains all required keys."""
        result = _make_result(
            action="mission",
            project_name="koan",
            project_path="/path/to/koan",
            mission_title="Fix the bug",
            autonomous_mode="implement",
            focus_area="code quality",
            available_pct=50,
            decision_reason="medium budget",
            display_lines=["line1"],
            recurring_injected=[],
        )
        expected_keys = {
            "action", "project_name", "project_path", "mission_title",
            "autonomous_mode", "focus_area", "available_pct", "decision_reason",
            "display_lines", "recurring_injected", "focus_remaining",
            "passive_remaining", "schedule_mode", "error", "tracker_error",
            "cost_today", "mission_tier",
        }
        assert set(result.keys()) == expected_keys

    def test_defaults(self):
        """Default values are applied correctly."""
        result = _make_result(
            action="autonomous",
            project_name="koan",
            autonomous_mode="deep",
            available_pct=80,
            decision_reason="high budget",
            display_lines=[],
            recurring_injected=[],
        )
        assert result["project_path"] == ""
        assert result["mission_title"] == ""
        assert result["focus_area"] == ""
        assert result["focus_remaining"] is None
        assert result["schedule_mode"] == "normal"
        assert result["error"] is None

    def test_overrides(self):
        """Custom values override defaults."""
        result = _make_result(
            action="focus_wait",
            project_name="koan",
            project_path="/koan",
            autonomous_mode="implement",
            available_pct=30,
            decision_reason="focus active",
            display_lines=[],
            recurring_injected=[],
            focus_remaining="2h 30m",
            schedule_mode="work",
            error="something went wrong",
        )
        assert result["focus_remaining"] == "2h 30m"
        assert result["schedule_mode"] == "work"
        assert result["error"] == "something went wrong"

    def test_none_project_path_becomes_empty(self):
        """None project_path is coerced to empty string."""
        result = _make_result(
            action="error",
            project_name="unknown",
            project_path=None,
            autonomous_mode="implement",
            available_pct=50,
            decision_reason="test",
            display_lines=[],
            recurring_injected=[],
        )
        assert result["project_path"] == ""


# === Tests: _pick_mission ===


class TestPickMission:

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix the bug")
    def test_returns_project_and_title(self, mock_pick):
        project, title = _pick_mission(Path("/instance"), PROJECTS_STR, 1, "deep", "")
        assert project == "koan"
        assert title == "Fix the bug"

    @patch("app.pick_mission.pick_mission", return_value="")
    def test_returns_none_for_autonomous(self, mock_pick):
        project, title = _pick_mission(Path("/instance"), PROJECTS_STR, 1, "deep", "")
        assert project is None
        assert title is None

    @patch("app.pick_mission.pick_mission", side_effect=OSError("boom"))
    def test_handles_error_gracefully(self, mock_pick):
        project, title = _pick_mission(Path("/instance"), PROJECTS_STR, 1, "deep", "")
        assert project is None
        assert title is None

    @patch("app.pick_mission.pick_mission", return_value="backend:Deploy v2.1")
    def test_parses_colon_in_title(self, mock_pick):
        project, title = _pick_mission(Path("/instance"), PROJECTS_STR, 1, "deep", "")
        assert project == "backend"
        assert title == "Deploy v2.1"


# === Tests: _should_contemplate ===


class TestShouldContemplate:

    @patch("random.randint", return_value=5)
    def test_contemplates_when_roll_succeeds(self, mock_rand):
        assert _should_contemplate("deep", False, 10) is True

    @patch("random.randint", return_value=15)
    def test_skips_when_roll_fails(self, mock_rand):
        assert _should_contemplate("deep", False, 10) is False

    def test_skips_in_wait_mode(self):
        assert _should_contemplate("wait", False, 10) is False

    def test_skips_in_review_mode(self):
        assert _should_contemplate("review", False, 10) is False

    def test_skips_when_focus_active(self):
        assert _should_contemplate("deep", True, 50) is False

    @patch("random.randint", return_value=5)
    def test_schedule_deep_hours_boosts_chance(self, mock_rand):
        """During deep hours, contemplative chance is tripled."""
        from app.schedule_manager import ScheduleState
        schedule = ScheduleState(in_deep_hours=True, in_work_hours=False)
        # base chance 10 → adjusted to 30, roll of 5 < 30 → True
        assert _should_contemplate("deep", False, 10, schedule) is True

    @patch("random.randint", return_value=5)
    def test_schedule_work_hours_zeroes_chance(self, mock_rand):
        """During work hours, contemplative chance is zero."""
        from app.schedule_manager import ScheduleState
        schedule = ScheduleState(in_deep_hours=False, in_work_hours=True)
        # chance becomes 0, roll of 5 >= 0 → False
        assert _should_contemplate("deep", False, 10, schedule) is False

    @patch("random.randint", return_value=5)
    def test_schedule_none_unchanged(self, mock_rand):
        """When schedule_state is None, chance is unchanged."""
        # base chance 10, roll of 5 < 10 → True
        assert _should_contemplate("deep", False, 10, None) is True


# === Tests: _check_focus ===


class TestCheckFocus:

    def test_returns_none_when_module_missing(self):
        """When focus_manager isn't available, returns None gracefully."""
        # _check_focus has try/except — if focus_manager doesn't exist, returns None
        with patch.dict("sys.modules", {"app.focus_manager": None}):
            assert _check_focus("/koan-root") is None

    def test_returns_none_when_not_active(self):
        """When focus_manager's check_focus returns None, so does _check_focus."""
        mock_module = MagicMock()
        mock_module.check_focus.return_value = None
        with patch.dict("sys.modules", {"app.focus_manager": mock_module}):
            assert _check_focus("/koan-root") is None

    def test_returns_state_when_active(self):
        """When focus_manager's check_focus returns a state, _check_focus returns it."""
        mock_state = MagicMock()
        mock_module = MagicMock()
        mock_module.check_focus.return_value = mock_state
        with patch.dict("sys.modules", {"app.focus_manager": mock_module}):
            assert _check_focus("/koan-root") is mock_state


# === Tests: _check_schedule ===


class TestCheckSchedule:

    def test_returns_state_when_configured(self):
        """Returns a ScheduleState when schedule is configured."""
        from app.schedule_manager import ScheduleState
        mock_state = ScheduleState(in_deep_hours=True, in_work_hours=False)
        with patch("app.schedule_manager.get_current_schedule", return_value=mock_state):
            result = _check_schedule()
            assert result is not None
            assert result.mode == "deep"

    def test_returns_normal_state_when_unconfigured(self):
        """Returns state (normal) when schedule has no windows configured."""
        from app.schedule_manager import ScheduleState
        mock_state = ScheduleState(in_deep_hours=False, in_work_hours=False)
        with patch("app.schedule_manager.get_current_schedule", return_value=mock_state):
            result = _check_schedule()
            assert result is not None
            assert result.mode == "normal"

    def test_returns_none_on_import_error(self):
        """Returns None gracefully when module is unavailable."""
        with patch("app.schedule_manager.get_current_schedule", side_effect=ImportError):
            result = _check_schedule()
            assert result is None


# === Tests: plan_iteration (integration) ===


class TestPlanIteration:

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix auth bug")
    @patch("app.usage_estimator.cmd_refresh")
    def test_mission_mode(self, mock_refresh, mock_pick, instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["project_name"] == "koan"
        assert result["project_path"] == "/path/to/koan"
        assert result["mission_title"] == "Fix auth bug"
        assert result["error"] is None
        assert result["tracker_error"] is None

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix auth bug")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.usage_tracker.UsageTracker", side_effect=ValueError("budget DB corrupted"))
    def test_tracker_error_propagates_to_plan_result(self, mock_tracker, mock_refresh, mock_pick,
                                                      instance_dir, koan_root, usage_state):
        """When UsageTracker crashes, tracker_error surfaces in the plan result for notification."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 50%\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["autonomous_mode"] == "review"
        assert result["tracker_error"] == "budget DB corrupted"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule", return_value=None)
    @patch("random.randint", return_value=99)  # No contemplation
    def test_autonomous_mode(self, mock_rand, mock_schedule, mock_focus, mock_refresh, mock_pick,
                             instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "autonomous"
        assert result["mission_title"] == ""
        assert result["autonomous_mode"] == "deep"
        assert result["error"] is None

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule", return_value=None)
    @patch("random.randint", return_value=3)  # Contemplation triggers (< 10%)
    def test_contemplative_mode(self, mock_rand, mock_schedule, mock_focus, mock_refresh, mock_pick,
                                instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "contemplative"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus")
    def test_focus_wait_mode(self, mock_focus, mock_refresh, mock_pick,
                             instance_dir, koan_root, usage_state):
        mock_state = MagicMock()
        mock_state.remaining_display.return_value = "2h remaining"
        mock_focus.return_value = mock_state

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "focus_wait"
        assert result["focus_remaining"] == "2h remaining"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule")
    @patch("random.randint", return_value=99)  # No contemplation
    def test_schedule_wait_mode(self, mock_rand, mock_schedule, mock_focus,
                                mock_refresh, mock_pick,
                                instance_dir, koan_root, usage_state):
        """When work_hours are active and no mission, returns schedule_wait."""
        from app.schedule_manager import ScheduleState
        mock_schedule.return_value = ScheduleState(in_deep_hours=False, in_work_hours=True)

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "schedule_wait"
        assert result["schedule_mode"] == "work"

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix auth bug")
    @patch("app.usage_estimator.cmd_refresh")
    def test_schedule_does_not_block_missions(self, mock_refresh, mock_pick,
                                              instance_dir, koan_root, usage_state):
        """Work hours schedule doesn't block queued missions."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        # Even though work hours would suppress exploration, missions still run
        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["mission_title"] == "Fix auth bug"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    def test_wait_pause_mode(self, mock_focus, mock_refresh, mock_pick,
                             instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 97% (reset in 1h)\nWeekly (7 day) : 50% (Resets in 3d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=5,
            count=4,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "wait_pause"
        assert result["autonomous_mode"] == "wait"

    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    def test_wait_mode_skips_exploration_filter(
        self, mock_focus, mock_refresh, mock_pick, mock_filter,
        instance_dir, koan_root, usage_state,
    ):
        """Wait mode should return wait_pause without calling
        _filter_exploration_projects — avoids wasted gh API calls."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 97% (reset in 1h)\nWeekly (7 day) : 50% (Resets in 3d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=5,
            count=4,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "wait_pause"
        assert result["autonomous_mode"] == "wait"
        # The key assertion: _filter_exploration_projects must NOT be called
        mock_filter.assert_not_called()

    @patch("app.config.is_unlimited_quota", return_value=True)
    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    def test_unlimited_quota_overrides_wait_to_deep(
        self, mock_focus, mock_refresh, mock_pick, mock_unlimited,
        instance_dir, koan_root, usage_state,
    ):
        """unlimited_quota: true must prevent wait_pause even when
        usage.md shows exhausted budget."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 97% (reset in 1h)\n"
            "Weekly (7 day) : 50% (Resets in 3d)\n"
        )

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=5,
            count=42,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] != "wait_pause"
        assert result["autonomous_mode"] != "wait"

    @patch("app.pick_mission.pick_mission", return_value="unknown_project:Fix thing")
    @patch("app.usage_estimator.cmd_refresh")
    def test_unknown_project_error(self, mock_refresh, mock_pick,
                                   instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "error"
        assert "unknown_project" in result["error"]
        assert "backend" in result["error"]
        assert "koan" in result["error"]

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix it")
    @patch("app.usage_estimator.cmd_refresh")
    def test_first_run_always_refreshes_usage(self, mock_refresh, mock_pick,
                                               instance_dir, koan_root, usage_state):
        """Count=0 must still refresh — critical after auto-resume."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=1,
            count=0,
            projects=PROJECTS_LIST,
            last_project="",
            usage_state_path=str(usage_state),
        )

        mock_refresh.assert_called_once()

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix it")
    @patch("app.usage_estimator.cmd_refresh")
    def test_recurring_injection_runs(self, mock_refresh, mock_pick,
                                      instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        # Create a recurring.json to trigger injection
        (instance_dir / "recurring.json").write_text("{}")

        with patch("app.recurring.check_and_inject", return_value=["daily: health check"]) as mock_inject:
            result = plan_iteration(
                instance_dir=str(instance_dir),
                koan_root=str(koan_root),
                run_num=2,
                count=1,
                projects=PROJECTS_LIST,
                last_project="koan",
                usage_state_path=str(usage_state),
            )

        assert result["recurring_injected"] == ["daily: health check"]
        mock_inject.assert_called_once()

    @patch("app.pick_mission.pick_mission", return_value="koan:Task with: colon in title")
    @patch("app.usage_estimator.cmd_refresh")
    def test_mission_title_with_colon(self, mock_refresh, mock_pick,
                                      instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["mission_title"] == "Task with: colon in title"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_focus_checked_once_for_autonomous(self, mock_rand, mock_focus,
                                               mock_refresh, mock_pick,
                                               instance_dir, koan_root, usage_state):
        """Focus is checked exactly once (not twice for contemplate + focus_wait)."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        mock_focus.assert_called_once()


# === Tests: _decide_autonomous_action ===


class TestDecideAutonomousAction:
    """Tests for the extracted autonomous decision priority chain."""

    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=3)  # < 10 → contemplation triggers
    def test_contemplative_wins_first(self, mock_rand, mock_focus):
        """Contemplative has highest priority in the chain."""
        result = _decide_autonomous_action("deep", "/tmp/root", None, 10)
        assert result.action == "contemplative"
        assert result.focus_remaining is None

    @patch("app.iteration_manager._check_focus")
    @patch("random.randint", return_value=99)  # No contemplation
    def test_focus_wait_when_focus_active(self, mock_rand, mock_focus):
        """Focus wait triggers when focus is active and contemplation skipped."""
        mock_state = MagicMock()
        mock_state.remaining_display.return_value = "3h remaining"
        mock_focus.return_value = mock_state

        result = _decide_autonomous_action("deep", "/tmp/root", None, 10)
        assert result.action == "focus_wait"
        assert result.focus_remaining == "3h remaining"

    @patch("app.iteration_manager._check_focus")
    @patch("random.randint", return_value=99)
    def test_focus_remaining_unknown_on_error(self, mock_rand, mock_focus):
        """Focus remaining falls back to 'unknown' on display error."""
        mock_state = MagicMock()
        mock_state.remaining_display.side_effect = ValueError("bad state")
        mock_focus.return_value = mock_state

        result = _decide_autonomous_action("deep", "/tmp/root", None, 10)
        assert result.action == "focus_wait"
        assert result.focus_remaining == "unknown"

    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_schedule_wait_during_work_hours(self, mock_rand, mock_focus):
        """Schedule wait triggers during work hours when no focus."""
        from app.schedule_manager import ScheduleState
        schedule = ScheduleState(in_deep_hours=False, in_work_hours=True)

        result = _decide_autonomous_action("deep", "/tmp/root", schedule, 10)
        assert result.action == "schedule_wait"
        assert result.focus_remaining is None

    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_autonomous_default(self, mock_rand, mock_focus):
        """Autonomous is the default when no higher-priority action matches."""
        result = _decide_autonomous_action("deep", "/tmp/root", None, 10)
        assert result.action == "autonomous"
        assert result.focus_remaining is None

    @patch("app.iteration_manager._check_focus")
    @patch("random.randint", return_value=3)  # Would contemplate if focus inactive
    def test_focus_suppresses_contemplation(self, mock_rand, mock_focus):
        """Focus active suppresses contemplation (_should_contemplate checks focus)."""
        mock_state = MagicMock()
        mock_state.remaining_display.return_value = "1h"
        mock_focus.return_value = mock_state

        result = _decide_autonomous_action("deep", "/tmp/root", None, 10)
        assert result.action == "focus_wait"

    @patch("app.iteration_manager._check_focus")
    @patch("random.randint", return_value=99)
    def test_focus_beats_schedule(self, mock_rand, mock_focus):
        """Focus wait wins over schedule wait when both would trigger."""
        from app.schedule_manager import ScheduleState
        schedule = ScheduleState(in_deep_hours=False, in_work_hours=True)
        mock_state = MagicMock()
        mock_state.remaining_display.return_value = "2h"
        mock_focus.return_value = mock_state

        result = _decide_autonomous_action("deep", "/tmp/root", schedule, 10)
        assert result.action == "focus_wait"

    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_returns_namedtuple(self, mock_rand, mock_focus):
        """Result is an AutonomousDecision namedtuple."""
        result = _decide_autonomous_action("deep", "/tmp/root", None, 10)
        assert isinstance(result, AutonomousDecision)
        assert result == AutonomousDecision(action="autonomous", focus_remaining=None)


# === Tests: Deep hours mode capping ===


class TestDeepHoursModeCap:
    """Tests for deep_hours schedule capping the autonomous mode."""

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule")
    @patch("app.schedule_manager.get_schedule_config", return_value=("0-8", ""))
    @patch("random.randint", return_value=99)  # No contemplation
    def test_deep_capped_outside_deep_hours(
        self, mock_rand, mock_sched_config, mock_schedule, mock_focus,
        mock_refresh, mock_pick, instance_dir, koan_root, usage_state,
    ):
        """Budget 'deep' is capped to 'implement' when outside configured deep_hours."""
        from app.schedule_manager import ScheduleState
        # 11 AM: outside deep_hours 0-8
        mock_schedule.return_value = ScheduleState(in_deep_hours=False, in_work_hours=False)

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 20% (reset in 3h)\nWeekly (7 day) : 10% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "autonomous"
        assert result["autonomous_mode"] == "implement"
        assert "capped from deep" in result["decision_reason"]

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule")
    @patch("app.schedule_manager.get_schedule_config", return_value=("0-8", ""))
    @patch("random.randint", return_value=99)
    def test_deep_allowed_during_deep_hours(
        self, mock_rand, mock_sched_config, mock_schedule, mock_focus,
        mock_refresh, mock_pick, instance_dir, koan_root, usage_state,
    ):
        """Budget 'deep' stays 'deep' when inside configured deep_hours."""
        from app.schedule_manager import ScheduleState
        # 3 AM: inside deep_hours 0-8
        mock_schedule.return_value = ScheduleState(in_deep_hours=True, in_work_hours=False)

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 20% (reset in 3h)\nWeekly (7 day) : 10% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "autonomous"
        assert result["autonomous_mode"] == "deep"
        assert "capped" not in result["decision_reason"]

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule")
    @patch("app.schedule_manager.get_schedule_config", return_value=("", ""))
    @patch("random.randint", return_value=99)
    def test_deep_allowed_when_no_deep_hours_configured(
        self, mock_rand, mock_sched_config, mock_schedule, mock_focus,
        mock_refresh, mock_pick, instance_dir, koan_root, usage_state,
    ):
        """Without deep_hours config, 'deep' budget mode is uncapped (backward compat)."""
        from app.schedule_manager import ScheduleState
        mock_schedule.return_value = ScheduleState(in_deep_hours=False, in_work_hours=False)

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 20% (reset in 3h)\nWeekly (7 day) : 10% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["autonomous_mode"] == "deep"
        assert "capped" not in result["decision_reason"]

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix auth bug")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_schedule")
    @patch("app.schedule_manager.get_schedule_config", return_value=("0-8", ""))
    def test_cap_applies_to_mission_mode_too(
        self, mock_sched_config, mock_schedule,
        mock_refresh, mock_pick, instance_dir, koan_root, usage_state,
    ):
        """Mode cap applies even when a mission is assigned (affects prompt)."""
        from app.schedule_manager import ScheduleState
        mock_schedule.return_value = ScheduleState(in_deep_hours=False, in_work_hours=False)

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 20% (reset in 3h)\nWeekly (7 day) : 10% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["autonomous_mode"] == "implement"
        assert "capped from deep" in result["decision_reason"]

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule")
    @patch("app.schedule_manager.get_schedule_config", return_value=("0-8", ""))
    @patch("random.randint", return_value=99)
    def test_cap_reason_includes_schedule_context(
        self, mock_rand, mock_sched_config, mock_schedule, mock_focus,
        mock_refresh, mock_pick, instance_dir, koan_root, usage_state,
    ):
        """Capped decision_reason explains the schedule constraint."""
        from app.schedule_manager import ScheduleState
        mock_schedule.return_value = ScheduleState(in_deep_hours=False, in_work_hours=False)

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 20% (reset in 3h)\nWeekly (7 day) : 10% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert "outside deep_hours schedule" in result["decision_reason"]

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule")
    @patch("app.schedule_manager.get_schedule_config", return_value=("0-8", ""))
    @patch("random.randint", return_value=99)
    def test_implement_mode_not_capped(
        self, mock_rand, mock_sched_config, mock_schedule, mock_focus,
        mock_refresh, mock_pick, instance_dir, koan_root, usage_state,
    ):
        """Implement mode (from budget) is not affected by schedule cap."""
        from app.schedule_manager import ScheduleState
        mock_schedule.return_value = ScheduleState(in_deep_hours=False, in_work_hours=False)

        usage_md = instance_dir / "usage.md"
        # 55% session + 10% margin → 35% remaining → implement mode
        # Use count=10 so avg cost (5.5%/run) stays affordable for implement
        usage_md.write_text("Session (5hr) : 55% (reset in 3h)\nWeekly (7 day) : 10% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=10,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["autonomous_mode"] == "implement"
        assert "capped" not in result["decision_reason"]

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule")
    @patch("app.schedule_manager.get_schedule_config", return_value=("0-8", ""))
    @patch("random.randint", return_value=99)
    def test_schedule_mode_reflects_cap(
        self, mock_rand, mock_sched_config, mock_schedule, mock_focus,
        mock_refresh, mock_pick, instance_dir, koan_root, usage_state,
    ):
        """The schedule_mode in result reflects the actual schedule state."""
        from app.schedule_manager import ScheduleState
        mock_schedule.return_value = ScheduleState(in_deep_hours=False, in_work_hours=False)

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 20% (reset in 3h)\nWeekly (7 day) : 10% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["schedule_mode"] == "normal"


# === Tests: _filter_exploration_projects ===


class TestFilterExplorationProjects:

    def test_returns_all_when_no_config(self, koan_root):
        """No projects.yaml → all projects returned (exploration enabled by default)."""
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        assert result.projects == PROJECTS_LIST
        assert result.pr_limited == []

    def test_filters_disabled_projects(self, koan_root):
        """Projects with exploration: false are excluded."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
  backend:
    path: /path/to/backend
    exploration: false
  webapp:
    path: /path/to/webapp
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        names = [name for name, _ in result.projects]
        assert "koan" in names
        assert "webapp" in names
        assert "backend" not in names

    def test_returns_empty_when_all_disabled(self, koan_root):
        """All projects disabled → empty list."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    exploration: false
  backend:
    path: /path/to/backend
    exploration: false
  webapp:
    path: /path/to/webapp
    exploration: false
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        assert result.projects == []

    def test_returns_all_when_all_enabled(self, koan_root):
        """All projects enabled → full list returned."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    exploration: true
  backend:
    path: /path/to/backend
  webapp:
    path: /path/to/webapp
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        assert len(result.projects) == 3

    def test_graceful_fallback_on_invalid_yaml(self, koan_root):
        """Invalid YAML → returns all projects (graceful fallback)."""
        (koan_root / "projects.yaml").write_text("not: valid: [yaml")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        assert result.projects == PROJECTS_LIST

    def test_defaults_section_applies(self, koan_root):
        """Defaults section exploration: false applies to all unless overridden."""
        (koan_root / "projects.yaml").write_text("""
defaults:
  exploration: false
projects:
  koan:
    path: /path/to/koan
    exploration: true
  backend:
    path: /path/to/backend
  webapp:
    path: /path/to/webapp
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        names = [name for name, _ in result.projects]
        assert names == ["koan"]


    def test_config_load_error_logs_to_stderr(self, koan_root, capsys):
        """When load_projects_config raises, error is logged to stderr."""
        with patch("app.projects_config.load_projects_config", side_effect=ValueError("bad yaml")):
            result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        assert result.projects == PROJECTS_LIST  # Fail-open
        captured = capsys.readouterr()
        assert "bad yaml" in captured.err


# === Tests: _filter_exploration_projects with focus mode ===


class TestFilterExplorationProjectsFocus:

    def test_filters_focused_projects(self, koan_root):
        """Projects with focus: true are excluded from exploration."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
  backend:
    path: /path/to/backend
    focus: true
  webapp:
    path: /path/to/webapp
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        names = [name for name, _ in result.projects]
        assert "koan" in names
        assert "webapp" in names
        assert "backend" not in names
        assert "backend" in result.focus_gated

    def test_returns_empty_when_all_focused(self, koan_root):
        """All projects focused → empty list."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    focus: true
  backend:
    path: /path/to/backend
    focus: true
  webapp:
    path: /path/to/webapp
    focus: true
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        assert result.projects == []
        assert set(result.focus_gated) == {"koan", "backend", "webapp"}

    def test_focused_projects_included_in_focus_gated_list(self, koan_root):
        """Focus-gated projects are tracked separately in FilterResult."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
  backend:
    path: /path/to/backend
    focus: true
  webapp:
    path: /path/to/webapp
    focus: true
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        assert result.focus_gated == ["backend", "webapp"]

    def test_focus_flag_as_string(self, koan_root):
        """Focus flag accepts string values like 'true' and 'yes'."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    focus: "true"
  backend:
    path: /path/to/backend
    focus: "yes"
  webapp:
    path: /path/to/webapp
    focus: "false"
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        names = [name for name, _ in result.projects]
        assert "webapp" in names
        assert "koan" not in names
        assert "backend" not in names

    def test_defaults_focus_applies(self, koan_root):
        """Defaults section focus: true applies to all unless overridden."""
        (koan_root / "projects.yaml").write_text("""
defaults:
  focus: true
projects:
  koan:
    path: /path/to/koan
    focus: false
  backend:
    path: /path/to/backend
  webapp:
    path: /path/to/webapp
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        names = [name for name, _ in result.projects]
        assert names == ["koan"]
        assert set(result.focus_gated) == {"backend", "webapp"}


# === Tests: _filter_exploration_projects with PR limits ===


class TestFilterExplorationProjectsPrLimit:

    def setup_method(self):
        """Clear the PR count cache between tests."""
        from app.github import _pr_count_cache
        _pr_count_cache.clear()

    @pytest.fixture(autouse=True)
    def _mock_batch(self):
        """Disable batch GraphQL so tests exercise the sequential fallback path."""
        with patch("app.github.batch_count_open_prs", return_value={}):
            yield

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=3)
    def test_under_limit_included(self, mock_count, mock_user, koan_root):
        """Project under PR limit is included."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 10
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        assert result.pr_limited == []

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=10)
    def test_at_limit_excluded(self, mock_count, mock_user, koan_root):
        """Project at PR limit is excluded."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 10
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=15)
    def test_over_limit_excluded(self, mock_count, mock_user, koan_root):
        """Project over PR limit is excluded."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 10
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=-1)
    def test_gh_error_treats_as_pr_limited(self, mock_count, mock_user, koan_root):
        """gh failure returns -1 → conservative, project treated as PR-limited."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert "koan" in result.pr_limited

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs")
    def test_no_github_url_included(self, mock_count, mock_user, koan_root):
        """Project with max_open_prs but no github_url — no gh call made."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    max_open_prs: 5
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        assert result.pr_limited == []
        mock_count.assert_not_called()

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs")
    def test_zero_limit_means_unlimited(self, mock_count, mock_user, koan_root):
        """max_open_prs: 0 means unlimited — no gh call made."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 0
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        mock_count.assert_not_called()

    @patch("app.github.get_gh_username", return_value="")
    @patch("app.github.count_open_prs")
    def test_no_author_skips_pr_checks(self, mock_count, mock_user, koan_root):
        """Empty author → all PR limit checks skipped, projects included."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        mock_count.assert_not_called()

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs")
    def test_mixed_projects(self, mock_count, mock_user, koan_root):
        """Mix of limited and unlimited projects."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
  backend:
    path: /path/to/backend
    github_url: owner/backend
    max_open_prs: 3
  webapp:
    path: /path/to/webapp
""")
        # koan: 4 open (under 5), backend: 3 open (at 3)
        mock_count.side_effect = lambda repo, author, **kw: (
            4 if "koan" in repo else 3
        )
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        names = [name for name, _ in result.projects]
        assert "koan" in names
        assert "webapp" in names  # No limit set
        assert "backend" not in names
        assert result.pr_limited == ["backend"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=10)
    def test_pr_limited_field_populated(self, mock_count, mock_user, koan_root):
        """pr_limited contains names of all PR-limited projects."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
  backend:
    path: /path/to/backend
    github_url: owner/backend
    max_open_prs: 3
  webapp:
    path: /path/to/webapp
    github_url: owner/webapp
    max_open_prs: 2
""")
        result = _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        assert result.projects == []
        assert sorted(result.pr_limited) == ["backend", "koan", "webapp"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=5)
    def test_exploration_false_checked_before_pr_limit(self, mock_count, mock_user, koan_root):
        """exploration: false is checked before PR limit — no gh call for disabled."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    exploration: false
    github_url: owner/koan
    max_open_prs: 10
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.pr_limited == []
        mock_count.assert_not_called()

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=1)
    def test_defaults_section_max_open_prs(self, mock_count, mock_user, koan_root):
        """Defaults section max_open_prs applies to all projects."""
        (koan_root / "projects.yaml").write_text("""
defaults:
  max_open_prs: 1
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs")
    def test_github_urls_checked_for_pr_count(self, mock_count, mock_user, koan_root):
        """PRs are counted across all github_urls, not just primary github_url."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
    github_urls:
    - owner/koan
    - upstream/koan
""")
        # Fork has 1, upstream has 6 → total 7, over limit of 5
        mock_count.side_effect = lambda repo, author, **kw: (
            1 if repo == "owner/koan" else 6
        )
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs")
    def test_github_urls_under_limit_included(self, mock_count, mock_user, koan_root):
        """PRs summed across github_urls under limit → project included."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 10
    github_urls:
    - owner/koan
    - upstream/koan
""")
        # Fork has 2, upstream has 3 → total 5, under limit of 10
        mock_count.side_effect = lambda repo, author, **kw: (
            2 if repo == "owner/koan" else 3
        )
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        assert result.pr_limited == []

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs")
    def test_github_urls_only_no_primary(self, mock_count, mock_user, koan_root):
        """Only github_urls present (no github_url) → still checks PRs."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    max_open_prs: 3
    github_urls:
    - upstream/koan
""")
        mock_count.return_value = 5
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs")
    def test_github_urls_partial_error_uses_valid_counts(self, mock_count, mock_user, koan_root):
        """One URL errors (-1), another returns valid count → uses valid count."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 3
    github_urls:
    - owner/koan
    - upstream/koan
""")
        # Fork errors, upstream has 5
        mock_count.side_effect = lambda repo, author, **kw: (
            -1 if repo == "owner/koan" else 5
        )
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=-1)
    def test_github_urls_all_errors_treats_as_pr_limited(self, mock_count, mock_user, koan_root):
        """All github_urls return errors → conservative, project treated as PR-limited."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 3
    github_urls:
    - owner/koan
    - upstream/koan
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert "koan" in result.pr_limited

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs")
    def test_github_urls_deduped(self, mock_count, mock_user, koan_root):
        """Duplicate URLs in github_url + github_urls are deduplicated."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
    github_urls:
    - owner/koan
""")
        mock_count.return_value = 3
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        # Should only be called once due to dedup (set)
        assert mock_count.call_count == 1


# === Tests: _filter_exploration_projects with batch GraphQL path ===


class TestFilterExplorationProjectsBatchPath:
    """Tests that verify the batch GraphQL path in _filter_exploration_projects."""

    def setup_method(self):
        from app.github import _pr_count_cache
        _pr_count_cache.clear()

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.batch_count_open_prs")
    def test_batch_provides_counts(self, mock_batch, mock_user, koan_root):
        """When batch succeeds, no sequential fallback needed."""
        mock_batch.return_value = {"owner/koan": 3}
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        mock_batch.assert_called_once()

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.batch_count_open_prs")
    def test_batch_at_limit_excludes(self, mock_batch, mock_user, koan_root):
        """Batch reports count at limit → project excluded."""
        mock_batch.return_value = {"owner/koan": 10}
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 10
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.batch_count_open_prs")
    def test_batch_multiple_repos_summed(self, mock_batch, mock_user, koan_root):
        """Batch sums counts across multiple URLs for the same project."""
        mock_batch.return_value = {"owner/koan": 2, "upstream/koan": 4}
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
    github_urls:
    - owner/koan
    - upstream/koan
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        # 2 + 4 = 6, over limit of 5
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.cached_count_open_prs", return_value=8)
    @patch("app.github.batch_count_open_prs", return_value={})
    def test_batch_failure_falls_back_to_sequential(
        self, mock_batch, mock_cached, mock_user, koan_root,
    ):
        """When batch returns empty, falls back to cached_count_open_prs."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]
        mock_cached.assert_called_once_with("owner/koan", "koan-bot")

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.batch_count_open_prs")
    def test_batch_receives_all_repos(self, mock_batch, mock_user, koan_root):
        """Batch is called with deduplicated repos from all projects."""
        mock_batch.return_value = {
            "owner/koan": 1,
            "owner/backend": 2,
        }
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
  backend:
    path: /path/to/backend
    github_url: owner/backend
    max_open_prs: 5
  webapp:
    path: /path/to/webapp
""")
        _filter_exploration_projects(PROJECTS_LIST, str(koan_root))
        # Should have called batch with both repos (webapp has no URL)
        repos_arg = mock_batch.call_args[0][0]
        assert set(repos_arg) == {"owner/koan", "owner/backend"}


# === Tests: _filter_exploration_projects with branch saturation ===


class TestFilterExplorationProjectsBranchSaturation:

    def setup_method(self):
        self._batch_patcher = patch("app.github.batch_count_open_prs", return_value={})
        self._batch_patcher.start()

    def teardown_method(self):
        self._batch_patcher.stop()

    @patch("app.branch_limiter.count_pending_branches", return_value=5)
    @patch("app.github.get_gh_username", return_value="koan-bot")
    def test_under_limit_included(self, mock_user, mock_count, koan_root):
        """Project under branch limit is included."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    max_pending_branches: 10
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        assert result.branch_saturated == []

    @patch("app.branch_limiter.count_pending_branches", return_value=10)
    @patch("app.github.get_gh_username", return_value="koan-bot")
    def test_at_limit_excluded(self, mock_user, mock_count, koan_root):
        """Project at branch limit is excluded."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    max_pending_branches: 10
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.branch_saturated == ["koan"]

    @patch("app.branch_limiter.count_pending_branches", return_value=15)
    @patch("app.github.get_gh_username", return_value="koan-bot")
    def test_over_limit_excluded(self, mock_user, mock_count, koan_root):
        """Project over branch limit is excluded."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    max_pending_branches: 10
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert result.projects == []
        assert result.branch_saturated == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    def test_zero_limit_means_unlimited(self, mock_user, koan_root):
        """max_pending_branches: 0 means unlimited — no branch count check."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    max_pending_branches: 0
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        assert result.branch_saturated == []

    @patch("app.branch_limiter.count_pending_branches", side_effect=Exception("git error"))
    @patch("app.github.get_gh_username", return_value="koan-bot")
    def test_error_allows_project(self, mock_user, mock_count, koan_root):
        """Branch count error → project allowed (fail-open)."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    max_pending_branches: 5
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
        )
        assert len(result.projects) == 1
        assert result.branch_saturated == []


# === Tests: _filter_exploration_projects with deep_hours PR limit relaxation ===


class TestFilterExplorationProjectsDeepHours:

    def setup_method(self):
        """Clear the PR count cache between tests."""
        from app.github import _pr_count_cache
        _pr_count_cache.clear()

    @pytest.fixture(autouse=True)
    def _mock_batch(self):
        """Disable batch GraphQL so tests exercise the sequential fallback path."""
        with patch("app.github.batch_count_open_prs", return_value={}):
            yield

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=10)
    def test_deep_hours_skips_pr_limit(self, mock_count, mock_user, koan_root):
        """During deep_hours, PR limit is relaxed — project included even at limit."""
        from app.schedule_manager import ScheduleState
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
""")
        schedule = ScheduleState(in_deep_hours=True, in_work_hours=False)
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
            schedule_state=schedule,
        )
        assert len(result.projects) == 1
        assert result.pr_limited == []
        # PR count should NOT be called — skipped entirely
        mock_count.assert_not_called()

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=10)
    def test_normal_hours_enforces_pr_limit(self, mock_count, mock_user, koan_root):
        """Outside deep_hours, PR limit is enforced normally."""
        from app.schedule_manager import ScheduleState
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
""")
        schedule = ScheduleState(in_deep_hours=False, in_work_hours=False)
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
            schedule_state=schedule,
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=10)
    def test_no_schedule_state_enforces_pr_limit(self, mock_count, mock_user, koan_root):
        """When schedule_state is None, PR limit is enforced (backward compat)."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    github_url: owner/koan
    max_open_prs: 5
""")
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
            schedule_state=None,
        )
        assert result.projects == []
        assert result.pr_limited == ["koan"]

    @patch("app.github.get_gh_username", return_value="koan-bot")
    @patch("app.github.count_open_prs", return_value=10)
    def test_deep_hours_still_respects_exploration_flag(self, mock_count, mock_user, koan_root):
        """Deep hours relaxes PR limit but NOT the exploration:false flag."""
        from app.schedule_manager import ScheduleState
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    exploration: false
    github_url: owner/koan
    max_open_prs: 5
""")
        schedule = ScheduleState(in_deep_hours=True, in_work_hours=False)
        result = _filter_exploration_projects(
            [("koan", "/path/to/koan")], str(koan_root),
            schedule_state=schedule,
        )
        assert result.projects == []


# === Tests: plan_iteration with exploration flag ===


class TestPlanIterationExploration:

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_exploration_disabled_skips_project(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """When one project is exploration-disabled, another is selected."""
        # Return only webapp (koan and backend filtered out)
        mock_filter.return_value = FilterResult(
            projects=[("webapp", "/path/to/webapp")], pr_limited=[], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "autonomous"
        assert result["project_name"] == "webapp"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    def test_all_disabled_returns_exploration_wait(
        self, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """All projects exploration-disabled → exploration_wait action."""
        mock_filter.return_value = FilterResult(projects=[], pr_limited=[], branch_saturated=[])

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "exploration_wait"
        assert "exploration disabled" in result["decision_reason"]

    @patch("app.pick_mission.pick_mission", return_value="backend:Fix bug")
    @patch("app.usage_estimator.cmd_refresh")
    def test_mission_still_runs_on_disabled_project(
        self, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """Explicit missions execute even on exploration-disabled projects."""
        # Write config with backend exploration disabled
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
  backend:
    path: /path/to/backend
    exploration: false
  webapp:
    path: /path/to/webapp
""")
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["project_name"] == "backend"
        assert result["mission_title"] == "Fix bug"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule", return_value=None)
    @patch("random.randint", return_value=3)  # Would trigger contemplation
    def test_contemplation_uses_filtered_project(
        self, mock_rand, mock_schedule, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """Contemplative sessions use exploration-filtered project list."""
        mock_filter.return_value = FilterResult(
            projects=[("webapp", "/path/to/webapp")], pr_limited=[], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "contemplative"
        assert result["project_name"] == "webapp"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_mixed_projects_selects_enabled(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """With mixed enabled/disabled, only enabled projects are selected."""
        mock_filter.return_value = FilterResult(
            projects=[("koan", "/path/to/koan"), ("webapp", "/path/to/webapp")],
            pr_limited=[], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "autonomous"
        assert result["project_name"] in ("koan", "webapp")
        assert result["project_name"] != "backend"


# === Tests: plan_iteration with PR limit ===


class TestPlanIterationPrLimit:

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_all_pr_limited_returns_pr_limit_wait(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """When all exploration-eligible projects are PR-limited, action is pr_limit_wait."""
        mock_filter.return_value = FilterResult(
            projects=[], pr_limited=["koan", "backend"], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "pr_limit_wait"
        assert "PR limit" in result["decision_reason"]
        assert "koan" in result["decision_reason"]
        assert "backend" in result["decision_reason"]

    @patch("app.pick_mission.pick_mission", return_value="koan:fix a bug")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    def test_missions_bypass_pr_limit(
        self, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """Explicit missions run even when projects are PR-limited."""
        # _filter_exploration_projects is never called for missions
        mock_filter.return_value = FilterResult(projects=[], pr_limited=["koan"], branch_saturated=[])

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["project_name"] == "koan"
        assert result["mission_title"] == "fix a bug"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_mixed_disabled_and_pr_limited_returns_pr_limit(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """Mix of exploration-disabled and PR-limited returns pr_limit_wait."""
        mock_filter.return_value = FilterResult(
            projects=[], pr_limited=["koan"], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "pr_limit_wait"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_some_pr_limited_still_explores_remaining(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """When only some projects are PR-limited, remaining are still explored."""
        mock_filter.return_value = FilterResult(
            projects=[("webapp", "/path/to/webapp")],
            pr_limited=["koan"], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "autonomous"
        assert result["project_name"] == "webapp"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_no_pr_limited_returns_exploration_wait(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """All disabled with no PR-limited → exploration_wait, not pr_limit_wait."""
        mock_filter.return_value = FilterResult(
            projects=[], pr_limited=[], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "exploration_wait"


# === Tests: plan_iteration with branch saturation ===


class TestPlanIterationBranchSaturation:

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_all_branch_saturated_returns_branch_saturated_wait(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """When all projects are branch-saturated, action is branch_saturated_wait."""
        mock_filter.return_value = FilterResult(
            projects=[], pr_limited=[], branch_saturated=["koan", "backend"],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "branch_saturated_wait"
        assert "Branch limit" in result["decision_reason"]

    @patch("app.branch_limiter.count_pending_branches", return_value=3)
    @patch("app.pick_mission.pick_mission", return_value="koan:fix a bug")
    @patch("app.usage_estimator.cmd_refresh")
    def test_mission_allowed_when_under_limit(
        self, mock_refresh, mock_pick, mock_count,
        instance_dir, koan_root, usage_state,
    ):
        """Mission proceeds when project is under branch limit."""
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    max_pending_branches: 10
""")

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["project_name"] == "koan"

    @patch("app.branch_limiter.count_pending_branches", return_value=50)
    @patch("app.pick_mission.pick_mission", return_value="koan:fix a bug")
    @patch("app.usage_estimator.cmd_refresh")
    def test_manual_mission_runs_despite_branch_saturation(
        self, mock_refresh, mock_pick, mock_count,
        instance_dir, koan_root, usage_state,
    ):
        """max_pending_branches is a self-throttle for autonomous exploration
        only — explicit missions in missions.md must run regardless of how
        many open PRs/unmerged branches the project has.

        Regression: previously the picker post-check (commit 5fd621c) and
        the saturated-projects loop (2b753ec) both returned
        branch_saturated_wait for a mission whose project was over the limit.
        A human queuing work should never be blocked by the agent's own
        throttle.
        """
        (koan_root / "projects.yaml").write_text("""
projects:
  koan:
    path: /path/to/koan
    max_pending_branches: 5
""")

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        # 50 >> 5 limit — but mission is manual, so it proceeds.
        assert result["action"] == "mission"
        assert result["project_name"] == "koan"
        assert result["mission_title"] == "fix a bug"


# === Tests: CLI interface ===


class TestCLI:

    def test_cli_outputs_valid_json(self, instance_dir, koan_root, usage_state):
        """CLI produces valid JSON output (autonomous mode when no missions)."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = subprocess.run(
            [
                sys.executable, "-m", "app.iteration_manager",
                "plan-iteration",
                "--instance", str(instance_dir),
                "--koan-root", str(koan_root),
                "--run-num", "2",
                "--count", "1",
                "--projects", PROJECTS_STR,
                "--last-project", "koan",
                "--usage-state", str(usage_state),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "KOAN_ROOT": str(koan_root), "PYTHONPATH": str(Path(__file__).parent.parent)},
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        # With no missions.md, should be autonomous
        assert data["action"] in ("autonomous", "contemplative")
        assert data["autonomous_mode"] in ("wait", "review", "implement", "deep")
        assert isinstance(data["available_pct"], int)
        assert isinstance(data["display_lines"], list)
        assert data["error"] is None

    def test_cli_with_mission(self, instance_dir, koan_root, usage_state):
        """CLI picks up a mission from missions.md (fallback picker, no Claude)."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        # Create missions.md with a pending mission
        missions_md = instance_dir / "missions.md"
        missions_md.write_text(
            "# Missions\n\n## Pending\n\n"
            "- [project:koan] Fix the test CLI\n\n"
            "## In Progress\n\n## Done\n"
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "app.iteration_manager",
                "plan-iteration",
                "--instance", str(instance_dir),
                "--koan-root", str(koan_root),
                "--run-num", "1",
                "--count", "0",
                "--projects", PROJECTS_STR,
                "--last-project", "",
                "--usage-state", str(usage_state),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "KOAN_ROOT": str(koan_root), "PYTHONPATH": str(Path(__file__).parent.parent)},
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["action"] == "mission"
        assert data["project_name"] == "koan"
        assert "Fix the test CLI" in data["mission_title"]


# === Tests: _select_random_exploration_project ===


class TestSelectRandomExplorationProject:

    def test_single_project_always_returned(self):
        """With one project, it's always selected regardless of last_project."""
        projects = [("koan", "/path/to/koan")]
        for _ in range(10):
            name, path = _select_random_exploration_project(projects, "koan")
            assert name == "koan"
            assert path == "/path/to/koan"

    def test_avoids_last_project(self):
        """With multiple projects, avoids repeating the last one."""
        projects = [("koan", "/path/to/koan"), ("backend", "/path/to/backend")]
        for _ in range(20):
            name, _ = _select_random_exploration_project(projects, "koan")
            assert name == "backend"

    def test_no_last_project_selects_any(self):
        """Without a last_project, any project can be selected."""
        projects = [("koan", "/path/koan"), ("backend", "/path/backend"), ("webapp", "/path/webapp")]
        seen = set()
        # Run enough times that random should hit all 3
        for _ in range(100):
            name, _ = _select_random_exploration_project(projects, "")
            seen.add(name)
        assert len(seen) == 3, f"Expected all 3 projects, got: {seen}"

    def test_last_project_not_in_list(self):
        """If last_project isn't in the list, any project can be selected."""
        projects = [("koan", "/path/koan"), ("backend", "/path/backend")]
        seen = set()
        for _ in range(50):
            name, _ = _select_random_exploration_project(projects, "unknown")
            seen.add(name)
        assert len(seen) == 2

    def test_multiple_projects_distributes_fairly(self):
        """With 3+ projects and a last_project, should pick from the remaining ones."""
        projects = [("a", "/a"), ("b", "/b"), ("c", "/c"), ("d", "/d")]
        seen = set()
        for _ in range(100):
            name, _ = _select_random_exploration_project(projects, "a")
            seen.add(name)
            assert name != "a"
        assert seen == {"b", "c", "d"}

    def test_returns_tuple(self):
        """Return value is a (name, path) tuple."""
        projects = [("koan", "/path/to/koan")]
        result = _select_random_exploration_project(projects, "")
        assert isinstance(result, tuple)
        assert len(result) == 2

    @patch("app.config._load_config", return_value={
        "prompt_caching": {"same_project_stickiness_percent": 100}
    })
    def test_cache_stickiness_can_keep_last_project(self, _mock_cfg):
        """When stickiness is enabled, selection may intentionally keep last project."""
        projects = [("koan", "/path/to/koan"), ("backend", "/path/to/backend")]
        for _ in range(10):
            name, _ = _select_random_exploration_project(projects, "koan")
            assert name == "koan"

    @patch("app.config._load_config", return_value={
        "prompt_caching": {"same_project_stickiness_percent": 0}
    })
    def test_cache_stickiness_zero_preserves_anti_repeat(self, _mock_cfg):
        """With stickiness=0, last_project must still be excluded when alternatives exist."""
        projects = [("koan", "/path/to/koan"), ("backend", "/path/to/backend")]
        for _ in range(50):
            name, _ = _select_random_exploration_project(projects, "koan")
            assert name != "koan"
            assert name == "backend"

    def test_weighted_selection_uses_freshness_drift_and_bandit(self, tmp_path):
        projects = [("alpha", "/a"), ("beta", "/b"), ("gamma", "/g")]

        def sample_for_project(_bandit, name):
            return {"alpha": 0.2, "beta": 0.1, "gamma": 0.9}[name]

        with (
            patch("app.session_tracker.load_outcomes", return_value=[]),
            patch("app.session_tracker.get_project_freshness", return_value={
                "alpha": 3,
                "beta": 10,
                "gamma": 4,
            }),
            patch("app.session_tracker.get_project_drift", return_value={
                "alpha": 0,
                "beta": 0,
                "gamma": 15,
            }),
            patch("app.mission_metrics.get_project_success_rates", return_value={
                "alpha": 0.4,
                "beta": 0.9,
                "gamma": 0.6,
            }),
            patch("app.bandit.load_bandit_state", return_value={}),
            patch("app.bandit.thompson_sample", side_effect=sample_for_project),
            patch("app.iteration_manager._log_selection_audit") as mock_audit,
        ):
            selected = _select_random_exploration_project(
                projects, instance_dir=str(tmp_path),
            )

        assert selected == ("gamma", "/g")
        mock_audit.assert_called_once()

    def test_weighted_selection_falls_back_when_bandit_errors(self, tmp_path):
        projects = [("alpha", "/a"), ("beta", "/b")]

        with (
            patch("app.session_tracker.load_outcomes", return_value=[]),
            patch("app.session_tracker.get_project_freshness", return_value={
                "alpha": 1,
                "beta": 9,
            }),
            patch("app.session_tracker.get_project_drift", return_value={}),
            patch("app.mission_metrics.get_project_success_rates", return_value={}),
            patch("app.bandit.load_bandit_state", side_effect=RuntimeError("bad state")),
            patch("random.choices", return_value=[("beta", "/b")]) as mock_choices,
            patch("app.iteration_manager._log_selection_audit"),
        ):
            selected = _select_random_exploration_project(
                projects, instance_dir=str(tmp_path),
            )

        assert selected == ("beta", "/b")
        mock_choices.assert_called_once()


# === Tests: plan_iteration random project selection ===


class TestPlanIterationRandomSelection:

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)  # no contemplation
    def test_autonomous_uses_random_selection(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """Autonomous mode should use random selection, not deterministic index."""
        mock_filter.return_value = FilterResult(
            projects=[("a", "/a"), ("b", "/b"), ("c", "/c")],
            pr_limited=[], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n"
        )

        seen = set()
        for run_num in range(1, 30):
            result = plan_iteration(
                instance_dir=str(instance_dir),
                koan_root=str(koan_root),
                run_num=run_num,
                count=0,
                projects=PROJECTS_LIST,
                last_project="",
                usage_state_path=str(usage_state),
            )
            assert result["action"] == "autonomous"
            seen.add(result["project_name"])

        # Over 29 iterations, random selection should cover multiple projects
        assert len(seen) >= 2, f"Expected multiple projects, got only: {seen}"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)  # no contemplation
    def test_autonomous_avoids_last_project(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick,
        instance_dir, koan_root, usage_state,
    ):
        """Autonomous mode should avoid the last project when multiple are available."""
        mock_filter.return_value = FilterResult(
            projects=[("koan", "/koan"), ("backend", "/backend")],
            pr_limited=[], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n"
        )

        for _ in range(10):
            result = plan_iteration(
                instance_dir=str(instance_dir),
                koan_root=str(koan_root),
                run_num=1,
                count=0,
                projects=PROJECTS_LIST,
                last_project="koan",
                usage_state_path=str(usage_state),
            )
            assert result["action"] == "autonomous"
            assert result["project_name"] == "backend"

    @patch("app.config._load_config", return_value={
        "prompt_caching": {"same_project_stickiness_percent": 100}
    })
    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)  # no contemplation
    def test_autonomous_can_keep_last_project_with_stickiness(
        self, mock_rand, mock_focus, mock_filter, mock_refresh, mock_pick, _mock_cfg,
        instance_dir, koan_root, usage_state,
    ):
        """With stickiness=100, autonomous selection should keep the previous project."""
        mock_filter.return_value = FilterResult(
            projects=[("koan", "/koan"), ("backend", "/backend")],
            pr_limited=[], branch_saturated=[],
        )

        usage_md = instance_dir / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n"
        )

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=1,
            count=0,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )
        assert result["action"] == "autonomous"
        assert result["project_name"] == "koan"


# === Tests: focus mode (config-level permanent focus) ===


class TestFocusModeContemplate:
    """_should_contemplate should return False under focus mode."""

    @patch("random.randint", return_value=0)  # roll would otherwise succeed
    def test_focus_skips_contemplation_with_ample_budget(self, mock_rand):
        assert _should_contemplate(
            "deep", False, 100, focus_mode=True,
        ) is False

    @patch("random.randint", return_value=0)
    def test_focus_skips_contemplation_in_implement(self, mock_rand):
        assert _should_contemplate(
            "implement", False, 100, focus_mode=True,
        ) is False

    @patch("random.randint", return_value=0)
    def test_non_focus_still_contemplates(self, mock_rand):
        """Sanity check: non-focus path still rolls."""
        assert _should_contemplate(
            "deep", False, 100, focus_mode=False,
        ) is True


class TestFocusModePlanIteration:
    """plan_iteration behavior under config-level focus mode."""

    @patch("app.config.is_focus_mode", return_value=True)
    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule", return_value=None)
    def test_no_mission_returns_focus_wait(
        self, mock_schedule, mock_focus, mock_refresh, mock_pick, mock_focus_mode,
        instance_dir, koan_root, usage_state,
    ):
        """Focus mode + no pending mission → focus_wait action."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n"
        )

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "focus_wait"
        assert result["mission_title"] == ""
        # DEEP is capped to implement under focus mode
        assert result["autonomous_mode"] == "implement"
        assert "focus" in result["decision_reason"].lower()

    @patch("app.iteration_manager._filter_exploration_projects")
    @patch("app.config.is_focus_mode", return_value=True)
    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule", return_value=None)
    def test_focus_mode_skips_exploration_filter(
        self, mock_schedule, mock_focus, mock_refresh, mock_pick, mock_focus_mode,
        mock_filter, instance_dir, koan_root, usage_state,
    ):
        """Focus mode should short-circuit before calling exploration filter."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n"
        )

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "focus_wait"
        mock_filter.assert_not_called()

    @patch("app.config.is_focus_mode", return_value=True)
    @patch("app.pick_mission.pick_mission", return_value="koan:Fix auth bug")
    @patch("app.usage_estimator.cmd_refresh")
    def test_queued_mission_still_runs_under_focus(
        self, mock_refresh, mock_pick, mock_focus_mode,
        instance_dir, koan_root, usage_state,
    ):
        """Focus mode never blocks an already-queued mission."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n"
        )

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["mission_title"] == "Fix auth bug"
        assert result["project_name"] == "koan"
        # Mode still capped at implement (ample budget)
        assert result["autonomous_mode"] == "implement"

    @patch("app.config.is_focus_mode", return_value=True)
    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule", return_value=None)
    @patch("random.randint", return_value=0)  # contemplation would normally fire
    def test_focus_mode_blocks_contemplative(
        self, mock_rand, mock_schedule, mock_focus, mock_refresh, mock_pick,
        mock_focus_mode, instance_dir, koan_root, usage_state,
    ):
        """Focus mode prevents contemplative action even on a 0-roll."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n"
        )

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "focus_wait"

    @patch("app.iteration_manager._inject_recurring")
    @patch("app.config.is_focus_mode", return_value=True)
    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule", return_value=None)
    def test_recurring_injection_still_runs(
        self, mock_schedule, mock_focus, mock_refresh, mock_pick, mock_focus_mode,
        mock_recurring, instance_dir, koan_root, usage_state,
    ):
        """Recurring missions are still injected under focus mode."""
        mock_recurring.return_value = ["recurring: Daily housekeeping"]
        usage_md = instance_dir / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n"
        )

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        mock_recurring.assert_called_once()
        assert result["recurring_injected"] == ["recurring: Daily housekeeping"]


class TestFocusModeConfigHelper:
    """Tests for app.config.is_focus_mode()."""

    def test_env_var_true(self, monkeypatch):
        from app.config import is_focus_mode
        monkeypatch.setenv("KOAN_FOCUS", "1")
        assert is_focus_mode() is True

    def test_env_var_false_overrides_config(self, monkeypatch):
        """Env var false should override config.yaml = true."""
        from app.config import is_focus_mode
        monkeypatch.setenv("KOAN_FOCUS", "0")
        with patch("app.config._load_config", return_value={"focus": True}):
            assert is_focus_mode() is False

    def test_config_true_when_env_unset(self, monkeypatch):
        from app.config import is_focus_mode
        monkeypatch.delenv("KOAN_FOCUS", raising=False)
        with patch("app.config._load_config", return_value={"focus": True}):
            assert is_focus_mode() is True

    def test_default_false(self, monkeypatch):
        from app.config import is_focus_mode
        monkeypatch.delenv("KOAN_FOCUS", raising=False)
        with patch("app.config._load_config", return_value={}):
            assert is_focus_mode() is False


class TestFocusModePromptOverride:
    """Tests for prompt_builder focus mode override."""

    def test_github_section_replaced_when_focus(self):
        from app.prompt_builder import _apply_focus_mode_override
        sample = (
            "# Mission\n\n"
            "<!-- BEGIN:github-issue-selection -->\n"
            "## GitHub Issue Selection (IMPLEMENT and DEEP modes)\n\n"
            "When you choose to work on a GitHub issue...\n"
            "more text here\n"
            "<!-- END:github-issue-selection -->\n\n"
            "# Autonomy\n\n"
            "some autonomy content\n"
        )
        with patch("app.prompt_builder._is_focus_mode", return_value=True):
            result = _apply_focus_mode_override(sample)
        assert "Focus Mode" in result
        assert "GitHub Issue Selection" not in result
        assert "# Autonomy" in result

    def test_github_section_intact_when_not_focus(self):
        from app.prompt_builder import _apply_focus_mode_override
        sample = (
            "<!-- BEGIN:github-issue-selection -->\n"
            "## GitHub Issue Selection (IMPLEMENT and DEEP modes)\n\n"
            "content\n"
            "<!-- END:github-issue-selection -->\n\n"
            "# Autonomy\n"
        )
        with patch("app.prompt_builder._is_focus_mode", return_value=False):
            result = _apply_focus_mode_override(sample)
        assert result == sample

    def test_missing_sentinels_returns_prompt_unchanged(self):
        from app.prompt_builder import _apply_focus_mode_override
        sample = "## GitHub Issue Selection\n\ncontent\n\n# Autonomy\n"
        with patch("app.prompt_builder._is_focus_mode", return_value=True):
            result = _apply_focus_mode_override(sample)
        assert result == sample

    def test_misordered_sentinels_returns_prompt_unchanged(self):
        from app.prompt_builder import _apply_focus_mode_override
        sample = (
            "<!-- END:github-issue-selection -->\n"
            "## GitHub Issue Selection\n\ncontent\n"
            "<!-- BEGIN:github-issue-selection -->\n"
        )
        with patch("app.prompt_builder._is_focus_mode", return_value=True):
            result = _apply_focus_mode_override(sample)
        assert result == sample


# === Tests: _log_selection_audit ===


class TestLogSelectionAudit:

    def test_writes_audit_entry(self, instance_dir):
        """Audit log should write a structured entry to .selection-audit.json."""
        candidates = [("koan", "/koan"), ("backend", "/backend")]
        _log_selection_audit(
            str(instance_dir), candidates,
            candidate_weights=[10, 8],
            freshness={"koan": 10, "backend": 8},
            drift={"koan": 5, "backend": 0},
            success_rates={"koan": 0.8, "backend": 0.4},
            ts_samples={"koan": 0.75, "backend": 0.45},
            combined=[7.5, 3.6],
            selected="koan",
        )
        audit_path = instance_dir / ".selection-audit.json"
        assert audit_path.exists()
        entries = json.loads(audit_path.read_text())
        assert len(entries) == 1
        assert entries[0]["selected"] == "koan"
        assert "koan" in entries[0]["candidates"]
        assert "backend" in entries[0]["candidates"]
        koan_entry = entries[0]["candidates"]["koan"]
        assert koan_entry["weight"] == 10
        assert koan_entry["freshness"] == 10
        assert koan_entry["drift"] == 5
        assert koan_entry["success_rate"] == 0.8
        assert koan_entry["ts_sample"] == 0.75

    def test_appends_to_existing(self, instance_dir):
        """Multiple audit entries should accumulate."""
        audit_path = instance_dir / ".selection-audit.json"
        audit_path.write_text('[{"selected": "old", "candidates": {}}]')

        candidates = [("koan", "/koan")]
        _log_selection_audit(
            str(instance_dir), candidates,
            candidate_weights=[10],
            freshness=None, drift=None, success_rates=None,
            ts_samples={"koan": 0.5}, combined=[5.0],
            selected="koan",
        )
        entries = json.loads(audit_path.read_text())
        assert len(entries) == 2
        assert entries[0]["selected"] == "old"
        assert entries[1]["selected"] == "koan"

    def test_caps_at_max_entries(self, instance_dir):
        """Ring buffer should cap at _MAX_SELECTION_AUDIT_ENTRIES."""
        from app.iteration_manager import _MAX_SELECTION_AUDIT_ENTRIES

        audit_path = instance_dir / ".selection-audit.json"
        existing = [{"selected": f"p{i}", "candidates": {}}
                     for i in range(_MAX_SELECTION_AUDIT_ENTRIES)]
        audit_path.write_text(json.dumps(existing))

        candidates = [("new", "/new")]
        _log_selection_audit(
            str(instance_dir), candidates,
            candidate_weights=[10],
            freshness=None, drift=None, success_rates=None,
            ts_samples={"new": 0.5}, combined=[5.0],
            selected="new",
        )
        entries = json.loads(audit_path.read_text())
        assert len(entries) == _MAX_SELECTION_AUDIT_ENTRIES
        assert entries[-1]["selected"] == "new"
        # First entry should have been evicted
        assert entries[0]["selected"] == "p1"

    def test_handles_none_signals_gracefully(self, instance_dir):
        """With all signals None, audit entry still records weights and selection."""
        candidates = [("koan", "/koan")]
        _log_selection_audit(
            str(instance_dir), candidates,
            candidate_weights=[10],
            freshness=None, drift=None, success_rates=None,
            ts_samples={}, combined=[],
            selected="koan",
        )
        audit_path = instance_dir / ".selection-audit.json"
        entries = json.loads(audit_path.read_text())
        koan_data = entries[0]["candidates"]["koan"]
        assert koan_data["freshness"] is None
        assert koan_data["drift"] is None
        assert koan_data["success_rate"] is None


class TestSelectionNoDoubleCountingSuccessRate:
    """Verify that success_rate no longer influences candidate weights.

    The Thompson Sampling bandit encodes productive/non-productive outcomes
    via its Beta distribution.  Adding a success-rate bonus in the weight
    computation double-counts the signal.  These tests confirm the fix.
    """

    @patch("app.iteration_manager._log_selection_audit")
    def test_high_success_rate_does_not_boost_weight(self, mock_audit):
        """success_rate >= 0.7 should NOT add to candidate weights."""
        projects = [("a", "/a"), ("b", "/b")]
        # Mock freshness and Thompson Sampling to be deterministic
        with patch("app.session_tracker.load_outcomes", return_value=[]), \
             patch("app.session_tracker.get_project_freshness",
                   return_value={"a": 10, "b": 10}), \
             patch("app.session_tracker.get_project_drift",
                   return_value={"a": 0, "b": 0}), \
             patch("app.mission_metrics.get_project_success_rates",
                   return_value={"a": 0.9, "b": 0.1}), \
             patch("app.bandit.load_bandit_state") as mock_bandit, \
             patch("app.bandit.thompson_sample", return_value=0.5):

            _select_random_exploration_project(projects, "", "/fake/instance")

            # Check the weights passed to audit — both should be 10
            # (freshness only, no success_rate adjustment)
            if mock_audit.called:
                call_kwargs = mock_audit.call_args
                weights_arg = call_kwargs[0][2]  # candidate_weights positional
                assert weights_arg == [10, 10], (
                    f"Weights should be equal (freshness only), got {weights_arg}"
                )



# === Tests: autonomous health config ===


class TestAutonomousHealthConfig:
    """Tests for get_autonomous_health_config()."""

    @patch("app.config._load_config", return_value={})
    def test_defaults(self, _mock):
        from app.config import get_autonomous_health_config
        cfg = get_autonomous_health_config()
        assert cfg["enabled"] is False
        assert cfg["success_rate_floor"] == 0.25
        assert cfg["staleness_floor"] == 3
        assert cfg["cooldown_days"] == 21
        assert cfg["min_mode"] == "implement"

    @patch("app.config._load_config", return_value={
        "autonomous_health": {
            "enabled": True,
            "success_rate_floor": 0.4,
            "staleness_floor": 5,
            "cooldown_days": 14,
            "min_mode": "deep",
        }
    })
    def test_custom_values(self, _mock):
        from app.config import get_autonomous_health_config
        cfg = get_autonomous_health_config()
        assert cfg["enabled"] is True
        assert cfg["success_rate_floor"] == 0.4
        assert cfg["staleness_floor"] == 5
        assert cfg["cooldown_days"] == 14
        assert cfg["min_mode"] == "deep"

    @patch("app.config._load_config", return_value={
        "autonomous_health": False
    })
    def test_false_disables(self, _mock):
        from app.config import get_autonomous_health_config
        cfg = get_autonomous_health_config()
        assert cfg["enabled"] is False

    @patch("app.config._load_config", return_value={
        "autonomous_health": {
            "staleness_floor": -1,
            "cooldown_days": 0,
            "success_rate_floor": 2.0,
            "min_mode": "invalid",
        }
    })
    def test_clamping_and_validation(self, _mock):
        from app.config import get_autonomous_health_config
        cfg = get_autonomous_health_config()
        assert cfg["staleness_floor"] == 1  # clamped to 1
        assert cfg["cooldown_days"] == 1  # clamped to 1
        assert cfg["success_rate_floor"] == 1.0  # clamped to [0, 1]
        assert cfg["min_mode"] == "implement"  # fallback to default


# === Tests: diagnostic type selection ===


class TestSelectDiagnosticType:
    """Tests for _select_diagnostic_type()."""

    @patch("app.mission_metrics.compute_project_trend", return_value="declining")
    def test_declining_trend_selects_tech_debt(self, _mock_trend):
        result = _select_diagnostic_type("/fake/instance", "koan")
        assert result == "tech_debt"

    @patch("app.mission_metrics.compute_project_trend", return_value="stable")
    @patch("app.mission_metrics.compute_project_metrics", return_value={
        "total_sessions": 10, "empty": 7, "blocked": 1, "productive": 2,
    })
    def test_majority_empty_selects_dead_code(self, _mock_metrics, _mock_trend):
        result = _select_diagnostic_type("/fake/instance", "koan")
        assert result == "dead_code"

    @patch("app.mission_metrics.compute_project_trend", return_value="stable")
    @patch("app.mission_metrics.compute_project_metrics", return_value={
        "total_sessions": 10, "empty": 3, "blocked": 5, "productive": 2,
    })
    def test_blocked_heavy_selects_audit(self, _mock_metrics, _mock_trend):
        result = _select_diagnostic_type("/fake/instance", "koan")
        assert result == "audit"

    @patch("app.mission_metrics.compute_project_trend", side_effect=ImportError)
    def test_import_error_falls_back_to_audit(self, _mock_trend):
        result = _select_diagnostic_type("/fake/instance", "koan")
        assert result == "audit"


# === Tests: diagnostic cooldown helpers ===


class TestDiagnosticCooldown:
    """Tests for cooldown load/save/check helpers."""

    def test_load_empty(self, instance_dir):
        assert _load_diagnostic_cooldowns(str(instance_dir)) == {}

    def test_save_and_load(self, instance_dir):
        _save_diagnostic_cooldown(str(instance_dir), "koan")
        cooldowns = _load_diagnostic_cooldowns(str(instance_dir))
        assert "koan" in cooldowns

    def test_cooldown_active(self, instance_dir):
        _save_diagnostic_cooldown(str(instance_dir), "koan")
        assert _is_diagnostic_on_cooldown(str(instance_dir), "koan", 21) is True

    def test_cooldown_not_active_for_other_project(self, instance_dir):
        _save_diagnostic_cooldown(str(instance_dir), "koan")
        assert _is_diagnostic_on_cooldown(str(instance_dir), "backend", 21) is False

    def test_expired_cooldown(self, instance_dir):
        """Cooldown of 0 days should make any past timestamp expired."""
        from datetime import datetime, timedelta
        cooldown_path = instance_dir / ".diagnostic-cooldowns.json"
        old_ts = (datetime.now() - timedelta(days=2)).isoformat()
        cooldown_path.write_text(json.dumps({"koan": old_ts}))
        assert _is_diagnostic_on_cooldown(str(instance_dir), "koan", 1) is False


# === Tests: _maybe_inject_diagnostic_mission ===


class TestMaybeInjectDiagnosticMission:
    """Tests for the diagnostic injection gate."""

    def _make_missions_file(self, instance_dir):
        """Create a minimal missions.md."""
        missions = instance_dir / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        return missions

    @patch("app.config._load_config", return_value={
        "autonomous_health": {"enabled": False}
    })
    def test_disabled_returns_none(self, _mock_cfg, instance_dir):
        result = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "deep",
        )
        assert result is None

    @patch("app.config._load_config", return_value={
        "autonomous_health": {"enabled": True, "min_mode": "deep"}
    })
    def test_mode_gate_blocks_low_mode(self, _mock_cfg, instance_dir):
        """implement mode should be blocked when min_mode is deep."""
        result = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "implement",
        )
        assert result is None

    @patch("app.config._load_config", return_value={
        "autonomous_health": {"enabled": True, "min_mode": "implement"}
    })
    @patch("app.mission_metrics.get_project_success_rates",
           return_value={"koan": 0.7})
    def test_high_success_rate_skips(self, _mock_rates, _mock_cfg, instance_dir):
        result = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "deep",
        )
        assert result is None

    @patch("app.config._load_config", return_value={
        "autonomous_health": {"enabled": True, "min_mode": "implement"}
    })
    @patch("app.mission_metrics.get_project_success_rates",
           return_value={"koan": 0.5})
    def test_neutral_rate_skips(self, _mock_rates, _mock_cfg, instance_dir):
        """Neutral 0.5 (insufficient data) should not trigger diagnostics."""
        result = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "deep",
        )
        assert result is None

    @patch("app.config._load_config", return_value={
        "autonomous_health": {
            "enabled": True, "min_mode": "implement",
            "success_rate_floor": 0.25, "staleness_floor": 3,
            "cooldown_days": 21,
        }
    })
    @patch("app.mission_metrics.get_project_success_rates",
           return_value={"koan": 0.15})
    @patch("app.session_tracker.get_staleness_score", return_value=1)
    def test_low_staleness_skips(self, _mock_stale, _mock_rates, _mock_cfg,
                                  instance_dir):
        """Staleness below floor should not trigger diagnostics."""
        result = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "deep",
        )
        assert result is None

    @patch("app.config._load_config", return_value={
        "autonomous_health": {
            "enabled": True, "min_mode": "implement",
            "success_rate_floor": 0.25, "staleness_floor": 3,
            "cooldown_days": 21,
        }
    })
    @patch("app.mission_metrics.get_project_success_rates",
           return_value={"koan": 0.15})
    @patch("app.session_tracker.get_staleness_score", return_value=5)
    @patch("app.mission_metrics.compute_project_trend", return_value="declining")
    def test_all_gates_pass_injects_mission(
        self, _mock_trend, _mock_stale, _mock_rates, _mock_cfg, instance_dir, monkeypatch,
    ):
        self._make_missions_file(instance_dir)
        # insert_pending_mission writes to KOAN_ROOT/instance — point it at instance_dir's parent
        monkeypatch.setattr("app.utils.KOAN_ROOT", instance_dir.parent)
        result = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "deep",
        )
        assert result is not None
        assert "[autonomous:health]" in result
        assert "[project:koan]" in result
        assert "/tech_debt" in result

        # Verify mission was written to missions.md
        content = (instance_dir / "missions.md").read_text()
        assert "[autonomous:health]" in content

        # Verify cooldown was set
        assert _is_diagnostic_on_cooldown(str(instance_dir), "koan", 21) is True

    @patch("app.config._load_config", return_value={
        "autonomous_health": {
            "enabled": True, "min_mode": "implement",
            "success_rate_floor": 0.25, "staleness_floor": 3,
            "cooldown_days": 21,
        }
    })
    @patch("app.mission_metrics.get_project_success_rates",
           return_value={"koan": 0.15})
    @patch("app.session_tracker.get_staleness_score", return_value=5)
    @patch("app.mission_metrics.compute_project_trend", return_value="stable")
    @patch("app.mission_metrics.compute_project_metrics", return_value={
        "total_sessions": 10, "empty": 8, "blocked": 1, "productive": 1,
    })
    def test_empty_heavy_injects_dead_code(
        self, _mock_metrics, _mock_trend, _mock_stale, _mock_rates,
        _mock_cfg, instance_dir,
    ):
        self._make_missions_file(instance_dir)
        result = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "deep",
        )
        assert result is not None
        assert "/dead_code" in result

    @patch("app.config._load_config", return_value={
        "autonomous_health": {
            "enabled": True, "min_mode": "implement",
            "success_rate_floor": 0.25, "staleness_floor": 3,
            "cooldown_days": 21,
        }
    })
    @patch("app.mission_metrics.get_project_success_rates",
           return_value={"koan": 0.15})
    @patch("app.session_tracker.get_staleness_score", return_value=5)
    @patch("app.mission_metrics.compute_project_trend", return_value="declining")
    def test_cooldown_prevents_second_injection(
        self, _mock_trend, _mock_stale, _mock_rates, _mock_cfg, instance_dir,
    ):
        self._make_missions_file(instance_dir)

        # First injection should succeed
        result1 = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "deep",
        )
        assert result1 is not None

        # Second injection should be blocked by cooldown
        result2 = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "deep",
        )
        assert result2 is None

    @patch("app.config._load_config", return_value={
        "autonomous_health": {
            "enabled": True, "min_mode": "implement",
            "success_rate_floor": 0.25, "staleness_floor": 3,
            "cooldown_days": 21,
        }
    })
    @patch("app.mission_metrics.get_project_success_rates",
           return_value={"koan": 0.15})
    @patch("app.session_tracker.get_staleness_score", return_value=5)
    @patch("app.mission_metrics.compute_project_trend", return_value="declining")
    def test_different_project_not_blocked_by_cooldown(
        self, _mock_trend, _mock_stale, _mock_rates, _mock_cfg, instance_dir,
    ):
        """Cooldown for project A should not block project B."""
        self._make_missions_file(instance_dir)
        _save_diagnostic_cooldown(str(instance_dir), "backend")

        result = _maybe_inject_diagnostic_mission(
            "koan", str(instance_dir), "deep",
        )
        assert result is not None
        assert "[project:koan]" in result


# === Tests: _MODE_RANK ===


class TestModeRank:
    """Validate the mode rank hierarchy."""

    def test_hierarchy(self):
        assert _MODE_RANK["wait"] < _MODE_RANK["review"]
        assert _MODE_RANK["review"] < _MODE_RANK["implement"]
        assert _MODE_RANK["implement"] < _MODE_RANK["deep"]


# === Tests: _downgrade_if_burning_fast ===


class TestDowngradeIfBurningFast:

    def test_wait_mode_not_downgraded(self):
        mode, prev = _downgrade_if_burning_fast(Path("/tmp"), 50.0, "wait")
        assert mode == "wait"
        assert prev is None

    def test_unknown_mode_not_downgraded(self):
        mode, prev = _downgrade_if_burning_fast(Path("/tmp"), 50.0, "unknown")
        assert mode == "unknown"
        assert prev is None

    @patch("app.burn_rate.BurnRateSnapshot", side_effect=ImportError)
    def test_import_error_returns_unchanged(self, _mock):
        mode, prev = _downgrade_if_burning_fast(Path("/tmp"), 50.0, "deep")
        assert mode == "deep"
        assert prev is None

    @patch("app.burn_rate.BurnRateSnapshot")
    def test_no_downgrade_when_tte_above_threshold(self, mock_snap_cls):
        mock_snap = MagicMock()
        mock_snap.time_to_exhaustion.return_value = 120.0
        mock_snap_cls.return_value = mock_snap
        mode, prev = _downgrade_if_burning_fast(Path("/tmp"), 50.0, "deep")
        assert mode == "deep"
        assert prev is None

    @patch("app.burn_rate.BurnRateSnapshot")
    def test_no_downgrade_when_tte_is_none(self, mock_snap_cls):
        mock_snap = MagicMock()
        mock_snap.time_to_exhaustion.return_value = None
        mock_snap_cls.return_value = mock_snap
        mode, prev = _downgrade_if_burning_fast(Path("/tmp"), 50.0, "deep")
        assert mode == "deep"
        assert prev is None

    @patch("app.burn_rate.BurnRateSnapshot")
    def test_downgrade_deep_to_implement(self, mock_snap_cls):
        mock_snap = MagicMock()
        mock_snap.time_to_exhaustion.return_value = 10.0
        mock_snap_cls.return_value = mock_snap
        mode, prev = _downgrade_if_burning_fast(Path("/tmp"), 50.0, "deep")
        assert mode == _MODE_DOWNGRADE["deep"]
        assert prev == "deep"

    @patch("app.burn_rate.BurnRateSnapshot")
    def test_downgrade_implement_to_review(self, mock_snap_cls):
        mock_snap = MagicMock()
        mock_snap.time_to_exhaustion.return_value = 5.0
        mock_snap_cls.return_value = mock_snap
        mode, prev = _downgrade_if_burning_fast(Path("/tmp"), 80.0, "implement")
        assert mode == _MODE_DOWNGRADE["implement"]
        assert prev == "implement"


# === Tests: _maybe_warn_burn_rate ===


class TestMaybeWarnBurnRate:

    def test_no_warning_when_no_usage_state(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        usage = tmp_path / "usage_state.json"
        _maybe_warn_burn_rate(inst, usage)
        outbox = inst / "outbox.md"
        assert not outbox.exists()

    @patch("app.iteration_manager._read_session_pct_and_reset", return_value=(None, None, None))
    def test_no_warning_when_session_pct_none(self, _mock, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        usage = tmp_path / "usage_state.json"
        usage.write_text("{}")
        _maybe_warn_burn_rate(inst, usage)
        assert not (inst / "outbox.md").exists()

    @patch("app.utils.append_to_outbox")
    @patch("app.burn_rate.mark_warned")
    @patch("app.burn_rate.BurnRateSnapshot")
    @patch("app.iteration_manager._read_session_pct_and_reset", return_value=(60.0, 300.0, {}))
    def test_fires_warning_when_tte_below_threshold(self, _pct, mock_snap_cls,
                                                      mock_mark, mock_outbox,
                                                      tmp_path):
        mock_snap = MagicMock()
        mock_snap.last_warned_at = None
        mock_snap.time_to_exhaustion.return_value = 20.0
        mock_snap.burn_rate_pct_per_minute.return_value = 0.5
        mock_snap_cls.return_value = mock_snap
        inst = tmp_path / "inst"
        inst.mkdir()
        _maybe_warn_burn_rate(inst, tmp_path / "usage.json")
        mock_outbox.assert_called_once()
        msg = mock_outbox.call_args[0][1]
        assert "Burn-rate alert" in msg
        assert "30.0%/h" in msg
        mock_mark.assert_called_once_with(inst)

    @patch("app.utils.append_to_outbox")
    @patch("app.burn_rate.BurnRateSnapshot")
    @patch("app.iteration_manager._read_session_pct_and_reset", return_value=(60.0, 300.0, {}))
    def test_no_warning_when_tte_above_threshold(self, _pct, mock_snap_cls,
                                                   mock_outbox, tmp_path):
        mock_snap = MagicMock()
        mock_snap.last_warned_at = None
        mock_snap.time_to_exhaustion.return_value = 120.0
        mock_snap_cls.return_value = mock_snap
        inst = tmp_path / "inst"
        inst.mkdir()
        _maybe_warn_burn_rate(inst, tmp_path / "usage.json")
        mock_outbox.assert_not_called()

    @patch("app.utils.append_to_outbox")
    @patch("app.burn_rate.BurnRateSnapshot")
    @patch("app.iteration_manager._read_session_pct_and_reset", return_value=(60.0, 60.0, {}))
    def test_no_warning_when_reset_imminent(self, _pct, mock_snap_cls,
                                              mock_outbox, tmp_path):
        mock_snap = MagicMock()
        mock_snap.last_warned_at = None
        mock_snap.time_to_exhaustion.return_value = 20.0
        mock_snap_cls.return_value = mock_snap
        inst = tmp_path / "inst"
        inst.mkdir()
        _maybe_warn_burn_rate(inst, tmp_path / "usage.json")
        mock_outbox.assert_not_called()

    @patch("app.utils.append_to_outbox")
    @patch("app.burn_rate.BurnRateSnapshot")
    @patch("app.iteration_manager._read_session_pct_and_reset")
    def test_no_duplicate_warning_when_already_warned(self, mock_pct, mock_snap_cls,
                                                        mock_outbox, tmp_path):
        from datetime import datetime, timezone, timedelta
        session_start = datetime.now(timezone.utc) - timedelta(minutes=30)
        warned_at = session_start + timedelta(minutes=10)
        mock_pct.return_value = (60.0, 300.0, {"session_start": session_start.isoformat()})
        mock_snap = MagicMock()
        mock_snap.last_warned_at = warned_at
        mock_snap.time_to_exhaustion.return_value = 20.0
        mock_snap_cls.return_value = mock_snap
        inst = tmp_path / "inst"
        inst.mkdir()
        _maybe_warn_burn_rate(inst, tmp_path / "usage.json")
        mock_outbox.assert_not_called()

    @patch("app.burn_rate.clear_warning")
    @patch("app.utils.append_to_outbox")
    @patch("app.burn_rate.mark_warned")
    @patch("app.burn_rate.BurnRateSnapshot")
    @patch("app.iteration_manager._read_session_pct_and_reset")
    def test_clears_stale_warning_from_previous_session(self, mock_pct, mock_snap_cls,
                                                          mock_mark, mock_outbox,
                                                          mock_clear, tmp_path):
        from datetime import datetime, timezone, timedelta
        old_warn = datetime.now(timezone.utc) - timedelta(hours=5)
        new_session = datetime.now(timezone.utc) - timedelta(minutes=10)
        mock_pct.return_value = (60.0, 300.0, {"session_start": new_session.isoformat()})
        mock_snap = MagicMock()
        mock_snap.last_warned_at = old_warn
        mock_snap.time_to_exhaustion.return_value = 15.0
        mock_snap.burn_rate_pct_per_minute.return_value = 0.8
        mock_snap_cls.return_value = mock_snap
        inst = tmp_path / "inst"
        inst.mkdir()
        _maybe_warn_burn_rate(inst, tmp_path / "usage.json")
        mock_clear.assert_called_once_with(inst)
        mock_outbox.assert_called_once()
        mock_mark.assert_called_once()

    @patch("app.burn_rate.clear_warning")
    @patch("app.utils.append_to_outbox")
    @patch("app.burn_rate.mark_warned")
    @patch("app.burn_rate.BurnRateSnapshot")
    @patch("app.iteration_manager._read_session_pct_and_reset")
    def test_uses_state_from_first_read_not_file(self, mock_pct, mock_snap_cls,
                                                   mock_mark, mock_outbox,
                                                   mock_clear, tmp_path):
        """TOCTOU fix: session_start from _read_session_pct_and_reset state dict
        must be used instead of re-reading the file. Write a stale session_start
        to the file; the returned state carries a newer session_start that should
        trigger the stale-warning clear.
        """
        from datetime import datetime, timezone, timedelta
        old_warn = datetime.now(timezone.utc) - timedelta(hours=5)
        new_session = datetime.now(timezone.utc) - timedelta(minutes=10)
        # state dict (from first read) carries new_session — the stale warning should clear
        mock_pct.return_value = (60.0, 300.0, {"session_start": new_session.isoformat()})
        mock_snap = MagicMock()
        mock_snap.last_warned_at = old_warn
        mock_snap.time_to_exhaustion.return_value = 15.0
        mock_snap.burn_rate_pct_per_minute.return_value = 0.8
        mock_snap_cls.return_value = mock_snap
        inst = tmp_path / "inst"
        inst.mkdir()
        # File carries an OLD session_start — if the code re-reads the file it
        # would get the old start and fail to clear the stale warning.
        usage = tmp_path / "usage.json"
        old_session = datetime.now(timezone.utc) - timedelta(hours=6)
        usage.write_text(json.dumps({"session_start": old_session.isoformat()}))
        _maybe_warn_burn_rate(inst, usage)
        # stale warning from old_warn (5h ago) should be cleared because state
        # says session started 10 min ago (old_warn < new_session)
        mock_clear.assert_called_once_with(inst)
        mock_outbox.assert_called_once()

    @patch("app.utils.append_to_outbox", side_effect=OSError("write failed"))
    @patch("app.burn_rate.mark_warned")
    @patch("app.burn_rate.BurnRateSnapshot")
    @patch("app.iteration_manager._read_session_pct_and_reset", return_value=(60.0, 300.0, {}))
    def test_outbox_write_failure_does_not_mark_warned(self, _pct, mock_snap_cls,
                                                         mock_mark, _outbox,
                                                         tmp_path):
        mock_snap = MagicMock()
        mock_snap.last_warned_at = None
        mock_snap.time_to_exhaustion.return_value = 10.0
        mock_snap.burn_rate_pct_per_minute.return_value = 1.0
        mock_snap_cls.return_value = mock_snap
        inst = tmp_path / "inst"
        inst.mkdir()
        _maybe_warn_burn_rate(inst, tmp_path / "usage.json")
        mock_mark.assert_not_called()

    @patch("app.config.is_unlimited_quota", return_value=True)
    @patch("app.utils.append_to_outbox")
    @patch("app.burn_rate.BurnRateSnapshot")
    @patch("app.iteration_manager._read_session_pct_and_reset", return_value=(60.0, 300.0, {}))
    def test_no_warning_when_unlimited_quota(self, _pct, mock_snap_cls,
                                             mock_outbox, _unlimited,
                                             tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        _maybe_warn_burn_rate(inst, tmp_path / "usage.json")
        mock_outbox.assert_not_called()
        mock_snap_cls.assert_not_called()


class TestDowngradeIfBurningFastUnlimitedQuota:

    @patch("app.config.is_unlimited_quota", return_value=True)
    @patch("app.burn_rate.BurnRateSnapshot")
    def test_no_downgrade_when_unlimited_quota(self, mock_snap_cls, _unlimited):
        mode, prev = _downgrade_if_burning_fast(Path("/tmp"), 50.0, "deep")
        assert mode == "deep"
        assert prev is None
        mock_snap_cls.assert_not_called()


class TestBudgetModeFallbackUnlimitedQuota:

    @patch("app.config.is_unlimited_quota", return_value=True)
    @patch("app.usage_tracker._get_budget_mode", side_effect=ValueError("corrupt"))
    @patch("app.iteration_manager._maybe_warn_burn_rate")
    @patch("app.pick_mission.pick_mission", return_value="koan:Fix bug")
    @patch("app.usage_estimator.cmd_refresh")
    def test_budget_mode_falls_back_to_disabled_when_unlimited(
        self, mock_refresh, mock_pick, mock_warn_burn, _mock_budget, _unlimited,
        instance_dir, koan_root, usage_state,
    ):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\n")
        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects=PROJECTS_LIST,
            last_project="koan",
            usage_state_path=str(usage_state),
        )
        assert result["action"] == "mission"
        mock_warn_burn.assert_not_called()
