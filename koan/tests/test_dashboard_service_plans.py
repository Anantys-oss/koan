"""Unit tests for app.dashboard_service.plans (no Flask client)."""
import json
from unittest.mock import patch

from app.dashboard_service import plans as svc

_PLAN = (
    "#### Phase 1: Setup\n- ✅ done\n\n"
    "#### Phase 2: Build\n- [ ] todo\n\n"
    "#### Phase 3: Ship\nDone when: shipped\n"
)


def test_parse_plan_progress_counts():
    result = svc.parse_plan_progress(_PLAN)
    assert result["total"] == 3
    assert result["completed"] == 1
    assert result["percent"] == 33


def test_parse_plan_progress_empty():
    assert svc.parse_plan_progress("") == {
        "phases": [], "completed": 0, "total": 0, "percent": 0
    }


def test_find_linked_missions(tmp_path):
    mf = tmp_path / "missions.md"
    mf.write_text(
        "## Pending\n\n"
        "- /plan see #42 here\n"
        "- unrelated\n"
        "- check https://github.com/o/r/issues/42\n"
    )
    with patch.object(svc.state, "MISSIONS_FILE", mf):
        linked = svc.find_linked_missions("https://github.com/o/r/issues/42", 42)
    assert any("#42" in m for m in linked)
    assert any("github.com/o/r/issues/42" in m for m in linked)


def test_get_project_repo_none_when_no_config():
    with patch("app.projects_config.load_projects_config", return_value=None):
        assert svc.get_project_repo("x") is None


def test_fetch_plans_for_project_parses(monkeypatch):
    sample = json.dumps([
        {"number": 1, "title": "Plan A", "state": "open",
         "body": _PLAN, "updatedAt": "2026-02-01", "url": "u"}
    ])
    with patch("app.github.run_gh", return_value=sample):
        result = svc.fetch_plans_for_project("proj", "owner/repo")
    assert len(result) == 1
    assert result[0]["project"] == "proj"
    assert result[0]["progress"]["total"] == 3


def test_fetch_plans_for_project_error_returns_empty():
    with patch("app.github.run_gh", side_effect=RuntimeError("boom")):
        assert svc.fetch_plans_for_project("proj", "owner/repo") == []
