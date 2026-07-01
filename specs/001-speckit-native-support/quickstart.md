# Quickstart — Native Spec-Kit (`/speckit`) Validation Guide

**Feature**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)

Runnable scenarios that prove `/speckit` works end-to-end. These are **validation**
scenarios (behavior), not implementation steps — full task-level steps live in
`tasks.md`. See the [skill interface contract](./contracts/skill-interface.md) and
[data model](./data-model.md) for the exact shapes.

## Prerequisites

- Kōan venv + a working Claude Code CLI provider (`make setup`, `make status`).
- A **target project** configured in `projects.yaml` with a spec-kit constitution at
  `<project>/.specify/memory/constitution.md` (the gate fails without it).
- The `.specify/` Claude integration present (speckit skills resolvable).
- Tests run with `KOAN_ROOT` set: `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest …`.

## Unit/behavior tests (fast, no Claude subprocess)

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_speckit_skill.py -v
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/ -k "core_skill_group" -v
```

Validate (assert on observable state, never source text):
1. **Constitution gate** — handler invoked for a project **without** a constitution →
   returns an error naming the file + project and calls `insert_pending_mission` **zero**
   times.
2. **Quota hold** — `remaining_budget()` below `quota_threshold` → mission is left
   Pending and skipped on pickup (not started, not failed); raising the budget lets it
   proceed.
3. **Mission queuing** — valid `/speckit myproject add X` → exactly one mission queued
   (`[project:myproject]`, `model_key: mission`); args/`repo:`/`branch:` tokens parsed
   and stripped from the goal text.
4. **From-branch arg parsing** — `/speckit_from_branch myrepo mybranch` → resolves
   project, sets `entry_mode=from_branch`, `base_branch=mybranch`; missing either arg →
   usage error.
5. **Prompt assembly** — the queued mission's prompt is loaded from
   `prompts/speckit.md` (no inline prompt); the `from_branch` prompt carries the
   specify-skip + base-branch instructions.
6. **Group enforcement** — both SKILL.md files have `group: code` (the
   `TestCoreSkillGroupEnforcement` test passes).

## End-to-end scenarios (require a live target project + Claude quota)

Run via the chat bridge (`make say m="…"`) or `@mention`. Each should land a **draft** PR
on a prefixed branch.

1. **Chat trigger → full run**
   - Send: `/speckit myproject add a CSV export button`
   - Expect: progress notes per step, then a draft PR in `myproject` bundling
     `spec.md`/`plan.md`/`tasks.md` + implementation; reply carries the PR link.

2. **Constitution-missing abort**
   - Send `/speckit` against a project with **no** constitution.
   - Expect: a clear, actionable error naming the missing file + project; no PR, no
     speckit steps run.

3. **Quota hold**
   - With quota below the threshold, send `/speckit myproject …`.
   - Expect: the mission is queued and held; it starts automatically once quota recovers
     (no abort, no failure).

4. **Issue-URL trigger**
   - Send: `/speckit https://github.com/owner/repo/issues/42 repo:myrepo branch:mybranch`
   - Expect: the issue title + body + comments become the goal; draft PR opens against
     `myrepo`.

5. **From-branch resume**
   - Push a validated `spec.md` to `my-spec-branch` in a constitution-backed project.
   - Send: `/speckit_from_branch myproject my-spec-branch`
   - Expect: `specify` is **not** run; `plan` consumes the existing spec; a new `koan/*`
     branch is cut from `my-spec-branch`; draft PR opens (human's branch untouched).

6. **Best-effort review/CI**
   - Force an unfixable review finding (or a failing CI check) on a run that otherwise
     implements successfully.
   - Expect: a draft PR **still** opens, with the unresolved finding/failure summarized
     in the PR body (steps 5–6 never abort).

## Expected outcomes (acceptance)

- Every successful run yields exactly **one** draft PR on a `koan/*` branch, never
  merged, bundling the speckit artifacts + implementation.
- The operator can tell from the final reply alone whether the run succeeded (PR link),
  aborted (step + reason), or completed with unresolved findings (PR link + summary)
  (SC-004).
- Implement history shows ≥ one commit per `tasks.md` task (SC-010).
