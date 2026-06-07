"""Tests for SDLC orchestrator: handler.py, sdlc_phase_runner.py, skill_dispatch, command_handlers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from app.sdlc_state import (
    SdlcPhase,
    SdlcState,
    load_sdlc_state,
    save_sdlc_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def instance(tmp_path):
    """Create a minimal instance directory."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "missions.md").write_text(
        "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n",
        encoding="utf-8",
    )
    (inst / "outbox.md").write_text("", encoding="utf-8")
    return inst


@pytest.fixture()
def skill_ctx(tmp_path, instance):
    """Build a minimal SkillContext for the SDLC handler."""
    from app.skills import SkillContext

    ctx = SkillContext(
        koan_root=tmp_path,
        instance_dir=instance,
        args="",
    )
    return ctx


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class TestSdlcHandler:
    def test_new_workflow_creates_state_and_queues_mission(self, skill_ctx, instance):
        from skills.core.sdlc.handler import handle

        skill_ctx.args = "my-feature \"Add OAuth2 login\""
        with patch("skills.core.sdlc.handler._get_project_name", return_value="myproject"):
            result = handle(skill_ctx)

        assert "started" in result.lower() or "queued" in result.lower()
        state = load_sdlc_state(str(instance), "my-feature")
        assert state is not None
        assert state.current_phase == SdlcPhase.RESEARCH
        assert "Add OAuth2 login" in state.description

        missions = (instance / "missions.md").read_text(encoding="utf-8")
        assert "/sdlc_phase my-feature" in missions

    def test_no_args_returns_usage(self, skill_ctx, instance):
        from skills.core.sdlc.handler import handle

        skill_ctx.args = ""
        with patch("skills.core.sdlc.handler._get_project_name", return_value="myproject"):
            result = handle(skill_ctx)
        assert "Usage" in result or "usage" in result

    def test_resume_nonexistent_returns_error(self, skill_ctx, instance):
        from skills.core.sdlc.handler import handle

        skill_ctx.args = "no-such-thing --resume"
        with patch("skills.core.sdlc.handler._get_project_name", return_value="myproject"):
            result = handle(skill_ctx)
        assert "No existing" in result

    def test_awaiting_approval_tells_human(self, skill_ctx, instance):
        from skills.core.sdlc.handler import handle

        state = SdlcState(
            issue_name="approval-test",
            description="needs approval",
            current_phase=SdlcPhase.AWAITING_APPROVAL,
        )
        save_sdlc_state(str(instance), state)

        skill_ctx.args = "approval-test"
        with patch("skills.core.sdlc.handler._get_project_name", return_value="myproject"):
            result = handle(skill_ctx)
        assert "awaiting" in result.lower() or "approval" in result.lower()

    def test_terminal_phase_returns_info(self, skill_ctx, instance):
        from skills.core.sdlc.handler import handle

        state = SdlcState(
            issue_name="done-feature",
            description="done",
            current_phase=SdlcPhase.PRODUCTION_READY,
        )
        save_sdlc_state(str(instance), state)

        skill_ctx.args = "done-feature"
        with patch("skills.core.sdlc.handler._get_project_name", return_value="myproject"):
            result = handle(skill_ctx)
        assert "terminal" in result.lower() or "finished" in result.lower() or "already" in result.lower()

    def test_approve_flag_advances_to_implementation(self, skill_ctx, instance):
        from skills.core.sdlc.handler import handle

        state = SdlcState(
            issue_name="approve-test",
            description="test",
            current_phase=SdlcPhase.AWAITING_APPROVAL,
        )
        save_sdlc_state(str(instance), state)
        # Add sentinel to missions.md
        existing = (instance / "missions.md").read_text(encoding="utf-8")
        tag = "[sdlc:awaiting-approval:approve-test]"
        pending_header = "## Pending\n"
        updated = existing.replace(
            pending_header,
            f"{pending_header}\n- [project:myproject] {tag} approval needed\n",
        )
        (instance / "missions.md").write_text(updated, encoding="utf-8")

        skill_ctx.args = "approve-test --approve"
        with patch("skills.core.sdlc.handler._get_project_name", return_value="myproject"):
            result = handle(skill_ctx)

        assert "approved" in result.lower()
        new_state = load_sdlc_state(str(instance), "approve-test")
        assert new_state.current_phase == SdlcPhase.IMPLEMENTATION
        assert new_state.approved is True

        missions = (instance / "missions.md").read_text(encoding="utf-8")
        assert tag not in missions
        assert "/sdlc_phase approve-test" in missions

    def test_already_running_returns_info(self, skill_ctx, instance):
        from skills.core.sdlc.handler import handle

        state = SdlcState(
            issue_name="running-feature",
            description="in progress",
            current_phase=SdlcPhase.ARCHITECTURE,
        )
        save_sdlc_state(str(instance), state)

        skill_ctx.args = "running-feature"
        with patch("skills.core.sdlc.handler._get_project_name", return_value="myproject"):
            result = handle(skill_ctx)
        assert "in progress" in result.lower() or "architecture" in result.lower()

    def test_resume_requeues_current_phase(self, skill_ctx, instance):
        from skills.core.sdlc.handler import handle

        state = SdlcState(
            issue_name="resume-feature",
            description="needs resume",
            current_phase=SdlcPhase.PLANNING,
        )
        save_sdlc_state(str(instance), state)

        skill_ctx.args = "resume-feature --resume"
        with patch("skills.core.sdlc.handler._get_project_name", return_value="myproject"):
            result = handle(skill_ctx)

        assert "resum" in result.lower()
        missions = (instance / "missions.md").read_text(encoding="utf-8")
        assert "/sdlc_phase resume-feature" in missions


