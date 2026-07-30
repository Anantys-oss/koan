# Tasks: Config-driven mission hooks (`.koan/config.yaml` pre/post hooks)

**Input**: Design documents from `specs/011-config-mission-hooks/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — koan requires test-first for behavior changes
(`koan/CLAUDE.md` SDLC hygiene). Each implementation task ships with its tests.

**Organization**: Phase 1 lands the declared architectural spec edits contract-first
(Principle II). Phase 2 builds the shared, gated machinery. Phases 3–5 are the three
user stories (each independently testable). Phase 6 is docs/polish.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no incomplete-task dependency)
- **[Story]**: US1 / US2 / US3 for story phases; setup/foundational/polish carry none

---

## Phase 1: Contract-first spec edits (Principle II — declared architectural change)

- [ ] T001 [P] Extend the `.koan/config.yaml` reader contract in `specs/components/skills.md`: add the `pre_hooks`/`post_hooks` surface, the mission-type-over-`default` (replace, per-phase) precedence, caps/validation, and the fail-safe no-op guarantee; note the new `project_koan.get_mission_hooks` resolver.
- [ ] T002 [P] Extend the mission-execution contract in `specs/components/agent-loop.md`: add the `mission_hooks` module (gate + executor), the operator opt-in gate (`mission_hooks.enabled` + per-project override, default off), and the three pre/post call sites (skill-dispatch + agent-loop) with the no-double-fire invariant.

**Checkpoint**: durable contracts describe the intended design before code lands.

---

## Phase 2: Foundational (shared, blocking — no story label)

- [ ] T003 [P] Add `get_mission_hooks(project_path, mission_type, phase) -> list[str]` and its caps (`_MAX_HOOKS_PER_LIST`, `_MAX_HOOK_CMD_LEN`) to `koan/app/project_koan.py`: read via `read_koan_config`, resolve `<type>.<phase>_hooks` else `default.<phase>_hooks` else `[]`, drop non-string/blank/over-long entries, cap the list, one diagnostic on drop/cap; never raise. Tests in `koan/tests/test_project_koan.py` (absent/empty/malformed, precedence replace-not-merge per phase, `""` type ⇒ default only, caps, blank/non-string dropping).
- [ ] T004 [P] Add `is_mission_hooks_enabled() -> bool` to `koan/app/config.py` reading `mission_hooks.enabled` from `instance/config.yaml`, **default `False`**, fail-safe. Tests in `koan/tests/test_config.py` (default false, true when set, malformed ⇒ false).
- [ ] T005 [P] Add `get_project_mission_hooks(project_name) -> Optional[bool]` to `koan/app/projects_config.py` reading the per-project `mission_hooks:` key from `projects.yaml`. Tests in `koan/tests/test_projects_config.py` (unset ⇒ None, true/false honored).
- [ ] T006 Create `koan/app/mission_hooks.py`: module constants (`MISSION_HOOK_TIMEOUT`, `_MAX_HOOK_LOG_CHARS`), `hooks_enabled(project_name)` (per-project override else global), a private `_run_commands(commands, project_path, mission_type, status)` executor (`subprocess.run(cmd, shell=True, cwd=project_path, env={**os.environ, KOAN_MISSION_TYPE, [KOAN_MISSION_STATUS]}, capture_output=True, text=True, timeout=...)` per command, per-command try/except for `TimeoutExpired`/`Exception`, bounded log capture via `run_log.log_safe`, never raises), and public `run_pre_hooks(project_path, project_name, mission_type)` / `run_post_hooks(project_path, project_name, mission_type, success)` that gate-check first and no-op with one "skipped (not enabled)" diagnostic when off. Depends on T003–T005.

**Checkpoint**: resolver + gated executor exist and are unit-tested in isolation.

---

## Phase 3: US3 — Operator safety gate (Priority: P1) 🎯 security invariant

**Goal**: nothing from a repo config runs unless the operator opted in.
**Independent test**: gate absent (default) ⇒ configured hooks execute zero commands + one skip diagnostic; per-project `false` overrides a global `true`.

- [ ] T007 [US3] Add gate-behavior tests to `koan/tests/test_mission_hooks.py`: with `is_mission_hooks_enabled()` false, `run_pre_hooks`/`run_post_hooks` execute **no** subprocess (assert the `subprocess.run` boundary is never called) and log exactly one skip diagnostic; per-project override `False` beats global `True`; per-project `True` beats global `False`.

**Checkpoint**: default-off gate proven; safe to wire execution into the loop.

---

## Phase 4: US1 — Per-mission-type hooks around real missions (Priority: P1) 🎯 MVP

**Goal**: pre/post hooks fire in order around a real mission on both execution paths (review/fix/plan/rebase/implement via skill dispatch; refactor/free-form via the agent loop), success and failure alike.
**Independent test**: with the gate on and `review.pre_hooks`/`review.post_hooks` set, a review mission runs each command in order before/after; a failed mission still fires post-hooks with `KOAN_MISSION_STATUS=failure`.

- [ ] T008 [US1] Wire the skill-dispatch path in `koan/app/mission_executor.py` `_handle_skill_dispatch`: resolve `mtype = skill_dispatch.mission_command_name(mission_title)`; call `mission_hooks.run_pre_hooks(project_path, project_name, mtype)` just before the `_run_skill_mission` `try`; call `mission_hooks.run_post_hooks(project_path, project_name, mtype, exit_code == 0)` in the existing `finally`. Wrap each call so a hook-subsystem error never disturbs finalization.
- [ ] T009 [US1] Wire the agent-loop pre path in `koan/app/mission_executor.py` `_run_iteration`: at the existing `pre_mission` `fire_hook` site (reached only for non-skill missions), call `mission_hooks.run_pre_hooks(project_path, project_name, mission_command_name(mission_title))`.
- [ ] T010 [US1] Wire the agent-loop post path in `koan/app/mission_runner.py` `_fire_post_mission_hook`: call `mission_hooks.run_post_hooks(project_path, project_name, mission_command_name(mission_title), exit_code == 0)` beside the Python `post_mission` fire.
- [ ] T011 [US1] Wiring tests in `koan/tests/test_mission_executor.py` and `koan/tests/test_mission_runner.py` (patch `mission_hooks.run_pre_hooks`/`run_post_hooks`): assert the skill-dispatch path calls pre before and post after with `success` from `exit_code`, and fires post on failure/early-return; assert the agent-loop path calls pre (executor) once and post once with the right mission type; assert **no double-fire** (skill missions don't also hit the agent-loop sites).
- [ ] T012 [US1] Executor behavior tests in `koan/tests/test_mission_hooks.py` (gate on, against trivial shell `true`/`false`/`sleep` in a temp repo): commands run in listed order; a non-zero/`TimeoutExpired` command is logged and does not abort remaining commands or raise; `KOAN_MISSION_STATUS`/`KOAN_MISSION_TYPE` are visible to the command; output is captured/bounded.

**Checkpoint**: the motivating capability works end-to-end on both paths.

---

## Phase 5: US2 — `default` fallback & precedence (Priority: P2)

**Goal**: a mission type without its own section inherits `default`; a type with its own section replaces `default` for that phase.
**Independent test**: `default.pre_hooks` only ⇒ a `plan` mission runs the default pre-hooks; `default` + `review` both set ⇒ a review runs only `review.pre_hooks`, and post falls back to `default.post_hooks` when `review.post_hooks` is absent.

- [ ] T013 [US2] Add precedence-integration tests to `koan/tests/test_mission_hooks.py`: drive `run_pre_hooks`/`run_post_hooks` (gate on) against a repo config exercising (a) default-only inheritance, (b) type replaces default for pre, (c) per-phase independence (type overrides pre, inherits default post). Assert exactly the expected commands ran (no default+type duplication).

**Checkpoint**: precedence is validated at the execution layer, not just the resolver.

---

## Phase 6: Polish & cross-cutting (docs, sample, security, sync)

- [ ] T014 [P] Document pre_hooks/post_hooks in `docs/users/koan-md.md` (schema, per-phase replace-not-merge precedence, ordering, `KOAN_MISSION_STATUS`/`KOAN_MISSION_TYPE`, timeout/caps, fail-safe, and the operator opt-in requirement).
- [ ] T015 [P] Extend `docs/reference/koan-config.sample.yaml` with annotated `default`/`review`/… `pre_hooks`/`post_hooks` examples and a prominent "requires operator opt-in (`mission_hooks.enabled`), disabled by default" note.
- [ ] T016 [P] Write `docs/security/mission-hooks.md`: the RCE threat model (shell from a repo-controlled file), why the default-off operator gate + per-project override is the control, `cwd`/env/timeout/log-audit specifics, and safe-use guidance; cross-link from `docs/security/threat-model-agent-disalignment.md`.
- [ ] T017 [P] Update the `instance.example/config.yaml` (if present) and any operator-config docs to show the `mission_hooks: { enabled: false }` stanza with a one-line comment.
- [ ] T018 Run `/brain sync` — bump `updated:` frontmatter on touched docs, add `description:` to the new security page, regenerate stale `index.md` via `okf_backfill.py indexes`, refresh `wiki/index.md` entries.
- [ ] T019 Run `make lint` and the full `make test` (with `KOAN_ROOT` set); fix any failures. Confirm the diff does not stage `.specify/feature.json`.

**Checkpoint**: feature documented, indexed, linted, and green.

---

## Dependencies & execution order

- **Phase 1 (T001–T002)** first — contract-first.
- **Phase 2 (T003–T006)** blocks all wiring; T006 depends on T003–T005.
- **Phase 3 (US3, T007)** depends on T006 (gate exists).
- **Phase 4 (US1, T008–T012)** depends on T006; T008/T009/T010 touch different files ([P]-able), T011/T012 after their targets.
- **Phase 5 (US2, T013)** depends on T006.
- **Phase 6** after the code lands; T018 after T014–T017; T019 last.

## Parallel opportunities

- T001 ∥ T002 (different spec files).
- T003 ∥ T004 ∥ T005 (project_koan / config / projects_config — different files).
- T008 ∥ T009 ∥ T010 (executor/runner wiring in different functions/files) — then tests.
- T014 ∥ T015 ∥ T016 ∥ T017 (independent docs files).

## MVP scope

**Foundational (Phase 2) + US3 gate (Phase 3) + US1 wiring (Phase 4)** is the MVP:
gated, safe, per-type pre/post hooks around real missions on both paths. US2
(explicit precedence validation) and Phase 6 docs harden and ship it.

## Independent test criteria

- **US1**: gate on + `review.pre_hooks`/`post_hooks` ⇒ ordered execution before/after a review; failed mission still fires post with `KOAN_MISSION_STATUS=failure`.
- **US2**: default inheritance for an unlisted type; type replaces default per phase (no duplication).
- **US3**: gate off (default) ⇒ zero commands + one skip diagnostic; per-project override wins.
