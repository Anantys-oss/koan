# Feature Specification: Auto-Generate & Enforce OpenAPI Spec for the REST API

**Feature Branch**: `005-openapi-enforcement`

**Created**: 2026-07-10

**Status**: Draft

**Input**: User description: "We are exposing some REST API to interact with the bot. The goal of that mission is to automatically generate and enforce some OpenAPI specs. We can consider adding a Makefile target to generate the openapi specs and enforce using a smart CI pipeline that the openapi specs is up to date. Only need to run the ci if some API are updated. If some delta is detected then we ask the user to run and update the openapi specs using a clear instruction. We should also add some hints to CLAUDE.md to automatically generate/update openapi specs after updating/adding/removing some api."

## Overview

Kōan ships an optional, token-authenticated HTTP control layer (the REST API under
`koan/app/api/`, documented in `docs/operations/rest-api.md`). Today its contract lives
only in prose docs and in the Flask route code. There is no machine-readable description
of the API, so external consumers cannot generate clients, validate requests, or diff the
surface across releases — and nothing prevents the docs and the code from silently drifting
apart.

This feature adds a **checked-in OpenAPI document** that is **generated from the running
Flask application** (never hand-maintained in isolation) plus a **drift guard** that keeps
the committed artifact in lock-step with the code. Contributors regenerate it with a single
command; CI verifies it is current, but only spends CI time when API-defining files actually
change. When drift is detected, CI fails with a copy-pasteable instruction telling the human
exactly how to refresh the artifact.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Generate the OpenAPI document from code (Priority: P1)

A contributor (human or the Kōan agent) has changed the REST API — added a route, removed
one, or changed its methods — and wants the machine-readable contract to reflect that. They
run one command and a canonical OpenAPI document is (re)written to a known path in the repo,
derived entirely from the actual Flask application so it cannot describe endpoints that do
not exist.

**Why this priority**: Without a generator, there is nothing to enforce and no artifact to
consume. This is the MVP — everything else builds on a deterministic "code → spec" step.

**Independent Test**: Run the generate command on a clean checkout; confirm it writes a
valid OpenAPI document whose paths/methods exactly match the routes registered by the Flask
app factory, and that running it twice with no code change produces byte-identical output.

**Acceptance Scenarios**:

1. **Given** the current REST API code, **When** the contributor runs the generate command,
   **Then** a valid OpenAPI document is written to the canonical path and every registered
   route (path + HTTP method) appears in it, and only routes that are registered appear.
2. **Given** an already-generated document and no code change, **When** the generate command
   is run again, **Then** the output is byte-for-byte identical (deterministic, stable key
   ordering) so it produces no spurious diff.
3. **Given** a route protected by the bearer-token decorator, **When** the document is
   generated, **Then** that operation is marked as requiring the security scheme, while the
   unauthenticated health probe is marked as not requiring it.

---

### User Story 2 - CI blocks drift, but only when the API changed (Priority: P1)

A contributor opens a pull request. If their change touches API-defining files but they
forgot to regenerate the committed OpenAPI document, CI fails and tells them precisely how
to fix it. If their change does **not** touch any API-defining file, the OpenAPI drift check
does not run at all — no wasted CI minutes.

