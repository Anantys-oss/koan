# Koan Cloud — Specifications

Architecture and roadmap documents for **Koan Cloud**, the managed SaaS offering of Kōan operated by Anantys.

These docs are the working source of truth for Alexis & Nicolas while we shape Epic #1. They are deliberately starting-point drafts — every document has open questions and is expected to evolve.

## Reading order

| # | Document | Purpose | Audience |
|---|----------|---------|----------|
| 00 | [Vision and Epic #1](00-vision-and-epic-1.md) | What we're building, ideal user scenario, Epic #1 scope (in/out) | Both |
| 01 | [Architecture — eagle view](01-architecture-eagle-view.md) | The 3-plane architecture (browser ↔ control plane ↔ tenant runtime), trust boundaries, data flows | Both |
| 02 | [Anantys stack reuse](02-anantys-stack-reuse.md) | Exactly which Anantys modules we reuse (auth, mail, websocket, Stripe, user model) and how Koan Cloud plugs into the existing Flask app | Alexis (lead) |
| 03 | [Tenant runtime](03-tenant-runtime.md) | The per-customer Railway service: HTTP API replacing Telegram, Anthropic key model, provisioning, updates | Nicolas (lead) |
| 04 | [Roadmap and Sprint 1](04-roadmap-and-sprint-1.md) | Phases, ownership split (Alexis / Nicolas), Sprint 1 deliverables, dependencies | Both |
| 05 | [Open questions](05-open-questions.md) | Everything we need clarified before locking the plan | Alexis to answer |

## Related GitHub issues

- **[#1 — RFC: Koan Cloud SaaS architecture and roadmap](https://github.com/Anantys/koan-private/issues/1)** — Original strategy doc. These specs build on it and resolve where it diverges from the latest direction (pricing, hosted-credits vs BYOK, dashboard hosting).
- **[#2 — Koan Cloud Dashboard v1 (hosted on anantys.com, Greptile-inspired)](https://github.com/Anantys/koan-private/issues/2)** — Dashboard sub-issue. Visual direction and IA defined there.

## Status

🟡 **Draft v0.1** — written 2026-05-07. Awaiting Alexis's answers to [open questions](05-open-questions.md) before promoting to v1 and locking Sprint 1.
