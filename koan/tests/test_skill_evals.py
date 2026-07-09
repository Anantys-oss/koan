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
    EVAL_EXEMPT_SKILLS,
    EvalCase,
    EvalReport,
    FindingExpect,
    brainstorm_live_fn,
    compare_to_baseline,
    fix_live_fn,
    format_report,
    get_live_fn,
    get_scorer,
    load_cases,
    main,
    plan_live_fn,
    rebase_live_fn,
    register_scorer,
    review_live_fn,
    run_eval,
    score_brainstorm,
    score_fix,
    score_plan,
    score_rebase,
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

    def test_empty_cases_returns_empty_report(self):
        rep = run_eval([], lambda c: None)
        assert rep.total == 0
        assert rep.results == []
        assert rep.mean_recall == 0.0

    def test_scorer_exception_is_isolated_not_abort(self):
        # A scorer that raises must record one errored case, not abort the run.
        def boom(case, out):
            raise RuntimeError("scorer blew up")

        register_scorer("boom_skill", boom)
        try:
            rep = run_eval(
                [EvalCase(id="x", skill="boom_skill", expect=CaseExpect(raw={})),
                 _sqli_case()],
                lambda c: {},
            )
            assert len(rep.errored) == 1
            assert "scorer raised" in rep.errored[0].error
            assert rep.total == 2  # the review case still ran
        finally:
            skill_evals.SCORERS.pop("boom_skill", None)


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
            return "PROMPT", ""

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

    def test_real_build_passes_string_prompt_to_run(self):
        # Regression guard: build_review_prompt returns (prompt, note); the
        # adapter must unpack it so _run_claude_review receives a str, not the
        # tuple. Uses the real build (only _run/_parse stubbed).
        captured = {}

        def run(prompt, project_path):
            captured["prompt"] = prompt
            return ("RAW", "")

        review_live_fn(
            _sqli_case(), "/repo",
            _run=run,
            _parse=lambda raw: _review([], lgtm=True),
        )
        assert isinstance(captured["prompt"], str)

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
    import os

    if not os.environ.get(skill_evals.LIVE_ENV):
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


# ===========================================================================
# Multi-skill: generalisation (US2) — review behaviour preserved + input/raw
# ===========================================================================


class TestGeneralization:
    def test_review_case_has_empty_input_and_raw_expect(self):
        cases = {c.id: c for c in load_cases("review")}
        c = cases["sql_injection"]
        assert c.input == {}  # review carries its input in `diff`, not `input`
        assert c.diff.strip()
        assert isinstance(c.expect.raw, dict)
        assert c.expect.raw.get("expect_lgtm") is False

    def test_non_review_case_populates_input(self, tmp_path):
        d = tmp_path / "cases"
        d.mkdir()
        (d / "x.json").write_text(json.dumps({
            "id": "x", "skill": "fix",
            "issue_title": "T", "issue_body": "B",
            "expect": {"expected_confidence": "HIGH"},
        }))
        c = load_cases(str(d))[0]
        assert c.diff == ""
        assert c.input == {"issue_title": "T", "issue_body": "B"}
        assert c.expect.raw == {"expected_confidence": "HIGH"}

    def test_case_needs_diff_or_input(self, tmp_path):
        d = tmp_path / "cases"
        d.mkdir()
        (d / "a.json").write_text(json.dumps({"id": "a", "expect": {}}))
        with pytest.raises(ValueError):
            load_cases(str(d))

    def test_dispatch_uses_skill_scorer(self):
        case = EvalCase(
            id="f", skill="fix",
            input={"issue_title": "t"},
            expect=CaseExpect(raw={"expected_confidence": "HIGH"}),
        )
        rep = run_eval([case], lambda c: {"confidence": "HIGH", "hypothesis": "h"})
        assert rep.skill == "fix"
        assert rep.results[0].passed is True


# ===========================================================================
# score_fix
# ===========================================================================


def _fix_case(**raw):
    return EvalCase(
        id="f", skill="fix",
        input={"issue_title": "t", "issue_body": "b"},
        expect=CaseExpect(raw=raw),
    )


