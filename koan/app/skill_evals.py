"""Kōan — skill evaluation harness.

A lightweight, deterministic framework for evaluating LLM-driven skills against
a checked-in golden dataset. Shipped first for the ``review`` skill; extensible
to other skills via the :data:`SCORERS` registry.

Two modes:

- **Offline (default, CI-safe):** :func:`score_review` / :func:`run_eval` score
  canned outputs against :class:`EvalCase` expectations. Never calls the Claude
  subprocess — the offline tests exercise the scorer directly with inline
  fixtures, so they run in the ``fast`` CI group.
- **Live (opt-in, ``KOAN_EVAL_LIVE``):** :func:`review_live_fn` invokes the real
  review pipeline through the *existing* seams (``build_review_prompt`` →
  ``_run_claude_review`` → ``_parse_review_json``), scores every case, and
  compares against a checked-in ``baseline.json`` so review-quality regressions
  are caught and improvements are measurable across prompt iterations.

Design contract: ``specs/002-review-skill-evals/`` (spec / plan / research).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_APP_DIR = Path(__file__).resolve().parent
_SKILLS_CORE_DIR = _APP_DIR.parent / "skills" / "core"
_REVIEW_EVALS_DIR = _SKILLS_CORE_DIR / "review" / "evals"

# Env var that must be set to allow any live (LLM-touching) eval. Default
# operation is fully offline so CI never invokes the Claude subprocess.
LIVE_ENV = "KOAN_EVAL_LIVE"

# Severity bands the review schema permits (mirror of review_schema.py). Kept
# here only to validate case expectations at load time, not to re-implement the
# schema (FR-002: validate_review stays the single source of truth).
_VALID_SEVERITIES = ("critical", "warning", "suggestion")

# Core skills deliberately NOT covered by golden-dataset evals, with the reason.
# These have no LLM-driven, checkable structured-output contract:
#   - `implement` is orchestration: run_implement() returns (success, summary),
#     mutates files + opens a PR — there is no structured artifact to score.
#   - `mission` is a pure-Python queue utility (no LLM at all).
# Fabricating a dataset for them would measure nothing real (constitution VII).
# Their quality bar is upheld by behavioural unit tests instead. Pinned by a
# guard test so the exclusion is intentional, not silently absent.
EVAL_EXEMPT_SKILLS = ("implement", "mission")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FindingExpect:
    """One expected finding the review SHOULD surface for a case.

    A review ``file_comments`` entry *matches* this expectation when:

    - its ``file`` equals ``file`` (exact — diffs pin paths), AND
    - its lowercased ``comment`` contains any ``keywords`` stem (when any are
      given), AND
    - its ``severity`` is in ``severity_in`` (when the band is constrained).
    """

    file: str
    keywords: tuple = ()
    severity_in: Optional[tuple] = None


@dataclass
class CaseExpect:
    """Ground-truth expectations for a case's output.

    The typed fields below describe the ``review`` skill's contract. Other
    skills carry their own expectations in :attr:`raw` (the original parsed
    ``expect`` JSON), which each per-skill scorer reads directly — this avoids a
    new dataclass per skill while keeping review's typed access.
    """

    expect_lgtm: Optional[bool] = None
    min_findings: int = 0
    require_valid_json: bool = True
    expect_findings: list = field(default_factory=list)
    forbidden_files: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class EvalCase:
    """A single golden eval case.

    ``diff`` is the ``review`` skill's input (a code diff). Other skills carry
    their inputs in :attr:`input` — e.g. ``issue_title``/``issue_body`` for
    ``fix``, ``idea`` for ``plan``, ``topic`` for ``brainstorm``, PR-context
    keys (``pr_title``/``pr_body``/``pr_diff``/``recent_commits``) for ``rebase``.
    A case MUST carry at least one of ``diff`` or ``input`` (enforced at load).
    """

    id: str
    expect: CaseExpect
    diff: str = ""
    input: dict = field(default_factory=dict)
    name: str = ""
    skill: str = "review"
    description: str = ""


@dataclass
class CaseCheck:
    """One named pass/fail check within a scored case."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    """The scored outcome for one case."""

    case_id: str
    skill: str
    valid_json: bool = False
    schema_errors: list = field(default_factory=list)
    recall: float = 0.0
    lgtm_correct: Optional[bool] = None
    precision_penalty: int = 0
    checks: list = field(default_factory=list)
    score: float = 0.0
    passed: bool = False
    errored: bool = False
    error: str = ""


@dataclass
class EvalReport:
    """Aggregate outcome over a set of scored cases."""

    skill: str
    results: list = field(default_factory=list)

    # -- aggregate views -------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def scored(self) -> list:
        """Non-errored results (the ones with meaningful metrics)."""
        return [r for r in self.results if not r.errored]

    @property
    def errored(self) -> list:
        return [r for r in self.results if r.errored]

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def valid_json_rate(self) -> float:
        s = self.scored
        if not s:
            return 0.0
        return sum(1 for r in s if r.valid_json) / len(s)

    @property
    def mean_recall(self) -> float:
        s = self.scored
        if not s:
            return 0.0
        return sum(r.recall for r in s) / len(s)

    @property
    def mean_score(self) -> float:
        s = self.scored
        if not s:
            return 0.0
        return sum(r.score for r in s) / len(s)

    @property
    def lgtm_accuracy(self) -> float:
        """Fraction of constrained cases that got ``lgtm`` right.

        Only counts cases whose expectation sets ``expect_lgtm``; unconstrained
        cases are excluded. Returns 0.0 when no case constrains it.
        """
        constrained = [r for r in self.scored if r.lgtm_correct is not None]
        if not constrained:
            return 0.0
        return sum(1 for r in constrained if r.lgtm_correct) / len(constrained)

    def metrics(self) -> dict:
        """Numeric metrics dict (for baseline comparison / JSON dump)."""
        return {
            "valid_json_rate": round(self.valid_json_rate, 4),
            "mean_recall": round(self.mean_recall, 4),
            "lgtm_accuracy": round(self.lgtm_accuracy, 4),
            "mean_score": round(self.mean_score, 4),
            "pass_rate": round(self.pass_rate, 4),
        }


# ---------------------------------------------------------------------------
# Scorer registry (extensibility seam — FR-011, US3)
# ---------------------------------------------------------------------------

ScorerFn = Callable[[EvalCase, object], CaseResult]

SCORERS: dict = {}


def register_scorer(skill: str, fn: ScorerFn) -> None:
    """Register a scorer for a skill name."""
    SCORERS[skill] = fn


def get_scorer(skill: str) -> ScorerFn:
    try:
        return SCORERS[skill]
    except KeyError as exc:
        raise ValueError(
            f"no scorer registered for skill {skill!r}; "
            f"known: {sorted(SCORERS)}"
        ) from exc


# ---------------------------------------------------------------------------
# Scoring (deterministic, pure) — research.md §6
# ---------------------------------------------------------------------------

# Blend weights for the continuous score. Validity and recall weighted highest
# because those are the regressions that matter most; ``lgtm`` is neutral (0.5)
# when a case does not constrain it.
_W_VALID = 0.4
_W_RECALL = 0.4
_W_LGTM = 0.2
_PENALTY_FORBIDDEN = 0.25


def _finding_matched(fexp: FindingExpect, comments: list) -> bool:
    """True if any comment entry satisfies this finding expectation."""
    for c in comments:
        if not isinstance(c, dict):
            continue
        if c.get("file") != fexp.file:
            continue
        if fexp.severity_in and c.get("severity") not in fexp.severity_in:
            continue
        if fexp.keywords:
            text = str(c.get("comment", "")).lower()
            if not any(k in text for k in fexp.keywords):
                continue
        return True
    return False


def score_review(case: EvalCase, review: object) -> CaseResult:
    """Score a review-output dict against a case's expectations.

    ``review`` may be a dict, ``None``, or any non-dict (malformed); all paths
    return a :class:`CaseResult` and never raise.
    """
    checks: list = []

    # -- validity (single source of truth: review_schema.validate_review) --
    if isinstance(review, dict):
        from app.review_schema import validate_review

        valid_json, errors = validate_review(review)
    else:
        valid_json, errors = False, ["review output is not a JSON object"]
    checks.append(
        CaseCheck("valid_json", valid_json, "; ".join(errors) if errors else "ok")
    )

    comments = []
    if valid_json and isinstance(review, dict):
        comments = review.get("file_comments") or []

    # -- recall --
    expected = case.expect.expect_findings or []
    if expected:
        matched = sum(1 for fexp in expected if _finding_matched(fexp, comments))
        recall = matched / len(expected)
    else:
        # No findings expected: recall is vacuously satisfied.
        matched, recall = 0, 1.0
    checks.append(
        CaseCheck("recall", recall >= 1.0, f"{matched}/{len(expected)} matched")
    )

    # -- lgtm correctness --
    if case.expect.expect_lgtm is None:
        lgtm_correct: Optional[bool] = None
    elif valid_json and isinstance(review, dict):
        actual = bool(
            (review.get("review_summary") or {}).get("lgtm", False)
        )
        lgtm_correct = actual == case.expect.expect_lgtm
    else:
        # An invalid review can never match a concrete lgtm expectation.
        lgtm_correct = False
    if case.expect.expect_lgtm is not None:
        checks.append(
            CaseCheck(
                "lgtm",
                bool(lgtm_correct),
                f"expected lgtm={case.expect.expect_lgtm}",
            )
        )

    # -- precision (forbidden files flagged as buggy) --
    flagged_files = {c.get("file") for c in comments if isinstance(c, dict)}
    penalty = sum(
        1 for ff in (case.expect.forbidden_files or []) if ff in flagged_files
    )
    checks.append(
        CaseCheck("precision", penalty == 0, f"{penalty} forbidden flag(s)")
    )

    # -- min_findings --
    n_findings = len(comments)
    if case.expect.min_findings:
        checks.append(
            CaseCheck(
                "min_findings",
                n_findings >= case.expect.min_findings,
                f"{n_findings}>={case.expect.min_findings}",
            )
        )

    # -- continuous score --
    lgtm_component = {True: 1.0, False: 0.0, None: 0.5}[lgtm_correct]
    score = (
        _W_VALID * (1.0 if valid_json else 0.0)
        + _W_RECALL * recall
        + _W_LGTM * lgtm_component
        - _PENALTY_FORBIDDEN * penalty
    )
    score = max(0.0, min(1.0, score))

    passed = (
        valid_json
        and recall >= 1.0
        and lgtm_correct in (None, True)
        and penalty == 0
        and n_findings >= case.expect.min_findings
    )

    return CaseResult(
        case_id=case.id,
        skill=case.skill,
        valid_json=valid_json,
        schema_errors=list(errors),
        recall=round(recall, 4),
        lgtm_correct=lgtm_correct,
        precision_penalty=penalty,
        checks=checks,
        score=round(score, 4),
        passed=passed,
    )


# Register the review scorer (default skill).
register_scorer("review", score_review)


# ---------------------------------------------------------------------------
# Multi-skill scorers (fix / plan / brainstorm / rebase)
# ---------------------------------------------------------------------------
#
# Each scorer reuses its skill's existing validator/parser as the single source
# of truth (constitution VI) and never raises — every output shape returns a
# CaseResult. ``valid_json`` generalises to "structurally valid output for this
# skill's contract"; ``recall`` carries the skill's dominant quality fraction.
# ``lgtm_correct`` stays None (review-only concept) so it is excluded from
# lgtm_accuracy.

_VALID_CONFIDENCES = ("HIGH", "MEDIUM", "LOW")
_VALID_PRIORITIES = ("Immediate", "Prototype First", "Research Further", "Skip")
_SCORE_AXES = ("Impact", "Difficulty", "Short-Term ROI", "Long-Term Value")

# Canonical headers a well-formed plan MUST contain (see plan-phases-format.md /
# plan-tail-sections.md). Cases may override via expect.required_sections.
_DEFAULT_PLAN_SECTIONS = (
    "### Summary",
    "### Alternatives Considered",
    "### File Map",
    "### Verification Criteria",
)
# Placeholders that indicate a non-specific, template-y plan (plan.md "No
# Placeholders" section). Cases may override via expect.banned_patterns.
_DEFAULT_BANNED_PLACEHOLDERS = (
    "TODO", "TBD", "FIXME", "<your", "[project name", "XXX", "lorem ipsum",
)


def _coerce_text(output: object) -> str:
    """Coerce any scorer output to text (markdown / fenced JSON / etc.)."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        for key in ("text", "output", "body", "raw", "plan", "markdown"):
            v = output.get(key)
            if isinstance(v, str):
                return v
        return json.dumps(output)
    return str(output)


