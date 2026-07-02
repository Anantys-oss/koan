# Feature Specification: Core Skill Evaluation Harness (multi-skill)

**Feature Branch**: `003-core-skill-evals`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "PR #2265 introduced eval for the `/review` core skill. Follow the same pattern to introduce evals for the main core skills: `/fix`, `/plan`, `/implement`, `/mission`, `/brainstorm`, `/rebase`. Each should be covered by eval and run as part of unit tests. New core skills or updates to skills should also contain evals and validate evals. Store the rule in the right CLAUDE.md."

## Scope decision (read first)

The review harness (`specs/002-review-skill-evals/`) is generic on
`(case, output, scorer)` **only for skills that are LLM-driven AND emit a
checkable structured output** (its own US3 + `specs/components/skills.md`
checklist item 7). Auditing the six named skills against that bar:

| Skill | LLM-driven? | Checkable output contract? | In this feature? |
|---|---|---|---|
| `fix` | yes — `fix_diagnose.run_diagnostic` | yes — `{confidence, hypothesis, code_paths, analysis}` (regex-parsed) | ✅ evaluable |
| `plan` | yes — Claude mission | yes — markdown: `### Summary`, `### Alternatives Considered`, `### File Map`, `#### Phase N:`, tail sections; banned placeholders | ✅ evaluable |
| `brainstorm` | yes — Claude decomposition | yes — JSON `{issues[]}`; each body carries the 7 `REQUIRED_ISSUE_SECTIONS`; priority enum; score bars | ✅ evaluable (strongest analog to review) |
| `rebase` | yes — `_check_if_already_solved` | yes — JSON decision `{already_solved, confidence, resolved_by}` | ✅ evaluable |
| `implement` | yes — but orchestration | **no** — `run_implement()` returns `(success, summary)`; mutates files + opens a PR; zero parse/schema logic | ❌ out of scope (see below) |
| `mission` | **no** — pure-Python queue utility | **no** — `handler.py` calls `insert_pending_mission`; no LLM anywhere | ❌ out of scope (see below) |

**`implement` and `mission` are deliberately NOT given golden-dataset
evals.** Forcing a fabricated "structured output" onto an orchestration skill
(`implement`) and a queue utility (`mission`) would measure nothing real — it
would be theatre, not measurement, violating the constitution's Principle VII
(Honest Reporting) and the harness's own design intent. Their quality bar is
already upheld by behavioural unit tests (`test_implement_runner.py` — 131
tests; ~15 `test_mission_*.py` files). They are documented as excluded with
rationale in the skills spec and the runbook, and a guard test asserts the
exclusion is intentional. *The agent proposes; the human decides* — a reviewer
who disagrees can add fabricated cases, but this feature will not ship them.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Offline evals for the four evaluable skills, run in CI (Priority: P1)

A maintainer changes a prompt or parser for `fix`, `plan`, `brainstorm`, or
`rebase` and wants the build to fail fast if the change breaks the skill's
output contract — e.g. `brainstorm` now emits issues missing the `## Scores`
section, or `plan` now omits `### Verification Criteria`, or `fix`'s diagnostic
no longer parses a confidence. Each skill ships a golden dataset + a
deterministic scorer; the offline suite (case validity, scorer correctness,
recorded-output checks) runs in the `fast` CI group on every PR, so a broken
harness or malformed case fails the build before merge.

