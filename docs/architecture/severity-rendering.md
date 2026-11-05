---
type: doc
title: "Shared Severity Rendering"
description: "Documents koan/app/severity.py: the single canonical severity → emoji / heading table shared by /review, /audit and /gha_audit, its alias table for review's 3-tier names, and what it deliberately does not cover (ordering, thresholds, effort)."
tags: [architecture]
created: 2026-11-04
updated: 2026-11-04
---

# Shared severity rendering (`app/severity.py`)

One canonical map from severity → emoji / heading, shared by `/review`, `/audit`
and `/gha_audit`, so a `CRITICAL` finding looks the same in every skill.

| Tier | Emoji | Heading (`/review` only) |
|------|-------|--------------------------|
| `critical` | 🔴 | Blocking |
| `high` | 🟠 | High Priority (no current caller) |
| `medium` | 🟡 | Important |
| `low` | 🟢 | Suggestions |

## API

- `severity_emoji(tier, default="⚪")` — case-insensitive lookup; unknown tiers
  return the caller's `default`.
- `severity_heading(tier, default="")` — same normalization, for `/review`'s
  section headings (English by design, see `specs/skills/review.md`).
- `SEVERITY_ALIASES` folds review's 3-tier vocabulary onto the canonical set:
  `warning` → `medium` (🟡), `suggestion` → `low` (🟢).

## Caller-side guards

Skills keep their own data-contract enums; only rendering is shared. Callers
whose input is not normalized check their own key set before calling the helper:

- **Review silent-failure hunter** renders only `CRITICAL`/`HIGH`/`MEDIUM`
  through it (its prompt contract); anything else stays ⚪.
- **`/audit`** renders only exact `_SEVERITY_ORDER` keys (lowercase
  `critical`/`high`/`medium`/`low`); raw LLM text such as `HIGH` or `warning`
  stays ❓, consistent with how ranking and threshold gating treat it.
- **`/gha_audit`** uses the table directly; its `LOW` renders 🟢.

## Out of scope

Ordering and threshold comparison (`audit_runner._SEVERITY_ORDER`,
`severity_at_or_above`) and audit's effort labels (`_EFFORT_LABELS`) are
different axes — behavior, not rendering — and stay in their skills.
