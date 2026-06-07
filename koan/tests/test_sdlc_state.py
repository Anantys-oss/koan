"""Tests for the SDLC state persistence layer (sdlc_state.py)."""

import json
from pathlib import Path

import pytest

from app.sdlc_state import (
    MAX_FIX_ITERATIONS,
    SDLC_ARTIFACTS,
    SdlcPhase,
    SdlcRiskLevel,
    SdlcState,
    archive_sdlc_workspace,
    get_artifact_path,
    get_sdlc_workspace,
    list_sdlc_workspaces,
    load_sdlc_state,
    save_sdlc_state,
)


# ---------------------------------------------------------------------------
# SdlcPhase enum
# ---------------------------------------------------------------------------


class TestSdlcPhase:
    def test_all_phases_have_string_values(self):
        for phase in SdlcPhase:
            assert isinstance(phase.value, str)

    def test_terminal_phases(self):
        assert SdlcPhase.PRODUCTION_READY.is_terminal
        assert SdlcPhase.ABANDONED.is_terminal

    def test_non_terminal_phases(self):
        non_terminal = [
            SdlcPhase.RESEARCH,
            SdlcPhase.ARCHITECTURE,
            SdlcPhase.PLANNING,
            SdlcPhase.AWAITING_APPROVAL,
            SdlcPhase.IMPLEMENTATION,
            SdlcPhase.REVIEW,
            SdlcPhase.FIX_LOOP,
            SdlcPhase.DOCUMENTATION,
        ]
        for phase in non_terminal:
            assert not phase.is_terminal, f"{phase} should not be terminal"

    def test_phases_are_str_subclass(self):
        assert isinstance(SdlcPhase.RESEARCH, str)
        assert SdlcPhase.RESEARCH == "research"


# ---------------------------------------------------------------------------
# SdlcState serialization
# ---------------------------------------------------------------------------


class TestSdlcStateRoundTrip:
    def _make_state(self, **kwargs) -> SdlcState:
        defaults = dict(
            issue_name="issue-42",
            description="Add auth module",
            current_phase=SdlcPhase.PLANNING,
        )
        defaults.update(kwargs)
        return SdlcState(**defaults)

    def test_basic_roundtrip(self):
        state = self._make_state()
        assert SdlcState.from_dict(state.to_dict()).issue_name == "issue-42"
        assert SdlcState.from_dict(state.to_dict()).current_phase == SdlcPhase.PLANNING

    def test_list_field_roundtrip(self):
        state = self._make_state(failing_experts=["security", "qa"])
        restored = SdlcState.from_dict(state.to_dict())
        assert restored.failing_experts == ["security", "qa"]

    def test_dict_field_roundtrip(self):
        state = self._make_state(artifact_checksums={"RESEARCH.md": "abc123"})
        restored = SdlcState.from_dict(state.to_dict())
        assert restored.artifact_checksums == {"RESEARCH.md": "abc123"}

    def test_bool_approved_roundtrip(self):
        state = self._make_state(approved=True)
        assert SdlcState.from_dict(state.to_dict()).approved is True
        state2 = self._make_state(approved=False)
        assert SdlcState.from_dict(state2.to_dict()).approved is False

    def test_all_phases_survive_roundtrip(self):
        for phase in SdlcPhase:
            state = self._make_state(current_phase=phase)
            assert SdlcState.from_dict(state.to_dict()).current_phase == phase

    def test_all_risk_levels_survive_roundtrip(self):
        for risk in SdlcRiskLevel:
            state = self._make_state(risk_level=risk)
            assert SdlcState.from_dict(state.to_dict()).risk_level == risk

    def test_to_dict_contains_expected_keys(self):
        state = self._make_state()
        d = state.to_dict()
        for key in (
            "issue_name",
            "description",
            "current_phase",
            "risk_level",
            "fix_iteration",
            "failing_experts",
            "approved",
            "started_at",
            "artifact_checksums",
        ):
            assert key in d, f"Missing key: {key}"

    def test_from_dict_unknown_phase_defaults_to_research(self):
        state = SdlcState.from_dict(
            {
                "issue_name": "x",
                "description": "",
                "current_phase": "nonexistent_phase",
            }
        )
        assert state.current_phase == SdlcPhase.RESEARCH

    def test_from_dict_unknown_risk_defaults_to_medium(self):
        state = SdlcState.from_dict(
            {
                "issue_name": "x",
                "description": "",
                "current_phase": "research",
                "risk_level": "Extreme",
            }
        )
        assert state.risk_level == SdlcRiskLevel.MEDIUM

    def test_from_dict_missing_optional_fields(self):
        # Minimal required keys; optional fields must have sensible defaults.
        state = SdlcState.from_dict({"issue_name": "y", "description": ""})
        assert state.fix_iteration == 0
        assert state.failing_experts == []
        assert state.approved is False
        assert state.artifact_checksums == {}
        assert state.started_at  # non-empty default


