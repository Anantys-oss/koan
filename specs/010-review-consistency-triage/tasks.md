---
description: "Task list for Review Consistency, Yellow-Tier Triage & Comprehensive Discovery"
---

# Tasks: Review Consistency, Yellow-Tier Triage & Comprehensive Discovery

**Input**: Design documents from `specs/010-review-consistency-triage/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: INCLUDED. Kōan's `koan/CLAUDE.md` and the Constitution mandate test-first for behavior
changes, and spec FR-022 requires eval coverage. Deterministic helpers get behavior unit tests (no
provider calls); prompt changes are paired with golden eval cases.

**Organization**: grouped by user story. Phase order follows the plan's dependency-driven build
order (A→E); each story carries its spec priority. US1/US2 are P1 (MVP core); US3/US5/US6/US7 are
P2; US4 is P3.

## Format: `[ID] [P?] [Story] Description with file path`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1–US7 (setup/foundational/polish carry no story label)

## Path conventions

Single Python package: `koan/app/`, `koan/skills/core/review/`, `koan/system-prompts/_partials/`,
`koan/tests/`. Run tests with `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest …`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: scaffolding shared by all stories. No behavior change yet.

- [~] T001 Modules are created in their OWNING phase (not empty stubs) to avoid dead-code commits — `review_identity.py` created in Phase 2; `review_reuse`/`review_reconcile` in US1; `review_triage` in US2; `review_dispositions` in US7
- [X] T002 [P] `instance.example/config.yaml`: documented `review_consistency` block (wired in US1). **Corrected during US6 review**: dropped the inert `review_severity` block — the yellow bar + `[Pre-Existing Issue]` label are *prompt-fixed* (in `review-severity-rubric.md`), not runtime knobs (the label must match the prompt), so **FR-014's "configurable bar" is intentionally not implemented — the bar is fixed-strict**, a reasonable default; runtime tuning is a future enhancement. `review_discovery`/`review_dispositions` blocks are added in their wiring phases (US3/US7), matching the no-speculative-config discipline used for getters.
- [~] T003 Eval case files are created in their story phases (see T015/T021/T026/T029/T035/T039) rather than as empty placeholders (empty JSON = dead/invalid data)

**Checkpoint**: module + config + eval scaffolding in place.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: identity, config getters, and the extended sidecar — every story depends on these.

**⚠️ CRITICAL**: No user-story work begins until this phase is complete.

- [X] T004 [P] Write behavior tests for the finding-identity key in `koan/tests/test_review_identity.py` (equal keys for reworded title / ±small line shift; distinct keys for different topics or files) — spec FR-002
- [X] T005 Implement `finding_key(finding) -> str` + `same_finding(...)` (file + tolerant region bucket + semantic category) in `koan/app/review_identity.py` (pure, no provider) — FR-002, research D1
- [~] T006 Config getters moved to their consuming phases (no speculative config): `get_review_consistency_config` → US1, `get_review_severity_config` → US2, `get_review_dispositions_config` → US7, `get_review_discovery_config` → US3; each ships with its `test_config.py` test
- [~] T007 See T006 — getters land in the phase that reads them
- [X] T008 [P] Write tests for the extended review sidecar in `koan/tests/test_review_runner.py::TestReviewSidecarIdentity` (write stamps `identity_key` + `schema_version`, no caller mutation, read round-trip, legacy/missing/corrupt fail-open). `base_sha`/`request_signature`/`dispositions` land in their consuming phases (US1/US7).
- [X] T009 Extend `_write_review_findings_sidecar` to stamp per-finding `identity_key` (via `review_identity.finding_key`) + `schema_version: 1`, without mutating caller findings; reader unchanged (fail-open, backward-compatible) — FR-002/007, research D6

**Checkpoint**: identity + config + sidecar ready; stories can proceed.

---

## Phase 3: User Story 1 — Consistent findings on repeated review (Priority: P1) 🎯 MVP

**Goal**: repeat review of unchanged code reproduces the prior review; re-review after commits
reconciles (recurring recur, fixed suppressed) with the freeze on unchanged code (critical excepted).

**Independent Test**: review a PR twice with no push → identical blocking set + verdict (reproduction);
fix one finding + push touching only that area → fixed suppressed, others unchanged, no new
non-critical finding on unchanged code.

- [X] T010 [P] [US1] Tests for reuse + request-signature equivalence in `koan/tests/test_review_reuse.py` (reuse iff head+base SHA + signature match; base movement / focus-flag / discovery toggle → no reuse; fail-safe on missing pieces) — FR-001, D2
- [X] T011 [P] [US1] Tests for the freeze in `koan/tests/test_review_reconcile.py` (fail-open on no prior head / unknown changeset; first-time non-critical on unchanged file → drop; recurring → survive; changed file → survive; first-time critical → surface with `[Pre-Existing Issue]`) — FR-003, SC-011, D3
- [X] T012 [US1] Implement `koan/app/review_reuse.py`: `request_signature(...)`, `should_reuse(...)`, `REPRODUCTION_NOTE` (fail-safe by construction) — FR-001/006
- [X] T013 [US1] Implement `koan/app/review_reconcile.py`: `compute_freeze(...)` (file-level, fail-open; returns drop indices + summary; labels pre-existing criticals) using `review_identity.same_finding`; runner applies existing `_remap_findings_after_drop` — FR-003
- [X] T014 [US1] Wire into `run_review`: reuse short-circuit after the existing no-new-commits skip (fail-open); freeze in `_apply_review_accuracy_gate`; add fail-open `_compare_changed_files` + `_merge_base_sha`; persist `base_sha` + `request_signature` in the sidecar; `get_review_consistency_config`. Integration tests in `TestReviewFreezeWiring` + `TestReviewSidecarReuseFields`. Full review_runner suite green (491) — FR-001/003/007
- [~] T015/T016 US1 consistency is *cross-run*; the eval harness scores *single-diff* cases, so it has no run-twice-compare hook. US1 is covered by the deterministic unit tests (identity/reuse/reconcile/wiring). A harness-level repeat-stability metric (FR-022/SC-007) is a genuine extension → moved to Polish (Phase 10, T048-adjacent).

**Checkpoint**: anti-whiplash consistency working and eval-guarded (SC-001/002/003/011). MVP.

---

## Phase 4: User Story 2 — A trustworthy yellow (Important) tier (Priority: P1)

**Goal**: `warning` reserved for clear blockers/real-harm; borderline → recommendation; noise dropped;
verdict re-derived from post-triage severities.

**Independent Test**: findings spanning blocker/borderline/cosmetic → blocker stays yellow, borderline
→ green, cosmetic dropped; verdict flips merge-ready when no clear blocker remains.

- [~] T017/T019 No `review_triage.py` in US2. The yellow-bar is prompt-driven (the model assigns severity per the sharpened rubric); the deterministic invariants US2 lists (verdict-follows-severity FR-012, noise-drop FR-010) are ALREADY enforced by the existing verdict finalizer + reflection pass. A deterministic Python severity classifier would be low-accuracy and re-introduce nothing US1 doesn't already stabilize. `review_triage.py` is introduced in US6 where deterministic labeling (`[Pre-Existing Issue]`) genuinely needs it.
- [X] T018 [US2] Create shared `koan/system-prompts/_partials/review-severity-rubric.md` — sharpened Important bar (reserve 🟡 for clear blocker/real-harm; demote borderline to 🟢; drop noise) + Verdict Contract — FR-008/009/010/012
- [X] T020 [US2] `{@include review-severity-rubric}` added to the JSON-output prompts `review.md` + `review-with-plan.md` (extracted review.md's inline calibration into the shared partial; plan variant previously had NO explicit severity/verdict guidance — now fixed). `review-architecture.md` outputs **markdown** (🔴/🟡/🟢, no `lgtm` JSON), so it gets a markdown-appropriate calibration Rules bullet instead of the JSON verdict rubric. Include resolution verified.
- [X] T021 [P] [US2] Added eval case `koan/skills/core/review/evals/cases/borderline_not_blocking.json` (borderline improvement → suggestion, lgtm true, not blocking). Dataset-validity + offline eval tests green (127). Baseline metrics are a null stub (populated by live `--update-baseline`), so no offline baseline edit needed.

**Checkpoint**: yellow tier trustworthy; false request-changes reduced (SC-004/005).

---

## Phase 5: User Story 6 — Pre-existing issues demoted and labeled (Priority: P2)

**Goal**: non-critical issue predating the changeset → `suggestion` + `[Pre-Existing Issue]`; critical
→ keep severity + prefix. Coexists with the freeze (FR-030). *(Sequenced right after US2 because it
extends the triage post-pass and rubric.)*

**Independent Test**: pre-existing non-critical → green `[Pre-Existing Issue]` (non-blocking);
pre-existing critical → keeps critical + prefix; PR-introduced issue → no label, triaged normally.

- [X] T022 [P] [US6] Tests in `koan/tests/test_review_triage.py` (non-critical `[Pre-Existing Issue]` → forced `suggestion`; critical → severity kept; untagged → untouched; prefix normalized to one; demotion re-derives lgtm; fail-open) — FR-027/028/029, SC-013
- [X] T023 [US6] Extend `review-severity-rubric.md` with the `[Pre-Existing Issue]` rule (reviewer's semantic "predates the changeset" assessment; non-critical → suggestion + prefix, critical → keep + prefix) — FR-029
- [X] T024 [US6] Implement `koan/app/review_triage.py`: `enforce_pre_existing()` (detection is the reviewer's prefix per FR-029; deterministic enforcement of severity + prefix normalization) + `derive_lgtm()`; wired in the accuracy gate AFTER the freeze (FR-030 freeze-wins) — FR-027–030
- [X] T025 [US6] Already satisfied in US1: `review_reconcile.compute_freeze` labels the surfaced critical-on-unchanged with `[Pre-Existing Issue]` (`PRE_EXISTING_PREFIX`, reused by `review_triage`) — FR-028
- [X] T026 [P] [US6] Added eval case `koan/skills/core/review/evals/cases/pre_existing_downgrade.json`; dataset-validity green (127). Baseline is a null stub (live `--update-baseline`).

**Checkpoint**: pre-existing issues fair and clearly labeled; never block on someone else's debt.

---

## Phase 6: User Story 5 — Exhaustive discovery in a single pass, default prompt (Priority: P2)

**Goal**: the default prompt keeps looking after finding several issues and aims to surface all in one
parse (always on; no config gate), without inflating blocking noise.

**Independent Test**: PR with several independent issues → single default review surfaces all within
coverage (not just the first few); added findings still pass triage.

- [ ] T027 [US5] Extend `koan/system-prompts/_partials/review-severity-rubric.md` with the exhaustive-discovery instruction (keep searching; aim for all issues in one parse; no self-imposed finding cap) — FR-025, contracts/prompt-partials.md
- [ ] T028 [US5] Confirm the three review prompts (`review.md`, `review-with-plan.md`, `review-architecture.md`) inherit the exhaustive instruction via the shared include; adjust any wording that implies stopping early (depends on T027) — FR-025
- [ ] T029 [P] [US5] Add golden eval case `koan/skills/core/review/evals/cases/recall_all.json` (N seeded issues → single pass surfaces all within coverage; precision not regressed) + baseline update — FR-026, SC-012/006

**Checkpoint**: default-path recall raised; round-1 completeness underpins the freeze.

---

## Phase 7: User Story 7 — Honor human dispositions from PR comments (Priority: P2)

**Goal**: on re-review, honor human comment dispositions — dismiss (stop re-raising as blocking),
defer (downgrade to `[Deferred]` recommendation) — any non-bot commenter, all severities incl.
critical, always attributed; sticky until retracted; injection-guarded.

**Independent Test**: comment "not a problem" on a finding → not re-raised as blocking + attributed;
comment "fix later" → `[Deferred]` recommendation; later "actually this is a problem" → re-evaluated.

- [ ] T030 [P] [US7] Write tests in `koan/tests/test_review_dispositions.py` (classify dismiss/defer/retract/none from comments; bot comments excluded; disposition→finding match by identity; ambiguous comment → no change; comment cannot alter rubric/verdict — FR-038 guardrail) — FR-031/032/038
- [ ] T031 [US7] Implement `review_dispositions.py`: parse ingested PR comments → `Disposition[]`, match to findings via `review_identity`, apply actions (dismiss→suppress-as-blocking; defer→forced `suggestion` + `[Deferred]`; all severities per `honor_critical`) with mandatory attribution payload (commenter + quoted rationale) (depends on T005, T007, T030) — FR-031–036
- [ ] T032 [US7] Create `koan/system-prompts/_partials/review-dispositions.md` (read/honor dispositions; attribution mandatory; untrusted-content guardrail mirroring `review-repo-conventions.md`) and include it on re-reviews — FR-036/038, contracts/prompt-partials.md
- [ ] T033 [US7] Persist dispositions in the sidecar and apply stickiness in `koan/app/review_runner.py` (dispositions recur by identity until a `retract`; wire disposition application with precedence over triage, per data-model.md precedence) (depends on T009, T031) — FR-037
- [ ] T034 [US7] Render attribution in the posted review (who requested + quoted rationale) for every comment-driven suppression/downgrade, incl. dismissed `critical` (depends on T031) — FR-036, SC-016
- [ ] T035 [P] [US7] Add golden eval case `koan/skills/core/review/evals/cases/disposition_dismiss.json` (dismissed finding not re-raised as blocking, attributed) + baseline update — FR-022, SC-014/015/016

**Checkpoint**: humans can close the loop and it stays closed; "the human decides" honored + auditable.

---

## Phase 8: User Story 3 — Comprehensive multi-perspective discovery, opt-in (Priority: P2)

**Goal**: config-gated (default OFF) prompt partial running a fixed perspective set, merged/deduped by
identity; fail-open + bounded; feeds the same triage/consistency.

**Independent Test**: OFF → review identical to today (prompt/findings/cost). ON → superset of
single-pass findings, no duplicates, immediate re-review adds ~nothing.

- [ ] T036 [P] [US3] Write tests in `koan/tests/test_review_runner.py` for the discovery gate (OFF → prompt byte-identical, partial NOT included, signature reflects off; ON → partial included, signature reflects on) and merge/dedup by identity (same underlying issue → one finding, highest-justified severity) — FR-016/017, SC-008
- [ ] T037 [US3] Create `koan/system-prompts/_partials/review-comprehensive-discovery.md` (fixed set: correctness, security, architecture, silent-failure, test-coverage; merge+dedup+reconcile; sub-agents where supported; fail-open + bounded; partial-coverage reported) — FR-015/019/021
- [ ] T038 [US3] Wire the config gate in `run_review` / prompt build: include the partial only when `get_review_discovery_config().enabled`; add discovery flag to the request signature; merge/dedup via `review_identity`; feed result into reconcile+triage unchanged (depends on T005, T007, T036, T037) — FR-016/017/018
- [ ] T039 [P] [US3] Add golden eval case `koan/skills/core/review/evals/cases/discovery_gain.json` (ON: recall ↑ ≥20% vs single-pass, 0 duplicate findings) + baseline update — FR-022, SC-009/010

**Checkpoint**: opt-in thoroughness; default path proven zero-regression (SC-008).

---

## Phase 9: User Story 4 — Visibility into what was demoted or dropped (Priority: P3)

**Goal**: compact, secondary accounting that filtering occurred, without burying blockers.

**Independent Test**: a review that demoted/dropped findings shows a compact "N demoted, M dropped"
note (secondary/collapsed), not competing with the primary findings.

- [ ] T040 [P] [US4] Write tests in `koan/tests/test_review_runner.py` for the demote/drop accounting note (counts correct; rendered secondary/collapsed; absent when nothing filtered) — FR-024
- [ ] T041 [US4] Render the compact demote/drop accounting from `review_triage` output into the posted review body (parsimony: collapsed/footnoted) in `koan/app/review_runner.py` (depends on T019, T040) — FR-024

**Checkpoint**: filtering auditable; trust in the aggressive triage.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: contract-first spec, docs, sync, and final gates.

- [ ] T042 Update the durable contract `specs/skills/review.md` **contract-first** with the new invariants (consistency guarantee + reuse key, re-review freeze + critical exception, yellow-tier bar, opt-in comprehensive discovery, `[Pre-Existing Issue]`, human-disposition authority + FR-036/FR-038 guardrails) — FR-023, Constitution II
- [ ] T043 [P] Capture user-facing docs: update `docs/users/user-manual.md` and `docs/users/skills.md` (`/review` consistency, `[Pre-Existing Issue]`, honoring PR-comment dispositions, opt-in discovery config) — CLAUDE.md docs step
- [ ] T044 [P] Capture design/architecture docs: update `docs/architecture/github-and-trackers.md` (disposition handling) and add a decision note for the consistency/freeze + US7 security tradeoff under `docs/design/` (reference `docs/security/threat-model-agent-disalignment.md`)
- [ ] T045 Run `/brain sync` to refresh frontmatter/description and regenerate stale `index.md` entries for the touched docs/specs pages (depends on T042, T043, T044)
- [ ] T046 [P] Verify the three review prompt variants remain in sync (shared includes only; no divergent rubric text) across `koan/skills/core/review/prompts/*.md`
- [ ] T047 Run `make lint` and the full suite `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/ -q`; run offline eval scorer `python -m app.skill_evals review`; fix any regression (depends on all prior)
- [ ] T048 Run the live eval `KOAN_EVAL_LIVE=1 python -m app.skill_evals review --live` and compare to `baseline.json`; confirm improvement / no regression before merge (spec SC-007)
- [ ] T049 Pre-PR hygiene: confirm the diff does not include `.specify/feature.json` (`git checkout main -- .specify/feature.json` if present); run the leak-pattern check; ensure the PR body **declares the architectural change** (FR-023)

---

## Dependencies & Execution Order

- **Setup (P1)** → **Foundational (P2)** gate everything. T005 (identity), T007 (config), T009 (sidecar) are hard prerequisites.
- **US1 (Phase 3)** depends on Foundational; it is the MVP and unblocks the consistency guarantee.
- **US2 (Phase 4)** depends on Foundational (independent of US1 at code level, but ship after US1 per priority). Provides the triage post-pass `review_triage.py`.
- **US6 (Phase 5)** depends on US2 (extends `review_triage`) and US1 (`review_reconcile` for FR-030 unify).
- **US5 (Phase 6)** depends on US2 (shared rubric partial); otherwise prompt-only.
- **US7 (Phase 7)** depends on Foundational (identity + sidecar); precedence over triage (needs US2 triage present to order correctly).
- **US3 (Phase 8)** depends on Foundational (identity) + US2/US1 (feeds triage/reconcile unchanged).
- **US4 (Phase 9)** depends on US2 (`review_triage` demote/drop output).
- **Polish (Phase 10)**: T042 contract-first can start early but must land with the code; T047/T048 last.

## Parallel execution examples

- **Foundational**: T004, T006, T008 (three independent test files) run in parallel; then T005/T007/T009.
- **US1**: T010 and T011 (separate test files) in parallel; T015 eval case parallel with T012/T013 code.
- **Cross-story after Foundational**: US1 code (T012/T013) and US2 test (T017) touch different files → parallelizable if two people/agents work concurrently, but ship in priority order.
- **Polish**: T043, T044, T046 in parallel; T045 after them; T047→T048 sequential.

## Implementation strategy

- **MVP = Phase 1 + 2 + Phase 3 (US1)**: delivers the core "no whiplash / reproduce-on-unchanged"
  guarantee — the user's most-cited frustration — and is independently shippable/testable.
- **Increment 2 = US2 (+US6)**: trustworthy yellow tier + pre-existing labeling.
- **Increment 3 = US5, US7**: exhaustive default recall + human-disposition honoring.
- **Increment 4 = US3 (opt-in), US4 (visibility)**: thoroughness escalation + auditability.
- Every increment: tests first (deterministic helpers) + a paired eval case, then wire into the
  runner, keeping the default path zero-regression (SC-008). The `specs/skills/review.md` contract
  update (T042) is landed contract-first and **declared** in the PR.
