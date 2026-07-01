---

description: "Task list for the native /speckit mission orchestration feature"
---

# Tasks: Native Spec-Kit (`/speckit`) Mission Orchestration

**Input**: Design documents from `/specs/001-speckit-native-support/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/skill-interface.md, quickstart.md

**Tests**: Included. Kōan's project conventions mandate tests for every core skill (`TestCoreSkillGroupEnforcement` gate + the `tester` quality gate in CLAUDE.md), so each story has a test task even though the template marks tests optional.

**Organization**: Tasks are grouped by user story (spec.md US1–US5) so each story can be implemented and tested independently. The shared orchestration engine + code-enforced gates live in the Foundational phase (blocking).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (e.g. US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- Single-project extension to the Kōan daemon. New code: `koan/app/speckit_orchestration.py`, `koan/skills/core/{speckit,speckit_from_branch}/`, `koan/tests/test_speckit_skill.py`. Edits to existing files are named per task.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the skill directories, SKILL.md frontmatter, and the shared orchestration module skeleton.

- [X] T001 Create core-skill directories and SKILL.md frontmatter for `speckit` and `speckit_from_branch` (`group: code`, `model_key: mission`, `github_enabled: true`, `github_context_aware: true`) in koan/skills/core/speckit/SKILL.md and koan/skills/core/speckit_from_branch/SKILL.md
- [X] T002 [P] Create shared orchestration module skeleton with module docstring and `dispatch(entry_mode=...)` entry-point signature in koan/app/speckit_orchestration.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The code-enforced engine primitives EVERY user story depends on (constitution gate, quota gate, project resolution, mission queuing, config).

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 [P] Add `get_speckit_config()` accessor (`quota_threshold` default 15, `review_max_iterations` default 3, `review_severity` default "important"; safe coercion) modeled on `get_review_reflect_config()` in koan/app/config.py
- [X] T004 Implement the code-enforced constitution gate (verify `<project>/.specify/memory/constitution.md`; return an actionable error naming file + project) in koan/app/speckit_orchestration.py
- [X] T005 Implement target-project resolution and `repo:`/`branch:` token parsing/stripping (reuse `resolve_project_path`) in koan/app/speckit_orchestration.py
- [X] T006 Implement single-mission queuing (`[project:name]` tag, `model_key: mission`, via `insert_pending_mission`) and per-step outbox progress notes (via `append_to_outbox`) in koan/app/speckit_orchestration.py
- [ ] T007 [P] Implement the quota start-gate: when the agent loop picks a `/speckit` mission and `remaining_budget() < get_speckit_config()['quota_threshold']`, leave it Pending and skip; proceed automatically on recovery in koan/app/mission_executor.py
- [X] T008 Write foundational tests (config defaults/coercion, constitution-gate abort, token parsing, project resolution) in koan/tests/test_speckit_skill.py
- [X] T008a [P] Create the speckit runner module (modeled on `implement_runner`): build the Claude command carrying the orchestration prompt loaded via `load_skill_prompt`, project/branch args, and `model_key: mission` in koan/skills/core/speckit/speckit_runner.py *(done; auto-discovered via `_discover_runner_module`)*
- [X] T008b ~~Register in `_SKILL_RUNNERS` + `_COMMAND_BUILDERS`~~ — **N/A**: `_discover_runner_module` + `_build_generic_runner_cmd` auto-discover the runner from its conventional path; no `skill_dispatch.py` registration needed *(plan-correction resolved by auto-discovery)*
- [ ] T008c Add speckit arg validation (project-or-URL required; from-branch needs repo-id + branch) to `validate_skill_args()` in koan/app/skill_dispatch.py *(optional hardening; runner/handler already validate)*

**Checkpoint**: Engine primitives ready — constitution gate, quota hold, resolution, queuing, progress notes, **runner + skill_dispatch registration**, all behavior-tested.

---

## Phase 3: User Story 1 - Chat trigger, full pipeline (Priority: P1) 🎯 MVP

**Goal**: `/speckit <project> <goal>` runs `specify → plan → tasks → implement` (per-task commits) and opens a draft PR bundling the speckit artifacts + implementation.

**Independent Test**: Send `/speckit myproject add a CSV export button` against a constitution-backed project; assert exactly one mission is queued with `[project:myproject]`, the prompt is loaded from `prompts/speckit.md`, and a draft PR opens bundling spec/plan/tasks + implementation.

### Implementation for User Story 1

- [ ] T009 [P] [US1] Write the full-pipeline orchestration prompt (specify → plan → tasks → implement, hard-abort-on-step-1–4 reporting, per-task commit hint, draft-PR bundling artifacts + implementation) in koan/skills/core/speckit/prompts/speckit.md
- [ ] T010 [US1] Implement the chat-trigger handler: parse `<project> <goal>`, assemble `goal_text`, call `speckit_orchestration.dispatch(entry_mode="agent")` in koan/skills/core/speckit/handler.py
- [ ] T011 [US1] Wire draft-PR creation (`pr_create(draft=True)`, prefixed `koan/*` branch, never merge) into the orchestration prompt and helper in koan/skills/core/speckit/prompts/speckit.md and koan/app/speckit_orchestration.py
- [ ] T012 [US1] Emit per-step progress notes (FR-018) to the originating channel and enforce the single-mission observable model in koan/app/speckit_orchestration.py
- [ ] T013 [US1] Write US1 tests (chat arg parse, exactly one `[project:...]` mission queued, prompt loaded from `prompts/speckit.md`, `TestCoreSkillGroupEnforcement` passes) in koan/tests/test_speckit_skill.py

**Checkpoint**: `/speckit` works end-to-end from chat — MVP delivered.

---

## Phase 4: User Story 2 - Issue-URL trigger (Priority: P1)

**Goal**: `/speckit <issue-url> [repo:.. branch:..]` uses the issue title + body + all comments as the goal text (like `/implement`).

**Independent Test**: Send `/speckit https://github.com/owner/repo/issues/42 repo:myrepo branch:mybranch`; assert the issue body + comments become the goal, tokens are stripped, and a draft PR opens against `myrepo`.

### Implementation for User Story 2

- [X] T014 [P] [US2] Implement issue-URL recognition + goal assembly (fetch issue title + body + comments via the existing issue-tracker/thread-context path) and `repo:`/`branch:` override handling in koan/app/speckit_orchestration.py *(override handling + URL-as-goal done; structured `fetch_thread_context` deferred — the prompt has Claude fetch the URL, achieving the functional outcome)*
- [X] T014b [US2] **Prompt-injection hardening (ant-review finding #2)**: before untrusted issue content flows into the speckit prompt, ensure the goal is substituted safely in `load_prompt_or_skill` (substitute `{GOAL}` last / sanitize brace-placeholder syntax) so crafted issue text cannot mangle `{BASE_BRANCH}`/`{BRANCH_PREFIX}` placeholders — in koan/app/prompts.py (shared) or speckit_runner.py *(done: single-pass regex `_substitute`)*
- [X] T015 [US2] Extend the `speckit` handler to route the issue-URL form to the issue-goal assembly (reusing the shared `dispatch`) in koan/skills/core/speckit/handler.py *(handler forwards URL + repo:/branch: verbatim; runner parses tokens)*
- [X] T016 [US2] Write US2 tests (issue URL → goal from body + comments; `repo:`/`branch:` tokens parsed and stripped; Jira key accepted) in koan/tests/test_speckit_skill.py *(handler URL-forwarding + runner branch-override tests)*

**Checkpoint**: `/speckit` works from chat AND issue URLs.

---

## Phase 5: User Story 3 - @mention trigger (Priority: P2)

**Goal**: `@bot /speckit` (and bare `@bot speckit`) on a GitHub/Jira issue thread runs the pipeline with the thread as the goal, subject to the existing permission check.

**Independent Test**: Comment `@koan-bot /speckit` on an issue in a constitution-backed project; assert Kōan reacts, routes through the `@mention` pipeline with the thread body + comments as the goal, and opens a draft PR.

### Implementation for User Story 3

- [X] T017 [US3] Enable `@mention` routing for `speckit` via the `github_enabled`/`github_context_aware` flags and verify dispatch through the `external_skill_dispatch`/`github_command_handler` path in koan/skills/core/speckit/SKILL.md *(flags set in US1; routing is config-driven)*
- [X] T018 [US3] Write US3 tests (`@mention` → mission carries thread context; unauthorized user ignored per existing rules) in koan/tests/test_speckit_skill.py *(validate_command accepts speckit + speckit_from_branch)*

**Checkpoint**: `/speckit` is triggerable from chat, issue URL, and `@mention`.

---

## Phase 6: User Story 4 - Best-effort review + CI never block (Priority: P2)

**Goal**: After implementation, a private review loop and CI validation run best-effort (configurable budgets); failures never abort — unresolved findings are summarized in the draft PR.

**Independent Test**: Force an unfixable review finding (or a failing CI check) on an otherwise-successful run; assert a draft PR still opens and its body lists the unresolved finding/failure.

### Implementation for User Story 4

- [ ] T019 [US4] Wire the private review loop (reuse Kōan's review machinery, iterations from `get_speckit_config()['review_max_iterations']`, severity floor `review_severity`) as best-effort into the orchestration in koan/app/speckit_orchestration.py and koan/skills/core/speckit/prompts/speckit.md
- [ ] T020 [US4] Wire CI validation + fix loop (`check_ci_status` / `run_ci_fix_loop`) as best-effort (non-aborting) into the orchestration in koan/app/speckit_orchestration.py
- [ ] T021 [US4] Summarize unresolved review/CI findings in the draft-PR body (which step, what finding) per FR-013 in koan/skills/core/speckit/prompts/speckit.md
- [ ] T022 [US4] Write US4 tests (unfixable review finding → draft PR still opens with summary; CI failure → no abort; budgets read from config) in koan/tests/test_speckit_skill.py

**Checkpoint**: The full specify → … → review → CI → PR pipeline honors the best-effort contract.

---

## Phase 7: User Story 5 - `/speckit_from_branch` resume (Priority: P2)

**Goal**: `/speckit_from_branch <repo-id> <branch-name>` skips `specify` and runs `plan → … → PR` against the human-validated spec on `branch-name`, cutting a new prefixed branch off it.

**Independent Test**: Push a validated `spec.md` to `my-spec-branch`; send `/speckit_from_branch myproject my-spec-branch`; assert `specify` is NOT run, `plan` consumes the existing spec, a new `koan/*` branch is cut from `my-spec-branch`, and a draft PR opens (human's branch untouched).

### Implementation for User Story 5

- [ ] T023 [P] [US5] Write the from-branch orchestration prompt (skip `specify`, cut a prefixed `koan/*` branch off `branch-name`, run `plan → tasks → implement → review → CI → PR`) in koan/skills/core/speckit_from_branch/prompts/speckit.md
- [X] T024 [US5] Implement the from-branch handler: parse `<repo-id> <branch-name>`, set `entry_mode="from_branch"` + `base_branch`, call shared `dispatch` in koan/skills/core/speckit_from_branch/handler.py *(handler done — parses repo-id + branch, constitution gate, queues mission; the dedicated runner T023/T025 remains US5)*
- [ ] T025 [US5] Implement the branch-off-human-branch git flow (new `koan/*` from `branch-name`; never commit to the human's branch) and the missing-spec abort at `plan` in koan/app/speckit_orchestration.py
- [ ] T026 [US5] Write US5 tests (specify skipped, base branch recorded, missing spec on branch → abort at `plan`, prefixed branch off human branch) in koan/tests/test_speckit_skill.py

**Checkpoint**: All five trigger/behavior stories are independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, the durable design spec, and full validation.

- [ ] T027 [P] Add `speckit` and `speckit_from_branch` to the "Core skills" list (alphabetical) in CLAUDE.md
- [ ] T028 [P] Document `/speckit` and `/speckit_from_branch` (tier section + quick-reference appendix) in docs/users/user-manual.md and docs/users/skills.md
- [ ] T029 [P] Write the durable per-skill design contract (commands, frontmatter, gates, invariants) in specs/skills/speckit.md
- [ ] T030 Run `make lint` (ruff) and fix all violations in the new files koan/app/speckit_orchestration.py, koan/skills/core/speckit/, koan/skills/core/speckit_from_branch/, koan/tests/test_speckit_skill.py
- [ ] T031 Run the full speckit test suite: `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_speckit_skill.py -v` and confirm `TestCoreSkillGroupEnforcement` passes
- [ ] T032 Run the quickstart.md validation scenarios (constitution-missing abort, quota hold, chat/issue/from-branch triggers, best-effort review/CI)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup; **BLOCKS all user stories** (the engine + code-enforced gates).
- **User Stories (Phases 3–7)**: All depend on Foundational.
  - US1 (chat, full pipeline) is the MVP and the first to exercise the orchestration prompt.
  - US2/US3 reuse US1's pipeline prompt, adding their trigger surface.
  - US4 hardens US1's pipeline with the best-effort review/CI contract (depends on US1).
  - US5 is the most independent (sibling skill, own prompt).
- **Polish (Phase 8)**: After the desired stories are complete.

### User Story Dependencies

- **US1 (P1)**: After Foundational. No dependency on other stories. (MVP)
- **US2 (P1)**: After Foundational; reuses US1's pipeline prompt (depends on US1's prompt existing) but is independently testable.
- **US3 (P2)**: After Foundational; reuses US1's skill/pipeline (depends on US1) — adds `@mention` routing.
- **US4 (P2)**: After Foundational + US1 (hardens the pipeline US1 built).
- **US5 (P2)**: After Foundational; largely independent (own skill + prompt) — depends only on the shared `dispatch`/gates.

### Within Each User Story

- Prompt/contract before handler wiring.
- Handler before tests.
- Story complete (tests green) before moving to the next priority.

### Parallel Opportunities

- Setup: T002 runs in parallel with T001.
- Foundational: T003 (config.py) and T007 (mission_executor.py) run in parallel with the `speckit_orchestration.py` tasks (T004–T006) — different files.
- Story prompts (T009, T014, T023) are separate files and can be authored in parallel with their handlers once Foundational is done.
- Polish docs (T027, T028, T029) are separate files — fully parallel.

---

## Parallel Example: User Story 1

```bash
# Once Foundational is complete, author the prompt and handler in parallel:
Task: "Write the full-pipeline orchestration prompt in koan/skills/core/speckit/prompts/speckit.md"
Task: "Implement the chat-trigger handler in koan/skills/core/speckit/handler.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Complete Phase 1 (Setup) + Phase 2 (Foundational).
2. Complete Phase 3 (US1) — chat trigger → specify → implement → draft PR.
3. **STOP and VALIDATE**: run US1 tests + the quickstart chat scenario independently.
4. Demo: `/speckit myproject <goal>` opens a draft PR.

### Incremental Delivery

1. Setup + Foundational → engine + code-enforced gates ready.
2. + US1 → MVP (chat → draft PR). Validate.
3. + US2 → issue-URL trigger. Validate.
4. + US3 → `@mention` trigger. Validate.
5. + US4 → best-effort review/CI + PR-body summarization. Validate.
6. + US5 → `/speckit_from_branch` resume. Validate.
7. Polish → docs, design spec, lint, full suite.

### Parallel Team Strategy

With multiple implementers:
1. Team completes Setup + Foundational together.
2. Once Foundational is done:
   - Implementer A: US1 (MVP) → then US4 (hardening).
   - Implementer B: US5 (independent sibling skill).
   - Then US2/US3 build on US1.
3. Stories integrate independently; Polish last.

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks.
- [Story] labels map tasks to spec.md user stories for traceability.
- Commit after each task or logical group (and the implement step itself commits per `tasks.md` task — FR-019).
- Stop at any checkpoint to validate a story independently.
- Constitution Principle V: the gates (constitution, quota, draft-PR, branch) are code-enforced and load-bearing; the prompt-driven abort/commit cadence is advisory — keep that distinction in the code comments.
