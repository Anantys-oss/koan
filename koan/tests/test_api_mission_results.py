"""Tests for the structured mission result store and resolver registry."""
import json
from pathlib import Path

from app.api import mission_index as mi
from app.api import mission_results as mr


def _seed(instance_dir: Path, text="- /review https://github.com/o/r/pull/1"):
    return mi.record_mission(instance_dir, text, project=None)


def test_attach_result_inline(tmp_path):
    inst = tmp_path / "instance"; inst.mkdir()
    mid = _seed(inst)
    payload = {"kind": "review",
               "review_summary": {"lgtm": True, "summary": "ok", "checklist": []},
               "file_comments": []}
    assert mi.attach_result(inst, mid, payload, cap_bytes=1_000_000) is True
    rec = mi.get_mission(inst, mid)
    assert rec["result"] == payload
    assert rec.get("result_ref") is None
    assert mi.load_full_result(inst, mid) == payload


def test_attach_result_spills_but_keeps_summary_inline(tmp_path):
    inst = tmp_path / "instance"; inst.mkdir()
    mid = _seed(inst)
    big = {"kind": "review",
           "file_comments": [{"comment": "x" * 5000} for _ in range(50)],
           "review_summary": {"lgtm": False, "summary": "s", "checklist": []}}
    mi.attach_result(inst, mid, big, cap_bytes=1024,
                     always_inline=["kind", "review_summary"])
    rec = mi.get_mission(inst, mid)
    # verdict + summary survive inline; file_comments spilled
    assert rec["result"]["review_summary"]["lgtm"] is False
    assert rec["result"]["kind"] == "review"
    assert rec["result"]["result_truncated"] is True
    assert "file_comments" not in rec["result"]
    assert rec["result_ref"].endswith(f"{mid}.json")
    # full blob is retrievable and complete
    assert mi.load_full_result(inst, mid) == big
    spilled = json.loads((inst / ".api-results" / f"{mid}.json").read_text())
    assert spilled == big


def test_attach_result_spills_null_when_no_always_inline(tmp_path):
    inst = tmp_path / "instance"; inst.mkdir()
    mid = _seed(inst)
    big = {"blob": "y" * 4000}
    mi.attach_result(inst, mid, big, cap_bytes=512)
    rec = mi.get_mission(inst, mid)
    assert rec["result"] is None
    assert rec["result_ref"].endswith(f"{mid}.json")
    assert mi.load_full_result(inst, mid) == big


def test_attach_result_is_idempotent(tmp_path):
    inst = tmp_path / "instance"; inst.mkdir()
    mid = _seed(inst)
    first = {"kind": "review", "file_comments": [],
             "review_summary": {"lgtm": True, "summary": "a", "checklist": []}}
    mi.attach_result(inst, mid, first, cap_bytes=1_000_000)
    assert mi.attach_result(inst, mid,
                            {"kind": "review", "file_comments": [],
                             "review_summary": {"lgtm": False, "summary": "b", "checklist": []}},
                            cap_bytes=1_000_000) is False
    assert mi.get_mission(inst, mid)["result"] == first


def test_review_resolver_reads_findings_sidecar(tmp_path):
    inst = tmp_path / "instance"; inst.mkdir()
    fdir = inst / ".review-findings"; fdir.mkdir()
    (fdir / "o_r_42.json").write_text(json.dumps({
        "file_comments": [{"file": "a.py", "line_start": 1, "line_end": 2,
                           "severity": "critical", "title": "t", "comment": "c",
                           "code_snippet": ""}],
        "review_summary": {"lgtm": False, "summary": "s", "checklist": []},
    }))
    text = "- [project:proj] /review https://github.com/o/r/pull/42"
    result = mr.resolve_mission_result(inst, text)
    assert result["kind"] == "review"
    assert result["review_summary"]["lgtm"] is False
    assert result["file_comments"][0]["severity"] == "critical"
    assert mr.always_inline_keys(text) == ["kind", "review_summary"]


def test_resolver_returns_none_for_non_structured(tmp_path):
    inst = tmp_path / "instance"; inst.mkdir()
    assert mr.resolve_mission_result(inst, "- Fix a typo") is None
    assert mr.always_inline_keys("- Fix a typo") == []


def test_resolver_returns_none_when_sidecar_missing(tmp_path):
    inst = tmp_path / "instance"; inst.mkdir()
    assert mr.resolve_mission_result(inst, "- /review https://github.com/o/r/pull/99") is None