def _extract_first_json(text: str) -> Optional[dict]:
    """Return the first JSON object in ``text``, or ``None``.

    Tries the flat-object regex first (``\\{[^{}]*\\}``) to mirror production
    ``rebase_pr._check_if_already_solved`` extraction (single source of truth),
    then falls back to a greedy object scan for robustness. Returns the first
    match that parses; ``None`` if none do.
    """
    if not text:
        return None
    for pattern in (r"\{[^{}]*\}", r"\{[\s\S]*\}"):
        for match in re.finditer(pattern, text):
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def _keyword_recall(text: str, keywords) -> tuple:
    """Return (matched_count, total) for lowercased keyword stems in text."""
    kws = [k.lower() for k in (keywords or [])]
    if not kws:
        return 0, 0
    lowered = (text or "").lower()
    matched = sum(1 for k in kws if k in lowered)
    return matched, len(kws)


def _mean_score(checks: list) -> float:
    """Equal-weight mean of check pass-states, clamped to [0, 1]."""
    if not checks:
        return 0.0
    return max(0.0, min(1.0, sum(1.0 for c in checks if c.passed) / len(checks)))


def score_fix(case: EvalCase, output: object) -> CaseResult:
    """Score a fix diagnostic dict against the case's expectations.

    ``output`` is the parsed diagnostic from ``fix_diagnose._parse_diagnostic``
    (``{confidence, hypothesis, code_paths, analysis, raw}``), or malformed.
    """
    checks: list = []
    exp = case.expect.raw or {}

    valid = isinstance(output, dict)
    checks.append(CaseCheck("valid_output", valid, "diagnostic dict" if valid else "not a dict"))

    confidence = hypothesis = code_paths = ""
    if valid:
        confidence = str(output.get("confidence", "") or "").strip().upper()
        hypothesis = str(output.get("hypothesis", "") or "").strip()
        code_paths = str(output.get("code_paths", "") or "").strip()

    conf_ok = confidence in _VALID_CONFIDENCES
    checks.append(CaseCheck("confidence_valid", conf_ok, f"confidence={confidence or '?'}"))

    expected_conf = str(exp.get("expected_confidence", "") or "").upper() or None
    conf_match = True
    if expected_conf:
        conf_match = confidence == expected_conf
        checks.append(CaseCheck("confidence_match", conf_match, f"expected {expected_conf}"))

    hyp_present = bool(hypothesis)
    checks.append(CaseCheck("hypothesis_present", hyp_present, f"{len(hypothesis)} chars"))

    hyp_matched, hyp_total = _keyword_recall(hypothesis, exp.get("hypothesis_keywords"))
    if hyp_total:
        checks.append(
            CaseCheck("hypothesis_recall", hyp_matched == hyp_total, f"{hyp_matched}/{hyp_total}")
        )
    cp_matched, cp_total = _keyword_recall(code_paths, exp.get("code_path_keywords"))
    if cp_total:
        checks.append(
            CaseCheck("code_path_recall", cp_matched == cp_total, f"{cp_matched}/{cp_total}")
        )

    recall = (hyp_matched / hyp_total) if hyp_total else (
        (cp_matched / cp_total) if cp_total else 1.0
    )
    passed = valid and conf_ok and hyp_present and conf_match and all(c.passed for c in checks)
    return CaseResult(
        case_id=case.id,
        skill=case.skill,
        valid_json=valid,
        recall=round(recall, 4),
        checks=checks,
        score=round(_mean_score(checks), 4),
        passed=passed,
    )