**Why this priority**: Enforcement is the core ask. A generator nobody is required to run
rots immediately. Path-scoping the check is an explicit requirement ("only need to run the
ci if some API are updated").

**Independent Test**: Open a PR that edits an API route without regenerating → the drift job
runs and fails with a clear instruction. Open a PR that edits only unrelated files → the
drift job is skipped (does not run). Open a PR that edits an API route and regenerates → the
drift job runs and passes.

**Acceptance Scenarios**:

1. **Given** a PR whose diff includes an API-defining file and a stale committed document,
   **When** CI runs, **Then** the drift check runs, fails, and its output states the exact
   command to regenerate and commit the document.
2. **Given** a PR whose diff includes **no** API-defining file, **When** CI runs, **Then**
   the drift check is skipped (not executed), regardless of the state of the document.
3. **Given** a PR whose diff includes an API-defining file **and** an already-regenerated
   document, **When** CI runs, **Then** the drift check runs and passes.

---

### User Story 3 - Contributors are reminded to regenerate (Priority: P2)

A contributor (especially the autonomous Kōan agent, whose per-project guidance comes from
`CLAUDE.md` and nested `CLAUDE.md` files) is editing the REST API. Project guidance instructs
them to regenerate and commit the OpenAPI document in the same change whenever they add,
remove, or modify an API endpoint, so drift is prevented at authoring time rather than caught
late in CI.

**Why this priority**: Prevention beats detection. This closes the loop so the common case
(regenerate before pushing) is documented and habitual; CI becomes the backstop, not the
primary discovery mechanism. Lower priority because the CI guard (US2) already makes drift
non-shippable — this reduces friction rather than adding safety.

**Independent Test**: Read the relevant `CLAUDE.md` guidance; confirm it names the trigger
(adding/removing/modifying an API endpoint), the regenerate command, and the requirement to
commit the regenerated artifact in the same change.

**Acceptance Scenarios**:

1. **Given** the project guidance files, **When** a contributor consults them before editing
   the API, **Then** they find an explicit instruction to regenerate and commit the OpenAPI
   document after any API change, with the exact command.

---

### Edge Cases

- **API disabled at runtime**: The API is disabled by default (`api.enabled: false`).
  Generation MUST NOT require the API to be enabled or a server to be running — it inspects
  the app's route table in-process, independent of runtime config or a bound port.
- **No token configured**: Generation MUST NOT require a real bearer token; it describes the
  *shape* of authentication (the security scheme), not any secret value. No secret is ever
  written into the document.
- **Ordering instability**: Route registration or dictionary iteration order MUST NOT change
  the output; the generator emits deterministically sorted keys so re-running never yields a
  spurious diff.
- **Partial / non-blueprint routes**: Routes defined directly on the app (e.g. the inline
  `/v1/health` endpoint) MUST be included alongside blueprint routes.
- **Non-default success codes**: Some operations return non-200 success (`POST /v1/missions`
  → 202, `POST /v1/projects` → 201). The document MUST reflect the actual documented success
  status where known, not assume 200 everywhere.
- **A future new blueprint / route file is added**: The set of "API-defining files" that
  triggers the CI check MUST be broad enough to include newly added route modules under the
  API package, not an enumerated allow-list that silently misses new files.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a single command (a Makefile target) that generates a
  machine-readable OpenAPI document describing the REST API and writes it to a canonical,
  committed path in the repository.
- **FR-002**: The OpenAPI document MUST be generated by introspecting the actual Flask
  application (its registered URL rules), so it can only describe endpoints that truly exist;
  it MUST NOT be a separately hand-maintained file that can describe non-existent routes.
- **FR-003**: Generation MUST NOT require the API to be enabled, a server to be listening, or
  a real bearer token to be configured. It MUST run purely from the in-process app object.
- **FR-004**: The generated document MUST include, for every registered route: its path
  (with path parameters expressed in OpenAPI `{param}` form), each HTTP method it serves, and
  whether the operation requires bearer-token authentication.
- **FR-005**: The document MUST declare the bearer-token security scheme and the standard JSON
  error envelope (`{"error": {"code", "message"}}`) used by the API, and mark the
  unauthenticated `/v1/health` endpoint as not requiring authentication (`security: []`).
- **FR-006**: Generation MUST be deterministic — repeated runs against unchanged code MUST
  produce byte-identical output (stable, sorted key ordering; fixed formatting), so the
  committed artifact only changes when the API changes.
- **FR-007**: The system MUST provide a check mode that regenerates the document into a
  temporary location and compares it against the committed artifact, exiting non-zero on any
  difference and printing a clear, copy-pasteable instruction to regenerate and commit.
- **FR-008**: CI MUST run the drift check on pull requests **only** when the change modifies
  an API-defining file (the API source package and/or the committed OpenAPI document and its
  generator/Makefile target). When no such file changes, the drift check MUST NOT run.
- **FR-009**: The set of paths that trigger the CI drift check MUST cover the entire API
  source package (so newly added route modules are covered automatically) plus the generator,
  its Makefile target, and the committed document — not a brittle enumeration that omits new
  files.
- **FR-010**: When the drift check fails in CI, the failure output MUST tell the human exactly
  what to run to fix it (the regenerate command) and to commit the result.
- **FR-011**: Project guidance (`CLAUDE.md`, and the nested guidance that covers the API
  package, e.g. `koan/app/CLAUDE.md`) MUST instruct contributors to regenerate and commit the
  OpenAPI document in the same change whenever they add, remove, or modify a REST API
  endpoint, including the exact command.
- **FR-012**: The generated OpenAPI document MUST be valid against the OpenAPI specification
  version it declares (structurally parseable; a validation assertion SHOULD exist in tests).
- **FR-013**: The feature MUST be covered by automated tests that assert the generated
  document matches the live route table (paths, methods, auth) and that the check mode
  detects drift — testing behavior, not source text.
- **FR-014**: User-facing documentation MUST describe the generate/check commands, the
  canonical artifact location, and the regenerate-on-change workflow.

### Key Entities *(include if feature involves data)*

- **OpenAPI document**: The canonical, committed machine-readable description of the REST API
  (paths, methods, parameters, security scheme, error envelope, version). Derived output, not
  a hand-authored source of truth.
- **API-defining file set**: The collection of repository paths whose modification can change
  the API surface — the API source package, the generator, its Makefile target, and the
  committed document itself. Drives the CI path filter.
- **Drift check**: The comparison between the freshly-regenerated document and the committed
  one; its pass/fail is the enforcement signal.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A contributor can produce a current OpenAPI document for the REST API with a
  single command, with zero manual editing of the artifact.
- **SC-002**: Running the generate command twice on unchanged code yields no diff (0 changed
  bytes) 100% of the time.
- **SC-003**: 100% of registered API routes (path + method) are present in the generated
  document, and it contains no path that is not a registered route.
- **SC-004**: A PR that changes an API route without regenerating fails CI with an actionable
  instruction; a PR that touches no API-defining file does not spend any CI time on the drift
  check.
- **SC-005**: Contributors (human or agent) consulting project guidance before an API change
  find an explicit, correct instruction to regenerate and commit the document.

## Assumptions

- The REST API is a Flask application assembled by an app factory whose route table can be
  introspected in-process (confirmed: `koan/app/api/__init__.py::create_app()`; 22 registered
  routes across 5 blueprints plus the inline health route).
- Request/response *body* schemas are not fully derivable from the current untyped Flask
  handlers; the first iteration guarantees an accurate **path/method/parameter/security**
  contract, with room to enrich per-operation request/response schemas incrementally via an
  optional in-code metadata mechanism (not a heavyweight framework migration).
- No new heavyweight web-framework dependency is required; the generator relies on the
  existing Flask app plus a small, already-available serialization helper (PyYAML, already a
  project dependency), consistent with the project's low-dependency posture.
- CI is GitHub Actions; path-filtered triggering uses the existing workflow mechanisms
  (`on.pull_request.paths`), consistent with the other workflows in `.github/workflows/`.
- The canonical artifact lives in the repository at a stable, documented path and is the file
  external consumers reference and CI diffs against.
