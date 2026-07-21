"""Tests for the /review accuracy gate: snippet validation, prior-finding
reconciliation, and native repo-convention ingestion (review_runner)."""

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.review_runner as rr


def _rd(findings, checklist=None):
    return {
        "file_comments": findings,
        "review_summary": {"summary": "s", "lgtm": True, "checklist": checklist or []},
    }


def _finding(**kw):
    base = {
        "file": "a.py", "line_start": 2, "line_end": 2, "code_snippet": "BUGGY",
        "severity": "warning", "title": "x", "comment": "c",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- _read_file_at_sha


def test_read_file_at_sha_git_show(tmp_path):
    fake = SimpleNamespace(returncode=0, stdout="hello world\n")
    with patch("app.review_runner.subprocess.run", return_value=fake):
        out = rr._read_file_at_sha("o", "r", "sha", "a.py", str(tmp_path))
    assert out == "hello world\n"


def test_read_file_at_sha_api_fallback():
    encoded = base64.b64encode(b"API CONTENT").decode()
    with patch("app.review_runner.run_gh", return_value=encoded) as gh:
        out = rr._read_file_at_sha("o", "r", "sha", "a.py", "")
    assert out == "API CONTENT"
    gh.assert_called_once()


def test_read_file_at_sha_unavailable_returns_none():
    with patch("app.review_runner.run_gh", side_effect=RuntimeError("404")):
        assert rr._read_file_at_sha("o", "r", "sha", "a.py", "") is None


# --------------------------------------------------------- _snippet_matches_at_anchor


def test_snippet_matches_exact():
    m, auth = rr._snippet_matches_at_anchor("a\nBUG\nc", 2, 2, "BUG")
    assert m is True and auth == "BUG"


def test_snippet_matches_whitespace_tolerant():
    m, _ = rr._snippet_matches_at_anchor("a\n    BUG\nc", 2, 2, "BUG")
    assert m is True


def test_snippet_matches_within_fuzz_window():
    # anchor says line 2, real content at line 4 — within the default fuzz.
    m, _ = rr._snippet_matches_at_anchor("a\nb\nc\nTARGET\ne", 2, 2, "TARGET")
    assert m is True


def test_snippet_mismatch_returns_authoritative():
    m, auth = rr._snippet_matches_at_anchor("a\nFIXED\nc", 2, 2, "BUGGY")
    assert m is False and auth == "FIXED"


def test_snippet_out_of_range():
    m, _ = rr._snippet_matches_at_anchor("a\nb", 10, 10, "x")
    assert m is False


# ------------------------------------------------------- _remap_findings_after_drop


def test_remap_drops_and_remaps_checklist():
    rd = _rd(
        [
            _finding(severity="warning", title="A"),
            _finding(severity="suggestion", title="B"),
            _finding(severity="critical", title="C"),
        ],
        checklist=[{"passed": False, "finding_refs": [0, 2]}],
    )
    rr._remap_findings_after_drop(rd, {2})
    assert [f["title"] for f in rd["file_comments"]] == ["A", "B"]
    assert rd["review_summary"]["checklist"][0]["finding_refs"] == [0]
    assert rd["review_summary"]["lgtm"] is False  # a warning remains


def test_remap_lgtm_true_when_no_blockers_left():
    rd = _rd([
        _finding(severity="warning", title="A"),
        _finding(severity="suggestion", title="B"),
    ])
    rr._remap_findings_after_drop(rd, {0})
    assert rd["review_summary"]["lgtm"] is True


# ------------------------------------------------------- _validate_finding_snippets


def test_validate_resync_replaces_stale_quote():
    rd = _rd([_finding(code_snippet="BUGGY")])
    with patch("app.review_runner._read_file_at_sha", return_value="a\nFIXED\nc"):
        rr._validate_finding_snippets(rd, "o", "r", "sha", "/p", on_mismatch="resync")
    assert rd["file_comments"][0]["code_snippet"] == "FIXED"


def test_validate_match_leaves_untouched():
    rd = _rd([_finding(code_snippet="OK")])
    with patch("app.review_runner._read_file_at_sha", return_value="a\nOK\nc"):
        rr._validate_finding_snippets(rd, "o", "r", "sha", "/p")
    assert rd["file_comments"][0]["code_snippet"] == "OK"


def test_validate_drop_mode_removes_and_remaps():
    rd = _rd([_finding(code_snippet="BUGGY")], checklist=[{"passed": False, "finding_refs": [0]}])
    with patch("app.review_runner._read_file_at_sha", return_value="a\nFIXED\nc"):
        rr._validate_finding_snippets(rd, "o", "r", "sha", "/p", on_mismatch="drop")
    assert rd["file_comments"] == []
    assert rd["review_summary"]["checklist"][0]["finding_refs"] == []
    assert rd["review_summary"]["lgtm"] is True


def test_validate_out_of_range_dropped():
    rd = _rd([_finding(line_start=50, line_end=50, code_snippet="X")])
    with patch("app.review_runner._read_file_at_sha", return_value="a\nb\nc"):
        rr._validate_finding_snippets(rd, "o", "r", "sha", "/p", on_mismatch="resync")
    assert rd["file_comments"] == []


def test_validate_fail_open_on_unavailable():
    rd = _rd([_finding(code_snippet="BUGGY")])
    with patch("app.review_runner._read_file_at_sha", return_value=None):
        rr._validate_finding_snippets(rd, "o", "r", "sha", "/p", on_mismatch="drop")
    assert rd["file_comments"][0]["code_snippet"] == "BUGGY"


def test_validate_skips_wholefile_and_empty_snippet():
    rd = _rd([
        _finding(line_start=0, code_snippet="whole-file note", title="wf"),
        _finding(line_start=2, code_snippet="", title="empty"),
    ])
    with patch("app.review_runner._read_file_at_sha", return_value="a\nZZZ\nc") as m:
        rr._validate_finding_snippets(rd, "o", "r", "sha", "/p")
    m.assert_not_called()
    assert len(rd["file_comments"]) == 2


# ------------------------------------------- sidecar + prior-finding reconciliation


def test_prior_sidecar_roundtrip(tmp_path):
    findings = [_finding(line_start=3, code_snippet="X")]
    rr._write_review_findings_sidecar(
        str(tmp_path), "o", "r", "5", findings, base_ref="main", head_sha="deadbeef",
    )
    got, head = rr._read_prior_findings_sidecar(str(tmp_path), "o", "r", "5")
    assert head == "deadbeef"
    assert got and got[0]["file"] == "a.py"


def test_prior_sidecar_missing():
    from tempfile import mkdtemp
    assert rr._read_prior_findings_sidecar(mkdtemp(), "o", "r", "99") == ([], "")


def test_reconcile_suppresses_reraised_fixed_finding():
    prior = [_finding(code_snippet="BUGGY", title="Bug")]
    rd = _rd([_finding(code_snippet="BUGGY", title="Bug")])
    with patch("app.review_runner._read_file_at_sha", return_value="a\nFIXED\nc"):
        resolved = rr._reconcile_prior_findings(rd, prior, "o", "r", "sha", "/p")
    assert len(resolved) == 1
    assert rd["file_comments"] == []  # re-raise of a resolved finding is dropped


def test_reconcile_keeps_still_broken_finding():
    prior = [_finding(code_snippet="BUGGY", title="Bug")]
    rd = _rd([_finding(code_snippet="BUGGY", title="Bug")])
    with patch("app.review_runner._read_file_at_sha", return_value="a\nBUGGY\nc"):
        resolved = rr._reconcile_prior_findings(rd, prior, "o", "r", "sha", "/p")
    assert resolved == []
    assert len(rd["file_comments"]) == 1  # still present at HEAD -> kept


def test_reconcile_fail_open():
    prior = [_finding(code_snippet="BUGGY", title="Bug")]
    rd = _rd([_finding(code_snippet="BUGGY", title="Bug")])
    with patch("app.review_runner._read_file_at_sha", return_value=None):
        resolved = rr._reconcile_prior_findings(rd, prior, "o", "r", "sha", "/p")
    assert resolved == []
    assert len(rd["file_comments"]) == 1


# ------------------------------------------------- end-to-end gate + markdown render


def test_gate_end_to_end_resync_and_resolved_render():
    prior = [{"file": "old.py", "line_start": 1, "line_end": 1,
              "code_snippet": "GONE", "title": "Old bug"}]
    rd = _rd([_finding(file="b.py", code_snippet="STALEQUOTE", title="Real",
                       comment="still broken")])

    def fake_read(owner, repo, sha, path, project_path, timeout=20):
        return {"old.py": "fixed now", "b.py": "x\nAUTHORITATIVE\ny"}.get(path)

    with patch("app.review_runner._read_prior_findings_sidecar",
               return_value=(prior, "psha")), \
         patch("app.review_runner._read_file_at_sha", side_effect=fake_read), \
         patch("app.config.get_review_reconcile_config",
               return_value={"enabled": True, "show_resolved": True}), \
         patch("app.config.get_review_snippet_validation_config",
               return_value={"enabled": True, "on_mismatch": "resync"}):
        rr._apply_review_accuracy_gate(
            rd, owner="o", repo="r", sha="sha", project_path="/p",
            project_name="", pr_number="1",
        )

    assert rd["file_comments"][0]["code_snippet"] == "AUTHORITATIVE"
    assert len(rd.get("_resolved_prior", [])) == 1

    md = rr._format_review_as_markdown(rd, title="t", owner="o", repo="r", head_sha="sha")
    assert "Resolved since last review" in md
    assert "AUTHORITATIVE" in md and "STALEQUOTE" not in md


# ----------------------------------------------------- native convention ingestion


def test_conventions_block_with_docs(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("ROOT-ABSOLUTE LINKS ARE ALLOWED")
    with patch("app.config._load_config", return_value={}), \
         patch("app.config._load_project_overrides", return_value={}):
        out = rr._build_repo_conventions_block(str(tmp_path), "")
    assert "Repository Conventions" in out
    assert "ROOT-ABSOLUTE LINKS ARE ALLOWED" in out


def test_conventions_block_empty_path():
    assert rr._build_repo_conventions_block("", "") == ""


def test_conventions_block_disabled():
    from tempfile import mkdtemp
    d = Path(mkdtemp())
    (d / "CLAUDE.md").write_text("stuff")
    with patch("app.config._load_config",
               return_value={"review_convention_docs": {"enabled": False}}), \
         patch("app.config._load_project_overrides", return_value={}):
        assert rr._build_repo_conventions_block(str(d), "") == ""


def test_conventions_block_absent_docs(tmp_path):
    with patch("app.config._load_config", return_value={}), \
         patch("app.config._load_project_overrides", return_value={}):
        assert rr._build_repo_conventions_block(str(tmp_path), "") == ""


def test_review_template_has_conventions_slot():
    review_md = (
        Path(rr.__file__).resolve().parents[1]
        / "skills" / "core" / "review" / "prompts" / "review.md"
    )
    assert "{REPO_CONVENTIONS}" in review_md.read_text()
