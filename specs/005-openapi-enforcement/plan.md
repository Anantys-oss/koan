# Implementation Plan: Auto-Generate & Enforce OpenAPI Spec for the REST API

**Branch**: `005-openapi-enforcement` | **Date**: 2026-07-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-openapi-enforcement/spec.md`

## Summary

Add a **code-derived OpenAPI 3.1 document** for the existing Kōan REST API
(`koan/app/api/`) plus a **drift guard**. A new generator module inspects the live Flask
app's route table (`create_app().url_map` + `view_functions`) and emits a deterministic
YAML document to a committed path (`koan/openapi.yaml`). A `make openapi` target regenerates
it; a `make openapi-check` / `--check` mode fails with a copy-pasteable fix instruction when
the committed file drifts from the code. A **path-filtered** GitHub Actions workflow runs the
drift check only when API-defining files change. Auth-required status is derived directly from
the `require_token` decorator (tagged on its wrapper), so new routes get correct security
automatically. Contributor guidance in `CLAUDE.md` / `koan/app/CLAUDE.md` tells authors to
regenerate + commit the artifact whenever they add/remove/modify an endpoint.

## Technical Context

**Language/Version**: Python 3.11+ (constitution constraint — no syntax/stdlib beyond 3.11).

**Primary Dependencies**: Existing stack only. Flask (already a dep) for the app factory and
route introspection; PyYAML (already a dep, `pyyaml>=6.0`) for deterministic serialization.
No new third-party dependency, no web-framework migration, no schema-generation library.

**Storage**: None. The committed artifact `koan/openapi.yaml` is a generated file in the
repo; no runtime state.

**Testing**: `pytest` with `KOAN_ROOT=/tmp/test-koan` prefix. New
`koan/tests/test_openapi_gen.py`: assert the generated spec's paths/methods/security match
the live route table, determinism (generate twice → identical), `--check` detects drift,
document validity (parseable OpenAPI 3.1, has `openapi`/`info`/`paths`, security scheme,
error component). Test behavior/outputs, never source text.

**Target Platform**: Same as Kōan (macOS/Linux daemon host). The generator runs offline,
in-process, without binding a port or enabling the API.

**Project Type**: Library/daemon extension — one new generator module inside the API package,
one committed artifact, one Makefile target pair, one CI workflow, docs + guidance updates.

**Performance Goals**: None. Generation is a sub-second, one-shot introspection.

**Constraints**:
- Deterministic output (stable sorted keys, fixed YAML formatting) — re-running unchanged
  code MUST produce byte-identical output (FR-006).
- No secret ever written to the document; generation needs no token and no running server
  (FR-003, FR-005).
- CI drift check runs **only** when an API-defining file changes (FR-008/FR-009) via
  `on.pull_request.paths`.
- Python 3.11+, ruff-clean, no inline LLM prompts (N/A here — no prompts).

**Scale/Scope**: ~1 new Python module (`koan/app/api/openapi_gen.py`), a 1-line marker in
`koan/app/api/auth.py`, 1 committed artifact (`koan/openapi.yaml`), 2 Makefile targets, 1 CI
workflow (`.github/workflows/openapi.yml`), 1 test file, guidance updates in `CLAUDE.md` +
`koan/app/CLAUDE.md`, doc updates in `docs/operations/rest-api.md`, and a durable component
spec note in `specs/components/web.md` (or the API component group). 22 existing routes across
5 blueprints + the inline `/v1/health`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against `.specify/memory/constitution.md` v2.0.0.

| # | Principle | Verdict | How this design honors it |
|---|---|---|---|
| I | Human Authority (NON-NEGOTIABLE) | ✅ PASS | All work on a `koan.atoomic/*` branch; a **draft** PR only; never merges; never touches `main`. The CI job only reports drift — it never pushes or commits. |
| II | Specs Are the Source of Truth | ✅ PASS | This feature's speckit trio lives in `specs/005-openapi-enforcement/`; the durable contract update lands in `specs/components/web.md` (the REST-API component group) in the same branch. |
| III | Local Files by Default; Mission State in the Store | ✅ PASS | No mission state touched. The OpenAPI document is a generated file, not authoritative state. |
| IV | Provider Isolation | ✅ PASS | Entirely provider-agnostic — introspects the Flask app; no CLI-provider branching. |
| V | Untrusted Inputs, Audited Outputs | ✅ PASS | Generator reads only in-repo code, writes only a derived doc. No secret is emitted (security scheme describes *shape*, not the token). |
| VI | Single Writer, Single Read Path | ✅ PASS | Auth-required status has one source of truth: the `require_token` decorator marker. No parallel enumeration of secured routes. One generator, one artifact path. |
| VII | Simplicity & Honest Reporting | ✅ PASS | Reuses the existing app factory; no new deps; no framework migration. The doc is honestly scoped to path/method/param/security (bodies deferred, stated in spec Assumptions). |

**Gate result**: PASS — no violations. Complexity Tracking left empty.

## Project Structure

### Documentation (this feature)

```text
specs/005-openapi-enforcement/
├── plan.md              # This file
├── research.md          # Phase 0 — approach decisions
├── data-model.md        # Phase 1 — the OpenAPI document model + API-file-set
├── quickstart.md        # Phase 1 — how to generate/check/consume
├── contracts/
│   └── openapi-generator.md   # Phase 1 — generator CLI + module contract
└── tasks.md             # Phase 2 output (/speckit-tasks — separate step)
```

### Source Code (repository root)

```text
koan/
├── app/
│   └── api/
│       ├── auth.py            # EDIT: tag require_token's wrapper with a marker
│       │                      #        attribute so security is decorator-derived.
│       └── openapi_gen.py     # NEW: build_spec(app) -> dict; dump_yaml(); CLI
│                              #      (--output PATH, --check). No inline prompts.
├── openapi.yaml              # NEW: committed generated artifact (canonical path).
└── tests/
    └── test_openapi_gen.py   # NEW: route-table match, determinism, --check drift,
                              #      validity, security scheme, error component.

Makefile                      # EDIT: `openapi` (generate) + `openapi-check` targets;
                              #        add both to .PHONY.
.github/workflows/
└── openapi.yml              # NEW: path-filtered drift check (pull_request.paths).

CLAUDE.md                     # EDIT: "regenerate OpenAPI on API change" convention.
koan/app/CLAUDE.md            # EDIT: same convention next to the API module map.
docs/operations/rest-api.md   # EDIT: document the OpenAPI artifact + generate/check flow.
specs/components/web.md       # EDIT (or create): durable contract note for the generator.
```

**Structure Decision**: Single-project extension. The generator lives **inside the API
package** (`koan/app/api/openapi_gen.py`) so the CI path filter `koan/app/api/**` covers both
the routes and their generator with one glob, and so new route modules are auto-covered
(FR-009). The committed artifact sits at `koan/openapi.yaml` (next to `requirements.txt`, the
dir the API/tests run from), added explicitly to the path filter.

## Key design decisions (details in research.md)

1. **Introspection, not annotation.** Derive paths/methods/params from Werkzeug rules
   (`app.url_map.iter_rules()`), converting `<conv:name>` → `{name}`. Guarantees the doc can
   only describe real routes (FR-002).
2. **Decorator-derived security.** `require_token` sets `decorated._koan_requires_token = True`
   on its wrapper; the generator reads `app.view_functions[endpoint]` and marks `security:
   [{bearerAuth: []}]` when the marker is present, `security: []` otherwise (so `/v1/health`
   is public). Single source of truth (Principle VI); new secured routes auto-correct.
3. **Determinism.** `yaml.safe_dump(..., sort_keys=True, default_flow_style=False,
   allow_unicode=True)`; methods sorted; a fixed header comment. `info.version` pinned to the
   **API** contract version (`1.0.0`, matching the `/v1` prefix), NOT `app.__version__`, so
   release bumps don't cause spurious drift (FR-006).
4. **Human-friendly metadata is also code-derived.** `summary` = first line of the view
   function docstring; `tags` = blueprint name (endpoint prefix). No separately-maintained
   metadata file that could drift.
5. **Responses.** Each operation gets its success response (200 by default; a tiny
   code-local `SUCCESS_STATUS` override map records the known non-200 successes — `POST
   /v1/missions`→202, `POST /v1/projects`→201) plus, for secured ops, `401`/`403`, and a
   `default` error response — all referencing a shared `Error` schema component modeling the
   `{"error": {"code", "message"}}` envelope (FR-005). Request/response **body** schemas are
   out of scope for iteration 1 (spec Assumptions); the enforceable core is
   path/method/param/security.
6. **CI path filter.** `on.pull_request.paths: [koan/app/api/**, koan/openapi.yaml, Makefile,
   .github/workflows/openapi.yml]`. When none match, the job does not run (FR-008). The check
   runs the generator in `--check` mode; on drift it exits non-zero and prints the exact fix
   command (FR-010).

## Complexity Tracking

*(Empty — no constitution violations. No simpler alternative rejected: the design reuses the
existing Flask app, adds no dependency, and derives every enforceable field from code.)*

## Deferred to `/speckit-tasks`

- Exact test assertions for the route-table/spec correspondence (parametrized over
  `create_app().url_map`).
- Whether the durable component note lives in `specs/components/web.md` or a new
  `specs/components/rest-api.md` — tasks.md picks based on what exists.
- Whether to also emit a JSON sibling (`openapi.json`) — deferred; YAML-only for iteration 1.
