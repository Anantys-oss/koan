---
type: component-spec
title: "Component Spec — Web Dashboard & REST API"
tags: [web]
created: 2026-06-27
updated: 2026-07-08
---

# Component Spec — Web Dashboard & REST API

**Packages:** `koan/app/dashboard/`, `koan/app/dashboard_service/`, `koan/app/api/`
+ shared `usage_service.py`, `log_reader.py`

## Purpose

Two read/write surfaces over the same runtime state Telegram exposes: a human-facing
Flask **dashboard** (port 5001) and a token-gated **REST API** (port 8420). Both are
built by `create_app()` factories and share pure business-logic and data-shaping helpers.

## Architecture

```
dashboard/  (Flask blueprints via create_app())
  ├─ core      index, auth, status/health/forecast/provider
  ├─ missions  mission CRUD + attention
  ├─ chat      chat + progress/state SSE
  ├─ usage     usage/metrics/efficiency/journal/logs
  ├─ agent     soul/memory/skills/config + pause/resume/restart
  ├─ config    config/nickname/rules/recurring
  ├─ prs       PRs + plans
  ├─ state.py     ← single home for patchable module globals (tests patch state.X)
  └─ _helpers.py  ← passphrase gate, cache-buster, context processor, template filters

dashboard_service/  (pure logic, no Flask client needed to test)
  missions · journal · plans · stats + read_file/mask_sensitive/validate_yaml

api/  (Flask blueprints via create_app())
  auth (require_token) · mission_index (sidecar) · routes_missions/projects/status/
  admin/observability · server.py (waitress entrypoint)
```

## Key types & functions

| Symbol | Contract |
|---|---|
| `dashboard.create_app()` / `api.create_app()` | Factory pattern; `from app.dashboard import app` exposes the module instance for tests. |
| `dashboard/state.py` | All patchable globals (paths, `CHAT_TIMEOUT`, `DASHBOARD_PWD`, caches, regexes). Route code reads `state.X` at call time → tests patch one target. |
| `dashboard_service/*` | Pure business logic — unit-tested without a Flask client. **New logic goes here, not in routes.** |
| `api/auth.require_token` | Bearer parse + `hmac.compare_digest`. Token: env `KOAN_API_TOKEN` → `api.token` → `""`. |
| `api/mission_index.py` | Sidecar `instance/.api-missions.json` (atomic). `record/get/list/reconcile/cancel`; `reconcile()` maps stored text → current `missions.md` section. Typed `result`/`result_ref` store: `attach_result()` (size-cap spill, summary-preserving), `load_full_result()` (inline-or-spill). |
| `api/mission_results.py` | Command→resolver registry (`register_resolver`, `resolve_mission_result`, `always_inline_keys`); built-in `/review`+`/ultrareview` resolver reads the PR-keyed findings sidecar. |
| `usage_service.build_usage_payload()` | Shared usage payload (week/month buckets) for dashboard **and** `GET /v1/usage`. |
| `log_reader.tail_log()/read_logs()` | Shared log tailing for dashboard **and** `GET /v1/logs`. |
| `api/server.py` | Validates token at startup (fail-closed), warns on non-loopback bind, serves via waitress. |

## Mission record: typed structured `result`

The mission record (`.api-missions.json`, exposed by `GET /v1/missions/{id}`
and `GET /v1/missions`) carries a typed `result` payload in addition to the
free-text `result_line`:

| Field         | Type            | Contract |
|---------------|-----------------|----------|
| `result_line` | `str \| null`   | Short human summary; unchanged, backward-compatible. |
| `result`      | `object \| null`| Structured result emitted by skills that produce one (e.g. `/review` → `{kind, file_comments[], review_summary{lgtm, summary, checklist}}`). `null` for skills with no resolver. Carries a `kind` discriminator. |
| `result_ref`  | `str \| null`   | Relative path (`.api-results/<id>.json`) to the full blob when it exceeds the inline cap; else `null`. |

**Resolver mechanism (pull-based, generic).** `api/mission_results.py` holds a
command→resolver registry (`register_resolver`, `resolve_mission_result`,
`always_inline_keys`). On the transition into `done`/`failed`, `reconcile()`
calls the resolver for the mission's slash-command and `attach_result()`s
whatever it returns — once, only while `result`/`result_ref` are unset
(idempotent). Skills run as subprocesses with no API mission id, so the API
*pulls* from the artifact the skill already persisted rather than the skill
pushing. The built-in `/review` (and `/ultrareview`) resolver reads the
PR-keyed findings sidecar `instance/.review-findings/{owner}_{repo}_{pr}.json`
(which `review_runner._write_review_findings_sidecar()` writes with both
`file_comments` and `review_summary`).

**Size cap + HTTP reachability.** Results over `DEFAULT_RESULT_CAP_BYTES`
(256 KB) spill to `instance/.api-results/<id>.json`; the record keeps
`result_ref` plus a trimmed inline copy of the resolver's `always_inline` keys
(`/review`: `kind`, `review_summary`, plus `result_truncated: true`) so the
verdict/summary never drop from list/GET payloads. `GET /v1/missions/{id}/result`
streams the complete result (inline or spilled) so remote clients that cannot
read the instance filesystem can always retrieve the full findings.

**Correlation is best-effort by PR key.** The `/review` resolver keys off the
PR URL in the mission text → latest PR-keyed sidecar (a re-review overwrites
it). Each mission attaches whatever the sidecar held at its own terminal
transition; serialized re-reviews of the same PR therefore attach the correct
run's result. Storage-agnostic: `attach_result`/`load_full_result`/resolver
port unchanged onto the #2140 missions table.

## Invariants

- **Logic in `dashboard_service/`, wiring in `dashboard/`.** Routes stay thin; testable
  logic must not live in route handlers.
- **Patch one target.** Module globals live in `state.py`; tests patch
  `app.dashboard.state.X`, not scattered module attributes.
- **API is fail-closed.** No token configured → server refuses to start; secrets are
  masked in `GET /v1/config`.
- **Dashboard and API share data shapers** (`usage_service`, `log_reader`) so the two
  surfaces never drift in what they report.
- **Default binds are loopback** (`127.0.0.1`); non-loopback bind warns.

## Integration points

- Reads agent state from signal files (`.koan-status`, pause/focus/passive) and
  `missions.md`.
- Mutating endpoints (pause/resume/restart/shutdown/update) drive the same managers the
  bridge uses (`pause_manager`, `restart_manager`, `update_manager`).
- Mission creation writes through `missions.py` + the API sidecar index.

## Known debt / watch-outs

- The dashboard and API are intentionally parallel factories — a feature exposed on one
  often belongs on the other; check both when adding observability.
- Per-request audit logging in the API must not log secrets.

## Change protocol

New endpoints add the pure logic to `dashboard_service/` (or a shared service), wire a
thin route, and — if observability — expose it on both surfaces. Update
`docs/operations/rest-api.md` for API changes and this spec for structural ones.
