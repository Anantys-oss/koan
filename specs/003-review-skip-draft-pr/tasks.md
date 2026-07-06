---

description: "Task list for the draft-PR auto-review gate (review_draft_skip)"

---

# Tasks: Draft-PR Auto-Review Gate (`review_draft_skip`)

**Input**: `specs/003-review-skip-draft-pr/plan.md`, `spec.md`

**Tests**: Included (project mandates tests for behavior changes).

**Organization**: Grouped by user story (spec.md US1–US4). Foundational config +
fetch changes (T1–T3) are done first; the gate (T4) depends on them.

## Format: `[ID] [P?] [Story] Description`

## Foundational

- [ ] **[T1]** [US1] **Config getter.** Add `get_review_draft_skip_config() -> {"enabled": bool}` (default `False`, safe coercion) to `koan/app/config.py`, beside the other `get_review_*` functions. `plan.md` D1.
- [ ] **[T2]** [US1] **Validator registration.** Register `review_draft_skip` in `koan/app/config_validator.py`: add `"review_draft_skip": _NESTED` to `CONFIG_SCHEMA` and `"review_draft_skip": {"enabled": "bool"}` to `SECTION_SCHEMAS`. `plan.md` D2.
- [ ] **[T3]** [US1] **`draft` in subject fetch.** In `koan/app/github_command_handler.py::_fetch_subject_info`, extend the `jq` to include `draft: .draft` and update the docstring. `plan.md` D3.

## Behavior

- [ ] **[T4]** [US1] **The soft-skip gate.** In `_try_assignment_notification` (`koan/app/github_command_handler.py`), after the closed/merged skip block, defer a draft `review_requested` notification when `get_review_draft_skip_config()["enabled"]` is true: log, notify, `mark_notification_read`, set `NOTIFICATION_OUTCOME_HANDLED_NOOP`, `return True`. Must NOT call `track_thread`/`set_review_cooldown`. `plan.md` D4, spec FR-003/FR-004.
- [ ] **[T5]** [US1] **Notify helper.** Add `_notify_draft_pr_skipped(owner, repo, subject_title, notification)` to `koan/app/github_command_handler.py`, mirroring `_notify_closed_subject_skipped` (INFO priority, swallows errors). `plan.md` D5, spec FR-007.

## Tests

- [ ] **[T6]** [P] [US1/US2/US3] **Assignment-path tests.** Extend the autouse `_stub_subject_info` seam in `koan/tests/test_github_command_handler.py` with a `subject_draft` fixture (default `False`). Add to `TestTryAssignmentNotification`: draft+gate-on defers (AC2); draft+gate-off queues (AC1); non-draft+gate-on queues (AC3); `@bot /review` mention on draft+gate-on queues (AC4). `plan.md` D6.
- [ ] **[T7]** [P] [US1] **Config unit test.** Add a test for `get_review_draft_skip_config`: default disabled, reads `enabled: true`, coerces non-dict/non-bool to disabled (AC5). Locate alongside existing review-config tests.
- [ ] **[T8]** [P] [US1] **Validator test.** Assert `review_draft_skip` is recognized (no "unrecognized key" warning) and that a non-bool `enabled` warns (AC6). Add to the config-validator test file.

## Docs

- [ ] **[T9]** [P] [US1] **Example config + docs.** Add a commented `review_draft_skip:` block to `instance.example/config.yaml` (after `review_inline_comments`) and a short entry to the relevant review-config doc page. `plan.md` D7.

## Done when

- AC1–AC6 pass; `make lint` clean; spec/plan/tasks committed with the implementation.
