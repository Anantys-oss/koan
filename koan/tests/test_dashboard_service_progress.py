"""Unit tests for dashboard_service.progress (no Flask client)."""
from app.dashboard_service import progress as prog


SAMPLE = """# Mission: /fix PROJ-123 flaky test
Project: koan
Started: 2026-07-17 10:00:00
Run: 1/5
Mode: mission

---
[cli] assistant — thinking
[cli] assistant — thinking
[cli] assistant — tool_use: Read: koan/app/run.py
[cli] assistant — text: Looking at the failure
[cli] tool_result toolu_abc
[cli] result: success (12s)
some unprefixed log line
"""


def test_parse_pending_header():
    h = prog.parse_pending_header(SAMPLE)
    assert h["title"] == "/fix PROJ-123 flaky test"
    assert h["project"] == "koan"
    assert h["started"] == "2026-07-17 10:00:00"
    assert h["run"] == "1/5"
    assert h["mode"] == "mission"


def test_parse_pending_header_autonomous():
    h = prog.parse_pending_header("# Autonomous run\nProject: demo\n")
    assert h["title"] == "Autonomous run"
    assert h["project"] == "demo"


def test_build_entries_collapses_thinking_and_suppresses_tool_result():
    entries = prog.build_entries(SAMPLE)
    kinds = [e["kind"] for e in entries]
    assert "thinking" in kinds
    think = next(e for e in entries if e["kind"] == "thinking")
    assert think["count"] >= 2
    assert "tool_use" in kinds
    assert "text" in kinds
    assert "result" in kinds
    assert "raw" in kinds  # unprefixed line
    assert all(e["kind"] != "tool_result" for e in entries)


def test_build_progress_payload_inactive():
    p = prog.build_progress_payload(active=False, content="")
    assert p == {
        "active": False,
        "content": "",
        "header": {
            "title": "", "project": "", "started": "", "run": "", "mode": "",
        },
        "entries": [],
    }


def test_build_progress_payload_active():
    p = prog.build_progress_payload(active=True, content=SAMPLE)
    assert p["active"] is True
    assert "Mission: /fix" in p["content"] or "/fix PROJ-123" in p["content"]
    assert p["header"]["project"] == "koan"
    assert len(p["entries"]) >= 3
