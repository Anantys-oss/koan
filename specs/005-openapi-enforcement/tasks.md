---
description: "Task list for OpenAPI auto-generation & enforcement"
---

# Tasks: Auto-Generate & Enforce OpenAPI Spec for the REST API

**Input**: Design documents from `/specs/005-openapi-enforcement/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi-generator.md, quickstart.md

**Tests**: Included — the spec requires automated coverage (FR-013).

**Organization**: Grouped by user story (US1 generate, US2 CI enforcement, US3 guidance).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3
- Paths are repo-relative from `/Users/atoobot/workspace/koan/workspace/koan`.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Confirm dependencies already present (Flask, PyYAML in `koan/requirements.txt`); no new deps to add. Verify `koan/app/api/__init__.py::create_app()` is importable with a temp `KOAN_ROOT`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The decorator marker every downstream piece depends on for correct auth labelling.

- [X] T002 [US1] In `koan/app/api/auth.py`, tag `require_token`'s wrapper with
  `decorated._koan_requires_token = True` so security is decorator-derived (single source of
  truth). No behavior change to auth enforcement.

**Checkpoint**: Auth marker in place — generator can read it.

---

## Phase 3: User Story 1 - Generate the OpenAPI document from code (Priority: P1) 🎯 MVP

**Goal**: One command produces a deterministic, code-derived OpenAPI 3.1 document.

**Independent Test**: `make openapi` writes `koan/openapi.yaml`; running twice yields no diff;
paths/methods/security match the live route table.

### Tests for User Story 1 ⚠️ (write first, expect failure)

- [X] T003 [P] [US1] `koan/tests/test_openapi_gen.py`: assert `build_spec(create_app(...))`
  contains exactly the live route table's (path, method) pairs (excluding HEAD/OPTIONS), with
  Werkzeug `<x>` rendered as `{x}`; assert no extra paths.
- [X] T004 [P] [US1] Same file: assert `/v1/health` has `security: []` and a secured route
  (e.g. `GET /v1/status`) inherits the global `bearerAuth`; assert
  `components.securitySchemes.bearerAuth` = `{type: http, scheme: bearer}` and
  `components.schemas.Error` models `{"error": {"code", "message"}}`.
- [X] T005 [P] [US1] Same file: determinism — `dump_yaml(build_spec(app))` equals itself on a
  second app build (byte-identical); document parses as YAML and declares `openapi: 3.1.x`.

### Implementation for User Story 1

- [X] T006 [US1] Create `koan/app/api/openapi_gen.py`: `build_spec(app)`, `dump_yaml(spec)`,
  `generate(output_path)`, `check(output_path)`, and an `argparse` CLI
  (`--output`, `--check`) per contracts/openapi-generator.md. Uses a throwaway temp
  `KOAN_ROOT` (via `utils.koan_tmp_dir()`), never binds a port, never reads a token. Pins
  `info.version="1.0.0"`. Ruff-clean, Python 3.11+.
- [X] T007 [US1] Add Makefile targets `openapi` and `openapi-check` (and add both to `.PHONY`),
  invoked like the `webhook` target (`cd koan && KOAN_ROOT=… PYTHONPATH=. …`).
- [X] T008 [US1] Run `make openapi` to create the committed artifact `koan/openapi.yaml`.
  Verify `make openapi-check` passes and a second `make openapi` produces no diff.

**Checkpoint**: MVP — the document exists, is deterministic, and matches the code.

---

## Phase 4: User Story 2 - CI blocks drift, but only when the API changed (Priority: P1)

**Goal**: Path-filtered CI drift check with an actionable failure message.

**Independent Test**: Editing an API route without regenerating fails the check; editing
unrelated files does not trigger the job.

### Tests for User Story 2 ⚠️

- [X] T009 [P] [US2] In `koan/tests/test_openapi_gen.py`: `check()` returns `0` against a
  freshly-generated file and `1` (with the fix instruction on stderr) against a
  stale/missing file.

### Implementation for User Story 2

- [X] T010 [US2] Create `.github/workflows/openapi.yml`: `on.pull_request.paths` = the
  API-defining file set (`koan/app/api/**`, `koan/openapi.yaml`, `Makefile`,
  `.github/workflows/openapi.yml`); one job checks out, sets up Python 3.14, installs
  `koan/requirements.txt`, and runs `python -m app.api.openapi_gen --check --output openapi.yaml`
  in `koan/` with `KOAN_ROOT`/`PYTHONPATH` set.
- [X] T011 [US2] Ensure the `check()` failure message names `make openapi` + the git add/commit
  so CI logs are self-explanatory (verified by T009).

**Checkpoint**: Drift is non-shippable when the API changes; unrelated PRs pay nothing.

---

## Phase 5: User Story 3 - Contributors are reminded to regenerate (Priority: P2)

**Goal**: Guidance tells authors to regenerate + commit on any API change.

**Independent Test**: The guidance files name the trigger, the command, and the same-change
commit requirement.

### Implementation for User Story 3

- [X] T012 [P] [US3] Add an "OpenAPI spec" convention bullet to `CLAUDE.md` (root) — regenerate
  `koan/openapi.yaml` via `make openapi` and commit it in the same change when adding/removing/
  modifying a REST API endpoint.
- [X] T013 [P] [US3] Add the same guidance to `koan/app/CLAUDE.md`, next to the REST API module
  map, so it loads when editing `koan/app/`.

**Checkpoint**: Prevention loop closed.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T014 [P] Update `docs/operations/rest-api.md`: document `koan/openapi.yaml`, the
  `make openapi` / `make openapi-check` commands, the CI drift guard, and the
  regenerate-on-change workflow.
- [X] T015 [P] Update the durable component spec `specs/components/web.md`: add the OpenAPI
  generator + drift-guard contract and its invariants (decorator-derived security,
  determinism, path-filtered CI).
- [X] T016 Run `make lint` and the OpenAPI tests (`KOAN_ROOT=/tmp/test-koan .venv/bin/pytest
  koan/tests/test_openapi_gen.py -v`); fix any failures. Run `make openapi-check` to confirm
  the committed artifact is current.

---

## Dependencies & Execution Order

- **T001** (setup) → **T002** (foundational auth marker) blocks all generation correctness.
- **US1** (T003–T008) depends on T002; tests (T003–T005) before impl (T006–T008).
- **US2** (T009–T011) depends on US1 (needs the generator + committed artifact).
- **US3** (T012–T013) depends only on US1 existing (commands to reference); independent of US2.
- **Polish** (T014–T016) after US1–US3.

### Parallel Opportunities

- T003/T004/T005 (test cases, same file — author together but they are independent assertions).
- T012/T013 (different guidance files). T014/T015 (different doc/spec files).

## Implementation Strategy

MVP = Phase 1–3 (generator + artifact). Then Phase 4 (enforcement), Phase 5 (guidance),
Phase 6 (docs/spec/lint). Commit after each task (skip empty commits).