class TestScoreFix:
    def test_high_confidence_match_passes(self):
        case = _fix_case(expected_confidence="HIGH",
                         hypothesis_keywords=["null", "parser"],
                         code_path_keywords=["parser"])
        out = {
            "confidence": "HIGH",
            "hypothesis": "A null dereference in the parser",
            "code_paths": "src/parser.py",
            "analysis": "",
        }
        r = score_fix(case, out)
        assert r.passed is True
        assert r.valid_json is True
        assert r.recall == 1.0
        assert isinstance(r.score, float)

    def test_wrong_confidence_fails(self):
        case = _fix_case(expected_confidence="HIGH")
        r = score_fix(case, {"confidence": "LOW", "hypothesis": "h"})
        assert r.passed is False
        assert {c.name for c in r.checks if not c.passed} == {"confidence_match"}

    def test_missing_hypothesis_fails(self):
        case = _fix_case()
        r = score_fix(case, {"confidence": "LOW", "hypothesis": ""})
        assert r.passed is False
        assert any(c.name == "hypothesis_present" and not c.passed for c in r.checks)

    def test_malformed_input_is_invalid_not_raise(self):
        case = _fix_case()
        r = score_fix(case, "not a dict")
        assert r.valid_json is False
        assert r.passed is False

    def test_vague_case_expects_low(self):
        case = _fix_case(expected_confidence="LOW")
        r = score_fix(case, {"confidence": "LOW", "hypothesis": "cannot localise"})
        assert r.passed is True


# ===========================================================================
# score_plan
# ===========================================================================


_GOOD_PLAN = (
    "Add a settings page\n\n"
    "### Summary\nDoes the thing.\n\n"
    "### Alternatives Considered\n- A (chosen)\n\n"
    "### File Map\n| Create | x.py | y |\n\n"
    "#### Phase 1: Build UI\n**Files**: x.py\n- step\n\n"
    "#### Phase 2: Persist\n**Files**: y.py\n- step\n\n"
    "### Verification Criteria\n- Given X when Y then Z.\n"
)


def _plan_case(**raw):
    return EvalCase(id="p", skill="plan", input={"idea": "x"}, expect=CaseExpect(raw=raw))


class TestScorePlan:
    def test_well_formed_plan_passes(self):
        r = score_plan(_plan_case(), _GOOD_PLAN)
        assert r.passed is True
        assert r.valid_json is True
        names = {c.name for c in r.checks}
        assert {"required_sections", "min_phases", "no_banned_placeholders", "title_present"} <= names

    def test_banned_placeholder_fails_even_with_sections(self):
        r = score_plan(_plan_case(), "Title\n### Summary\nx\nTODO: finish\n#### Phase 1: p\n")
        assert r.passed is False
        assert any(c.name == "no_banned_placeholders" and not c.passed for c in r.checks)

    def test_missing_section_fails(self):
        r = score_plan(_plan_case(), "Title\n### Summary\nx\n#### Phase 1: p\n")
        assert r.passed is False
        assert any(c.name == "required_sections" and not c.passed for c in r.checks)

    def test_too_few_phases_fails(self):
        case = _plan_case(min_phases=2)
        r = score_plan(case, "Title\n### Summary\nx\n### File Map\nf\n#### Phase 1: only\n")
        assert r.passed is False
        assert any(c.name == "min_phases" and not c.passed for c in r.checks)

    def test_accepts_dict_with_text(self):
        r = score_plan(_plan_case(), {"text": _GOOD_PLAN})
        assert r.passed is True

    def test_first_line_heading_is_not_a_title(self):
        r = score_plan(_plan_case(), "# A heading\n### Summary\nx\n#### Phase 1: p\n")
        assert any(c.name == "title_present" and not c.passed for c in r.checks)


# ===========================================================================
# score_brainstorm
# ===========================================================================


