# Feature Specification: Review Skill Evaluation Harness

**Feature Branch**: `002-review-skill-evals`

**Created**: 2026-07-01

**Status**: Draft

**Input**: User description: "Let's introduce some eval mechanism for the review skill. The goal is to be able to identify regressions and confirm improvements over iterations. Ideally these eval should be run as part of CI tests if possible. Let's keep it simple and add some eval to the core review skill as a starter. So we can then extend to other skills later."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Detect review-quality regressions in CI (Priority: P1)

A maintainer changes the review skill's prompt (or its output parsing) and wants
to know immediately whether the change made reviews *worse* — e.g. the review
prompt now emits invalid JSON, or no longer flags a known SQL-injection pattern
it used to catch. The eval harness ships a golden dataset of review cases and a
deterministic scorer; the offline portion of the suite (case validity, scorer
correctness, recorded-output checks) runs in the normal `fast` CI group on every
PR, so a broken harness or a malformed case fails the build before merge.

**Why this priority**: Regression detection is the primary stated goal. Without
a checked-in dataset + scorer that CI exercises, quality drifts silently between
prompt iterations. This story is the MVP — it is independently valuable even
before the live-LLM eval exists.

**Independent Test**: Run `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_skill_evals.py -v`
and confirm the scorer returns the expected pass/fail for canned buggy, clean,
false-positive, and malformed reviews, and that every checked-in golden case
loads and validates.

**Acceptance Scenarios**:

1. **Given** a golden case describing a diff with a seeded SQL-injection bug,
   **When** the scorer is fed a review JSON that flags that file at
   `critical`/`warning`, **Then** the case result reports `valid_json=True`,
   recall ≥ the expected threshold, and `passed=True`.
2. **Given** the same case, **When** the scorer is fed an LGTM review that misses
   the bug, **Then** the result reports recall below threshold and `passed=False`.
3. **Given** a clean (no-issue) case, **When** the scorer is fed a review that
   invents a finding on a file the case marks `forbidden`, **Then** the result
   reports a precision penalty and `passed=False`.
4. **Given** a malformed (non-JSON / schema-violating) review, **When** scored,
   **Then** the result reports `valid_json=False` and surfaces the schema errors.
5. **Given** the full checked-in case dataset, **When** `load_cases("review")`
   runs, **Then** every case parses, has a non-empty diff, and carries only valid
   expectations.

---

### User Story 2 - Measure live review quality to confirm improvements (Priority: P2)

An operator iterates on the review prompt and wants to *quantify* whether the new
prompt is better or worse than the last one across the golden dataset — recall of
seeded bugs, false-positive discipline on clean code, JSON validity rate. The
harness provides an opt-in **live** mode that actually invokes the review LLM
through the existing review-pipeline seam, scores every case, prints a metrics
report, and compares it against a checked-in `baseline.json` so a run can report
"improved / regressed / unchanged" per metric.

**Why this priority**: This is the "confirm improvements over iterations" half of
the goal. It depends on US1's dataset and scorer, and cannot run in default CI
(the constitution forbids calling the Claude subprocess in tests), so it is an
opt-in, locally- or nightly-run tool.

**Independent Test**: With a model/API-key env set, run
`KOAN_EVAL_LIVE=1 python -m app.skill_evals review --live` and confirm it emits a
per-case + aggregate metrics report and exits non-zero when scores fall below the
checked-in baseline.

**Acceptance Scenarios**:

1. **Given** `KOAN_EVAL_LIVE=1` and a configured model, **When** the live CLI
   runs, **Then** each golden case is scored against the real LLM's review and an
   aggregate report (mean recall, JSON-validity rate, LGTM accuracy, overall
   score) is printed.
2. **Given** no `KOAN_EVAL_LIVE`/model env, **When** the live pytest path or CLI
   runs, **Then** it skips cleanly with an explanatory message (never errors, never
   calls the LLM).
3. **Given** a checked-in `baseline.json`, **When** the live run finishes,
   **Then** the report marks each metric as improved/regressed/unchanged versus the
   baseline and the process exits non-zero on any regression.

---

### User Story 3 - Extend the harness to other skills later (Priority: P3)

A maintainer wants to add the same eval discipline to another LLM-driven skill
(e.g. `fix`, `implement`, `silent-failure-hunter`). The harness is generic on
`(case, output, scorer)` and keyed by skill name, so adding a skill means adding
a scorer adapter + a `cases/` directory — not forking the harness.

**Why this priority**: The goal explicitly asks for extensibility, but only the
`review` skill ships now (YAGNI). This story is satisfied by *structure*, not by
shipping a second skill: a documented registry seam and a "how to add a skill"
note.

**Independent Test**: Confirm `run_eval(cases, review_fn)` works with a second
trivial scorer registered under a fake skill name, proving the dispatch is not
hard-coded to `review`.

**Acceptance Scenarios**:

1. **Given** a scorer registered for skill `my_skill`, **When** `run_eval` is
   called with that skill's cases, **Then** each case is scored by that scorer and
   the report aggregates correctly.
2. **Given** the maintainer doc, **When** a new skill is added per the steps,
   **Then** `load_cases("<new-skill>")` discovers its `cases/` without harness code
   changes.

---

### Edge Cases

- **Live run hits a quota/invocation error on one case** — the harness records
  that case as `errored` (not `passed`), continues the remaining cases, and
  reflects the error count in the aggregate report. A single LLM failure must not
  abort the whole eval.
