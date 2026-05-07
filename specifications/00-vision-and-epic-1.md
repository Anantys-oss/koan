# 00 — Vision and Epic #1

## Product vision

> **Koan Cloud** is a click-and-play SaaS that gives any developer their own autonomous engineering agent on their own GitHub repos in under 5 minutes — no install, no YAML, no Telegram, no API key juggling.

The customer never sees Kōan's mechanics. They see a web dashboard where they queue missions, chat with their agent, watch it ship draft PRs, and pay a monthly bill that covers everything (compute + tokens + product).

## Ideal user scenario

The "happy path" we are building toward:

1. User lands on the marketing site (working name: `koan.cloud`).
2. Clicks **"Get started"** → redirected to **GitHub OAuth**.
3. Returns authenticated. An Anantys account has been created behind the scenes (no separate signup).
4. **Stripe checkout** → picks a plan ($89 / $249 / $499 — pricing TBC, see [#5](05-open-questions.md)).
5. **Repository picker** → selects 1–N GitHub repos to plug Kōan onto.
6. Behind the scenes, a fresh **Railway service** is provisioned (their personal Kōan instance), env vars injected (Anthropic key, GitHub token, instance bearer token, `projects.yaml`), volume mounted, agent loop started.
7. User lands on the **dashboard** — they see their first mission already queued ("Hi! Let me introduce myself and scan your repos…") and Kōan is starting to work.
8. From now on, the dashboard *is* the product: queue missions, chat with Kōan, monitor activity, see usage / billing.

**Hard target:** under 5 minutes from `koan.cloud` landing to first mission running, no human intervention from our side.

## What "Epic #1" means

Epic #1 is the **smallest end-to-end product** that makes that scenario real for a paying customer. If we ship it, we have a product. If we don't, we don't.

### In scope (MVP)

- ✅ GitHub OAuth-first signup, account auto-created in Anantys.
- ✅ Stripe checkout with 3 paid tiers (no free tier in v1 — see [open questions](05-open-questions.md)).
- ✅ Repo picker (GitHub OAuth → list user repos → multi-select).
- ✅ Automated Railway provisioning (1 service per customer).
- ✅ Per-tenant **HTTP API** on the Kōan instance (replaces Telegram).
- ✅ Web dashboard hosted on the Anantys platform with:
  - Missions queue (list, create, view results)
  - Chatbot surface (real-time, with `/slash` command parity)
  - Activity / journal view
  - Skills run-form
  - Settings (pause/focus/passive, soul.md edit, project config)
  - Billing & usage (current month, budget cap)
- ✅ Real Anthropic API in **API mode** (no Pro/Max session tokens). Hosted-credits or BYOK strategy → see [open questions](05-open-questions.md).
- ✅ Reuse Anantys mail service for transactional emails (welcome, billing, alerts).
- ✅ Tenant suspend/resume on payment status changes.

### Explicit non-goals for Epic #1

- ❌ Standalone product domain front-end (`koan.cloud` marketing aside, the dashboard runs under the Anantys platform — see [02 — Anantys stack reuse](02-anantys-stack-reuse.md)).
- ❌ Telegram bridge support in the cloud product (still works in self-hosted).
- ❌ Multi-user teams / workspaces. v1 = single user per tenant.
- ❌ Mobile-polished UX. Desktop-first, mobile-responsive only.
- ❌ Free tier or trial — TBC, see [#3](05-open-questions.md).
- ❌ EU data residency / region selection.
- ❌ Self-service plan upgrades mid-cycle (manual ops for v1 if needed).
- ❌ GitHub App (we start with GitHub OAuth + tokens; App is a v2 upgrade).

## Definition of "Epic #1 done"

We declare Epic #1 done when:

1. A new user (no prior Anantys account) signs up via GitHub OAuth, pays via Stripe, picks repos, and reaches a working dashboard with a running Kōan instance — **without any manual intervention** from us — in under 5 minutes.
2. From that dashboard, the user can queue a mission, chat with their agent, and merge the resulting PR back in their repo.
3. The next month, the user is automatically charged via Stripe; if their card fails, their tenant is gracefully suspended; if they cancel, their tenant is cleanly destroyed.
4. We have onboarded **at least 5 paying design partners** through this flow with zero manual ops touch on the happy path.

## Why this scope

The RFC (issue #1) breaks the work into Phases 0–4. Epic #1 corresponds roughly to **Phase 0 + Phase 1 + Phase 2 + Phase 3 (subset)** of that RFC. Phase 4 (alerting, public docs, soft launch polish) is part of Epic #1 *only* to the extent strictly needed to take real money from real customers safely. Everything else slips to Epic #2+.