_GOOD_BODY = (
    "## Why This Matters\nx\n## Approach\nx\n## Acceptance Criteria\nx\n"
    "## Risks & Caveats\nx\n## Scores\nImpact: h\nDifficulty: m\n"
    "Short-Term ROI: l\nLong-Term Value: h\n## Priority\nImmediate\n"
    "## Dependencies\nx\n"
)


def _issue(title, body=_GOOD_BODY):
    return {"title": title, "body": body}


def _decomp(titles, **kw):
    data = {"issues": [_issue(t) for t in titles]}
    data.update(kw)
    return data


def _brain_case(**raw):
    return EvalCase(id="b", skill="brainstorm", input={"topic": "x"}, expect=CaseExpect(raw=raw))


class TestScoreBrainstorm:
    def test_clean_decomposition_passes(self):
        case = _brain_case(min_issues=3, max_issues=8, theme_keywords=["login", "session"])
        data = _decomp(["login throttling", "session expiry", "token refresh"])
        r = score_brainstorm(case, json.dumps(data))
        assert r.passed is True
        assert r.valid_json is True

    def test_too_few_issues_fails(self):
        case = _brain_case(min_issues=3)
        r = score_brainstorm(case, json.dumps(_decomp(["only one"])))
        assert r.passed is False
        assert any(c.name == "issue_count" and not c.passed for c in r.checks)

    def test_missing_section_fails(self):
        case = _brain_case()
        data = _decomp(["a", "b", "c"])
        data["issues"][0]["body"] = data["issues"][0]["body"].replace("## Priority\nImmediate\n", "")
        r = score_brainstorm(case, json.dumps(data))
        assert r.passed is False
        assert any(c.name == "sections_per_issue" and not c.passed for c in r.checks)

    def test_invalid_json_is_invalid(self):
        r = score_brainstorm(_brain_case(), "not json at all")
        assert r.valid_json is False
        assert r.passed is False

    def test_accepts_already_parsed_dict(self):
        case = _brain_case(min_issues=3)
        r = score_brainstorm(case, _decomp(["a", "b", "c"]))
        assert r.passed is True

    def test_fenced_json_is_parsed(self):
        case = _brain_case(min_issues=3)
        fenced = "```json\n" + json.dumps(_decomp(["a", "b", "c"])) + "\n```"
        r = score_brainstorm(case, fenced)
        assert r.valid_json is True


# ===========================================================================
# score_rebase
# ===========================================================================


def _rebase_case(**raw):
    return EvalCase(id="r", skill="rebase", input={"pr_title": "t"}, expect=CaseExpect(raw=raw))


class TestScoreRebase:
    def test_solved_high_confidence_passes(self):
        case = _rebase_case(expect_solved=True)
        r = score_rebase(case, '{"already_solved": true, "confidence": "high", "reasoning": "commit x adds it"}')
        assert r.passed is True
        assert r.recall == 1.0

    def test_solved_medium_confidence_fails_production_rule(self):
        case = _rebase_case(expect_solved=True)
        r = score_rebase(case, '{"already_solved": true, "confidence": "medium", "reasoning": "maybe"}')
        assert r.passed is False
        assert any(c.name == "decision_correct" and not c.passed for c in r.checks)

    def test_not_solved_when_expected_solved(self):
        case = _rebase_case(expect_solved=True)
        r = score_rebase(case, '{"already_solved": false, "confidence": "low", "reasoning": "no match"}')
        assert r.passed is False

    def test_negative_case_passes(self):
        case = _rebase_case(expect_solved=False)
        r = score_rebase(case, '{"already_solved": false, "confidence": "low", "reasoning": "not present"}')
        assert r.passed is True

    def test_malformed_does_not_raise(self):
        r = score_rebase(_rebase_case(), "no json here")
        assert r.valid_json is False
        assert r.passed is False

    def test_accepts_dict(self):
        case = _rebase_case(expect_solved=True)
        r = score_rebase(case, {"already_solved": True, "confidence": "high", "reasoning": "r"})
        assert r.passed is True

    def test_nested_json_extracts_flat_like_production(self):
        # Production's flat-object regex grabs the inner object (no
        # already_solved key) from nested JSON → treated as a negative. The
        # scorer matches that extraction (single source of truth).
        case = _rebase_case(expect_solved=False)
        r = score_rebase(case, 'noise {"already_solved": true, "meta": {"x": 1}} tail')
        assert r.valid_json is False


