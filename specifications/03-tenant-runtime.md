# 03 — Tenant runtime (per-customer Kōan service)

Each paying customer gets a dedicated **Kōan instance** running on a dedicated **Railway service**. This document specifies what changes inside `koan/` to make that instance a cloud-citizen, and how the control plane provisions and operates it.

## What stays unchanged

The agent loop is the heart of the product. We **do not fork it**. Self-hosted users keep the same code path.

Untouched modules:

- `koan/run.py` — agent loop with restart wrapper
- `koan/app/missions.py` — mission lifecycle
- `koan/app/skill_dispatch.py` + `koan/skills/` — skill registry & dispatch
- `koan/app/provider/` — CLI provider abstraction
- `koan/app/usage_tracker.py` — usage accounting
- `koan/app/mission_runner.py`, `loop_manager.py`, `quota_handler.py`, `contemplative_runner.py`
- `koan/app/memory_manager.py`, journal, hooks
- `koan/app/git_sync.py`, `git_auto_merge.py`, `github.py`, `rebase_pr.py`, `recreate_pr.py`, `pr_review_learning.py`
- `koan/app/auto_update.py` — kept, but **disabled** in cloud mode (control plane drives updates)

## What's new in cloud mode

### `koan/app/api_server.py` (new)

The HTTP API replacing the Telegram bridge (`awake.py`).

**Stack:** Flask + Flask-Sock for WebSockets (same stack as Anantys finagent, keeps shared mental model). Alternative: FastAPI — to be decided based on async needs and existing team familiarity. Recommendation: Flask, for stack uniformity.

**Endpoints (MVP set):**

| Method | Path | Purpose | Underlying logic |
|---|---|---|---|
| `GET` | `/health` | Liveness probe (used by Railway + control plane) | trivial |
| `GET` | `/status` | Current Koan state (mission, focus, pause, last journal entry) | reads `.koan-status`, `pause_manager`, `focus_manager` |
| `GET` | `/missions` | Mission queue (Pending / In Progress / Done) | `missions.py` parsers |
| `POST` | `/missions` | Queue a new mission | calls into `missions.py` write path |
| `POST` | `/skills/{name}` | Run a skill (with args) | `skill_dispatch.dispatch()` |
| `GET` | `/journal/today` | Today's journal entries | reads `instance/journal/YYYY-MM-DD/` |
| `GET` | `/usage` | Token + compute usage (rolling window, this month) | `usage_tracker` |
| `POST` | `/pause` / `/resume` / `/focus` | Lifecycle controls | `pause_manager`, `focus_manager` |
| `POST` | `/config` | Update `instance/config.yaml` (subset of safe fields) | atomic write |
| `WS` | `/chat` | Real-time chat stream | command_handlers.handle_chat() |
| `WS` | `/logs` | Live stdout/stderr stream from agent loop | tail of run.py log file |

**Auth:** every request must carry `Authorization: Bearer <KOAN_INSTANCE_TOKEN>`. Token is set by the control plane at provisioning time, stored hashed in `KoanTenant.instance_token_hash`, and rotatable from the admin dashboard.

**Reused:** the API endpoints are thin wrappers around `koan/app/command_handlers.py` logic. We do not duplicate the Telegram → mission translation; we share it.

### `koan/app/cloud_mode.py` (new)

Tiny switch module. Read at startup:

```python
KOAN_MODE = os.environ.get("KOAN_MODE", "local")  # "local" | "cloud"
```

In `local` mode: existing behavior (Telegram bridge if configured).
In `cloud` mode: `awake.py` is disabled, `api_server.py` is started, `auto_update.py` is disabled.

Self-hosted users see no change unless they opt in to `cloud` mode.

### Dockerfile changes

The existing `Dockerfile` already builds a working image. For cloud mode we add:

- A new `entrypoint.cloud.sh` (or env-driven branching in `docker-entrypoint.sh`) that:
  - Mounts `KOAN_ROOT=/data` (Railway persistent volume).
  - Runs DB migrations (n/a — Koan has no SQL DB) → no-op.
  - Starts both `run.py` (agent loop) and `api_server.py` (HTTP). Either via `supervisord`, `honcho`, or a single Python entrypoint that spawns both as threads/subprocesses.
- Healthcheck endpoint wired to `/health` for Railway.

### Config & secrets injected at provisioning

```
KOAN_MODE=cloud
KOAN_TENANT_ID=<uuid>
KOAN_INSTANCE_TOKEN=<random-256bit>           # bearer token
KOAN_ROOT=/data
ANTHROPIC_API_KEY=<...>                       # see open Q #6 for BYOK vs hosted
GITHUB_TOKEN=<oauth-issued>                   # scope: repo, workflow
KOAN_CLI_PROVIDER=claude
KOAN_BRANCH_PREFIX=koan
KOAN_CONTROL_PLANE_URL=https://anantys.com    # for usage push, restart signaling
KOAN_CONTROL_PLANE_TOKEN=<...>                # tenant → control plane auth (different from instance token)
```

### `instance/projects.yaml` generation

Built by the control plane at provisioning time from the user's GitHub repo selection:

