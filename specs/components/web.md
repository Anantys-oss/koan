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
| `api/mission_index.py` | Sidecar `instance/.api-missions.json` (atomic). `record/get/list/reconcile/cancel`; `reconcile()` maps stored text → current `missions.md` section. |
| `usage_service.build_usage_payload()` | Shared usage payload (week/month buckets) for dashboard **and** `GET /v1/usage`. |
| `log_reader.tail_log()/read_logs()` | Shared log tailing for dashboard **and** `GET /v1/logs`. |
| `api/server.py` | Validates token at startup (fail-closed), warns on non-loopback bind, serves via waitress. |

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