class TestScorerRobustness:
    """Every scorer must return a CaseResult (never raise) on any input shape."""

    @pytest.mark.parametrize("fn", [score_fix, score_plan, score_brainstorm, score_rebase])
    def test_none_input_does_not_raise(self, fn):
        case = EvalCase(id="x", skill="x", input={"idea": "i"}, expect=CaseExpect(raw={}))
        r = fn(case, None)
        assert isinstance(r, CaseResult)
        assert r.passed is False
        assert r.valid_json is False

    @pytest.mark.parametrize("fn", [score_plan, score_rebase])
    def test_empty_string_does_not_raise(self, fn):
        case = EvalCase(id="x", skill="x", input={"idea": "i"}, expect=CaseExpect(raw={}))
        r = fn(case, "")
        assert isinstance(r, CaseResult)
        assert r.passed is False

    def test_fix_none_input_is_invalid(self):
        r = score_fix(EvalCase(id="x", skill="fix", expect=CaseExpect(raw={})), None)
        assert r.valid_json is False

    def test_brainstorm_non_dict_issues_never_raises(self):
        case = EvalCase(id="b", skill="brainstorm", input={"topic": "x"},
                        expect=CaseExpect(raw={}))
        for bad in ({"issues": ["str"]}, {"issues": [1, 2]},
                    {"issues": [{"title": "t"}, "bad"]}):
            r = score_brainstorm(case, bad)
            assert isinstance(r, CaseResult)
            assert r.passed is False  # malformed issue items fail section coverage


# ===========================================================================
# Datasets (US1) — validity + offline CLI for the four skills
# ===========================================================================


class TestMultiSkillDatasets:
    @pytest.mark.parametrize("skill", ["fix", "plan", "brainstorm", "rebase"])
    def test_dataset_loads_with_valid_cases(self, skill):
        cases = load_cases(skill)
        assert len(cases) >= 3
        for c in cases:
            assert c.skill == skill
            assert c.diff.strip() or c.input, c.id
            assert isinstance(c.expect.raw, dict) and c.expect.raw, c.id

    @pytest.mark.parametrize("skill", ["fix", "plan", "brainstorm", "rebase"])
    def test_offline_cli_lists_cases(self, skill, capsys):
        rc = main([skill])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Offline mode" in out

    def test_each_evaluable_skill_has_a_scorer(self):
        for skill in ("fix", "plan", "brainstorm", "rebase", "review"):
            assert get_scorer(skill) is not None


# ===========================================================================
# CLI dispatch (US2/US3)
# ===========================================================================


