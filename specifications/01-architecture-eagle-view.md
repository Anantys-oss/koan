# 01 — Architecture (eagle view)

## Three planes

```
┌─────────────────────────────────────────────────────────────────┐
│  PLANE 1 — Customer browser                                     │
│                                                                 │
│  Marketing site (koan.cloud) ──► sign-up CTA                    │
│  Dashboard SPA (Next.js, hosted on Anantys)                     │
│  Realtime chat / log stream (WS over Anantys backend)           │
└─────────────────────────────────────────────────────────────────┘
                               │
                               │   HTTPS, JWT (Anantys session)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  PLANE 2 — Anantys control plane                                │
│            (existing anantys-back Flask app, extended)          │
│                                                                 │
│  • Auth (existing native_auth + new GitHub OAuth provider)      │
│  • Stripe billing (existing stripe_service.py)                  │
│  • Mail (existing mail_service.py — Mailgun)                    │
│  • Tenant registry: user → tenant → railway_service_id          │
│  • Provisioning service (Railway API client)                    │
│  • Per-tenant proxy: dashboard call → tenant Koan API           │
│  • Usage aggregation (token + compute → Stripe metered billing) │
│  • WebSocket hub (existing finagent infra reused for chat)      │
└─────────────────────────────────────────────────────────────────┘
                               │
                               │  HTTPS, per-tenant bearer token
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  PLANE 3 — Per-tenant Kōan runtime (Railway, 1 service / cust.) │
│                                                                 │
│  • run.py            — agent loop, unchanged                    │
│  • api_server.py     — NEW: HTTP API replacing Telegram bridge  │
│  • instance/         — persistent Railway volume (KOAN_ROOT)    │
│  • env: ANTHROPIC_API_KEY, GITHUB_TOKEN, KOAN_INSTANCE_TOKEN,   │
│         KOAN_TENANT_ID, KOAN_MODE=cloud                         │
└─────────────────────────────────────────────────────────────────┘
```

## What lives where

### Plane 1 — Browser

- **Marketing site**: `koan.cloud` (or sub-path, see [open questions](05-open-questions.md)). Static, separate from the dashboard.
- **Dashboard SPA**: Next.js. **Open question:** lives inside an existing Anantys Next.js app (e.g. a new `/koan/*` route group inside `corp-anantys`) or a brand new Next.js app in `anantys-back/src/`. See [02 — Anantys stack reuse](02-anantys-stack-reuse.md).
- **Realtime chat** uses the same WebSocket pattern as Anantys finagent (Flask-Sock + Redis sessions), exposed under a new `/koan/chat` namespace.

### Plane 2 — Anantys control plane (the most code we write)

This is **a new product surface inside the existing `anantys-back` monolith**, following the established blueprint pattern (`portal`, `nextapi`, `admin`, `auth`, `webhooks`). Working name: **`koancloud` blueprint**.

Modules to add (proposed layout):

```
anantys-back/
├── app/
│   ├── routes/
│   │   ├── koancloud/                  # NEW
│   │   │   ├── dashboard_api.py        # JSON API consumed by dashboard SPA
│   │   │   ├── onboarding.py           # Stripe + repo picker + provisioning trigger
│   │   │   ├── proxy.py                # Per-tenant API proxy (forwards to Railway)
│   │   │   ├── chat_ws.py              # WS bridge → tenant chat endpoint
│   │   │   └── webhooks.py             # Stripe + Railway webhooks
│   │   └── auth/
│   │       └── github_oauth.py         # NEW: GitHub provider
│   └── models/
│       └── koan_tenant.py              # NEW: Tenant model (see below)
├── services/
│   ├── oauth/
│   │   └── github.py                   # NEW: GitHub OAuth client
│   ├── koancloud/                      # NEW
│   │   ├── tenant_service.py           # Tenant CRUD + lifecycle
│   │   ├── railway_client.py           # Railway API wrapper
│   │   ├── provisioning_service.py     # Orchestrates tenant creation
│   │   ├── usage_aggregator.py         # Pulls usage from tenant API → Stripe
│   │   └── projects_yaml_builder.py    # GitHub repos → projects.yaml
│   └── stripe_service.py               # EXISTING — extended with Koan plans
```

**Reused, untouched** (or near-untouched):

- `services/mail_service.py` — transactional emails (welcome, payment failed, instance crashed, budget cap reached).
- `services/native_auth/` — fallback email/password login (most users will sign in via GitHub OAuth, but reuse covers edge cases).
- `services/stripe_service.py` — extend with new product/plan IDs for Koan tiers; reuse the rest.
- `services/finagent/` WebSocket infrastructure — pattern reused for Koan chat. We may instantiate a second Flask-Sock namespace rather than fork the code (TBC, see [open questions](05-open-questions.md)).

