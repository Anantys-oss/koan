# 02 — Anantys stack reuse

The biggest force multiplier for Koan Cloud is the **anantys-back** monolith. Auth, billing, mail, websocket, user lifecycle, admin tooling — it's all there, battle-tested, and on Railway. We extend it instead of forking it.

This document inventories what we reuse, exactly how, and what's net-new.

## Anantys assets we reuse

| Asset | Anantys module | Koan Cloud usage | Net-new code? |
|---|---|---|---|
| **User model + native auth** | `services/native_auth/`, `app/models/user_profile.py` (and `User`) | Anantys account = Koan Cloud account. Add a `koan_cloud` relationship on `User` (1:1 → `KoanTenant`). | None — extend, don't fork |
| **OAuth providers** | `services/oauth/` (Google, Apple) | Add **GitHub** as a new OAuth provider following the same pattern. | New: `services/oauth/github.py`, `app/routes/auth/github_oauth.py` |
| **Stripe billing** | `services/stripe_service.py`, `app/cli/commands/stripe_cli.py` | Add Koan product + 3 plan IDs. Reuse subscription lifecycle, webhook handling, invoice generation. Add **metered billing** for tokens (new line item). | Light: plan/price configs + metered usage push |
| **Mail (transactional)** | `services/mail_service.py` (Mailgun) | All Koan Cloud transactional emails: welcome, payment failure, instance ready, budget cap reached, instance crashed, unsubscribe. | New: Koan-specific Jinja email templates under `app/templates/emails/koan/` |
| **WebSocket infra** | `services/finagent/chat/` + `run_ws.py` (`RAILWAY_TARGET=websocket`) | Same Flask-Sock + Redis session pattern, **second namespace** for Koan chat. The actual chat handler is different (it bridges to the tenant runtime), but the connection/session/auth scaffolding is reused. | New: `services/koancloud/chat_handler.py` |
| **Plan / quota service** | `services/plan_service.py` | Reuse to gate dashboard features by Koan plan tier. | Light extension |
| **Greylisting / abuse guard** | `services/greylisting_guard_service.py` | Apply to per-tenant proxy endpoints to block brute force on bearer tokens. | None — wire it up |
| **Admin dashboard** | `app/routes/admin/` | Add a "Koan Cloud" section: tenant list, suspend/resume, force redeploy, view logs. | New admin page |
| **Encryption helper** | `services/encryption/` | Encrypt-at-rest GitHub tokens, Anthropic keys, instance bearer tokens stored on `KoanTenant`. | None — reuse |
| **CLI scaffolding** | `app/cli/commands/` (Click groups) | Add a `flask koan ...` group: `provision`, `suspend`, `redeploy`, `usage-sync`. | New: `app/cli/commands/koan.py` |
| **JWT auth decorator** | `@jwt_required` | All `/koan/api/*` routes. | None — reuse |
| **CSRF / form protection** | Existing Jinja patterns | Onboarding forms (Stripe redirect, repo selection). | None — reuse |
| **AI usage tracker** | `services/ai_usage_service.py` | **Pattern** reused, not the service itself — Koan's token usage comes from the tenant runtime, not Anantys's openai_gateway. We write a new aggregator that mirrors the API. | New aggregator |

## Where Koan Cloud lives in `anantys-back`

We follow the **blueprint-per-product-surface** convention already established in the codebase. New top-level pieces:

```
anantys-back/
├── app/
│   ├── routes/
│   │   ├── koancloud/                       # NEW Flask blueprint
│   │   │   ├── __init__.py                  # Blueprint registration
│   │   │   ├── dashboard_api.py             # @jwt_required JSON endpoints
│   │   │   ├── onboarding.py                # GitHub OAuth callback continuation, plan selection, repo picker, provisioning trigger
│   │   │   ├── proxy.py                     # Per-tenant API proxy
│   │   │   ├── chat_ws.py                   # WS bridge to tenant chat
│   │   │   └── webhooks.py                  # Stripe + Railway webhooks specific to Koan
│   │   └── auth/
│   │       └── github_oauth.py              # NEW: GitHub provider routes
│   ├── models/
│   │   ├── koan_tenant.py                   # NEW
│   │   └── koan_usage_record.py             # NEW (token + compute usage rows)
│   ├── templates/
│   │   └── emails/
│   │       └── koan/                        # NEW transactional email templates
│   └── cli/
│       └── commands/
│           └── koan.py                      # NEW: flask koan ... CLI group
├── services/
│   ├── oauth/
│   │   └── github.py                        # NEW
│   └── koancloud/                           # NEW service module
│       ├── __init__.py
│       ├── tenant_service.py
│       ├── railway_client.py
│       ├── provisioning_service.py
│       ├── usage_aggregator.py
│       ├── chat_handler.py                  # WS bridge logic
│       └── projects_yaml_builder.py
└── src/
    └── koan-cloud-app/                      # NEW Next.js dashboard SPA
        # — OR — a route group inside an existing Next.js app
        # Decision: see open question #11
```

Blueprint registration mirrors the existing convention in `app/__init__.py:create_app()`:

```python
from app.routes.koancloud import koancloud_bp
app.register_blueprint(koancloud_bp, url_prefix="/koan")
```

## User model extension

