"""Tests for skill_evals.py — the deterministic skill-evaluation harness.

All tests here are offline: they score canned review outputs and exercise the
loader/runner/baseline logic directly. They never call the Claude subprocess.
The one opt-in live test is ``@pytest.mark.slow`` and skips unless
``KOAN_EVAL_LIVE`` is set.
"""

import json

import pytest

from app import skill_evals
from app.skill_evals import (
    CaseExpect,
    CaseResult,
    EvalCase,
    EvalReport,
    FindingExpect,
    compare_to_baseline,
    format_report,
    get_scorer,
    load_cases,
    main,
    register_scorer,
    review_live_fn,
    run_eval,
    score_review,
    write_baseline,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _comment(file="db.py", severity="critical", comment="SQL injection: parameterize."):
    return {
        "file": file,
        "line_start": 1,
        "line_end": 1,
        "severity": severity,
        "title": "t",
        "comment": comment,
        "code_snippet": "",
    }


def _review(comments, lgtm=False, summary="s"):
    return {
        "file_comments": comments,
        "review_summary": {"lgtm": lgtm, "summary": summary, "checklist": []},
    }


def _sqli_case():
    """A buggy case expecting one finding on db.py at critical/warning."""
    return EvalCase(
        id="sqli",
        diff="d",
        expect=CaseExpect(
            expect_lgtm=False,
            min_findings=1,
            expect_findings=[
                FindingExpect(
                    file="db.py",
                    keywords=("inject", "sql", "parameteriz"),
                    severity_in=("critical", "warning"),
                )
            ],
        ),
    )


def _clean_case():
    return EvalCase(
        id="clean",
        diff="d",
        expect=CaseExpect(expect_lgtm=True, forbidden_files=["utils.py"]),
    )


# ---------------------------------------------------------------------------
# score_review
# ---------------------------------------------------------------------------


class TestScoreReview:
    def test_good_review_passes(self):
        case = _sqli_case()
        review = _review([_comment()], lgtm=False)
        r = score_review(case, review)
        assert r.passed is True
        assert r.valid_json is True
        assert r.recall == 1.0
        assert r.lgtm_correct is True
        assert r.precision_penalty == 0
        assert r.score == 1.0
        names = {c.name for c in r.checks}
        assert {"valid_json", "recall", "lgtm", "precision", "min_findings"} <= names

    def test_missing_bug_fails_recall_and_lgtm(self):
        case = _sqli_case()
        # Review says LGTM and finds nothing — misses the seeded bug.
        r = score_review(case, _review([], lgtm=True))
        assert r.passed is False
        assert r.recall == 0.0
        assert r.lgtm_correct is False  # expected False, got True

    def test_wrong_severity_does_not_match(self):
        case = _sqli_case()  # severity_in critical/warning
        review = _review(
            [_comment(severity="suggestion", comment="SQL injection here")],
            lgtm=False,
        )
        r = score_review(case, review)
        # suggestion is outside the band -> finding not matched -> recall 0.
        assert r.recall == 0.0
        assert r.passed is False

    def test_wrong_keyword_does_not_match(self):
        case = _sqli_case()
        review = _review(
            [_comment(comment="Nice variable name.")], lgtm=False
        )
        assert score_review(case, review).recall == 0.0

    def test_invalid_dict_review(self):
        case = _sqli_case()
        # Valid JSON object but violates schema (missing review_summary).
        r = score_review(case, {"file_comments": []})
        assert r.valid_json is False
        assert r.passed is False
        assert r.schema_errors

    def test_non_dict_review(self):
        case = _sqli_case()
        r = score_review(case, "not json")
        assert r.valid_json is False
        assert r.passed is False

    def test_none_review(self):
        case = _sqli_case()
        r = score_review(case, None)
        assert r.valid_json is False
        assert r.passed is False

    def test_clean_case_lgtm_correct(self):
        case = _clean_case()
        r = score_review(case, _review([], lgtm=True))
        assert r.passed is True
        assert r.lgtm_correct is True

    def test_clean_case_false_positive_fails(self):
        case = _clean_case()  # forbidden_files=['utils.py'], expect_lgtm=True
        review = _review([_comment(file="utils.py")], lgtm=False)
        r = score_review(case, review)
        assert r.passed is False
        assert r.precision_penalty == 1
        assert r.lgtm_correct is False

    def test_lgtm_unconstrained_is_neutral(self):
        # expect_lgtm=None -> lgtm_correct None, neutral score component.
        case = EvalCase(id="c", diff="d", expect=CaseExpect())
        r = score_review(case, _review([], lgtm=True))
        assert r.lgtm_correct is None
        # No 'lgtm' check emitted when unconstrained.
        assert "lgtm" not in {c.name for c in r.checks}

    def test_min_findings_not_met(self):
        case = EvalCase(
            id="c",
            diff="d",
            expect=CaseExpect(min_findings=2, expect_lgtm=False),
        )
        r = score_review(case, _review([_comment()], lgtm=False))
        assert r.passed is False  # only 1 finding, needed 2

    def test_file_mismatch_no_match(self):
        case = _sqli_case()  # expects db.py
        review = _review(
            [_comment(file="other.py", comment="SQL injection")], lgtm=False
        )
        assert score_review(case, review).recall == 0.0


# ---------------------------------------------------------------------------
# run_eval + registry
# ---------------------------------------------------------------------------


class TestRunEval:
    def test_aggregates_and_reports_skill(self):
        cases = [_sqli_case(), _clean_case()]

        def review_fn(c):
            if c.id == "sqli":
                return _review([_comment()], lgtm=False)
            return _review([], lgtm=True)

        rep = run_eval(cases, review_fn)
        assert rep.skill == "review"
        assert rep.total == 2
        assert len(rep.scored) == 2
        assert rep.pass_rate == 1.0
        m = rep.metrics()
        assert m["valid_json_rate"] == 1.0
        assert m["mean_recall"] == 1.0

    def test_exception_records_errored(self):
        def boom(c):
            raise RuntimeError("nope")

        rep = run_eval([_sqli_case()], boom)
        assert len(rep.errored) == 1
        assert rep.scored == []
        assert rep.errored[0].errored is True
        assert "nope" in rep.errored[0].error

    def test_none_review_is_invalid_not_errored(self):
        rep = run_eval([_sqli_case()], lambda c: None)
        assert rep.scored == rep.results
        assert rep.results[0].valid_json is False
        assert rep.results[0].errored is False

    def test_mixed_skill_label(self):
        c1 = _sqli_case()
        c2 = EvalCase(id="x", diff="d", expect=CaseExpect(), skill="other")
        # 'other' has no scorer -> run_eval raises per-case.
        with pytest.raises(ValueError):
            run_eval([c1, c2], lambda c: _review([], lgtm=True))


class TestScorerRegistry:
    def test_unknown_skill_raises(self):
        with pytest.raises(ValueError):
            get_scorer("does-not-exist")

    def test_register_and_dispatch_fake_skill(self, tmp_path):
        # A trivial scorer: pass iff the review dict has a 'ok' key True.
        def fake_score(case, review):
            ok = bool(isinstance(review, dict) and review.get("ok"))
            return CaseResult(
                case_id=case.id, skill=case.skill, passed=ok, score=1.0 if ok else 0.0
            )

        register_scorer("my_team_fake", fake_score)
        try:
            assert get_scorer("my_team_fake") is fake_score
            cases_dir = tmp_path / "cases"
            cases_dir.mkdir()
            (cases_dir / "a.json").write_text(
                json.dumps(
                    {"id": "a", "diff": "d", "skill": "my_team_fake", "expect": {}}
                )
            )
            cases = load_cases(str(cases_dir))
            assert cases[0].skill == "my_team_fake"
            rep = run_eval(
                cases,
                lambda c: {"ok": True} if c.id == "a" else {"ok": False},
            )
            assert rep.results[0].passed is True
            assert rep.skill == "my_team_fake"
        finally:
            skill_evals.SCORERS.pop("my_team_fake", None)


# ---------------------------------------------------------------------------
# load_cases
# ---------------------------------------------------------------------------


class TestLoadCases:
    def test_loads_review_dataset(self):
        cases = load_cases("review")
        ids = {c.id for c in cases}
        assert {
            "sql_injection",
            "bare_except",
            "hardcoded_secret",
            "clean_refactor",
            "benign_style",
        } <= ids
        for c in cases:
            assert c.diff.strip()
            assert c.skill == "review"
            # Every expectation is well-formed.
            assert isinstance(c.expect, CaseExpect)

    def test_seeded_bug_cases_expect_findings(self):
        cases = {c.id: c for c in load_cases("review")}
        for cid in ("sql_injection", "bare_except", "hardcoded_secret"):
            assert cases[cid].expect.expect_findings, cid
            assert cases[cid].expect.expect_lgtm is False, cid
        for cid in ("clean_refactor", "benign_style"):
            assert cases[cid].expect.expect_findings == [], cid
            assert cases[cid].expect.expect_lgtm is True, cid

    def test_load_by_explicit_dir(self):
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        cases_dir = os.path.join(
            repo_root, "koan", "skills", "core", "review", "evals", "cases"
        )
        assert len(load_cases(cases_dir)) >= 5

    def test_missing_dir_raises(self):
        with pytest.raises(ValueError):
            load_cases("no_such_skill_zzz")

    def test_empty_dir_raises(self, tmp_path):
        with pytest.raises(ValueError):
            load_cases(str(tmp_path))

    def test_invalid_json_raises(self, tmp_path):
        d = tmp_path / "cases"
        d.mkdir()
        (d / "bad.json").write_text("{not json")
        with pytest.raises(ValueError):
            load_cases(str(d))

    def test_missing_id_raises(self, tmp_path):
        d = tmp_path / "cases"
        d.mkdir()
        (d / "a.json").write_text(json.dumps({"diff": "d", "expect": {}}))
        with pytest.raises(ValueError):
            load_cases(str(d))

    def test_missing_diff_raises(self, tmp_path):
        d = tmp_path / "cases"
        d.mkdir()
        (d / "a.json").write_text(json.dumps({"id": "a", "expect": {}}))
        with pytest.raises(ValueError):
            load_cases(str(d))

    def test_expect_not_object_raises(self, tmp_path):
        d = tmp_path / "cases"
        d.mkdir()
        (d / "a.json").write_text(json.dumps({"id": "a", "diff": "d", "expect": []}))
        with pytest.raises(ValueError):
            load_cases(str(d))

    def test_finding_without_file_raises(self, tmp_path):
        d = tmp_path / "cases"
        d.mkdir()
        (d / "a.json").write_text(
            json.dumps(
                {"id": "a", "diff": "d", "expect": {"expect_findings": [{"keywords": ["x"]}]}}
            )
        )
        with pytest.raises(ValueError):
            load_cases(str(d))

    def test_invalid_severity_raises(self, tmp_path):
        d = tmp_path / "cases"
        d.mkdir()
        (d / "a.json").write_text(
            json.dumps(
                {
                    "id": "a",
                    "diff": "d",
                    "expect": {
                        "expect_findings": [
                            {"file": "f.py", "severity_in": ["catastrophic"]}
                        ]
                    },
                }
            )
        )
        with pytest.raises(ValueError):
            load_cases(str(d))


# ---------------------------------------------------------------------------
# baseline comparison
# ---------------------------------------------------------------------------


def _report(passed=2, total=2, recall=1.0):
    results = [
        CaseResult(case_id=f"c{i}", skill="review", passed=passed > i, recall=recall)
        for i in range(total)
    ]
    return EvalReport(skill="review", results=results)


class TestBaseline:
    def test_missing_baseline_is_no_baseline(self, tmp_path):
        rep = _report()
        out = compare_to_baseline(rep, str(tmp_path / "missing.json"))
        assert out["status"] == "no_baseline"

    def test_malformed_baseline_is_no_baseline(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text("{not json")
        assert compare_to_baseline(_report(), str(p))["status"] == "no_baseline"

    def test_regression_detected(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text(
            json.dumps({"skill": "review", "metrics": {"mean_recall": 1.0}})
        )
        rep = _report(recall=0.5)
        out = compare_to_baseline(rep, str(p))
        assert out["status"] == "regressed"
        assert out["per_metric"]["mean_recall"] == "regressed"

    def test_improvement_detected(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text(
            json.dumps({"skill": "review", "metrics": {"mean_recall": 0.5}})
        )
        rep = _report(recall=1.0)
        out = compare_to_baseline(rep, str(p))
        assert out["status"] == "improved"

    def test_unchanged(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text(
            json.dumps(
                {"skill": "review", "metrics": _report().metrics()}
            )
        )
        out = compare_to_baseline(_report(), str(p))
        assert out["status"] == "unchanged"

    def test_new_metric_status(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text(json.dumps({"skill": "review", "metrics": {}}))
        out = compare_to_baseline(_report(), str(p))
        assert all(v == "new" for v in out["per_metric"].values())

    def test_write_then_compare(self, tmp_path):
        p = tmp_path / "b.json"
        write_baseline(_report(), str(p))
        data = json.loads(p.read_text())
        assert data["skill"] == "review"
        assert "metrics" in data
        # Reading it back should be 'unchanged' against an identical report.
        assert compare_to_baseline(_report(), str(p))["status"] == "unchanged"


# ---------------------------------------------------------------------------
# review_live_fn (injected deps — no LLM)
# ---------------------------------------------------------------------------


class TestReviewLiveFn:
    def test_success_returns_parsed_dict(self):
        case = _sqli_case()
        captured = {}

        def build(ctx, skill_dir=None, project_path=None):
            captured["ctx"] = ctx
            return "PROMPT"

        def run(prompt, project_path):
            captured["prompt"] = prompt
            captured["project_path"] = project_path
            return ("RAW", "")

        def parse(raw):
            captured["raw"] = raw
            return _review([_comment()], lgtm=False)

        out = review_live_fn(case, "/repo", _build=build, _run=run, _parse=parse)
        assert out["review_summary"]["lgtm"] is False
        # Context built from the case, hermetic (project_path=None to build).
        assert captured["ctx"]["diff"] == case.diff
        assert captured["ctx"]["title"]
        assert captured["project_path"] == "/repo"
        assert captured["prompt"] == "PROMPT"
        assert captured["raw"] == "RAW"

    def test_provider_error_raises(self):
        def run(prompt, project_path):
            return ("", "quota exceeded")

        with pytest.raises(RuntimeError):
            review_live_fn(_sqli_case(), "/repo", _run=run)

    def test_parse_returns_none_propagates(self):
        # parse returning None (unparseable) is passed through, not raised.
        out = review_live_fn(
            _sqli_case(),
            "/repo",
            _run=lambda p, pp: ("junk", ""),
            _parse=lambda raw: None,
        )
        assert out is None


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_renders_scored_and_errored(self):
        rep = EvalReport(
            skill="review",
            results=[
                CaseResult(case_id="a", skill="review", passed=True, score=1.0, recall=1.0),
                CaseResult(case_id="b", skill="review", errored=True, error="boom"),
            ],
        )
        text = format_report(rep, {"status": "unchanged", "per_metric": {}})
        assert "Eval report" in text
        assert "[a] PASS" in text
        assert "[b] ERRORED" in text
        assert "Baseline: unchanged" in text


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------


class TestCli:
    def test_offline_prints_dataset_summary(self, capsys):
        rc = main(["review"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Offline mode" in out
        assert "sql_injection" in out

    def test_live_without_env_refuses(self, monkeypatch, capsys):
        monkeypatch.delenv("KOAN_EVAL_LIVE", raising=False)
        rc = main(["review", "--live"])
        out = capsys.readouterr().out
        assert rc == 2
        assert "KOAN_EVAL_LIVE" in out

    def test_live_with_env_runs_and_reports(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("KOAN_EVAL_LIVE", "1")
        baseline = tmp_path / "baseline.json"

        # Stub the LLM-touching adapter: LGTM everything (clean) so the run is
        # deterministic and hermetic. Per-case canned reviews.
        def fake_live(case, project_path):
            if case.expect.expect_lgtm is True:
                return _review([], lgtm=True)
            return _review([_comment(file="db.py")], lgtm=False)

        monkeypatch.setattr(skill_evals, "review_live_fn", fake_live)

        rc = main(
            ["review", "--live", "--baseline", str(baseline), "--project-path", "/repo"]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "Eval report" in out
        assert "Aggregate metrics" in out

    def test_live_update_baseline_writes_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KOAN_EVAL_LIVE", "1")
        baseline = tmp_path / "baseline.json"

        def fake_live(case, project_path):
            return _review([], lgtm=True) if case.expect.expect_lgtm is True else _review(
                [_comment(file="db.py")], lgtm=False
            )

        monkeypatch.setattr(skill_evals, "review_live_fn", fake_live)
        rc = main(["review", "--live", "--baseline", str(baseline), "--update-baseline"])
        assert rc == 0
        data = json.loads(baseline.read_text())
        assert data["skill"] == "review"
        assert "metrics" in data


# ---------------------------------------------------------------------------
# Opt-in live eval (real LLM) — skipped by default
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_live_eval_against_real_pipeline():
    """Runs the real review pipeline over the golden dataset.

    SKIPPED unless ``KOAN_EVAL_LIVE=1`` is set (and a review provider is
    configured). This is the regression/improvement gate an operator runs
    before/after a prompt change; the default suite never invokes the Claude
    subprocess. Run with an extended timeout, e.g.::

        KOAN_EVAL_LIVE=1 pytest koan/tests/test_skill_evals.py \\
            -m slow -p no:timeout
    """
    if not skill_evals._is_live_enabled():
        pytest.skip(f"set {skill_evals.LIVE_ENV}=1 to run the live eval")

    import os

    project_path = os.getcwd()
    cases = load_cases("review")
    report = run_eval(cases, lambda c: review_live_fn(c, project_path))
    assert report.total == len(cases)
    # SC-003: the current review prompt must emit valid JSON for every case.
    assert report.valid_json_rate == 1.0
    # Regression guard: seeded bugs should be caught at least most of the time.
    assert report.mean_recall >= 0.5