**Why this priority**: Regression detection is the primary stated goal ("run as
part of unit tests"). Without a checked-in dataset + scorer that CI exercises,
output-contract drift goes unnoticed between prompt iterations. This is the MVP
— independently valuable even before any live-LLM eval exists.

**Independent Test**:
`KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_skill_evals.py -v` →
the four new scorers return the expected pass/fail for canned good/bad outputs,
and every checked-in golden case for the four skills loads and validates.

**Acceptance Scenarios**:

1. **Given** a `brainstorm` case expecting 3–8 issues each with all 7 required
   sections, **When** the scorer is fed a decomposition JSON that satisfies
   that, **Then** the result reports `valid_json=True`, section-recall ≥
   threshold, and `passed=True`.
2. **Given** the same case, **When** the scorer is fed a decomposition missing
   `## Priority` on one issue, **Then** the result reports the section check
   failed and `passed=False`.
3. **Given** a `plan` case, **When** the scorer is fed a plan markdown that
   contains a banned placeholder (`TODO`, `TBD`, `<your`), **Then** the result
   reports `passed=False` even if all sections are present.
4. **Given** a `fix` case expecting `confidence=HIGH`, **When** the scorer is
   fed a diagnostic with `CONFIDENCE: HIGH` and a hypothesis naming the seeded
   root cause, **Then** recall ≥ threshold and `passed=True`.
5. **Given** a `rebase` case where the PR intent is already on `main`
   (`expect_solved=true`), **When** the scorer is fed a decision
   `{already_solved:true, confidence:"high"}`, **Then** `decision_correct=True`
   and `passed=True`; fed `{already_solved:false}`, `passed=False`.
6. **Given** the full checked-in datasets, **When** `load_cases("fix")` /
   `("plan")` / `("brainstorm")` / `("rebase")` run, **Then** every case parses
   and carries only valid expectations.

---

### User Story 2 - Generalize the harness so non-review skills load (Priority: P1)

The shipped `EvalCase`/`CaseExpect` model is review-shaped (a mandatory `diff`,
review-only expectation fields). To host the four new skills without forking the
harness, the data model is generalised: `diff` becomes optional, a generic
`input` payload carries skill-specific inputs (issue text, idea, topic, PR
context), and `CaseExpect` preserves its raw JSON so each scorer reads its own
keys. `run_eval` already dispatches by `case.skill`; the CLI picks the live
adapter by skill. Review behavior is unchanged (its cases still parse and score
identically).

**Why this priority**: This is the foundation US1 stands on — without it the new
cases cannot even load. It is structural and ships no user-facing behaviour
beyond "more skills work."

**Independent Test**: `load_cases("review")` still returns the same five cases
with the same expectations (review regression), AND `load_cases("fix")` /
`("plan")` / `("brainstorm")` / `("rebase")` each return their cases; the review
scorer's existing tests stay green.

**Acceptance Scenarios**:

1. **Given** a review case JSON (top-level `diff` + review `expect`), **When**
   loaded, **Then** `EvalCase.diff` is populated, `input` is empty, and
   `score_review` returns the same result as before this feature.
2. **Given** a `fix` case JSON (top-level `issue_title`/`issue_body` + a
   fix-shaped `expect`), **When** loaded, **Then** the issue fields land in
   `EvalCase.input`, `diff` is empty, and `score_fix` is the dispatched scorer.
3. **Given** a case missing both `diff` and any input, **When** loaded,
   **Then** `load_cases` raises a clear `ValueError(id, reason)`.
4. **Given** a fake scorer registered for skill `my_skill`, **When** `run_eval`
   runs that skill's cases, **Then** each is scored by `my_skill`'s scorer
   (dispatch is not hard-coded to a fixed set).

---

### User Story 3 - Live evals to confirm improvements for the four skills (Priority: P2)

An operator iterates on, say, the `brainstorm` prompt and wants to *quantify*
whether the new prompt still produces well-formed decompositions across the
golden dataset — section coverage, issue-count discipline, JSON validity. The
opt-in **live** mode invokes each skill's real pipeline through its existing
seam (`fix`→`run_diagnostic`, `brainstorm`→decomposition prompt + parse,
`plan`→plan prompt, `rebase`→`_check_if_already_solved`), scores every case,
and compares against a checked-in `baseline.json` ("improved / regressed /
unchanged").

**Why this priority**: The "confirm improvements over iterations" half. It
depends on US1's dataset + scorer and cannot run in default CI (constitution:
never call the Claude subprocess in tests), so it is opt-in, locally/nightly.

**Independent Test**: With a model/key env set,
`KOAN_EVAL_LIVE=1 python -m app.skill_evals brainstorm --live` emits a per-case +
aggregate report and exits non-zero on regression; without the env it refuses
and never calls the LLM.

**Acceptance Scenarios**:

1. **Given** `KOAN_EVAL_LIVE=1`, **When** the live CLI runs for a skill, **Then**
   each golden case is scored against the real LLM output and an aggregate
   report is printed.
2. **Given** no `KOAN_EVAL_LIVE`, **When** `--live` is requested, **Then** it
   refuses with an explanatory message and exits non-zero (never calls the LLM).
3. **Given** a checked-in `baseline.json`, **When** the live run finishes,
   **Then** the report marks each metric improved/regressed/unchanged and exits
   non-zero on regression.
4. **Given** `--live` for a skill with no live adapter, **Then** the CLI reports
   "no live adapter" and exits non-zero (honest about coverage).

---

### User Story 4 - Document the eval-required rule + the implement/mission exclusion (Priority: P2)

A skill author adding a new core skill, or changing an existing skill's output
contract, must know whether to add eval cases. The rule is made discoverable:
the skill-authoring CLAUDE.md checklist and the skills component spec state that
**LLM-driven skills with a checkable output contract MUST ship eval cases**,
and that orchestration/queue skills without one (`implement`, `mission`) are
exempt with rationale. A guard test documents the intentional exclusion so it
is not silently "fixed" later.

**Why this priority**: The goal's "store the rule in the right CLAUDE.md" clause.
Without it the convention lives only in a spec a contributor may never open.

**Independent Test**: `grep` the skill-authoring CLAUDE.md and
`specs/components/skills.md` for the eval rule; run the guard test asserting
`implement`/`mission` are registered as eval-exempt.

**Acceptance Scenarios**:

1. **Given** the skill-authoring CLAUDE.md, **When** read, **Then** the new-skill
   checklist names the eval obligation with a pointer to the harness.
2. **Given** `specs/components/skills.md`, **When** read, **Then** the harness
   section lists all evaluated skills and the exempt skills with rationale.
3. **Given** the guard test, **When** run, **Then** `implement` and `mission`
   appear in `EVAL_EXEMPT_SKILLS` and the test explains why.

---

### Edge Cases

- **Live run hits a quota/invocation error on one case** — recorded as
  `errored`, the run continues, and the error count shows in the report (a
  single LLM failure must not abort the eval — already true for review).
- **LLM wraps JSON in prose / fences** — the live adapters reuse each skill's
  existing extraction (`_parse_decomposition`, `_check_if_already_solved`'s
  regex, `_parse_diagnostic`), so fence-wrapped output is handled like
  production.
- **A `brainstorm` decomposition has the right sections but fewer than
  `min_issues`** — counted as a count-check failure, not a crash.
- **A `plan` output is valid markdown but contains a literal `Phase` word
  without the `#### Phase N:` heading** — phase detection reuses
  `parse_plan_progress`, so only true phase headings count.
- **A `rebase` decision is `already_solved:true` but `confidence:"medium"`** —
  scored as `decision_correct=False` (production requires `high`), surfacing the
  real production rule rather than a looser one.
- **A new case references a key the scorer ignores** — ignored silently (forward
  compatibility), but unknown `severity_in` values on review findings still
  reject at load (existing behaviour).
- **`baseline.json` missing/malformed for a new skill** — live run reports "no
  baseline" and writes the current run as the new baseline.
- **Empty case dataset** — CLI reports "0 cases" and exits 0.

## Requirements *(mandatory)*

### Functional Requirements

#### Harness generalisation (US2)

- **FR-001**: `EvalCase.diff` MUST become optional (default empty); a generic
  `EvalCase.input: dict` MUST carry skill-specific inputs. A case with neither
  `diff` nor any input MUST be rejected at load with a clear error.
- **FR-002**: `CaseExpect` MUST preserve the original `expect` JSON (`.raw`)
  so per-skill scorers read their own keys without a new dataclass per skill.
- **FR-003**: `run_eval` MUST dispatch by `case.skill` via the `SCORERS`
  registry (already true); adding a skill MUST NOT require editing `run_eval`.
- **FR-004**: Review behavior MUST be unchanged: existing review cases load and
  score identically, and the review scorer's tests stay green.
- **FR-005**: The CLI (`python -m app.skill_evals <skill>`) MUST pick the live
  adapter by skill via a registry; `--live` for a skill with no adapter MUST
  exit non-zero with a clear message.

#### Per-skill scorers + datasets (US1)

- **FR-006**: A `score_fix(case, output) -> CaseResult` MUST score a fix
  diagnostic dict: confidence validity/match, hypothesis non-empty + keyword
  recall, code-path keyword recall; never raise.
- **FR-007**: A `score_plan(case, output) -> CaseResult` MUST score plan
  markdown (accept str or dict-with-text): required-section presence, min-phase
  count (reusing `parse_plan_progress`), banned-placeholder absence; never raise.
- **FR-008**: A `score_brainstorm(case, output) -> CaseResult` MUST score a
  decomposition (str or dict): JSON validity, issue-count range, per-issue
  required-section coverage (reusing `REQUIRED_ISSUE_SECTIONS` +
  `_validate_issue_bodies` as single source of truth), priority-enum validity,
  score-bar presence, theme-keyword recall; never raise.
- **FR-009**: A `score_rebase(case, output) -> CaseResult` MUST score an
  already-solved decision (str or dict): JSON validity, `decision_correct`
  (honoring the production `already_solved && confidence=="high"` rule),
  confidence validity, reasoning presence; never raise.
- **FR-010**: Each of the four skills MUST ship a golden dataset under
  `koan/skills/core/<name>/evals/cases/*.json` (≥3 cases each, including at
  least one negative/precision case) and a `baseline.json` stub.
- **FR-011**: Each scorer MUST be registered in `SCORERS` at import so
  `load_cases` + `run_eval` work with no extra wiring.

#### Live adapters (US3)

- **FR-012**: A live adapter MUST exist for each of the four skills, composing
  that skill's existing pipeline seam, and MUST be gated by `KOAN_EVAL_LIVE`
  (default operation never calls the Claude subprocess).
- **FR-013**: Each live adapter MUST be unit-testable offline via injected seams
  (like `review_live_fn`'s `_build`/`_run`/`_parse`), so CI covers adapter logic
  without the subprocess.

#### Rule + exclusion (US4)

- **FR-014**: The skill-authoring CLAUDE.md (`koan/skills/CLAUDE.md`) new-skill
  checklist MUST state the eval obligation for LLM-driven skills with a
  checkable output contract, with a pointer to the harness.
- **FR-015**: `specs/components/skills.md` MUST list all evaluated skills and the
  exempt skills (`implement`, `mission`) with rationale.
- **FR-016**: A guard test MUST assert `implement` and `mission` are
  intentionally eval-exempt (documented, not silently absent).

#### Test discipline

- **FR-017**: All harness code MUST be covered by offline unit tests that never
  call the Claude subprocess (constitution: mock the provider, never invoke it).

### Key Entities

- **EvalCase** (extended): `id`, `name`, `skill`, `description`, optional `diff`,
  generic `input: dict`, `expect: CaseExpect`.
- **CaseExpect** (extended): review fields unchanged + `.raw` (the full expect
  JSON) for skill-specific scorers.
- **ScorerFn**: `Callable[[EvalCase, object], CaseResult]` — generalised to
  accept any output shape (dict for fix/brainstorm/rebase, str for plan).
- **SCORERS / LIVE_FNS**: skill-keyed registries; `review` joined by
  `fix`, `plan`, `brainstorm`, `rebase`.
- **EVAL_EXEMPT_SKILLS**: the documented set (`implement`, `mission`) the guard
  test pins.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `load_cases` succeeds for `review`, `fix`, `plan`, `brainstorm`,
  `rebase` with zero errors; each scorer returns the documented pass/fail for
  its canned cases (all green in `fast` CI).
- **SC-002**: The harness module stays at ≥ 90% line coverage from offline tests,
  so the PR does not regress the repo coverage baseline (within the 0.5% CI
  tolerance).
- **SC-003**: Review regression — the existing `test_skill_evals.py` review
  assertions stay green unchanged (US2 generalisation is behaviour-preserving
  for review).
- **SC-004**: A maintainer can add a golden case to any of the four skills by
  dropping one JSON file into its `evals/cases/`, and a new evaluable skill by
  one scorer + one `cases/` dir + one registry line — no `run_eval` edit.
- **SC-005**: The eval-required rule is greppable in the skill-authoring
  CLAUDE.md, and the implement/mission exclusion is asserted by the guard test.

## Assumptions

- Each of the four skills' output contract (`fix_diagnose._parse_diagnostic`,
  the plan markdown structure + `parse_plan_progress`, brainstorm's
  `REQUIRED_ISSUE_SECTIONS` + `_parse_decomposition`, rebase's
  `_check_if_already_solved` JSON) is the stable surface under evaluation; this
  feature evaluates against it, it does not change it.
- CI has no Claude/API credentials, so anything calling the LLM MUST be opt-in
  and skip-by-default (constitution + existing test rules).
- A small hand-authored dataset (3–5 cases per skill) is sufficient to start;
  breadth grows as real failures are seen — matching the review starter.
- `implement` and `mission` are honestly non-evaluable; their behavioural test
  suites are the correct quality gate for them.
