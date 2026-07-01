# Tasks: Review Skill Evaluation Harness

**Input**: Design documents from `/specs/002-review-skill-evals/`

**Prerequisites**: plan.md (required), spec.md (required), research.md.

**Tests**: included — the harness is a quality tool, so tests are part of the
feature, not optional.

**Organization**: grouped by user story. Commit after every task (skip empty).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependencies)
- **[Story]**: US1 (offline/CI), US2 (live), US3 (extensibility), or X (cross-cutting)

---

## Phase 1: Foundational (offline scorer + data model)

**Purpose**: the deterministic core everything else composes. BLOCKS all other
phases.

- [ ] T001 [US1] Create `koan/app/skill_evals.py` with the data model:
  `EvalCase`, `CaseExpect`, `FindingExpect`, `CaseCheck`, `CaseResult`,
  `EvalReport` dataclasses; the module-level `SCORERS` registry seeded with
  `{"review": score_review}` (FR-001, FR-011).
- [ ] T002 [US1] Implement `score_review(case, review) -> CaseResult` in
  `skill_evals.py`: reuse `app.review_schema.validate_review` for `valid_json`;
  compute recall (file + keyword-stem + optional severity-band matching),
  `lgtm_correct`, `precision_penalty`, blended `score`, and `passed` per
  research.md §6 (FR-001..FR-004).
- [ ] T003 [US1] Implement `run_eval(cases, review_fn) -> EvalReport` in
  `skill_evals.py`: per-case dispatch via `SCORERS[case.skill]`; treat a
  `review_fn` that returns `None`/raises as `errored` and continue (FR-007);
  aggregate counts + mean metrics.
- [ ] T004 [US1] Implement `load_cases(skill_or_dir) -> list[EvalCase]` in
  `skill_evals.py`: discover `cases/*.json` under a skill's `evals/` dir, parse,
  and raise a clear `ValueError(case_id, reason)` on any malformed case
  (FR-006).

**Checkpoint**: scorer + loader + report compile and are unit-testable.

---

## Phase 2: User Story 1 — golden dataset + offline tests (CI) 🎯 MVP

**Goal**: a checked-in dataset the scorer can grade, exercised by fast CI tests
that never call the LLM.

**Independent Test**:
`KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_skill_evals.py -v` → all green.

- [ ] T005 [US1] Add golden cases under `koan/skills/core/review/evals/cases/`:
  `sql_injection.json` (seeded bug, expect finding), `bare_except.json` (seeded
  bug), `hardcoded_secret.json` (seeded bug, expect critical), `clean_refactor.json`
  (clean, expect LGTM), `benign_style.json` (precision trap — cosmetic change,
  expect no critical/LGTM). Each carries a realistic diff + `expect`
  (FR-005).
- [ ] T006 [US1] Write `koan/tests/test_skill_evals.py` covering scorer behavior
  with canned reviews: valid-and-recall, misses-bug (low recall), clean-LGTM
  correct, false-positive on forbidden file, invalid-JSON/malformed, severity-band
  mismatch (FR-001..FR-004). Mock nothing LLM-related here — pure function.
- [ ] T007 [US1] Add dataset-validity + report tests to `test_skill_evals.py`:
  `load_cases("review")` returns all cases well-formed (FR-006); `run_eval` with
  a stub `review_fn` aggregates correctly (FR-007); empty-dataset case exits clean.

**Checkpoint**: US1 done — regression detection runs in `fast` CI.

---

## Phase 3: User Story 2 — live eval + baseline + CLI

**Goal**: opt-in live LLM scoring that confirms improvements and flags
regressions vs a baseline.

**Independent Test**: `KOAN_EVAL_LIVE=1 python -m app.skill_evals review --live`
prints a metrics report; without the env it skips cleanly.

- [ ] T008 [US2] Implement the live adapter `review_live_fn(case, project_path)`
  in `skill_evals.py`: build a minimal review `context` from the case diff,
  reuse `build_review_prompt` → `_run_claude_review` → `_parse_review_json`;
  catch provider errors → `errored` (FR-008, FR-007).
- [ ] T009 [US2] Implement `compare_to_baseline(report, baseline_path)` and the
  `--update-baseline` write path in `skill_evals.py`: per-metric
  improved/regressed/unchanged; missing/malformed baseline reports "no baseline"
  and (on `--update-baseline`) writes the current run (FR-009, edge cases).