class TestCliDispatch:
    def test_live_for_skill_without_adapter_reports_no_adapter(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("KOAN_EVAL_LIVE", "1")
        # A skill with a scorer + cases but NO live adapter must report honestly.
        register_scorer(
            "my_team_nolive",
            lambda case, out: CaseResult(case_id=case.id, skill=case.skill),
        )
        try:
            d = tmp_path / "cases"
            d.mkdir()
            (d / "a.json").write_text(json.dumps(
                {"id": "a", "skill": "my_team_nolive", "idea": "x", "expect": {}}
            ))
            rc = main([str(d), "--live"])
            out = capsys.readouterr().out
            assert rc == 2
            assert "no live adapter" in out.lower()
        finally:
            skill_evals.SCORERS.pop("my_team_nolive", None)

    def test_get_live_fn_none_for_excluded_skill(self):
        assert get_live_fn("implement") is None
        assert get_live_fn("mission") is None


# ===========================================================================
# Live adapters (US3) — injected seams, no LLM
# ===========================================================================


class TestLiveAdapters:
    def test_fix_live_pulls_issue_fields_from_input(self):
        case = EvalCase(
            id="f", skill="fix",
            input={"issue_title": "Crash", "issue_body": "Steps"},
            expect=CaseExpect(raw={}),
        )
        captured = {}

        def diag(pp, url, title, body, ctx, skill_dir=None):
            captured.update(title=title, body=body)
            return {"confidence": "HIGH", "hypothesis": "h", "code_paths": ""}

        out = fix_live_fn(case, "/repo", _diagnose=diag)
        assert out["confidence"] == "HIGH"
        assert captured["title"] == "Crash"
        assert captured["body"] == "Steps"

    def test_brainstorm_live_composes_seams(self):
        case = EvalCase(id="b", skill="brainstorm", input={"topic": "auth"}, expect=CaseExpect(raw={}))
        out = brainstorm_live_fn(
            case, "/repo",
            _build=lambda t, sd: f"P[{t}]",
            _run=lambda p, pp: "RAW",
            _parse=lambda r: {"issues": [_issue("x")]},
        )
        assert out["issues"][0]["title"] == "x"

    def test_plan_live_returns_cli_markdown(self):
        case = EvalCase(id="p", skill="plan", input={"idea": "dark mode"}, expect=CaseExpect(raw={}))
        seen = {}

        def run(prompt, pp):
            seen["prompt"] = prompt
            return "Title\n### Summary\nx"

        out = plan_live_fn(case, "/repo", _run=run)
        assert out.startswith("Title")
        assert "dark mode" in seen["prompt"]

    def test_rebase_live_extracts_first_json(self):
        case = EvalCase(
            id="r", skill="rebase",
            input={"pr_title": "T", "pr_body": "B", "pr_diff": "D", "recent_commits": "C"},
            expect=CaseExpect(raw={}),
        )
        out = rebase_live_fn(
            case, "/repo",
            _run=lambda p, pp: 'noise {"already_solved": true, "confidence": "high"} tail',
        )
        assert out["already_solved"] is True
        assert out["confidence"] == "high"


# ===========================================================================
# Exemption guard (US4)
# ===========================================================================


class TestEvalExemption:
    """implement and mission are intentionally NOT evaluable.

    They have no LLM-driven, checkable structured-output contract:
      - implement is orchestration (returns (success, summary), no parse/schema).
      - mission is a pure-Python queue utility (no LLM at all).
    Fabricating a dataset would measure nothing real (constitution VII). Their
    quality bar is upheld by behavioural unit tests instead. This test pins the
    exclusion so it is not silently "fixed".
    """

    def test_exempt_set_is_documented(self):
        assert set(EVAL_EXEMPT_SKILLS) == {"implement", "mission"}

    def test_exempt_skills_have_no_scorer_or_live_fn(self):
        for skill in EVAL_EXEMPT_SKILLS:
            assert skill not in skill_evals.SCORERS, skill
            assert skill not in skill_evals.LIVE_FNS, skill
            assert get_live_fn(skill) is None

    def test_evaluable_core_skills_are_covered(self):
        # The LLM-driven skills with checkable output that this feature covers.
        covered = {"review", "fix", "plan", "brainstorm", "rebase"}
        for skill in covered:
            assert skill in skill_evals.SCORERS, skill


# ---------------------------------------------------------------------------
# Opt-in live evals for the four new skills (real LLM) — skipped by default
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("skill", ["fix", "plan", "brainstorm", "rebase"])
def test_live_eval_for_skill(skill):
    """Run the real pipeline for a skill over its golden dataset.

    SKIPPED unless ``KOAN_EVAL_LIVE=1`` is set. Default suite never invokes the
    Claude subprocess.
    """
    import os

    if not os.environ.get(skill_evals.LIVE_ENV):
        pytest.skip(f"set {skill_evals.LIVE_ENV}=1 to run the live eval")

    project_path = os.getcwd()
    cases = load_cases(skill)
    live_fn = get_live_fn(skill)
    report = run_eval(cases, lambda c: live_fn(c, project_path))
    assert report.total == len(cases)
    # The skill's output contract must hold for every case at least structurally.
    assert report.valid_json_rate >= 0.5