def score_plan(case: EvalCase, output: object) -> CaseResult:
    """Score plan markdown against the case's expectations.

    ``output`` is the plan markdown (str), or a dict carrying it. Reuses
    ``parse_plan_progress`` for phase detection (single source of truth).
    """
    from app.dashboard_service.plans import parse_plan_progress

    checks: list = []
    exp = case.expect.raw or {}
    text = _coerce_text(output)

    required = tuple(exp.get("required_sections") or _DEFAULT_PLAN_SECTIONS)
    missing = [h for h in required if h not in text]
    checks.append(
        CaseCheck("required_sections", not missing, f"{len(required) - len(missing)}/{len(required)}")
    )

    min_phases = int(exp.get("min_phases", 1) or 0)
    progress = parse_plan_progress(text)
    n_phases = progress.get("total", 0)
    checks.append(CaseCheck("min_phases", n_phases >= min_phases, f"{n_phases}>={min_phases}"))

    banned = tuple(b.lower() for b in (exp.get("banned_patterns") or _DEFAULT_BANNED_PLACEHOLDERS))
    found_banned = [b for b in banned if b in text.lower()]
    checks.append(CaseCheck("no_banned_placeholders", not found_banned, f"{len(found_banned)} banned"))

    # A plan's first line is its title (plan-title-instruction.md).
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    has_title = bool(first_line) and not first_line.startswith("#")
    checks.append(CaseCheck("title_present", has_title, "first non-blank line is a title"))

    section_recall = (len(required) - len(missing)) / len(required) if required else 1.0
    valid = has_title and not missing
    passed = valid and not found_banned and n_phases >= min_phases
    return CaseResult(
        case_id=case.id,
        skill=case.skill,
        valid_json=valid,
        recall=round(section_recall, 4),
        checks=checks,
        score=round(_mean_score(checks), 4),
        passed=passed,
    )


