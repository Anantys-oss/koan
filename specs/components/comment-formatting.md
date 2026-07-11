---
type: component-spec
title: "Component Spec — Comment Formatting (GitHub alert callouts)"
description: "Design contract for build_alert(), the single constructor for GitHub alert callouts, plus the type→situation mapping and the parsimony rule every skill must follow."
tags: [git-github, skills]
created: 2026-07-10
updated: 2026-07-10
---

# Component Spec — Comment Formatting (GitHub alert callouts)

**Module:** `koan/app/github_alerts.py` (`build_alert`)

## Why this component exists

GitHub renders `> [!NOTE|TIP|IMPORTANT|WARNING|CAUTION]` blocks as distinct
colored icon callouts in the PR/issue UI, in email notifications, and on mobile —
the intended mechanism for making one line un-missable. Before this component,
call sites hand-typed the syntax (`rebase_pr.py`, `review_runner.py`) and other
skills reinvented emoji headers (#2304). `koan/app/github_alerts.build_alert()`
is the single constructor: every future skill has one function to call instead of
reinventing an emoji scheme or ignoring the mechanism.

## Contract

- `build_alert(kind, text, provider="github") -> str` is the ONLY sanctioned way
  to emit an alert callout. `kind` is one of the five native GitHub types
  (case-insensitive; leading/trailing whitespace tolerated); any other value
  raises `ValueError`.
- The returned block carries NO leading/trailing blank lines — callers own the
  surrounding whitespace. This keeps migrations byte-identical: existing callers
  keep their `+ "\n"` / leading `"\n\n"` spacing.
- Multi-line `text` is prefixed on **every** line, including blank paragraph
  separators (rendered as a bare `>`), so the whole body renders as one callout.
- `provider != "github"` degrades to a plain-text `KIND: text` prefix so callers
  never special-case Jira or other trackers. Embedded newlines are preserved.

## Type → situation mapping

| Type | Use for |
|------|---------|
| NOTE | Neutral context the reader should not miss. |
| TIP | Optional advice / a better way to do something. |
| IMPORTANT | Info critical to success (e.g. "the branch moved during review"). |
| WARNING | Needs immediate reader attention; risk of a bad outcome (e.g. "review feedback was NOT applied"). |
| CAUTION | Risk / negative consequence of an action. |

Non-native project conventions (QUESTION / TODO / BUG / SUCCESS / ERROR) have
**no** GitHub-native callout — express them with emoji + bold in prose, NOT via
`build_alert()`. They are intentionally absent from the helper's allowed kinds:
encoding them in code would be the "templating DSL" scope creep this component
deliberately avoids.

## Parsimony rule (hard)

At most **1–2** alerts per posted comment. NEVER one alert per finding — a wall
of callouts defeats the "un-missable" purpose and is noise. Reserve alerts for
the single most important thing the reader must not miss.

## Consumers

- `koan/app/rebase_pr.py` — WARNING when review feedback was dropped.
- `koan/app/review_runner.py` — IMPORTANT when the branch moved mid-review.
- `koan/app/review_runner.py` — IMPORTANT for blocked-review (`lgtm: false`) verdict summaries.
- `koan/skills/core/audit/audit_runner.py` — CAUTION for critical-severity audit findings.

## Invariants

- `build_alert` imports nothing from `app`, so it is safe to import from any
  module (no cycle risk).
- The allowed-kinds set is exactly the five native GitHub types. Widening it is a
  contract change and requires an architectural declaration.
