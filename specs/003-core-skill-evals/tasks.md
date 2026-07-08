# Tasks: Core Skill Evaluation Harness (multi-skill)

**Input**: Design documents from `/specs/003-core-skill-evals/`

**Prerequisites**: plan.md (required), spec.md (required).

**Tests**: included — the harness is a quality tool, so tests are part of the
feature, not optional.

**Organization**: grouped by phase, tagged by user story. Commit after every
task (skip empty). `[P]` = parallel-safe (different files, no deps).

## Format: `[ID] [P?] [Story] Description`

- **[Story]**: US1 (offline/CI scorers+datasets), US2 (harness generalisation),
  US3 (live adapters), US4 (rule + exclusion docs).

---

## Phase 1: Foundational — generalise the data model (US2)

**Purpose**: the structural change that lets non-review cases load. BLOCKS all
other phases. Behaviour-preserving for review.

- [ ] T001 [US2] In `koan/app/skill_evals.py`: make `EvalCase.diff` optional
  (default `""`); add `EvalCase.input: dict = field(default_factory=dict)`; add
  `CaseExpect.raw: dict = field(default_factory=dict)`; loosen `ScorerFn` to
  `Callable[[EvalCase, object], CaseResult]`.
- [ ] T002 [US2] Update `_case_from_dict`: relax the "diff must be non-empty"
  rule to "must have a non-empty `diff` OR ≥1 input key"; capture every
  non-reserved top-level key into `EvalCase.input`; stash the raw `expect` dict
  onto `CaseExpect.raw`; keep all review parsing/validation unchanged.
- [ ] T003 [US2] Generalise the CLI `main()`: add a `LIVE_FNS` registry
  (seeded with `{"review": review_live_fn}`); resolve the live fn by `skill`;
  `--live` for a skill with no adapter exits non-zero with a clear message;
  keep offline mode working for any registered scorer.
- [ ] T004 [US2] Add module-level `EVAL_EXEMPT_SKILLS = ("implement", "mission")`
  with a docstring stating why (no LLM-driven checkable output contract).

**Checkpoint**: `load_cases("review")` unchanged; a synthetic non-review case
loads into `input`; `score_review` results unchanged. (Verified by Phase 4
review-regression tests.)

---

## Phase 2: User Story 1 — four scorers (US1)

**Purpose**: deterministic per-skill scorers reusing each skill's validator as
single source of truth. Each is pure, accepts the skill's output shape, never
raises.

- [ ] T005 [US1] Implement `score_fix(case, output) -> CaseResult` in
  `skill_evals.py`: accept a diagnostic dict (or malformed input); checks =
  `confidence_valid` (HIGH/MEDIUM/LOW), `confidence_match` (vs
  `expect.raw.get("expected_confidence")`), `hypothesis_present`, `hypothesis_recall`
  (fraction of `expect.raw.get("hypothesis_keywords")` found in the hypothesis
  text), `code_path_recall`. Blend into `score`; compute `passed`. Register
  `register_scorer("fix", score_fix)`.
- [ ] T006 [US1] Implement `score_plan(case, output) -> CaseResult`: accept str
  or dict-with-text; checks = `required_sections` (each header in
  `expect.raw.get("required_sections", DEFAULT_PLAN_SECTIONS)` present),
  `min_phases` (reuse `app.dashboard_service.plans.parse_plan_progress`),
  `no_banned_placeholders` (none of `expect.raw.get("banned_patterns",
  DEFAULT_BANNED)`), `title_present`. Blend + `passed`. Register
  `register_scorer("plan", score_plan)`.
