# Phase 1 Data Model — OpenAPI generation

This feature produces a **derived document**, not persistent state. The "data model" is the
in-memory structure the generator builds and the file it serializes.

## Entity: OpenAPI document (generated)

Top-level structure (OpenAPI 3.1.0), all fields code-derived except where noted:

| Field | Source | Notes |
|-------|--------|-------|
| `openapi` | constant `"3.1.0"` | Declared spec version. |
| `info.title` | constant `"Kōan REST API"` | |
| `info.version` | constant `"1.0.0"` | The `/v1` contract version — NOT `app.__version__` (determinism, FR-006). |
| `info.description` | constant | Short blurb + pointer to `docs/operations/rest-api.md`. |
| `servers` | constant | `http://127.0.0.1:8420` (documented default bind/port). |
| `security` | derived | Global default `[{bearerAuth: []}]`; per-op override `[]` for public routes. |
| `tags` | derived | Sorted unique blueprint names. |
| `paths` | derived | One entry per unique route path (see below). |
| `components.securitySchemes.bearerAuth` | constant | `{type: http, scheme: bearer}`. |
| `components.schemas.Error` | constant | Models `{"error": {"code": str, "message": str}}`. |

## Entity: Path Item / Operation (one per route × method)

Built by iterating `app.url_map.iter_rules()`:

| Operation field | Source |
|-----------------|--------|
| path key | `rule.rule` with `<...>` → `{...}` |
| method key | each of `rule.methods` minus `HEAD`/`OPTIONS`, lowercased |
| `operationId` | `endpoint` with `.` → `_` (stable, unique) |
| `summary` | first non-empty line of the view function docstring (fallback: humanized endpoint) |
| `tags` | `[blueprint name]` (endpoint prefix before `.`; `default` if none) |
| `parameters` | one `path` parameter per `{name}` in the path (required, `schema: {type: string}`) |
| `security` | omitted (inherits global) if the view has `_koan_requires_token`; `[]` if public |
| `responses` | success status (see map) + `401`/`403` (secured only) + `default`, all → `#/components/schemas/Error` for errors |

### SUCCESS_STATUS override map (code-local, in the generator)

Introspection cannot see the returned HTTP status. A small explicit map records known
non-200 successes; everything else defaults to `200`:

| (method, path) | status |
|----------------|--------|
| `(POST, /v1/missions)` | `202` |
| `(POST, /v1/projects)` | `201` |

The success response body is described generically (`description` only) in iteration 1;
per-operation response *schemas* are deferred (spec Assumptions).

## Entity: API-defining file set (drives CI)

Not a runtime object — the list of globs in `.github/workflows/openapi.yml` `paths:`:

- `koan/app/api/**` — all route modules + the generator (auto-covers new files).
- `koan/openapi.yaml` — the committed artifact.
- `koan/requirements.txt` — PyYAML/dep bumps change the generator's byte output, so a
  dependency change must re-run the drift check (else drift merges silently and later
  false-fails an unrelated API PR).
- `Makefile` — the `openapi` / `openapi-check` targets.
- `.github/workflows/openapi.yml` — the workflow itself.

## Determinism contract

`build_spec(app)` returns a plain `dict`; `dump_yaml(spec)` returns the canonical text.
`build_spec` is pure w.r.t. the app's route table: same routes → equal dict. `dump_yaml`
sorts keys and fixes formatting so equal dicts → equal bytes. Therefore unchanged code →
byte-identical file (FR-006, SC-002).