# ---------------------------------------------------------------------------
# get_sdlc_workspace
# ---------------------------------------------------------------------------


class TestGetSdlcWorkspace:
    def test_creates_directory(self, tmp_path):
        ws = get_sdlc_workspace(str(tmp_path), "feature-auth")
        assert ws.is_dir()

    def test_idempotent(self, tmp_path):
        ws1 = get_sdlc_workspace(str(tmp_path), "feature-auth")
        ws2 = get_sdlc_workspace(str(tmp_path), "feature-auth")
        assert ws1 == ws2

    def test_workspace_inside_sdlc_subdir(self, tmp_path):
        ws = get_sdlc_workspace(str(tmp_path), "my-issue")
        assert ws.parent == tmp_path / "sdlc"

    def test_different_issues_have_different_workspaces(self, tmp_path):
        ws_a = get_sdlc_workspace(str(tmp_path), "issue-a")
        ws_b = get_sdlc_workspace(str(tmp_path), "issue-b")
        assert ws_a != ws_b

    def test_unsafe_chars_in_issue_name(self, tmp_path):
        ws = get_sdlc_workspace(str(tmp_path), "issue/with/../slashes")
        assert ws.exists()
        # The resolved path must stay inside the sdlc dir.
        assert str(ws).startswith(str(tmp_path / "sdlc"))


# ---------------------------------------------------------------------------
# load_sdlc_state
# ---------------------------------------------------------------------------


class TestLoadSdlcState:
    def test_returns_none_when_workspace_absent(self, tmp_path):
        result = load_sdlc_state(str(tmp_path), "nonexistent")
        assert result is None

    def test_returns_none_when_state_file_absent(self, tmp_path):
        get_sdlc_workspace(str(tmp_path), "issue-x")  # create dir, no STATE.json
        result = load_sdlc_state(str(tmp_path), "issue-x")
        assert result is None

    def test_returns_none_on_corrupt_json(self, tmp_path):
        ws = get_sdlc_workspace(str(tmp_path), "issue-corrupt")
        (ws / "STATE.json").write_text("{ not valid json }")
        result = load_sdlc_state(str(tmp_path), "issue-corrupt")
        assert result is None

    def test_returns_none_on_empty_file(self, tmp_path):
        ws = get_sdlc_workspace(str(tmp_path), "issue-empty")
        (ws / "STATE.json").write_text("")
        result = load_sdlc_state(str(tmp_path), "issue-empty")
        assert result is None


# ---------------------------------------------------------------------------
# save_sdlc_state + load_sdlc_state round-trip
# ---------------------------------------------------------------------------


class TestSaveLoadCycle:
    def _make_state(self, issue_name: str = "issue-99", **kwargs) -> SdlcState:
        defaults = dict(
            description="test workflow",
            current_phase=SdlcPhase.RESEARCH,
        )
        defaults.update(kwargs)
        return SdlcState(issue_name=issue_name, **defaults)

    def test_save_then_load(self, tmp_path):
        state = self._make_state()
        save_sdlc_state(str(tmp_path), state)
        loaded = load_sdlc_state(str(tmp_path), state.issue_name)
        assert loaded is not None
        assert loaded.issue_name == state.issue_name
        assert loaded.current_phase == SdlcPhase.RESEARCH

    def test_state_file_is_valid_json(self, tmp_path):
        state = self._make_state()
        save_sdlc_state(str(tmp_path), state)
        ws = get_sdlc_workspace(str(tmp_path), state.issue_name)
        raw = (ws / "STATE.json").read_text()
        data = json.loads(raw)
        assert data["issue_name"] == state.issue_name

    def test_save_creates_workspace_if_absent(self, tmp_path):
        state = self._make_state(issue_name="fresh-issue")
        ws = tmp_path / "sdlc" / "fresh-issue"
        assert not ws.exists()
        save_sdlc_state(str(tmp_path), state)
        assert ws.exists()

    def test_update_phase(self, tmp_path):
        state = self._make_state()
        save_sdlc_state(str(tmp_path), state)

        state.current_phase = SdlcPhase.IMPLEMENTATION
        save_sdlc_state(str(tmp_path), state)

        loaded = load_sdlc_state(str(tmp_path), state.issue_name)
        assert loaded.current_phase == SdlcPhase.IMPLEMENTATION

    def test_update_failing_experts(self, tmp_path):
        state = self._make_state()
        save_sdlc_state(str(tmp_path), state)

        state.failing_experts = ["security"]
        save_sdlc_state(str(tmp_path), state)

        loaded = load_sdlc_state(str(tmp_path), state.issue_name)
        assert loaded.failing_experts == ["security"]

    def test_multiple_issues_isolated(self, tmp_path):
        a = self._make_state(issue_name="alpha", current_phase=SdlcPhase.PLANNING)
        b = self._make_state(issue_name="beta", current_phase=SdlcPhase.REVIEW)
        save_sdlc_state(str(tmp_path), a)
        save_sdlc_state(str(tmp_path), b)

        loaded_a = load_sdlc_state(str(tmp_path), "alpha")
        loaded_b = load_sdlc_state(str(tmp_path), "beta")
        assert loaded_a.current_phase == SdlcPhase.PLANNING
        assert loaded_b.current_phase == SdlcPhase.REVIEW

    def test_high_fix_iteration_survives(self, tmp_path):
        state = self._make_state(fix_iteration=MAX_FIX_ITERATIONS)
        save_sdlc_state(str(tmp_path), state)
        loaded = load_sdlc_state(str(tmp_path), state.issue_name)
        assert loaded.fix_iteration == MAX_FIX_ITERATIONS