- [ ] T007 [US1] Implement `score_brainstorm(case, output) -> CaseResult`:
  accept str (parse via the skill's `_parse_decomposition`) or dict; checks =
  `valid_json`, `issue_count` (within `min_issues`..`max_issues`, defaults 3..8),
  `sections_per_issue` (reuse `REQUIRED_ISSUE_SECTIONS` +
  `_validate_issue_bodies`), `priority_valid` (Immediate|Prototype First|Research
  Further|Skip), `score_bars_present` (4 axes in `## Scores`), `theme_recall`
  (`expect.raw.get("theme_keywords")` across issue titles). Blend + `passed`.
  Register `register_scorer("brainstorm", score_brainstorm)`.
- [ ] T008 [US1] Implement `score_rebase(case, output) -> CaseResult`: accept
  str (first JSON object) or dict; checks = `valid_json`, `decision_correct`
  (production rule: `already_solved and confidence == "high"` must equal
  `expect.raw.get("expect_solved")`), `confidence_valid`, `reasoning_present`.
  Blend + `passed`. Register `register_scorer("rebase", score_rebase)`.

**Checkpoint**: all four scorers return a `CaseResult` for good, bad, and
malformed outputs without raising; each is registered in `SCORERS`.

---

## Phase 3: User Story 1 — golden datasets (US1)

**Purpose**: checked-in cases the scorers grade. ≥3 cases per skill incl. one
negative/precision case. Generic content only (no private identifiers).

- [ ] T009 [US1] [P] Add `koan/skills/core/fix/evals/cases/*.json` (≥3, e.g.
  `null_deref` HIGH-confidence, `race_condition` MEDIUM, `vague_report` LOW) +
  `evals/baseline.json` stub. Each: `issue_title`/`issue_body` inputs + fix-shaped
  `expect` (`expected_confidence`, `hypothesis_keywords`, `code_path_keywords`).
- [ ] T010 [US1] [P] Add `koan/skills/core/plan/evals/cases/*.json` (≥3, e.g.
  `well_formed`, `missing_verification`, `banned_placeholder`) + baseline stub.
  Each: `idea` input + plan-shaped `expect` (`required_sections`, `min_phases`,
  `banned_patterns`).
- [ ] T011 [US1] [P] Add `koan/skills/core/brainstorm/evals/cases/*.json` (≥3,
  e.g. `clean_decomposition`, `too_few_issues`, `missing_sections`) + baseline
  stub. Each: `topic` input + brainstorm-shaped `expect` (`min_issues`,
  `max_issues`, `theme_keywords`).
- [ ] T012 [US1] [P] Add `koan/skills/core/rebase/evals/cases/*.json` (≥3, e.g.
  `already_solved_high`, `not_solved`, `low_confidence_false_positive`) +
  baseline stub. Each: `pr_title`/`pr_body`/`pr_diff`/`recent_commits` inputs +
  rebase-shaped `expect` (`expect_solved`, `expect_confidence`).

**Checkpoint**: `load_cases` succeeds for all four skills; every case validates.

---

## Phase 4: User Story 1 + 2 — offline tests (CI) 🎯 MVP

**Goal**: scorers + datasets exercised by fast CI that never calls the LLM.

**Independent Test**:
`KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_skill_evals.py -v` → green.

- [ ] T013 [US2] Add/extend tests asserting the generalisation is
  behaviour-preserving for review: existing review assertions unchanged; a
  loaded review case has `input == {}`; a synthetic non-review case populates
  `input` and `expect.raw`; load rejects a case with neither diff nor input.
- [ ] T014 [US1] Add scorer tests for `score_fix`: HIGH-match passes; wrong
  confidence fails; missing hypothesis fails; malformed (non-dict) input →
  `valid` False, no raise.
- [ ] T015 [US1] Add scorer tests for `score_plan`: full sections + phases +
  no banned → pass; banned placeholder → fail even with all sections; missing
  section → fail; str and dict-with-text both accepted.
- [ ] T016 [US1] Add scorer tests for `score_brainstorm`: clean 4-issue
  decomposition passes; <3 issues fails; an issue missing `## Priority` fails;
  invalid JSON → `valid_json` False; theme recall measured.
- [ ] T017 [US1] Add scorer tests for `score_rebase`: solved+high →
  `decision_correct` True; solved+medium → False (production rule); not-solved
  → False vs `expect_solved=true`; malformed → no raise.
- [ ] T018 [US1] Add dataset-validity tests: `load_cases(s)` for
  `s in {fix,plan,brainstorm,rebase}` returns ≥3 cases, each with a non-empty
  input and a dict `expect`; CLI offline mode (`main([skill])`) lists cases and
  exits 0 for each skill.
- [ ] T019 [US2] Add CLI dispatch tests: `--live` without `KOAN_EVAL_LIVE`
  refuses (exit 2); `--live` for an exempt/unregistered skill exits non-zero
  with a clear message; offline `main(["review"])` still works.

---

## Phase 5: User Story 3 — live adapters (US3)

**Purpose**: opt-in live evals composing each skill's existing seam; injectable
for offline unit testing.

- [ ] T020 [US3] [P] `fix_live_fn(case, project_path, *, _run=None)`: compose
  `fix_diagnose.run_diagnostic` (injectable `_run`); return its parsed dict;
  register in `LIVE_FNS`.
- [ ] T021 [US3] [P] `brainstorm_live_fn(case, project_path, *, _build=None,
  _run=None, _parse=None)`: build the decomposition prompt, run CLI (injectable
  `_run`), parse via `_parse_decomposition`; register in `LIVE_FNS`.
- [ ] T022 [US3] [P] `plan_live_fn(case, project_path, *, _run=None)`: build the
  plan prompt (`load_skill_prompt`), run CLI (injectable), return the markdown;
  register in `LIVE_FNS`.
- [ ] T023 [US3] [P] `rebase_live_fn(case, project_path, *, _run=None,
  _parse=None)`: build the `already_solved` prompt, run CLI (injectable), extract
  first JSON object; register in `LIVE_FNS`.
- [ ] T024 [US3] Add injected-seam unit tests for each live adapter (offline —
  `_run` returns canned output, assert the adapter feeds it to the right
  parser) + one opt-in `@pytest.mark.slow` live test per skill (skips without
  `KOAN_EVAL_LIVE`).

---

## Phase 6: User Story 4 — rule + exclusion docs (US4)

- [ ] T025 [US4] Add an eval-exemption guard test: assert
  `EVAL_EXEMPT_SKILLS == {"implement","mission"}` and that no scorer/live-fn is
  registered for them, with the rationale asserted in the test docstring.
- [ ] T026 [US4] Update `specs/components/skills.md`: list the evaluated skills
  (review/fix/plan/brainstorm/rebase) and the exempt skills with rationale;
  state the rule crisply.
- [ ] T027 [US4] Update `koan/skills/CLAUDE.md` new-skill checklist: add the
  eval obligation for LLM-driven skills with a checkable output contract, with a
  pointer to `specs/components/skills.md` + the harness module.
- [ ] T028 [US4] [P] Add an `## Evaluation` section to `specs/skills/fix.md`,
  `plan.md`, `rebase.md`; create `specs/skills/brainstorm.md` (per-skill spec +
  Evaluation section). Document the implement/mission exemption in
  `specs/skills/implement.md` and `mission.md`.
- [ ] T029 [US4] Update `docs/operations/skill-evals.md`: add the four skills
  (datasets, scored dimensions, live command) and a "why some skills are
  exempt" subsection.

---

## Phase 7: Verification

- [ ] T030 Run `make lint`; fix all violations (no `# noqa` without documented
  reason).
- [ ] T031 Run the full suite
  `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_skill_evals.py koan/tests/test_skills.py koan/tests/test_review_schema.py -v`;
  confirm green + no coverage regression vs the 88% baseline.
- [ ] T032 Confirm the new `evals/` data dirs do not disturb skill discovery
  (`TestCoreSkillGroupEnforcement` + a `load_cases` smoke for each skill).