- **LLM returns valid JSON that violates the review schema** (e.g. wrong severity
  enum, missing field) — scored as `valid_json=False` with the schema errors
  surfaced; never raises.
- **LLM wraps JSON in prose / markdown fences** — the live adapter reuses the
  existing `_parse_review_json` extraction, so fence-wrapped output is handled
  identically to production reviews.
- **A case's `expect` references a file the diff never touches** — treated as an
  unmatched expectation (recall penalty), not a crash; case-load validation warns.
- **`baseline.json` missing or malformed** — live run reports "no baseline" and
  writes the current run as the new baseline rather than failing opaquely.
- **Empty case dataset** — CLI reports "0 cases" and exits 0 (offline tests still
  exercise the scorer directly).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The harness MUST provide a deterministic `score_review(case, review)`
  that, given a review-output dict and an `EvalCase`, returns a structured
  `CaseResult` with at least: `valid_json`, `schema_errors`, `lgtm_correct`,
  `recall`, `precision_penalty`, `score`, and `passed`.
- **FR-002**: Schema validity MUST be computed by reusing
  `app.review_schema.validate_review` — the eval MUST NOT re-implement the review
  JSON schema (single source of truth).
- **FR-003**: Recall MUST measure the fraction of a case's `expect_findings` that
  are matched by the review (matching by file + keyword-in-comment, with optional
  severity-band enforcement).
- **FR-004**: Precision discipline MUST be measurable: a case may declare
  `forbidden_files` and/or `expect_lgtm=True`; flagging a forbidden file or
  returning `lgtm=False` on a clean case lowers the score and flips `passed`.
- **FR-005**: The harness MUST ship a golden dataset under
  `koan/skills/core/review/evals/cases/*.json`, each case carrying a realistic
  diff and structured expectations. At least one case per category: seeded bug
  (expect finding), clean code (expect LGTM), and a precision/false-positive trap.
- **FR-006**: `load_cases(skill)` MUST discover and parse all `cases/*.json` for a
  skill and reject malformed cases with a clear error (case id + reason).
- **FR-007**: `run_eval(cases, review_fn)` MUST apply `review_fn(case)` to each
  case, score it, and aggregate a report; a `review_fn` that returns `None` or
  raises MUST be recorded as `errored`, not abort the run.
- **FR-008**: A live mode MUST invoke the real review pipeline via the existing
  seams (`build_review_prompt` → `_run_claude_review` → `_parse_review_json`) and
  MUST be gated so it never runs without an explicit opt-in env (`KOAN_EVAL_LIVE`)
  — default operation never calls the Claude subprocess.
- **FR-009**: The live mode MUST compare results against a checked-in
  `baseline.json` and report per-metric improved/regressed/unchanged, exiting
  non-zero on regression.
- **FR-010**: A CLI entry (`python -m app.skill_evals`) MUST run the eval for a
  named skill, supporting offline (default) and `--live` modes, and print a
  human-readable report.
- **FR-011**: The scorer dispatch MUST be keyed by skill name via a registry so
  that adding a skill does not require editing `run_eval` (extensibility, US3).
- **FR-012**: All harness code MUST be covered by offline unit tests that never
  call the Claude subprocess (constitution: mock the provider, never invoke it).

### Key Entities *(include if feature involves data)*

- **EvalCase**: a single golden test — `id`, `name`, `skill`, `diff` (the code
  change under review), `description`, and `expect` (`CaseExpect`).
- **CaseExpect**: structured ground truth — `expect_lgtm`, `min_findings`,
  `expect_findings: [FindingExpect]`, `forbidden_files`, `require_valid_json`.
- **FindingExpect**: one expected finding — `file`, `severity_in` (optional allow
  list), `keywords` (comment must contain one to match).
- **CaseResult**: the scored outcome for one case — booleans, recall/precision
  metrics, per-check breakdown, aggregate `score`, `passed`.
- **EvalReport**: aggregate over all `CaseResult`s — counts, mean metrics,
  per-metric delta vs baseline.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every checked-in golden case loads and validates via
  `load_cases("review")` with zero errors; the scorer returns the documented
  pass/fail for each canned review in the offline suite (all green in `fast` CI).
- **SC-002**: The new harness module reaches ≥ 90% line coverage from the offline
  tests, so the PR does not regress the 88% repo coverage baseline (within the
  0.5% CI tolerance).
- **SC-003**: In live mode (when opted in), the harness produces a metrics report
  whose "JSON-validity rate" is 100% for the current review prompt, demonstrating
  the harness can confirm a baseline quality level.
- **SC-004**: A maintainer can add a new golden case by dropping one JSON file
  into `evals/cases/` with no code change, and a new skill by adding one scorer +
  one `cases/` dir — measured by the US3 acceptance test.

## Assumptions

- The review skill's output contract (the JSON schema in `review_schema.py` and
  the prompt in `review.md`) is the stable surface under evaluation; this feature
  evaluates against it, it does not change it.
- CI has no Claude/API credentials, so anything that calls the LLM MUST be opt-in
  and skip-by-default (consistent with the constitution and existing test rules).
- A small, hand-authored golden dataset (≈4–6 cases) is sufficient to start;
  breadth grows incrementally as real review failures are seen. This is the
  "starter" the goal asks for.
- The existing review seams (`build_review_prompt`, `_run_claude_review`,
  `_parse_review_json`, `validate_review`) remain the public entry points the
  harness composes; the harness does not fork or duplicate them.