- [ ] T010 [US2] Implement the CLI `python -m app.skill_evals <skill>
  [--live] [--update-baseline] [--project-path P]` in `skill_evals.py`: offline
  default scores recorded/canned outputs; `--live` gates on `KOAN_EVAL_LIVE`
  (refuse + hint if unset); print human-readable per-case + aggregate report;
  exit non-zero on regression (FR-008, FR-009, FR-010).
- [ ] T011 [US2] Add `koan/skills/core/review/evals/baseline.json` as a stub
  baseline (documented schema: `{skill, metrics:{valid_json_rate,mean_recall,
  lgtm_accuracy,mean_score}, updated}`) — initial values null/0 with a comment
  that the first live `--update-baseline` run fills them.
- [ ] T012 [US2] Add one `@pytest.mark.slow` live test to
  `test_skill_evals.py` guarded by `KOAN_EVAL_LIVE` + a model env: skips with a
  clear reason otherwise; when live, runs ≥2 cases, asserts report non-empty and
  JSON-validity rate == 1.0 for the current prompt (SC-003).

**Checkpoint**: US2 done — improvements are measurable and regressions trip a
non-zero exit.

---

## Phase 4: User Story 3 — extensibility seam + tests

**Goal**: prove a second skill can be added without touching `run_eval`.

**Independent Test**: register a fake skill scorer, run `run_eval`, assert it
dispatches and aggregates (US3 acceptance).

- [ ] T013 [US3] Add `register_scorer(skill, fn)` + ensure `run_eval` looks up
  `SCORERS[case.skill]` per-case (raise on unknown skill) in `skill_evals.py`
  (FR-011).
- [ ] T014 [US3] Add an offline test in `test_skill_evals.py`: register a trivial
  scorer under a fake skill name, load cases via a temp `evals/cases` dir, run
  `run_eval`, assert dispatch + aggregation (US3 acceptance #1).

**Checkpoint**: US3 done — the harness is skill-agnostic by construction.

---

## Phase 5: Polish & Cross-Cutting (specs + docs)

**Purpose**: keep specs/docs in sync (constitution II; CLAUDE.md "specs & docs
discipline").

- [ ] T015 [X] [P] Edit `specs/skills/review.md`: add an **Evaluation** section
  documenting the golden dataset location, scorer dimensions, offline/live modes,
  and the `KOAN_EVAL_LIVE` gate as part of the review skill's contract.
- [ ] T016 [X] [P] Edit `specs/components/skills.md`: document the generic eval
  harness, the `SCORERS` registry, and the "how to add evals to a new skill"
  steps (US3 contract).
- [ ] T017 [X] [P] Create `docs/operations/skill-evals.md`: operator-facing
  runbook — run offline tests, run live eval, add a case, add a skill, read the
  baseline/regression report.
- [ ] T018 [X] [P] Edit `docs/README.md`: link `docs/operations/skill-evals.md`
  under the operations list.
- [ ] T019 [X] Run `make lint` and `make test` (or targeted pytest with
  `KOAN_ROOT`); fix any failures. Verify the new module's coverage ≥ 90%
  (SC-002) and that the repo baseline is not regressed.

---

## Dependencies & Execution Order

- **Phase 1** is foundational — T001→T004 are sequential (each builds on the
  prior data model/functions).
- **Phase 2** depends on Phase 1; T005 (cases) and T006 (scorer tests) are
  independent of each other and could be written in either order, but T007
  (dataset/report tests) needs both.
- **Phase 3** depends on Phase 1 (adapter reuses scorer) and Phase 2 (cases);
  T008→T010 sequential, T011 independent, T012 last.
- **Phase 4** depends on Phase 1 (registry) and Phase 2 (test patterns).
- **Phase 5** is independent and parallelizable (different files), best done
  last so it reflects the final shape.

### Parallel Opportunities

- T015 / T016 / T017 / T018 (Phase 5) touch different files — all `[P]`.
- T005 (author cases) and T006 (author scorer tests) are independent.

## Implementation Strategy

MVP first: Phase 1 + Phase 2 deliver US1 (regression detection in CI) and are
the independently valuable slice. Stop and validate (`make test`) before Phase 3.
Then Phase 3 (live), Phase 4 (extensibility proof), Phase 5 (docs).

## Notes

- Commit after each task (or logical group); skip empty commits.
- Every code task adds or updates a test in the same commit where practical.
- Offline tests must never call the Claude subprocess — live behavior is behind
  `KOAN_EVAL_LIVE` + `@pytest.mark.slow`.
