"""Tests for codex_adversarial_review.py — opt-in Codex /review backend."""

import json
import subprocess
from unittest.mock import MagicMock

from app.codex_adversarial_review import (
    map_adversarial_to_review,
    run_adversarial_review,
)
from app.review_schema import validate_review


def _finding(severity, file="db.py", rec=""):
    return {
        "severity": severity, "title": f"{severity} issue", "body": "Details.",
        "file": file, "line_start": 10, "line_end": 12,
        "confidence": 0.9, "recommendation": rec,
    }


def test_map_blocking_findings_and_lgtm():
    review = map_adversarial_to_review({
        "verdict": "needs-attention",
        "summary": "Ship-blockers present.",
        "findings": [
            _finding("high", rec="Use parameterized queries."),
            _finding("low", file="util.py"),
        ],
        "next_steps": ["Add fuzz tests"],
    })
    ok, errors = validate_review(review)
    assert ok, errors
    by_file = {c["file"]: c for c in review["file_comments"]}
    assert by_file["db.py"]["severity"] == "critical"
    assert "parameterized" in by_file["db.py"]["comment"]
    assert by_file["util.py"]["severity"] == "suggestion"
    assert review["review_summary"]["lgtm"] is False
    assert "Add fuzz tests" in review["review_summary"]["summary"]


def test_map_medium_is_warning_and_blocks_lgtm():
    review = map_adversarial_to_review({"findings": [_finding("medium")]})
    assert validate_review(review)[0]
    assert review["file_comments"][0]["severity"] == "warning"
    assert review["review_summary"]["lgtm"] is False


def test_map_ignores_provider_verdict():
    # needs-attention but no findings → lgtm derived from severities.
    review = map_adversarial_to_review({
        "verdict": "needs-attention", "summary": "", "findings": [],
        "next_steps": [],
    })
    assert validate_review(review)[0]
    assert review["review_summary"]["lgtm"] is True
    assert review["file_comments"] == []


def test_map_coerces_malformed_finding():
    review = map_adversarial_to_review({
        "findings": [{"severity": "weird", "line_start": "x", "line_end": -3}, "junk"],
    })
    ok, errors = validate_review(review)
    assert ok, errors
    (c,) = review["file_comments"]
    assert c["severity"] == "suggestion"
    assert (c["line_start"], c["line_end"]) == (0, 0)


def _fake_run(stdout="", returncode=0, stderr=""):
    def run(cmd, **kwargs):
        run.cmd, run.kwargs = cmd, kwargs
        return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
    return run


def _script(tmp_path):
    script = tmp_path / "codex-companion.mjs"
    script.write_text("// stub\n")
    return str(script)


def test_run_parses_result(monkeypatch, tmp_path):
    payload = {"result": {"verdict": "approve", "summary": "Clean.",
                          "findings": [], "next_steps": []}}
    fake = _fake_run(stdout=json.dumps(payload))
    monkeypatch.setattr("app.codex_adversarial_review.subprocess.run", fake)
    review, raw, err = run_adversarial_review(
        project_path=str(tmp_path), base_ref="main",
        companion_script=_script(tmp_path), timeout_seconds=30, model="gpt-5.4",
    )
    assert err == ""
    assert review["review_summary"]["lgtm"] is True
    assert raw
    assert fake.cmd[2:] == ["adversarial-review", "--json", "--base", "main",
                            "--model", "gpt-5.4"]
    assert fake.kwargs["cwd"] == str(tmp_path)
    assert fake.kwargs["timeout"] == 30


def test_run_omits_model_flag_when_unset(monkeypatch, tmp_path):
    payload = {"result": {"summary": "Clean.", "findings": []}}
    fake = _fake_run(stdout=json.dumps(payload))
    monkeypatch.setattr("app.codex_adversarial_review.subprocess.run", fake)
    run_adversarial_review(
        project_path=str(tmp_path), base_ref="main",
        companion_script=_script(tmp_path),
    )
    assert "--model" not in fake.cmd


def test_run_not_configured(tmp_path):
    review, _raw, err = run_adversarial_review(
        project_path=str(tmp_path), base_ref="main", companion_script="",
    )
    assert review is None
    assert "not configured" in err


def test_run_missing_script(tmp_path):
    review, _raw, err = run_adversarial_review(
        project_path=str(tmp_path), base_ref="main",
        companion_script=str(tmp_path / "absent.mjs"),
    )
    assert review is None
    assert "not found" in err


def test_run_node_missing(monkeypatch, tmp_path):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("node")
    monkeypatch.setattr("app.codex_adversarial_review.subprocess.run", boom)
    review, _raw, err = run_adversarial_review(
        project_path=str(tmp_path), base_ref="main",
        companion_script=_script(tmp_path),
    )
    assert review is None
    assert "node" in err


def test_run_empty_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.codex_adversarial_review.subprocess.run",
        _fake_run(stdout="", returncode=1, stderr="not logged in"),
    )
    review, raw, err = run_adversarial_review(
        project_path=str(tmp_path), base_ref="main",
        companion_script=_script(tmp_path),
    )
    assert review is None
    assert raw == ""
    assert "exit=1" in err
    assert "not logged in" in err


def test_run_non_json_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr("app.codex_adversarial_review.subprocess.run",
                        _fake_run(stdout="Codex says hi"))
    review, raw, err = run_adversarial_review(
        project_path=str(tmp_path), base_ref="main",
        companion_script=_script(tmp_path),
    )
    assert review is None
    assert "not JSON" in err
    assert raw == "Codex says hi"


def test_run_missing_result(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.codex_adversarial_review.subprocess.run",
        _fake_run(stdout=json.dumps({"result": None, "parseError": "bad"})),
    )
    review, _raw, err = run_adversarial_review(
        project_path=str(tmp_path), base_ref="main",
        companion_script=_script(tmp_path),
    )
    assert review is None
    assert "bad" in err


def test_run_timeout(monkeypatch, tmp_path):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)
    monkeypatch.setattr("app.codex_adversarial_review.subprocess.run", boom)
    review, _raw, err = run_adversarial_review(
        project_path=str(tmp_path), base_ref="main",
        companion_script=_script(tmp_path), timeout_seconds=30,
    )
    assert review is None
    assert "timed out" in err
