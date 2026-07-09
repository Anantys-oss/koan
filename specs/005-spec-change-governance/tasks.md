---
description: "Task list for the spec-change governance gate"
---

# Tasks: Spec Changes Are Reviewed Architectural Changes

**Input**: `specs/005-spec-change-governance/{spec,plan,research,data-model,contracts/,quickstart}.md`

**Tests**: Included — FR-011 explicitly requires unit tests for the guard.

**Organization**: Grouped by user story (US1 enforcement, US2 author-time surfacing,
US3 written discipline). Each commit is one task (per the `/speckit` pipeline).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no ordering dependency).

---

## Phase 1: Enforcement core — US1 (P1)

**Purpose**: The load-bearing guard + its tests. This is the MVP: without it the
instruction change is advisory.

- [x] **T001** [US1] Implement `scripts/spec_change_guard.py` with pure functions
  `is_durable_contract`, `has_architecture_declaration`, `evaluate`, the impure
  `changed_files(base_ref)`, and a `main()`/`argparse` CLI honouring the exit-code and
  output contract in `contracts/spec_change_guard.md`. Stdlib only.
- [x] **T002** [US1] Add `koan/tests/test_spec_change_guard.py` covering: durable-contract
  detection (components/skills positive; nested skill path), exclusions (`index.md`,
  `SKILL_SPEC_TEMPLATE.md`, `specs/<NNN>/…`, `specs/README.md`, `docs/…`), declaration
  parsing (checked / unchecked / absent / case + bullet variants), `evaluate` verdicts
  (clean, undeclared, declared, fail-closed), and the CLI exit codes via `subprocess`
  or `main()` return. (FR-005, FR-006, FR-010, FR-011; SC-001, SC-002.)

---

## Phase 2: Author-time surfacing — US2 (P2)

**Purpose**: Make the declaration discoverable so it is not a surprise CI failure.

- [x] **T003** [P] [US2] Add `.github/PULL_REQUEST_TEMPLATE.md` containing the
  architectural-change declaration section with the exact marker phrase the guard
  recognises, plus a one-line "check this when you touched `specs/components/**` or
  `specs/skills/**`" instruction, and a normal summary/testing scaffold. (FR-008; SC-004.)
- [x] **T004** [US2] Add `.github/workflows/spec-change-guard.yml`: `pull_request`
  (`opened|synchronize|reopened|edited` → `main`), `permissions: contents: read`,
  checkout full-depth, setup-python 3.11, write `github.event.pull_request.body` to a
  file, run the guard with `--base-ref origin/$BASE_REF --pr-body-file`. No push, no
  secrets. (FR-007.)

---

## Phase 3: Written discipline — US3 (P1)

**Purpose**: Reconcile the four governance texts + record the decision. Depends on the
final marker string/paths chosen in Phase 1–2.

- [x] **T005** [US3] Amend `.specify/memory/constitution.md`: rewrite Principle II so a
  durable-contract change is a contract-first, rare, **declared** architectural change
  (remove "update the spec in the same branch to reflect the new design"); update the
  Workflow & Quality Gates "docs-and-specs-in-branch" gate to reference the declaration;
  bump the version (2.0.0 → 3.0.0, MAJOR — prior compliance redefined) and update
  **Last Amended**; prepend a fresh Sync Impact Report enumerating every reconciled
  artifact. (FR-001, FR-002; SC-003, SC-006.)
- [x] **T006** [P] [US3] Update the *Specs discipline (mandatory)* section in **both**
  `CLAUDE.md` (repo root) and `workspace/koan/CLAUDE.md` to the new rule, cross-linking
  the guard (`scripts/spec_change_guard.py`) and the decision doc. (FR-003; SC-003.)
- [x] **T007** [P] [US3] Update `specs/README.md` *Spec discipline* section to the new
  rule, preserving the durable-vs-ephemeral distinction it already documents. (FR-004;
  SC-003.)
- [x] **T008** [P] [US3] Write `docs/design/spec-changes-are-architectural.md` (OKF
  frontmatter, `tags: [design]`): rationale (specs are contract-first, not code-mirrors),
  customer origin (PR #2052 comment), the enforcement (guard + declaration + template),
  and the rejected heavier alternative (mandatory two-PR split). Add a short
  cross-linked entry to `docs/design/decisions.md`. (FR-009.)

---

## Phase 4: Close the loop

- [ ] **T009** Run `make lint` and the guard's tests
  (`KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_spec_change_guard.py -v`);
  fix failures. Run `/brain sync` to refresh frontmatter/indexes for the new/edited
  docs & specs pages. (SC-005.)

---

## Dependencies

- **T001 → T002** (tests import the module).
- **T001/T003 → T004** (workflow invokes the guard; template defines the marker).
- **T001–T004 → T005–T008** (governance text cites the final guard path + marker).
- **all → T009**.

## Parallelizable

- T003 is independent of T001/T002 (different files) — [P].
- T006, T007, T008 touch different files — [P] among themselves (after T005 fixes the
  canonical wording).

## MVP

T001 + T002 (US1) alone deliver the enforceable gate. US2 (T003–T004) makes it usable;
US3 (T005–T008) makes the written discipline match. Ship all three for the full request.