def score_brainstorm(case: EvalCase, output: object) -> CaseResult:
    """Score a brainstorm decomposition (str or dict) against expectations.

    Reuses ``REQUIRED_ISSUE_SECTIONS`` + ``_validate_issue_bodies`` as the single
    source of truth for the per-issue body *section* contract, and
    ``_parse_decomposition`` for JSON extraction. The priority-enum and
    score-axis content checks are eval-only — production has no validator for
    them (only the prompt + retry reminder document them), so this scorer is
    their first mechanical check.
    """
    from skills.core.brainstorm.brainstorm_runner import (
        REQUIRED_ISSUE_SECTIONS,
        _parse_decomposition,
        _validate_issue_bodies,
    )

    checks: list = []
    exp = case.expect.raw or {}

    # Normalize to a parsed decomposition dict.
    data: Optional[dict]
    if isinstance(output, dict) and isinstance(output.get("issues"), list):
        data = output
    else:
        raw = _coerce_text(output)
        try:
            data = _parse_decomposition(raw) if raw.strip() else None
        except ValueError:
            data = None
    issues_list = data.get("issues") if isinstance(data, dict) else None
    valid = isinstance(issues_list, list) and bool(issues_list)
    checks.append(CaseCheck("valid_json", valid, "decomposition parsed" if valid else "unparseable"))

    issues = issues_list if valid else []
    # Non-dict issue items are malformed data — coerce before the validator
    # (which calls .get on each item) so the scorer never raises. They count
    # against section coverage instead.
    safe_issues = [i for i in issues if isinstance(i, dict)]
    min_issues = int(exp.get("min_issues", 3) or 0)
    max_issues = int(exp.get("max_issues", 8) or 0)
    count_ok = bool(issues) and (not min_issues or len(issues) >= min_issues) and (
        not max_issues or len(issues) <= max_issues
    )
    checks.append(CaseCheck("issue_count", count_ok, f"{len(issues)} issues"))

    # Per-issue required-section coverage (single source of truth).
    malformed = len(issues) - len(safe_issues)
    diagnostics = _validate_issue_bodies(safe_issues) if safe_issues else ["no issues"]
    if malformed:
        diagnostics = [*diagnostics, f"{malformed} malformed issue(s)"]
    sections_ok = valid and not diagnostics
    checks.append(
        CaseCheck("sections_per_issue", sections_ok, f"{len(diagnostics)} diagnostic(s)")
    )

    # Priority enum + score bars are content checks inside each issue body.
    # NOTE: production has no validator for these (only the prompt + retry
    # reminder document them), so these are eval-only content checks.
    priority_ok = True
    bars_ok = True
    for issue in safe_issues:
        body = str(issue.get("body", "") or "")
        prio_line = _section_line(body, "## Priority")
        if not any(p.lower() in prio_line.lower() for p in _VALID_PRIORITIES):
            priority_ok = False
        scores_section = _section_text(body, "## Scores")
        if not all(ax.lower() in scores_section.lower() for ax in _SCORE_AXES):
            bars_ok = False
    if issues:
        checks.append(CaseCheck("priority_valid", priority_ok, "each issue names a priority"))
        checks.append(CaseCheck("score_bars_present", bars_ok, "4 score axes per issue"))

    # Theme recall across issue titles.
    titles = " ".join(str(i.get("title", "")) for i in safe_issues)
    t_matched, t_total = _keyword_recall(titles, exp.get("theme_keywords"))
    if t_total:
        checks.append(
            CaseCheck("theme_recall", t_matched == t_total, f"{t_matched}/{t_total} themes")
        )

    section_recall = 1.0 - (len(diagnostics) / (len(issues) * len(REQUIRED_ISSUE_SECTIONS))) if (
        valid and issues
    ) else 0.0
    recall = (t_matched / t_total) if t_total else section_recall
    passed = valid and count_ok and sections_ok and priority_ok and bars_ok and all(
        c.passed for c in checks
    )
    return CaseResult(
        case_id=case.id,
        skill=case.skill,
        valid_json=valid,
        recall=round(recall, 4),
        checks=checks,
        score=round(_mean_score(checks), 4),
        passed=passed,
    )


