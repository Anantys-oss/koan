# 04 — Roadmap and Sprint 1

## Team

| Person | Strengths | Likely owner of |
|---|---|---|
| **Alexis** | Fullstack, Next.js, REST APIs, Kōan creator (deepest core knowledge) | Anantys control plane (Flask blueprint), dashboard SPA, GitHub OAuth, Stripe wiring, mail templates, Anantys integration glue |
| **Nicolas** | Railway automation, CI, Kōan main contributor | Tenant Docker image + cloud mode, `api_server.py`, Railway provisioning client, update / redeploy pipeline, ops/observability |

This split is not a wall — both work in `koan/` and `anantys-back`. It's about who **drives** each domain.

## Phases (mapped to Epic #1)

These are derived from RFC #1 with adjustments based on the user's brief (hosted credits over BYOK, dashboard on Anantys, end-to-end click-and-play in v1).

| Phase | Goal | Lead | Calendar (estimated) |
|---|---|---|---|
| **0 — POC & de-risking** | Manually deploy 1 Koan tenant on Railway. Validate Anthropic API mode end-to-end. Validate Railway API can do everything we need. | Nicolas | Week 1 |
| **1 — Tenant HTTP API** | `api_server.py`, `cloud_mode.py`, Dockerfile cloud entry, parity with Telegram commands | Nicolas | Weeks 2–4 |
| **2 — Anantys control plane** | `koancloud` blueprint, GitHub OAuth, Stripe plans, tenant model, provisioning service, Railway client | Alexis | Weeks 2–5 (parallel with Phase 1) |
| **3 — Dashboard SPA** | Next.js app, Greptile-inspired UI per issue #2, all primary screens consuming the proxy API | Alexis | Weeks 4–7 |
| **4 — Billing & metering** | Stripe metered, usage aggregator, budget caps, dashboard billing screen | Alexis (data flow) + Nicolas (tenant-side push) | Weeks 6–8 |
| **5 — Polish & soft launch** | Suspend/resume, redeploy pipeline, alerting, transactional emails, ToS/privacy, 5 design partners | Both | Weeks 8–10 |

**Headline target:** Epic #1 shippable to design partners in **~10 weeks** with both engineers near full-time on it. The original RFC said ~3 months for 2 engineers, this aligns.

## Dependencies (critical path)

```
Phase 0  ──►  validates: Railway API, Anthropic API mode, Docker image boots
                │
                ├──► Phase 1 (Nicolas)
                │      │
                │      └──► Tenant API surface stable enough
                │
                └──► Phase 2 (Alexis)
                       │
                       ├──► Provisioning works end-to-end (needs Phase 1 image)
                       │
                       └──► Phase 3 (Alexis)
                              │
                              └──► Dashboard consumes proxy → tenant API
                                       │
                                       └──► Phase 4 (both): metering hooks in
                                                │
                                                └──► Phase 5: launch readiness
```

The longest chain is Phase 0 → 1 → 2 → 3, and Phase 3 (dashboard) is roughly the longest single phase. Working Phases 1 and 2 in parallel after Phase 0 closes is essential to hit the 10-week target.

## Sprint 1 (week 1) — proposed plan

Sprint 1 is **Phase 0** plus **alignment** on every open question that blocks Phase 1+2. The deliverable is not code in production; it's "we have de-risked everything and committed a workable plan."

### Sprint 1 goals

1. **One Koan tenant alive on Railway**, manually provisioned, executing a mission end-to-end with `ANTHROPIC_API_KEY` (no Pro/Max session). [Nicolas]
2. **Provisioning script** that creates a Railway service, sets env vars, mounts volume, deploys, hits health check. [Nicolas]
3. **Architectural decisions ratified** by Alexis on every "Recommendation: …" in [01](01-architecture-eagle-view.md), [02](02-anantys-stack-reuse.md), [03](03-tenant-runtime.md) and answers to all blocking [open questions](05-open-questions.md). [Alexis to drive]
4. **Pricing decision committed**: tiers, hosted-credits vs BYOK, free tier or not. [Alexis]
5. **Stripe products & prices created** in test mode. [Alexis]
6. **GitHub OAuth app registered** (dev + prod). [Alexis]
7. **Issues opened in `koan-private` and `anantys-back`** for every Phase 1 and Phase 2 task, with owner assigned. [Both]