# ---------------------------------------------------------------------------
# get_artifact_path
# ---------------------------------------------------------------------------


class TestGetArtifactPath:
    def test_returns_path_inside_workspace(self, tmp_path):
        p = get_artifact_path(str(tmp_path), "my-issue", "PLAN.md")
        assert p == tmp_path / "sdlc" / "my-issue" / "PLAN.md"

    def test_path_need_not_exist(self, tmp_path):
        p = get_artifact_path(str(tmp_path), "ghost", "RESEARCH.md")
        assert not p.exists()

    def test_all_defined_artifacts_have_valid_paths(self, tmp_path):
        for name in SDLC_ARTIFACTS:
            p = get_artifact_path(str(tmp_path), "issue-x", name)
            assert p.name == name


# ---------------------------------------------------------------------------
# archive_sdlc_workspace
# ---------------------------------------------------------------------------


class TestArchiveSdlcWorkspace:
    def _make_state(self, tmp_path: Path, issue_name: str, phase: SdlcPhase) -> SdlcState:
        state = SdlcState(
            issue_name=issue_name,
            description="",
            current_phase=phase,
        )
        save_sdlc_state(str(tmp_path), state)
        return state

    def test_archives_production_ready(self, tmp_path):
        self._make_state(tmp_path, "done-issue", SdlcPhase.PRODUCTION_READY)
        dest = archive_sdlc_workspace(str(tmp_path), "done-issue")
        assert dest is not None
        assert dest.exists()
        ws_original = tmp_path / "sdlc" / "done-issue"
        assert not ws_original.exists()

    def test_archives_abandoned(self, tmp_path):
        self._make_state(tmp_path, "dead-issue", SdlcPhase.ABANDONED)
        dest = archive_sdlc_workspace(str(tmp_path), "dead-issue")
        assert dest is not None
        assert str(dest).startswith(str(tmp_path / "sdlc" / "_archived"))

    def test_returns_none_for_non_terminal(self, tmp_path):
        self._make_state(tmp_path, "active-issue", SdlcPhase.IMPLEMENTATION)
        result = archive_sdlc_workspace(str(tmp_path), "active-issue")
        assert result is None
        assert (tmp_path / "sdlc" / "active-issue").exists()

    def test_returns_none_for_missing_workspace(self, tmp_path):
        result = archive_sdlc_workspace(str(tmp_path), "ghost")
        assert result is None

    def test_no_clobber_existing_archive(self, tmp_path):
        self._make_state(tmp_path, "repeated-issue", SdlcPhase.ABANDONED)
        dest1 = archive_sdlc_workspace(str(tmp_path), "repeated-issue")

        # Re-create and archive again.
        self._make_state(tmp_path, "repeated-issue", SdlcPhase.ABANDONED)
        dest2 = archive_sdlc_workspace(str(tmp_path), "repeated-issue")

        assert dest1 != dest2
        assert dest1.exists()
        assert dest2.exists()


# ---------------------------------------------------------------------------
# list_sdlc_workspaces
# ---------------------------------------------------------------------------


class TestListSdlcWorkspaces:
    def test_empty_when_no_sdlc_dir(self, tmp_path):
        assert list_sdlc_workspaces(str(tmp_path)) == []

    def test_lists_active_workspaces(self, tmp_path):
        for name in ("alpha", "beta", "gamma"):
            get_sdlc_workspace(str(tmp_path), name)
        workspaces = list_sdlc_workspaces(str(tmp_path))
        assert set(workspaces) == {"alpha", "beta", "gamma"}

    def test_excludes_archived(self, tmp_path):
        get_sdlc_workspace(str(tmp_path), "active")
        archived = tmp_path / "sdlc" / "_archived"
        archived.mkdir(parents=True)
        workspaces = list_sdlc_workspaces(str(tmp_path))
        assert "_archived" not in workspaces
        assert "active" in workspaces