def score_rebase(case: EvalCase, output: object) -> CaseResult:
    """Score a rebase already-solved decision (str or dict) against expectations.

    Honors the production rule in ``rebase_pr._check_if_already_solved``: a PR
    is "solved" only when ``already_solved`` is true AND ``confidence == "high"``.
    """
    checks: list = []
    exp = case.expect.raw or {}

    if isinstance(output, dict):
        data = output
    else:
        data = _extract_first_json(_coerce_text(output)) or {}
    valid = bool(data) and "already_solved" in data
    checks.append(CaseCheck("valid_json", valid, "decision JSON parsed" if valid else "unparseable"))

    already_solved = bool(data.get("already_solved", False))
    confidence = str(data.get("confidence", "") or "").strip().lower()
    reasoning = str(data.get("reasoning", "") or "").strip()
    conf_ok = confidence in ("high", "medium", "low")
    checks.append(CaseCheck("confidence_valid", conf_ok, f"confidence={confidence or '?'}"))

    # The production decision: high-confidence positive.
    decided_solved = already_solved and confidence == "high"
    expect_solved = exp.get("expect_solved")
    if expect_solved is not None:
        decision_correct = decided_solved == bool(expect_solved)
        checks.append(
            CaseCheck("decision_correct", decision_correct, f"expected_solved={expect_solved}")
        )
    else:
        decision_correct = True

    reasoning_ok = bool(reasoning)
    checks.append(CaseCheck("reasoning_present", reasoning_ok, f"{len(reasoning)} chars"))

    recall = 1.0 if decision_correct else 0.0
    passed = valid and conf_ok and reasoning_ok and decision_correct and all(
        c.passed for c in checks
    )
    return CaseResult(
        case_id=case.id,
        skill=case.skill,
        valid_json=valid,
        recall=round(recall, 4),
        checks=checks,
        score=round(_mean_score(checks), 4),
        passed=passed,
    )


def _section_text(body: str, header: str) -> str:
    """Return the content under a ``## Header`` until the next ``## `` heading."""
    idx = body.find(header)
    if idx < 0:
        return ""
    start = idx + len(header)
    nxt = body.find("\n## ", start)
    return body[start:] if nxt < 0 else body[start:nxt]


def _section_line(body: str, header: str) -> str:
    """Return the first non-empty content line under a ``## Header``."""
    return next(
        (ln.strip() for ln in _section_text(body, header).splitlines() if ln.strip()),
        "",
    )


register_scorer("fix", score_fix)
register_scorer("plan", score_plan)
register_scorer("brainstorm", score_brainstorm)
register_scorer("rebase", score_rebase)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _errored_result(case: EvalCase, error: str) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        skill=case.skill,
        errored=True,
        error=error,
        checks=[CaseCheck("invoke", False, error)],
    )


def run_eval(cases: list, review_fn: Callable[[EvalCase], Optional[dict]]) -> EvalReport:
    """Score every case via ``review_fn`` and aggregate a report.

    ``review_fn(case)`` must return a review-output dict (or ``None``). A return
    of ``None`` or a raised exception records the case as ``errored`` and the
    run continues — a single failure never aborts the eval (FR-007).
    """
    if not cases:
        return EvalReport(skill="mixed", results=[])

    results: list = []
    for case in cases:
        scorer = get_scorer(case.skill)
        try:
            review = review_fn(case)
        except Exception as exc:  # eval must survive any review_fn failure
            results.append(_errored_result(case, str(exc)))
            continue
        try:
            # A scorer must never raise (never-raise contract), but wrap it so a
            # bug in one scorer records one errored case instead of aborting the
            # whole run (FR-007: a single failure never aborts the eval).
            results.append(scorer(case, review))
        except Exception as exc:  # pragma: no cover - defensive
            results.append(_errored_result(case, f"scorer raised: {exc}"))

    skills = {c.skill for c in cases}
    skill = next(iter(skills)) if len(skills) == 1 else "mixed"
    return EvalReport(skill=skill, results=results)


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------


def _resolve_cases_dir(skill_or_dir: str) -> Path:
    """Resolve a skill name or explicit dir to its ``cases/`` directory."""
    candidate = Path(skill_or_dir)
    if candidate.is_dir():
        return candidate
    # Treat as a skill name under skills/core/<name>/evals/cases.
    return _SKILLS_CORE_DIR / skill_or_dir / "evals" / "cases"