```yaml
projects:
  - name: my-awesome-app
    path: /data/repos/my-awesome-app
    github_url: https://github.com/user/my-awesome-app
    cli_provider: claude
    git_auto_merge: false
    branch_prefix: koan
```

Pushed into the tenant via the API at provisioning, written atomically by `api_server.py` to `$KOAN_ROOT/projects.yaml`.

### Initial mission auto-queued

On first boot, control plane calls `POST /missions` with:

> "Hi! Introduce yourself in a journal entry. Then scan each linked repo, write a brief overview of what it does, suggest 3 improvements you'd start with, and finish your turn."

This gives the customer immediate signal that Kōan is alive and useful.

## Provisioning flow (control plane responsibility)

```
1. Stripe webhook: subscription.created with Koan plan
   ↓
2. tenant_service.create_pending(user_id, plan)
   → row in koan_tenant: status="provisioning"
   ↓
3. provisioning_service.provision(tenant_id)
   ├─ railway_client.create_service(template="koan-cloud:latest")
   ├─ railway_client.set_env_vars(service_id, vars=...)
   ├─ railway_client.attach_volume(service_id, "/data")
   ├─ railway_client.deploy(service_id)
   └─ wait_for_health(service_url, timeout=120s)
   ↓
4. POST {tenant}/projects.yaml + POST {tenant}/missions (welcome mission)
   ↓
5. Update tenant status = "active", send welcome email
   ↓
6. Redirect user from "We're setting things up..." page → dashboard
```

**Target time:** under 90 seconds from step 1 to step 6 (RFC target). Hard ceiling: 5 minutes.

## Anthropic API key model

This is the single highest-impact decision still open. See [open Q #6](05-open-questions.md). Two clean models:

### Model A — BYOK (RFC's original recommendation)

- Customer brings their own Anthropic API key.
- Pricing is purely for compute (Railway) + product (Koan SaaS).
- Pros: no AUP exposure for us, no margin on tokens, simpler legal.
- Cons: friction at signup ("get an Anthropic API key first"); pricing power capped.

### Model B — Hosted credits (this brief's implied direction)

- Anantys holds the master Anthropic key.
- Tier price = compute + token allowance (e.g. $89/mo includes $X of tokens).
- Overage billed via Stripe metered billing.
- Pros: zero friction signup; pricing power; predictable customer bill if budget cap is set.
- Cons: AUP enforcement is on us (logging, abuse detection); margin model needed.

**Recommendation:** **Model B (hosted credits)** for Epic #1, because it is the only one that delivers the "click and play" promise. Model A becomes an opt-in on a higher tier later ("bring your own key, get a discount").

## Update strategy

The RFC offered two options:

- (a) Per-instance `auto_update.py` pulling `main`.
- (b) Control-plane-driven rolling redeploy via Railway API.

**Recommendation:** **(b) for cloud tenants** — the control plane decides the version; we redeploy in waves (10% → 50% → 100%) with health checks. (a) is disabled in cloud mode.

This means:

- `auto_update.py` is disabled when `KOAN_MODE=cloud`.
- New images are tagged in CI (`koan-cloud:v1.2.3`, `koan-cloud:latest`).
- A `flask koan redeploy` CLI command rolls a wave.
- Optional: a `koan_cloud_releases` table tracking which version each tenant is on.

## Tenant lifecycle states

```
[ provisioning ]
       │
       ▼
[ active ]  ◄──────────┐
   │                   │
   ├─► [ paused ]      │  /pause /resume from dashboard
   │     │             │  budget cap reached
   │     └─────────────┘
   │
   ├─► [ suspended ]   ◄── payment failed (3 attempts) — instance stopped, data retained
   │     │
   │     └─► resumed once paid
   │
   └─► [ destroyed ]   ◄── customer canceled — 7-day grace then service deleted, volume snapshot retained 30 days
```

Each transition is driven from the control plane, never from the tenant itself. The tenant has no authority over its own lifecycle.

## Resource sizing per plan tier

**Open question:** what does each tier give the customer? Possibilities:

- **Starter ($89):** small Railway instance (~512MB RAM), $X token allowance, 1 project, 8h/day active window.
- **Pro ($249):** standard instance (~2GB), $Y tokens, 3 projects, 24/7.
- **Scale ($499):** larger instance (~4GB), $Z tokens, 10 projects, 24/7, priority support.

These numbers are placeholders until we have Phase 0 cost data. See [open Q #18](05-open-questions.md).

## Backups & data retention

- `instance/` is the only stateful directory. Daily snapshot of the Railway volume → S3-compatible bucket (Railway has S3 backups, or we run our own via the control plane).
- On `destroyed`: snapshot retained 30 days for recovery if customer changes their mind, then permanently deleted.
- GitHub tokens, Anthropic keys: stored encrypted on `KoanTenant`, not in the tenant runtime's env (env vars are populated at deploy time but not persisted by us outside the encrypted column).

## What does NOT change in the agent

To be explicit: from the agent loop's point of view, the cloud is **just a different bridge**. The mission loop, skills, prompts, learnings, journal — all of it works exactly the same as on a self-hosted Mac. That's the whole point.

If a feature works in self-hosted Koan, it works in Koan Cloud. If it doesn't, we file the bug against shared core code, not against cloud-specific code.