```python
# app/models/user_profile.py  (or User model — TBD per Anantys layout)

class User:
    # ... existing fields ...

    # NEW relationship (1:1, lazy)
    koan_tenant = relationship("KoanTenant", back_populates="user", uselist=False)

    @property
    def has_koan_cloud(self) -> bool:
        return self.koan_tenant is not None and self.koan_tenant.status == "active"
```

`KoanTenant` lives in its own model file (`koan_tenant.py`) and references the `User`. See [01 — Architecture](01-architecture-eagle-view.md) for the field list.

## Auth strategy

**Two paths into the product:**

1. **Primary (signup): GitHub OAuth.** New user clicks "Sign up with GitHub" on `koan.cloud`. We:
   - Receive GitHub OAuth callback in `app/routes/auth/github_oauth.py`.
   - Look up by GitHub email; if no Anantys user, create one via the existing user-creation flow (`services/user_init_service.py`) with `signup_source="koan_cloud"`.
   - Issue Anantys session JWT.
   - Continue onboarding (plan selection → repo picker → provisioning).

2. **Existing Anantys user adds Koan Cloud.** Already-logged-in Anantys user clicks "Enable Koan Cloud". They go through the same onboarding from step "GitHub OAuth → repo picker" onward. (No second signup.)

**Open question:** does the Anantys app expose a single shared "session" cookie domain (`*.anantys.com`) that the Koan dashboard can ride on, or do we issue a Koan-scoped token after onboarding? See [open Q #14](05-open-questions.md).

## Stripe integration

We add to existing `services/stripe_service.py` rather than fork:

- **Products:** one new Stripe product `Koan Cloud`.
- **Prices:** 3 monthly recurring prices (Starter / Pro / Scale — names TBC) + 1 metered price for token overage (if hosted-credits model).
- **Webhooks:** Anantys already handles `customer.subscription.updated`, `invoice.payment_failed`. Add Koan-specific reactions:
  - On `customer.subscription.created` with Koan plan → trigger provisioning.
  - On `customer.subscription.updated` to `canceled` → schedule tenant destroy after 7 days.
  - On `invoice.payment_failed` (3rd attempt) → suspend tenant via `pause_manager`.
- **Metered billing:** if we go hosted-credits, the `usage_aggregator` pushes token usage records to Stripe via the metered billing API on a schedule.

## Mail service reuse

Each Koan Cloud transactional email is a new Jinja template under `app/templates/emails/koan/`, sent through the existing `mail_service.send_*()` helpers. Initial set:

- `welcome.html` — sent on tenant provisioning success
- `instance_ready.html` — link to dashboard, "your first mission is queued"
- `payment_failed.html` — Stripe failure → 3-day grace → suspend
- `budget_cap_reached.html` — monthly budget hit, instance auto-paused
- `instance_crashed.html` — ops alert (also goes to a Slack hook)

**Open question:** sending domain. Mix Koan emails with `@anantys.com` sender, or use a Koan-branded subdomain like `noreply@koan.anantys.com`? See [open Q #15](05-open-questions.md).

## WebSocket reuse

Anantys finagent WS architecture (`services/finagent/chat/`, `run_ws.py`):

- Flask-Sock based, gevent worker (separate Railway target `websocket`).
- Redis ephemeral sessions, token tracking, quota.
- Tool-use / streaming tokens to the client.

For Koan Cloud chat:

- We **reuse** the connection/session/auth scaffolding (Redis, JWT auth, `RAILWAY_TARGET=websocket` deploy target).
- We **add** a new namespace `/koan/chat` with its own handler that:
  - Authenticates the user JWT.
  - Resolves user → tenant.
  - Opens a WS to the tenant's `api_server.py /chat` endpoint.
  - Pipes messages bidirectionally, tagging each leg with the right session ID.

This lets the tenant runtime keep using its own simple WS (no per-customer auth complexity on the tenant side — the bearer token is the only secret the tenant cares about).

## What we do NOT reuse (and why)

- **finagent itself** (the investment-domain LLM logic) — irrelevant to Koan Cloud.
- **services/openai_gateway.py** — Koan calls Anthropic, not OpenAI, and via the Claude Code CLI inside the tenant. The control plane never directly calls an LLM for Koan operations.
- **A/B framework** (`app/utils/experiment.py`) — not needed for Epic #1.
- **Banking, market data, smartnews, achievements, retention emails** — Anantys-specific.
- **Existing investmindr-nextapp / corp-anantys frontends** — Koan Cloud gets its own dashboard (likely a separate Next.js app, see open Q #11).

## Risks of monolith extension

- **Coupling**: a bug in the Koan blueprint that takes down the gunicorn worker also impacts Anantys investmindr. Mitigation: same standards as existing blueprints (no global state, fast handlers, errors logged via `logger.exception`), plus per-route error isolation.
- **Deploy cadence collision**: Anantys ships fast on `main`. Koan Cloud changes deploy with the same train. Mitigation: feature flag (`KOAN_CLOUD_ENABLED`) gating routes during early development.
- **Test surface growth**: pytest suite already large. We add tests under `tests/koancloud/` and keep them isolated.

These are the same trade-offs Anantys already lives with — we're not introducing a new risk class, we're paying the existing tax for a new product.