def _case_from_dict(data: dict, source: str) -> EvalCase:
    """Build an :class:`EvalCase` from parsed JSON, validating structure."""
    cid = data.get("id")
    if not cid:
        raise ValueError(f"{source}: missing required 'id'")
    diff = data.get("diff", "")
    if not isinstance(diff, str):
        raise ValueError(f"{source} ({cid}): 'diff' must be a string")
    # Skill-specific inputs: every top-level key that is not reserved flows into
    # EvalCase.input (e.g. issue_title/issue_body for fix, idea for plan, topic
    # for brainstorm, pr_* keys for rebase). review uses `diff` and has none.
    reserved = {"id", "name", "skill", "description", "diff", "expect"}
    inputs = {k: v for k, v in data.items() if k not in reserved}
    if not diff.strip() and not inputs:
        raise ValueError(
            f"{source} ({cid}): a case needs a non-empty 'diff' and/or at "
            f"least one skill-input key (found neither)"
        )
    raw_expect = data.get("expect")
    if not isinstance(raw_expect, dict):
        raise ValueError(f"{source} ({cid}): 'expect' must be an object")

    expect = CaseExpect(
        expect_lgtm=raw_expect.get("expect_lgtm"),
        min_findings=int(raw_expect.get("min_findings", 0) or 0),
        require_valid_json=bool(raw_expect.get("require_valid_json", True)),
        forbidden_files=list(raw_expect.get("forbidden_files") or []),
        raw=dict(raw_expect),
    )

    findings: list = []
    for i, f in enumerate(raw_expect.get("expect_findings") or []):
        if not isinstance(f, dict) or not f.get("file"):
            raise ValueError(
                f"{source} ({cid}): expect_findings[{i}] needs a 'file'"
            )
        sev = f.get("severity_in")
        if sev is not None:
            sev_list = list(sev)
            bad = [s for s in sev_list if s not in _VALID_SEVERITIES]
            if bad:
                raise ValueError(
                    f"{source} ({cid}): expect_findings[{i}] invalid "
                    f"severity_in {bad}; allowed {list(_VALID_SEVERITIES)}"
                )
        keywords = tuple(k.lower() for k in (f.get("keywords") or []))
        findings.append(
            FindingExpect(
                file=f["file"], keywords=keywords, severity_in=tuple(sev) if sev else None
            )
        )
    expect.expect_findings = findings

    return EvalCase(
        id=cid,
        diff=diff,
        input=inputs,
        expect=expect,
        name=data.get("name") or cid,
        skill=data.get("skill") or "review",
        description=data.get("description") or "",
    )


def load_cases(skill_or_dir: str) -> list:
    """Discover and parse all ``cases/*.json`` for a skill (FR-006).

    ``skill_or_dir`` is either a skill name (resolved to
    ``skills/core/<name>/evals/cases``) or an explicit directory path. Raises
    ``ValueError`` with the offending file + reason on any malformed case.
    """
    cases_dir = _resolve_cases_dir(skill_or_dir)
    if not cases_dir.is_dir():
        raise ValueError(f"cases directory not found: {cases_dir}")
    files = sorted(cases_dir.glob("*.json"))
    # baseline.json (and other non-case JSON) live in the parent evals/ dir,
    # not cases/, so we don't filter it out here.
    if not files:
        raise ValueError(f"no *.json cases found in {cases_dir}")
    cases: list = []
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"{fp.name}: invalid JSON ({exc})") from exc
        cases.append(_case_from_dict(data, source=fp.name))
    return cases


# ---------------------------------------------------------------------------
# Live adapter (opt-in) — composes the existing review seams (FR-008)
# ---------------------------------------------------------------------------


def build_minimal_review_context(case: EvalCase) -> dict:
    """Build the PR-context dict ``build_review_prompt`` requires from a case."""
    return {
        "title": case.name or case.id,
        "author": "eval",
        "branch": "eval-branch",
        "base": "main",
        "body": case.description or "",
        "diff": case.diff,
        "review_comments": "",
        "reviews": "",
        "issue_comments": "",
    }


def review_live_fn(
    case: EvalCase,
    project_path: str,
    *,
    _build=None,
    _run=None,
    _parse=None,
) -> Optional[dict]:
    """Invoke the real review pipeline on a case and return its review dict.

    Composes ``build_review_prompt`` → ``_run_claude_review`` →
    ``_parse_review_json``. The three seams are injectable (``_build``/``_run``
    /``_parse``) so the logic is unit-testable without the Claude subprocess;
    in live mode they default to the real functions.

    ``project_path`` is passed to ``_run_claude_review`` (the CLI cwd, so the
    reviewer can Read/Grep the codebase like a real review) but NOT to
    ``build_review_prompt`` (memory injection disabled), keeping the eval
    hermetic — it does not depend on the operator's ``learnings.md``.
    """
    from app.review_runner import (
        _parse_review_json,
        _run_claude_review,
        build_review_prompt,
    )

    build = _build or build_review_prompt
    run = _run or _run_claude_review
    parse = _parse or _parse_review_json

    skill_dir = _SKILLS_CORE_DIR / case.skill
    context = build_minimal_review_context(case)
    prompt, _coverage_note = build(context, skill_dir=skill_dir, project_path=None)
    raw, error = run(prompt, project_path)
    if error:
        raise RuntimeError(f"review invocation failed: {error}")
    return parse(raw)


# ---------------------------------------------------------------------------
# Live-adapter registry (FR-005, US3)
# ---------------------------------------------------------------------------

# Maps skill → the live-adapter attribute name on THIS module. Values are names
# (not bound functions) resolved via get_live_fn at call time, so a test can
# monkeypatch ``skill_evals.<adapter>`` and the CLI observes the stub — matching
# the existing review live-fn test. Adapters are added per skill below.
LIVE_FNS: dict = {
    "review": "review_live_fn",
}


