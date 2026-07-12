"""Unit tests for app.dashboard_service.missions (no Flask client)."""
from unittest.mock import patch

from app.dashboard_service import missions as svc


def test_parse_missions_sections(tmp_path):
    mf = tmp_path / "missions.md"
    mf.write_text(
        "## Pending\n\n- A\n- [project:koan] B\n\n"
        "## In Progress\n\n## Done\n\n- ~~C~~\n"
    )
    with patch.object(svc.state, "MISSIONS_FILE", mf):
        result = svc.parse_missions()
    assert len(result["pending"]) == 2
    assert any("B" in m for m in result["pending"])


def test_parse_missions_empty(tmp_path):
    with patch.object(svc.state, "MISSIONS_FILE", tmp_path / "nope.md"):
        result = svc.parse_missions()
    assert result["pending"] == []
    assert result["in_progress"] == []
    assert result["done"] == []


def test_filter_missions_by_project():
    missions = {
        "pending": ["- [project:koan] A", "- [project:other] B"],
        "in_progress": [],
        "done": [],
    }
    filtered = svc.filter_missions_by_project(missions, "koan")
    assert filtered["pending"] == ["- [project:koan] A"]
    # empty project returns input unchanged
    assert svc.filter_missions_by_project(missions, "") is missions


def test_get_all_project_names(tmp_path):
    mf = tmp_path / "missions.md"
    mf.write_text("## Pending\n\n- [project:zeta] A\n\n## In Progress\n\n## Done\n")
    with patch.object(svc.state, "MISSIONS_FILE", mf), \
         patch.object(svc, "get_known_projects", return_value=[("alpha", "/a")]):
        names = svc.get_all_project_names()
    assert names == ["alpha", "zeta"]


def test_get_mission_skill_commands(tmp_path):
    with patch.object(svc.state, "INSTANCE_DIR", tmp_path):
        result = svc.get_mission_skill_commands()
    assert isinstance(result, list)
    assert result == sorted(result, key=str.lower)