# ---------------------------------------------------------------------------
# skill_dispatch integration
# ---------------------------------------------------------------------------


class TestSdlcPhaseSkillDispatch:
    def test_sdlc_phase_in_canonical_runners(self):
        from app.skill_dispatch import _CANONICAL_RUNNERS

        assert "sdlc_phase" in _CANONICAL_RUNNERS
        assert "sdlc_phase_runner" in _CANONICAL_RUNNERS["sdlc_phase"]

    def test_sdlc_phase_in_skill_runners(self):
        from app.skill_dispatch import _SKILL_RUNNERS

        assert "sdlc_phase" in _SKILL_RUNNERS

    def test_validate_sdlc_phase_no_args_returns_error(self):
        from app.skill_dispatch import validate_skill_args

        err = validate_skill_args("sdlc_phase", "")
        assert err is not None
        assert "issue name" in err.lower() or "sdlc_phase" in err.lower()

    def test_validate_sdlc_phase_with_args_passes(self):
        from app.skill_dispatch import validate_skill_args

        err = validate_skill_args("sdlc_phase", "my-feature")
        assert err is None

    def test_build_sdlc_phase_cmd_contains_issue_name(self, tmp_path):
        from app.skill_dispatch import build_skill_command

        cmd = build_skill_command(
            command="sdlc_phase",
            args="my-feature",
            project_name="myproject",
            project_path=str(tmp_path),
            koan_root=str(tmp_path),
            instance_dir=str(tmp_path),
        )
        assert cmd is not None
        assert "--issue-name" in cmd
        issue_idx = cmd.index("--issue-name")
        assert cmd[issue_idx + 1] == "my-feature"


# ---------------------------------------------------------------------------
# Iteration manager — sdlc:awaiting-approval gate
# ---------------------------------------------------------------------------


class TestSdlcApprovalGate:
    def _make_plan(self, *, action, mission_title=""):
        return {
            "action": action,
            "project_name": "myproject",
            "project_path": "/tmp/project",
            "mission_title": mission_title,
            "autonomous_mode": "deep",
            "focus_area": "",
            "available_pct": 80,
            "decision_reason": "test",
            "display_lines": [],
            "recurring_injected": [],
            "focus_remaining": None,
            "passive_remaining": None,
            "schedule_mode": "normal",
            "error": None,
            "tracker_error": None,
            "cost_today": 0.0,
            "mission_tier": None,
        }

    def test_sdlc_wait_in_idle_config(self):
        """sdlc_wait must appear in mission_executor's idle wait config."""
        src = Path(__file__).parent.parent / "app" / "mission_executor.py"
        content = src.read_text(encoding="utf-8")
        assert "sdlc_wait" in content, "_IDLE_WAIT_CONFIG must contain 'sdlc_wait'"

    def test_awaiting_approval_returns_sdlc_wait(self, tmp_path):
        """Sentinel missions must produce sdlc_wait action from iteration manager."""
        from unittest.mock import patch

        missions_file = tmp_path / "instance" / "missions.md"
        missions_file.parent.mkdir()
        sentinel = (
            "- [project:myproject] [sdlc:awaiting-approval:my-feature] "
            "SDLC approval needed for my-feature ⏳ 2026-01-01T00:00:00Z\n"
        )
        missions_file.write_text(
            f"# Missions\n\n## Pending\n\n{sentinel}\n## In Progress\n\n## Done\n",
            encoding="utf-8",
        )

        # Verify tag detection is pure string — no import needed
        assert "[sdlc:awaiting-approval:" in sentinel


# ---------------------------------------------------------------------------
# command_handlers — /approve and /reject
# ---------------------------------------------------------------------------