def get_live_fn(skill: str) -> Optional[Callable]:
    """Resolve a skill's live adapter at call time, or ``None`` if it has none.

    Returning ``None`` (rather than guessing) lets the CLI report "no live
    adapter" honestly for skills without one (FR-005).
    """
    name = LIVE_FNS.get(skill)
    if name is None:
        return None
    return globals().get(name)


def _default_run(prompt: str, project_path: str, *, allowed_tools=None,
                 model_key: str = "chat", max_turns: int = 5,
                 timeout: int = 120) -> str:
    """Live-mode default CLI runner (only invoked under KOAN_EVAL_LIVE).

    Thin wrapper over ``run_command_streaming`` so each adapter states its
    tool/model needs explicitly instead of duplicating the call. Never reached
    by the offline suite.
    """
    from app.cli_provider import run_command_streaming

    return run_command_streaming(
        prompt,
        project_path,
        allowed_tools=list(allowed_tools or []),
        model_key=model_key,
        max_turns=max_turns,
        timeout=timeout,
    )


def fix_live_fn(case: EvalCase, project_path: str, *, _diagnose=None) -> Optional[dict]:
    """Invoke the real fix diagnostic on a case, returning the parsed dict.

    Composes ``fix_diagnose.run_diagnostic``. ``_diagnose`` is injectable so the
    wiring (issue fields pulled from ``case.input``) is unit-testable without
    the Claude subprocess; in live mode it defaults to the real function, which
    returns a LOW-confidence diagnostic on any CLI failure (never raises).
    """
    from skills.core.fix.fix_diagnose import run_diagnostic

    inp = case.input
    issue_title = inp.get("issue_title") or case.name or case.id
    issue_body = inp.get("issue_body") or case.description or ""
    diagnose = _diagnose or run_diagnostic
    return diagnose(
        project_path, "", issue_title, issue_body, "",
        skill_dir=_SKILLS_CORE_DIR / "fix",
    )


def brainstorm_live_fn(
    case: EvalCase, project_path: str, *, _build=None, _run=None, _parse=None
) -> Optional[dict]:
    """Invoke the real brainstorm decomposition on a case, returning parsed JSON.

    Composes ``_build_decompose_prompt`` → ``_call_claude_with_prompt`` →
    ``_parse_decomposition``. All three seams are injectable for offline tests.
    A parse failure raises ``ValueError`` (recorded as an errored case by
    :func:`run_eval`).
    """
    from skills.core.brainstorm.brainstorm_runner import (
        _build_decompose_prompt,
        _call_claude_with_prompt,
        _parse_decomposition,
    )

    skill_dir = _SKILLS_CORE_DIR / "brainstorm"
    topic = case.input.get("topic") or case.description or case.name
    build = _build or _build_decompose_prompt
    run = _run or _call_claude_with_prompt
    parse = _parse or _parse_decomposition
    prompt = build(topic, skill_dir)
    raw = run(prompt, project_path)
    return parse(raw or "")


def plan_live_fn(case: EvalCase, project_path: str, *, _run=None) -> str:
    """Invoke the real plan pipeline on a case, returning the plan markdown.

    Builds the plan prompt via ``load_prompt_or_skill`` and runs the CLI.
    ``_run`` is injectable for offline tests; in live mode it defaults to a
    read-only planning invocation.
    """
    from app.prompts import load_prompt_or_skill

    skill_dir = _SKILLS_CORE_DIR / "plan"
    idea = case.input.get("idea") or case.description or case.name
    prompt = load_prompt_or_skill(
        skill_dir, "plan", IDEA=idea, CONTEXT="", PROJECT_MEMORY=""
    )
    run = _run or _default_run
    if _run is not None:
        return run(prompt, project_path)
    return run(
        prompt, project_path,
        allowed_tools=["Glob", "Grep", "Read"],
        model_key="mission", max_turns=8, timeout=180,
    )


def rebase_live_fn(
    case: EvalCase, project_path: str, *, _run=None, _parse=None
) -> Optional[dict]:
    """Invoke the real rebase already-solved check on a case, returning the JSON.

    Builds the ``already_solved`` prompt and runs the CLI (no tools, matching
    ``rebase_pr._check_if_already_solved``), then extracts the first JSON
    object. ``_run``/``_parse`` are injectable for offline tests.
    """
    from app.prompts import load_prompt_or_skill

    skill_dir = _SKILLS_CORE_DIR / "rebase"
    inp = case.input
    prompt = load_prompt_or_skill(
        skill_dir, "already_solved",
        TITLE=inp.get("pr_title") or case.name or "",
        BRANCH=inp.get("pr_branch") or "eval-branch",
        BASE=inp.get("pr_base") or "main",
        BODY=inp.get("pr_body") or case.description or "",
        DIFF=inp.get("pr_diff") or "",
        RECENT_COMMITS=inp.get("recent_commits") or "",
    )
    run = _run or _default_run
    parse = _parse or _extract_first_json
    if _run is not None:
        raw = run(prompt, project_path)
    else:
        raw = run(prompt, project_path, allowed_tools=[], model_key="review",
                  max_turns=3, timeout=120)
    return parse(raw or "")


# Register the live adapters. Values are attribute names resolved at call time
# by get_live_fn (see LIVE_FNS comment above).
LIVE_FNS.update({
    "fix": "fix_live_fn",
    "plan": "plan_live_fn",
    "brainstorm": "brainstorm_live_fn",
    "rebase": "rebase_live_fn",
})