### Plane 3 — Per-tenant Kōan runtime

One **dedicated Railway service per customer**, deployed from a Kōan template Docker image.

Net-new in `koan/`:

- `koan/app/api_server.py` — Flask (or FastAPI) HTTP server. Exposes the same surface as Telegram commands, plus chat and log streams. Auth: per-instance bearer token rotatable from the control plane.
- `koan/app/cloud_mode.py` — small switch that, when `KOAN_MODE=cloud`, disables `awake.py` (Telegram) and starts `api_server.py` instead.

**Unchanged**:

- `run.py` agent loop, mission lifecycle, skill dispatch, provider abstraction, usage tracker, journal, memory, hooks. The cloud mode is a **bridge replacement**, not a fork of the agent.

### Trust boundaries

| Boundary | Crosses how | Auth | Risk |
|----------|-------------|------|------|
| Browser ↔ Anantys control plane | HTTPS | Anantys session JWT | Standard web auth |
| Anantys control plane ↔ Tenant runtime | HTTPS to Railway service URL (or via Anantys reverse proxy — TBD) | Per-tenant bearer token, rotatable | Token leak = full instance access; mitigated by rotation + scoped to one tenant |
| Tenant runtime ↔ GitHub | HTTPS | Per-tenant GitHub token (OAuth-issued) | Standard GH integration risk |
| Tenant runtime ↔ Anthropic | HTTPS | Anthropic API key (BYOK or master, see [open Q #6](05-open-questions.md)) | If master key: AUP enforcement on us |

**Hard rule:** the browser **never** holds the per-tenant bearer token. All dashboard ↔ tenant calls go through the Anantys proxy.

## Data flows (key paths)

### Onboarding

```
Browser ─GitHub OAuth─► Anantys ─webhook─► User row created
       ─Stripe Checkout──► Anantys ─webhook─► Subscription active
       ─POST /koan/onboarding ─► Anantys
                                 │
                                 ├─► Railway API: create service from template
                                 ├─► Inject env vars (tokens + projects.yaml)
                                 ├─► Wait for service health check
                                 └─► Return dashboard URL
       ◄─dashboard URL─
```

### Mission queueing

```
Browser ─POST /koan/api/missions─► Anantys (auth: user JWT)
                                   │
                                   └─proxy POST /missions─► Tenant runtime (auth: bearer)
                                                            │
                                                            └─► writes missions.md
```

### Live chat

```
Browser ─WS connect /koan/chat─► Anantys WS hub
                                  │
                                  └─proxy WS─► Tenant /api/chat
                                               │
                                               └─► same chat stack as Telegram
                                                   (skill dispatch, command_handlers)
```

### Usage / billing

```
Tenant ─usage_tracker writes to local sink
       ─GET /usage polled by Anantys aggregator (every N minutes)
       ─usage rows ─► Anantys DB
                  ─► Stripe metered billing (token line item, monthly close)

Railway ─cost via Railway API ─► Anantys aggregator ─► Stripe (compute line item)
```

## Tenant model (proposed)

```python
class KoanTenant:
    id: UUID
    user_id: int                 # FK Anantys User
    plan: str                    # "starter" | "pro" | "scale" (TBC)
    status: str                  # "provisioning" | "active" | "suspended" | "destroyed"
    railway_service_id: str
    railway_service_url: str
    instance_token_hash: str     # bearer token (hashed at rest)
    github_token_encrypted: bytes
    anthropic_key_encrypted: bytes  # only if hosted-credits model
    github_repos: JSON           # list of {owner, name, default_branch}
    created_at: datetime
    last_activity_at: datetime
    monthly_budget_usd: Decimal  # nullable; soft cap reuses pause_manager
```

## Key architectural decisions to ratify

These are the choices that shape everything downstream. They live as questions in [05 — Open questions](05-open-questions.md) and need an answer before Sprint 1 starts.

1. **Anantys monolith vs separate service** for the control plane. Recommendation: extend the monolith (this doc assumes it).
2. **Dashboard inside an existing Next.js app vs new app.** Recommendation: new Next.js app under `anantys-back/src/koan-cloud-app/`, sharing the design system module.
3. **BYOK vs hosted credits at launch.** Recommendation: **hosted credits** for the click-and-play promise, with the master-key Anthropic Commercial Terms model. The original RFC favored BYOK; the user's pricing brief implies hosted credits. Needs explicit confirmation.
4. **GitHub OAuth vs GitHub App.** Recommendation: **OAuth + user token** at launch, GitHub App in v2.
5. **Per-tenant Railway service vs shared infra.** Confirmed by RFC: 1 service per tenant.
6. **Update strategy.** Recommendation: control-plane-driven rolling redeploy via Railway API for paid tenants. (Per-instance auto-update is fine for self-hosted, not for paying customers.)
