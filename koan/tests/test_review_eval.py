"""Eval harness for the ``/review`` skill — CI-safe (no model invocation).

Three dimensions, all plain pytest so they run in the existing ``fast`` CI group.
See ``skills/core/review/eval/PLAN.md`` for the full rationale.

  A. ``TestReviewPromptContract``  — every review prompt keeps its load-bearing
     contract (``{@include}`` partials resolve; a prompt that describes the
     output schema cannot half-describe it). This is the regression net for
     prompt drift, the #1 unprotected risk for a prompt-driven skill.
  B. ``TestGoldenReviews``         — curated fixtures stay schema-valid and
     invariant-clean. Regression anchors for any schema/invariant change.
  C. ``TestSemanticInvariants``    — ``evaluate_review`` flags reviews that are
     schema-valid but semantically broken, plus the structural-fail early return.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.prompts import load_skill_prompt
from app.review_eval import evaluate_review
from app.review_schema import validate_review

REVIEW_SKILL_DIR = Path(__file__).parent.parent / "skills" / "core" / "review"
PROMPTS_DIR = REVIEW_SKILL_DIR / "prompts"
FIXTURES_DIR = REVIEW_SKILL_DIR / "eval" / "fixtures"

# Matches an unresolved ``{@include name}`` directive (load_skill_prompt leaves
# missing partials as-is). Kept local so the test does not depend on a private
# regex in app.prompts.
_INCLUDE_RE = re.compile(r"\{@include\s+[\w-]+\}")

PROMPT_FILES = sorted(PROMPTS_DIR.glob("*.md"))


# ---------------------------------------------------------------------------
# A. Prompt-contract eval
# ---------------------------------------------------------------------------


class TestReviewPromptContract:
    """The review prompts ARE the skill. Protect them as a contract.

    These tests fail when an edit silently degrades a prompt: a renamed/deleted
    partial, or an output-schema section half-deleted. They do NOT assert on
    prompt prose — only on the load-bearing structure.
    """

    def test_prompt_files_collected(self):
        """Guard against a vacuous contract eval.

        ``PROMPT_FILES`` is computed at collection time, so a missing/empty
        prompts dir collects zero parametrized cases — the in-body skip would
        never fire and the eval would silently pass with nothing checked. Fail
        loudly instead so a misconfigured/renamed prompts dir surfaces.
        """
        assert PROMPT_FILES, (
            f"no prompts found in {PROMPTS_DIR} — the prompt-contract eval "
            "ran zero cases"
        )

    @pytest.mark.parametrize("prompt_file", PROMPT_FILES, ids=lambda p: p.stem)
    def test_all_includes_resolve(self, prompt_file):
        """Every ``{@include}`` directive resolves to non-empty content.

        A broken partial (rename, deletion, typo) is the most common silent
        regression — the prompt still loads but ships degraded instructions.
        """
        rendered = load_skill_prompt(REVIEW_SKILL_DIR, prompt_file.stem)
        leftovers = _INCLUDE_RE.findall(rendered)
        assert not leftovers, (
            f"{prompt_file.name}: unresolved {len(leftovers)} include(s): {leftovers}. "
            "A partial was renamed, deleted, or misnamed."
        )

    @pytest.mark.parametrize("prompt_file", PROMPT_FILES, ids=lambda p: p.stem)
    def test_json_output_contract_intact(self, prompt_file):
        """JSON-output prompts keep their load-bearing output contract when rendered.

        Asserts two markers chosen because they are NOT redundantly supplied, so
        their absence is a genuine silent regression:

        * the ``valid JSON`` directive — lives in the prompt body only (no
          partial supplies it). Without it the model may emit prose/markdown and
          break the JSON parser downstream.
        * all three severities ``critical`` / ``warning`` / ``suggestion`` —
          arrive via the output-rules partial. Dropping one degrades severity
          calibration.

        Self-maintaining: focused-pass prompts that never describe the JSON
        output are exempt (they augment the base review prompt at run time).
        """
        rendered = load_skill_prompt(REVIEW_SKILL_DIR, prompt_file.stem)
        if "file_comments" not in rendered:
            pytest.skip(f"{prompt_file.name}: does not emit the review JSON schema")
        assert "valid JSON" in rendered, (
            f"{prompt_file.name}: missing the 'valid JSON' output directive — "
            "the model is no longer told to emit pure JSON, risking parse failures."
        )
        for sev in ("critical", "warning", "suggestion"):
            assert sev in rendered, (
                f"{prompt_file.name}: severity '{sev}' dropped from the rendered "
                "prompt — severity calibration is incomplete."
            )


# ---------------------------------------------------------------------------
# B. Golden-output anchors
# ---------------------------------------------------------------------------


class TestGoldenReviews:
    """Curated fixtures embodying 'a well-formed review'.

    Each must be schema-valid AND pass the semantic eval. Any change to the
    schema or invariants that breaks a golden output fails here — these are the
    anchors that make 'confirm improvements over iterations' meaningful.
    """

    def test_fixtures_collected(self):
        """Guard against a vacuous golden eval.

        The golden tests are parametrized over the fixture glob at collection
        time, so a missing/empty fixtures dir collects zero cases — the eval
        would silently pass with nothing checked. Fail loudly instead so a lost
        fixtures directory is a loud failure.
        """
        assert sorted(FIXTURES_DIR.glob("*.json")), (
            f"no fixtures found in {FIXTURES_DIR} — the golden-output eval "
            "ran zero cases"
        )

    @pytest.mark.parametrize(
        "fixture", sorted(FIXTURES_DIR.glob("*.json")), ids=lambda p: p.stem
    )
    def test_golden_is_schema_valid(self, fixture):
        data = json.loads(fixture.read_text())
        ok, errors = validate_review(data)
        assert ok, f"{fixture.name}: schema-invalid: {errors}"

    @pytest.mark.parametrize(
        "fixture", sorted(FIXTURES_DIR.glob("*.json")), ids=lambda p: p.stem
    )
    def test_golden_is_invariant_clean(self, fixture):
        data = json.loads(fixture.read_text())
        report = evaluate_review(data)
        assert report.passed, (
            f"{fixture.name}: invariant violations: {report.errors}"
        )


# ---------------------------------------------------------------------------
# C. Semantic-invariant + adversarial eval
# ---------------------------------------------------------------------------


def _review(*, file_comments=None, lgtm=False, checklist=None):
    """Build a minimal schema-valid review object for invariant tests."""
    return {
        "file_comments": file_comments or [],
        "review_summary": {
            "lgtm": lgtm,
            "summary": "stub summary",
            "checklist": checklist or [],
        },
        "comment_replies": [],
    }


def _finding(severity="warning", file="src/x.py"):
    return {
        "file": file,
        "line_start": 1,
        "line_end": 1,
        "severity": severity,
        "title": "stub",
        "comment": "stub",
        "code_snippet": "",
    }


# (name, payload, kind) — kind in {"error:<token>", "warning", "schema-invalid"}.
# error cases are schema-valid (isolating the semantic layer) and must FAIL eval
# with the named token in the error text.
ADVERSARIAL = [
    (
        "critical_finding_with_lgtm_true",
        _review(file_comments=[_finding("critical")], lgtm=True),
        "error:lgtm",
    ),
    (
        "warning_finding_with_lgtm_true",
        _review(file_comments=[_finding("warning")], lgtm=True),
        "error:lgtm",
    ),
    (
        "dangling_finding_ref",
        _review(
            file_comments=[_finding("warning")],
            lgtm=False,
            checklist=[{"item": "x", "passed": False, "finding_refs": [5]}],
        ),
        "error:out of range",
    ),
    (
        "negative_finding_ref",
        _review(
            file_comments=[_finding("warning")],
            lgtm=False,
            checklist=[{"item": "x", "passed": False, "finding_refs": [-1]}],
        ),
        "error:out of range",
    ),
    (
        "empty_comments_but_not_lgtm",
        _review(file_comments=[], lgtm=False),
        "warning",
    ),
    (
        "structurally_invalid_root",
        ["not", "an", "object"],
        "schema-invalid",
    ),
]


class TestSemanticInvariants:
    """``evaluate_review`` must catch what the schema cannot.

    Each error case is schema-valid (passes validate_review) so the only thing
    flagged is the semantic violation — proving the eval layer adds value beyond
    structural validation.
    """

    @pytest.mark.parametrize("name,payload,kind", ADVERSARIAL, ids=[c[0] for c in ADVERSARIAL])
    def test_adversarial(self, name, payload, kind):
        report = evaluate_review(payload)

        if kind == "warning":
            # Schema-valid, no hard error, but the soft signal must surface.
            assert report.passed, f"{name}: should pass (warning only)"
            assert report.warnings, f"{name}: expected a warning, got none"
            return

        if kind == "schema-invalid":
            assert not report.passed, f"{name}: structural break must fail"
            assert any(e.startswith("schema:") for e in report.errors), (
                f"{name}: expected schema-prefixed errors, got {report.errors}"
            )
            return

        # kind == "error:<token>" — isolate the semantic layer: confirm the
        # payload is schema-valid on its own, then that the eval flags it.
        token = kind.split(":", 1)[1]
        ok, _ = validate_review(payload)
        assert ok, f"{name}: must be schema-valid to isolate the semantic test"
        assert not report.passed, f"{name}: expected a semantic violation"
        joined = " ".join(report.errors).lower()
        assert token in joined, f"{name}: expected '{token}' in errors, got {report.errors}"

    def test_clean_review_passes(self):
        """A well-formed review with a warning finding and lgtm=false passes cleanly."""
        report = evaluate_review(
            _review(
                file_comments=[_finding("warning")],
                lgtm=False,
                checklist=[{"item": "x", "passed": False, "finding_refs": [0]}],
            )
        )
        assert report.passed
        assert report.errors == []
        assert report.warnings == []

    def test_int_like_float_finding_ref_accepted(self):
        """JSON ints arrive as floats; a 0.0 ref must count as index 0, not flagged."""
        report = evaluate_review(
            _review(
                file_comments=[_finding("warning")],
                lgtm=False,
                checklist=[{"item": "x", "passed": False, "finding_refs": [0.0]}],
            )
        )
        assert report.passed, report.errors
