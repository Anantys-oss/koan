"""Tests for the review findings sidecar (includes review_summary)."""
import json

from app.review_runner import _write_review_findings_sidecar


def test_sidecar_includes_review_summary(tmp_path):
    inst = tmp_path / "instance"; inst.mkdir()
    summary = {"lgtm": False, "summary": "needs work",
               "checklist": [{"item": "tests", "passed": False}]}
    _write_review_findings_sidecar(
        str(inst), "o", "r", "7",
        [{"file": "a.py", "line_start": 1, "line_end": 1, "severity": "warning",
          "title": "t", "comment": "c", "code_snippet": ""}],
        base_ref="main", head_sha="deadbeef", project_name="proj",
        review_summary=summary,
    )
    data = json.loads((inst / ".review-findings" / "o_r_7.json").read_text())
    assert data["review_summary"] == summary
    assert data["file_comments"][0]["severity"] == "warning"