# ---------------------------------------------------------------------------
# Baseline comparison (FR-009)
# ---------------------------------------------------------------------------

_EPS = 1e-9


def _metric_status(current: float, baseline: object) -> str:
    if baseline is None:
        return "new"
    try:
        b = float(baseline)
    except (TypeError, ValueError):
        return "new"
    if current > b + _EPS:
        return "improved"
    if current < b - _EPS:
        return "regressed"
    return "unchanged"


def compare_to_baseline(report: EvalReport, baseline_path: Optional[str]) -> dict:
    """Compare a report's metrics to a checked-in baseline.

    Returns ``{status, per_metric, current, baseline}``. A missing/malformed
    baseline yields ``status="no_baseline"`` (the caller may write the current
    run as the new baseline via :func:`write_baseline`).
    """
    current = report.metrics()
    if not baseline_path or not Path(baseline_path).exists():
        return {"status": "no_baseline", "per_metric": {}, "current": current, "baseline": {}}
    try:
        data = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "no_baseline", "per_metric": {}, "current": current, "baseline": {}}
    base = data.get("metrics", {}) if isinstance(data, dict) else {}
    per_metric = {k: _metric_status(v, base.get(k)) for k, v in current.items()}
    if any(s == "regressed" for s in per_metric.values()):
        status = "regressed"
    elif any(s == "improved" for s in per_metric.values()):
        status = "improved"
    else:
        status = "unchanged"
    return {"status": status, "per_metric": per_metric, "current": current, "baseline": base}


def write_baseline(report: EvalReport, baseline_path: str) -> None:
    """Persist a report's metrics as the new baseline."""
    payload = {"skill": report.skill, "metrics": report.metrics(), "updated": _today()}
    Path(baseline_path).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _today() -> str:
    """ISO date string (factored out so tests can monkeypatch)."""
    import datetime

    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_report(report: EvalReport, comparison: Optional[dict] = None) -> str:
    """Render a human-readable eval report."""
    lines = [f"# Eval report — skill: {report.skill}"]
    lines.append(f"cases: {report.total}  passed: {sum(1 for r in report.results if r.passed)}")
    if report.errored:
        lines.append(f"errored: {len(report.errored)}")
    lines.append("")
    lines.append("## Aggregate metrics")
    for k, v in report.metrics().items():
        lines.append(f"  {k}: {v}")
    if comparison:
        lines.append("")
        lines.append(f"## Baseline: {comparison.get('status')}")
        for k, s in (comparison.get("per_metric") or {}).items():
            lines.append(f"  {k}: {s}")
    lines.append("")
    lines.append("## Per-case")
    for r in report.results:
        if r.errored:
            lines.append(f"  [{r.case_id}] ERRORED — {r.error}")
            continue
        flag = "PASS" if r.passed else "FAIL"
        checks = ", ".join(
            f"{c.name}={'y' if c.passed else 'n'}" for c in r.checks
        )
        lines.append(
            f"  [{r.case_id}] {flag}  score={r.score}  recall={r.recall}  ({checks})"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _is_live_enabled() -> bool:
    import os

    return bool(os.environ.get(LIVE_ENV))


def main(argv: Optional[list] = None) -> int:
    """CLI entry: ``python -m app.skill_evals <skill> [--live] [...]``."""
    import argparse
    import os

    parser = argparse.ArgumentParser(
        prog="app.skill_evals",
        description="Evaluate an LLM skill against its golden dataset.",
    )
    parser.add_argument("skill", help="skill name (e.g. review) or a cases/ dir")
    parser.add_argument(
        "--live",
        action="store_true",
        help="invoke the skill's real pipeline via its live adapter (requires KOAN_EVAL_LIVE)",
    )
    parser.add_argument(
        "--project-path",
        default=None,
        help="project path passed to the live adapter (codebase context)",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="path to baseline.json (default: <skill>/evals/baseline.json)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write the current run's metrics as the new baseline",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.skill)
    skill = cases[0].skill if cases else args.skill

    if not args.live:
        # Offline: validate the dataset and report its shape (the scorer itself
        # is exercised by the pytest suite with inline fixtures).
        print(f"Loaded {len(cases)} case(s) for skill '{skill}'.")
        for c in cases:
            print(f"  - {c.id}: {c.name}")
        print("Offline mode: dataset valid. Use --live (KOAN_EVAL_LIVE) to score.")
        return 0

    if not _is_live_enabled():
        print(
            f"Refusing --live without {LIVE_ENV}=1: live eval calls the Claude "
            "subprocess and must be opt-in."
        )
        return 2

    project_path = args.project_path or os.getcwd()
    baseline = args.baseline or str(_SKILLS_CORE_DIR / skill / "evals" / "baseline.json")

    live_fn = get_live_fn(skill)
    if live_fn is None:
        print(
            f"No live adapter for skill {skill!r}; this skill is offline-only "
            f"(evaluates canned outputs via its scorer)."
        )
        return 2
    report = run_eval(cases, lambda c: live_fn(c, project_path))
    comparison = compare_to_baseline(report, baseline)

    if args.update_baseline:
        write_baseline(report, baseline)
        print(f"Baseline written to {baseline}")

    print(format_report(report, comparison))

    # Exit non-zero on regression (FR-009) or any hard error.
    if comparison.get("status") == "regressed":
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