### Sprint 1 deliverables (concrete)

| ID | Deliverable | Owner | Definition of done |
|---|---|---|---|
| S1-01 | Manual Koan-on-Railway POC | Nicolas | Railway service running latest Koan, picks up a mission from `missions.md`, Claude API call succeeds, mission archived to Done |
| S1-02 | Provisioning script (`scripts/provision_tenant.sh` or Python) | Nicolas | Single command creates a working tenant given a tenant ID + GitHub token + Anthropic key |
| S1-03 | Railway API client prototype (`services/koancloud/railway_client.py` skeleton) | Nicolas | Can list services, create service, set env, deploy, attach volume |
| S1-04 | Open-questions doc fully answered by Alexis | Alexis | All Q1–Q35 in [05](05-open-questions.md) have a `**Decision:**` line |
| S1-05 | Stripe Koan product + 3 price IDs (test mode) | Alexis | Product visible in Stripe test dashboard, IDs documented |
| S1-06 | GitHub OAuth dev app | Alexis | Client ID/secret recorded in 1Password, callback URL chosen |
| S1-07 | Phase 1 task list (issues in `koan-private`) | Nicolas | One issue per substantive task, prioritized |
| S1-08 | Phase 2 task list (issues in `anantys-back`) | Alexis | Same |
| S1-09 | Sprint 2 plan agreed | Both | Selected tasks loaded into a board, calendar slot booked |

## Working agreements (proposed — confirm in Sprint 1 retro)

- **Cadence:** 1-week sprints. Mondays kickoff, Friday demo+retro.
- **Async-first:** progress in PR descriptions and a daily Slack note; meetings booked only when an open question blocks.
- **Where work lives:**
  - `koan-private` repo: tenant runtime, `api_server.py`, cloud mode, Dockerfile changes, sprint specs (this directory).
  - `anantys-back` repo: control plane blueprint, dashboard, Stripe wiring, mail, GitHub OAuth.
  - `koan.cloud` marketing site: separate (out of scope for Sprint 1).
- **Branch convention:** `koancloud/<short-desc>` in `anantys-back`, normal Koan convention in `koan-private`.
- **Definition of done for any task:** PR opened, tests added/updated, peer-reviewed, merged.
- **No production traffic** in Sprint 1 — everything is test mode (Stripe test, dev Anthropic key, dev Railway project).

## Risks for Sprint 1 specifically

| Risk | Mitigation |
|---|---|
| Anantys deploy cadence collides with our exploratory work | Feature-flag every Koan route during Phase 2 (`if not app.config["KOAN_CLOUD_ENABLED"]: return 404`) |
| Railway API has a gap we don't discover until Phase 2 | That's the whole point of Phase 0 — discover it now, not in week 6 |
| Open questions don't get answered → planning paralysis | Time-box: by end of week 1, every question has a written decision (even if the decision is "punt to v2") |
| Anthropic key model decision flips late | All tenant-side code stays generic — both BYOK and master-key inject the same `ANTHROPIC_API_KEY` env var. The decision affects *who provides the key*, not how the tenant uses it |

## Beyond Epic #1 (preview, not committed)

Topics deliberately deferred:

- Multi-user / team workspaces.
- Self-service plan upgrades / downgrades mid-cycle.
- GitHub App (replacing OAuth).
- EU data residency.
- Mobile-polished UX.
- Public marketing site at `koan.cloud`.
- Public ToS, privacy policy review by counsel.
- Anthropic AUP enforcement tooling (content moderation, abuse detection — needed before scale, not before launch).
- Affiliate / referral program.
- Self-serve API docs portal (for power users wanting raw tenant API access).