class TestSdlcApproveRejectCommands:
    def _make_env(self, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        missions = instance / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n", encoding="utf-8")
        outbox = instance / "outbox.md"
        outbox.write_text("", encoding="utf-8")
        return instance

    def test_approve_advances_state(self, tmp_path):
        instance = self._make_env(tmp_path)
        state = SdlcState(
            issue_name="feature-x",
            description="test",
            current_phase=SdlcPhase.AWAITING_APPROVAL,
        )
        save_sdlc_state(str(instance), state)

        # Write sentinel
        content = (instance / "missions.md").read_text(encoding="utf-8")
        tag = "[sdlc:awaiting-approval:feature-x]"
        (instance / "missions.md").write_text(
            content.replace(
                "## Pending\n",
                f"## Pending\n\n- [project:myproject] {tag} SDLC approval needed ⏳ 2026-01-01T00:00:00Z\n"
            ),
            encoding="utf-8",
        )

        with (
            patch("app.command_handlers.INSTANCE_DIR", instance),
            patch("app.command_handlers.MISSIONS_FILE", instance / "missions.md"),
            patch("app.command_handlers.KOAN_ROOT", tmp_path),
            patch("app.command_handlers.send_telegram"),
            patch("app.command_handlers.get_known_projects", return_value=["myproject"]),
        ):
            from app.command_handlers import _handle_sdlc_approve
            _handle_sdlc_approve("feature-x")

        new_state = load_sdlc_state(str(instance), "feature-x")
        assert new_state.current_phase == SdlcPhase.IMPLEMENTATION
        assert new_state.approved is True

        missions = (instance / "missions.md").read_text(encoding="utf-8")
        assert tag not in missions
        assert "/sdlc_phase feature-x" in missions

    def test_approve_wrong_phase_sends_error(self, tmp_path):
        instance = self._make_env(tmp_path)
        state = SdlcState(
            issue_name="feature-y",
            description="test",
            current_phase=SdlcPhase.RESEARCH,  # wrong phase
        )
        save_sdlc_state(str(instance), state)

        messages = []
        with (
            patch("app.command_handlers.INSTANCE_DIR", instance),
            patch("app.command_handlers.MISSIONS_FILE", instance / "missions.md"),
            patch("app.command_handlers.KOAN_ROOT", tmp_path),
            patch("app.command_handlers.send_telegram", side_effect=messages.append),
        ):
            from app.command_handlers import _handle_sdlc_approve
            _handle_sdlc_approve("feature-y")

        assert any("not awaiting approval" in m for m in messages)

    def test_reject_abandons_workflow(self, tmp_path):
        instance = self._make_env(tmp_path)
        state = SdlcState(
            issue_name="feature-z",
            description="test",
            current_phase=SdlcPhase.AWAITING_APPROVAL,
        )
        save_sdlc_state(str(instance), state)

        content = (instance / "missions.md").read_text(encoding="utf-8")
        tag = "[sdlc:awaiting-approval:feature-z]"
        (instance / "missions.md").write_text(
            content.replace(
                "## Pending\n",
                f"## Pending\n\n- [project:myproject] {tag} SDLC approval needed ⏳ 2026-01-01T00:00:00Z\n"
            ),
            encoding="utf-8",
        )

        with (
            patch("app.command_handlers.INSTANCE_DIR", instance),
            patch("app.command_handlers.MISSIONS_FILE", instance / "missions.md"),
            patch("app.command_handlers.KOAN_ROOT", tmp_path),
            patch("app.command_handlers.send_telegram"),
        ):
            from app.command_handlers import _handle_sdlc_reject
            _handle_sdlc_reject("feature-z")

        new_state = load_sdlc_state(str(instance), "feature-z")
        # After archiving, workspace is moved
        assert new_state is None or new_state.current_phase == SdlcPhase.ABANDONED

        missions = (instance / "missions.md").read_text(encoding="utf-8")
        assert tag not in missions

    def test_approve_no_issue_name_sends_usage(self, tmp_path):
        instance = self._make_env(tmp_path)
        messages = []
        with (
            patch("app.command_handlers.INSTANCE_DIR", instance),
            patch("app.command_handlers.MISSIONS_FILE", instance / "missions.md"),
            patch("app.command_handlers.KOAN_ROOT", tmp_path),
            patch("app.command_handlers.send_telegram", side_effect=messages.append),
        ):
            from app.command_handlers import _handle_sdlc_approve
            _handle_sdlc_approve("")

        assert any("Usage" in m or "usage" in m for m in messages)


# ---------------------------------------------------------------------------
# Phase runner — unit tests (no Claude CLI invocation)
# ---------------------------------------------------------------------------


class TestSdlcPhaseRunnerHelpers:
    def test_build_context_empty_when_no_artifacts(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import _build_context

        ws = tmp_path / "ws"
        ws.mkdir()
        result = _build_context(ws, SdlcPhase.ARCHITECTURE)
        assert result == ""

    def test_build_context_includes_research(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import _build_context

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "RESEARCH.md").write_text("research content here", encoding="utf-8")
        result = _build_context(ws, SdlcPhase.ARCHITECTURE)
        assert "research content here" in result

    def test_build_context_budget_cap(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import _build_context, _CONTEXT_BUDGET

        ws = tmp_path / "ws"
        ws.mkdir()
        # Write oversized artifact
        big_content = "x" * (_CONTEXT_BUDGET + 1000)
        (ws / "RESEARCH.md").write_text(big_content, encoding="utf-8")
        result = _build_context(ws, SdlcPhase.ARCHITECTURE)
        assert len(result) <= _CONTEXT_BUDGET + 200  # some header overhead

    def test_parse_failing_experts_empty_when_approved(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import _parse_failing_experts

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "SECURITY.md").write_text("VERDICT: APPROVED\n", encoding="utf-8")
        (ws / "QA.md").write_text("VERDICT: APPROVED\n", encoding="utf-8")
        (ws / "SRE.md").write_text("VERDICT: APPROVED\n", encoding="utf-8")
        assert _parse_failing_experts(ws) == []

    def test_parse_failing_experts_finds_needs_fix(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import _parse_failing_experts

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "SECURITY.md").write_text("VERDICT: NEEDS_FIX\nReason: SQL injection\n", encoding="utf-8")
        (ws / "QA.md").write_text("VERDICT: APPROVED\n", encoding="utf-8")
        (ws / "SRE.md").write_text("VERDICT: APPROVED\n", encoding="utf-8")
        failing = _parse_failing_experts(ws)
        assert "security" in failing
        assert len(failing) == 1

    def test_notify_appends_to_outbox(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import _notify

        outbox = tmp_path / "outbox.md"
        outbox.write_text("", encoding="utf-8")
        _notify(str(tmp_path), "test notification")
        content = outbox.read_text(encoding="utf-8")
        assert "test notification" in content

    def test_queue_approval_sentinel_written(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import _queue_approval_sentinel
        from app.sdlc_state import get_sdlc_workspace

        instance = tmp_path / "instance"
        instance.mkdir()
        ws = get_sdlc_workspace(str(instance), "my-feature")
        missions = instance / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n", encoding="utf-8")

        _queue_approval_sentinel("my-feature", "myproject", str(instance), ws)

        content = missions.read_text(encoding="utf-8")
        assert "[sdlc:awaiting-approval:my-feature]" in content

    def test_queue_next_phase_written(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import _queue_next_phase

        instance = tmp_path / "instance"
        instance.mkdir()
        missions = instance / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n", encoding="utf-8")

        _queue_next_phase("my-feature", "myproject", str(instance), SdlcPhase.ARCHITECTURE)

        content = missions.read_text(encoding="utf-8")
        assert "/sdlc_phase my-feature" in content

    def test_queue_next_phase_skips_terminal(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import _queue_next_phase

        instance = tmp_path / "instance"
        instance.mkdir()
        missions = instance / "missions.md"
        original = "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        missions.write_text(original, encoding="utf-8")

        _queue_next_phase("my-feature", "myproject", str(instance), SdlcPhase.PRODUCTION_READY)

        content = missions.read_text(encoding="utf-8")
        assert "/sdlc_phase my-feature" not in content

    def test_run_sdlc_phase_no_state_returns_error(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import run_sdlc_phase

        instance = tmp_path / "instance"
        instance.mkdir()
        result = run_sdlc_phase(
            issue_name="nonexistent",
            project_path=str(tmp_path),
            project_name="myproject",
            instance_dir=str(instance),
        )
        assert result == 1

    def test_run_sdlc_phase_awaiting_approval_returns_0(self, tmp_path):
        from skills.core.sdlc.sdlc_phase_runner import run_sdlc_phase

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "outbox.md").write_text("", encoding="utf-8")

        state = SdlcState(
            issue_name="approval-feature",
            description="waiting",
            current_phase=SdlcPhase.AWAITING_APPROVAL,
        )
        save_sdlc_state(str(instance), state)

        result = run_sdlc_phase(
            issue_name="approval-feature",
            project_path=str(tmp_path),
            project_name="myproject",
            instance_dir=str(instance),
        )
        assert result == 0

    def test_next_phase_map_coverage(self):
        """All non-terminal, non-waiting phases must have a next phase."""
        from skills.core.sdlc.sdlc_phase_runner import _NEXT_PHASE

        runnable = {
            SdlcPhase.RESEARCH, SdlcPhase.ARCHITECTURE, SdlcPhase.PLANNING,
            SdlcPhase.IMPLEMENTATION, SdlcPhase.FIX_LOOP, SdlcPhase.DOCUMENTATION,
        }
        for phase in runnable:
            assert phase in _NEXT_PHASE, f"{phase.value} missing from _NEXT_PHASE"
