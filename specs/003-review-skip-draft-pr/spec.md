# Feature Specification: Draft-PR Auto-Review Gate (`review_draft_skip`)

**Feature Branch**: `003-review-skip-draft-pr`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "Currently review automatically occurs whatever is the state of a PR. We want to preserve that behavior as default. But introduce a new boolean configuration variable to only start a review when the PR is non-draft. So a draft PR would not trigger a review even when the bot is attached as a reviewer. If a human pings `/review` from chat or GitHub then we always honor it. The flag only concerns when the bot is attached as a reviewer."

## Overview

Kōan auto-queues a `/review` mission whenever GitHub notifies it that it was
**attached as a reviewer** on a PR (notification reason `review_requested`),
routed through `_try_assignment_notification()` in
`koan/app/github_command_handler.py`. Today this fires regardless of whether the
PR is a draft — reviewing work-in-progress the author has explicitly marked "not
ready."

This feature adds an **opt-in** configuration flag, `review_draft_skip`, that
defers that automatic review while the PR is in draft state. Default behavior
(review always, including drafts) is preserved exactly.

## Scope

**In scope** — the `review_requested` → `/review` auto-queue path (the "bot
attached as reviewer" notification). This is the *only* path the flag gates.

**Out of scope (deliberate)**:

- **Explicit `/review` requests** — a human typing `/review` in chat, or
  `@bot /review <url>` on a GitHub thread, is dispatched on a **separate** path
  (`process_single_notification` processes all `@mention` comments *before*
  falling back to the assignment path). These are **always honored** and are
  never subject to this flag. This is the documented human override.
- **Post-mission autoreview** (`mission_runner._maybe_queue_autoreview`) — the
  bot reviewing its *own* freshly-created PR after a mission completes, gated by
  the separate per-project `autoreview` flag. That is not "the bot attached as a
  reviewer by a human" and is untouched. (Kōan-authored PRs are not draft by
  default, so the gate would be inert there regardless.)

## Functional Requirements

### FR-001 — Default behavior is unchanged
When `review_draft_skip.enabled` is `false` (the default) or absent, a
`review_requested` notification queues `/review` exactly as today, including for
draft PRs. No observable change for operators who do not opt in.

### FR-002 — New config key `review_draft_skip`
A new top-level `config.yaml` section:

```yaml
review_draft_skip:
  enabled: false   # default; set true to defer auto-review of draft PRs
```

- Type: nested mapping with a single boolean `enabled`.
- Default: `enabled: false`.
- Malformed values (non-dict section, non-bool `enabled`) coerce to `false`
  (fail to the safe/default behavior), never raise.

### FR-003 — Draft PRs are deferred when the gate is enabled
When `review_draft_skip.enabled` is `true` **and** the PR is draft (GitHub PR
object `draft: true`), a `review_requested` notification does **not** queue a
`/review` mission. The notification is consumed (marked read) and reported as a
handled no-op.

### FR-004 — Soft skip (re-armable)
The draft deferral is a **soft skip**: it must **not** record the thread
(`track_thread`) or set the review cooldown (`set_review_cooldown`). This
guarantees the review fires automatically when GitHub re-fires
`review_requested` after the PR is marked ready for review (new notification id
→ fresh processing), and never suppresses a later legitimate review.

### FR-005 — Human `/review` is always honored
Explicit `/review` from chat or from a GitHub `@mention` is never gated by this
flag (separate dispatch path). This requirement is satisfied structurally by the
existing dispatch ordering and is asserted by a regression test.

### FR-006 — Non-draft PRs are unaffected
When the gate is enabled but the PR is **not** draft, the review is queued as
normal. The gate affects draft PRs only.

### FR-007 — Visibility
A single best-effort Telegram INFO notification is sent when a draft review is
deferred, so the operator understands the review was intentionally deferred
(not silently dropped), with the remedy ("mark ready for review, or send
`/review`"). Failures sending this notification are logged and never abort
processing. (Mirrors the existing closed/merged skip notification.)

## Data Model

`_fetch_subject_info()` already performs one GitHub API call returning
`{state, merged, head_sha}`. It is extended to also return `draft`
(`jq="{state: .state, merged: .merged, head_sha: .head.sha, draft: .draft}"`).
For issues (the `assign` path) `.draft` is absent → `null`; for non-draft PRs it
is `false`; for draft PRs `true`. No additional API call is introduced.

## Non-Functional

- **No new network calls** — reuses the single existing subject fetch.
- **No config-breakage** — default off; existing configs behave identically.
- **Python 3.11+**, ruff-clean (PERF), no `# noqa` without documented reason.
- **Config validation** — the new key is registered in `config_validator.py`
  (top-level `_NESTED` + section schema) so it is recognized and type-checked,
  matching how `review_concurrency` / `review_ignore` are registered.

## User Stories

### US1 — Operator opts into draft-skipping
As an operator, I can set `review_draft_skip: { enabled: true }` so the bot stops
automatically reviewing PRs their authors have marked draft, until they are
ready.

### US2 — Default operators see no change
As an operator who does nothing, review behavior is identical to today
(draft PRs are still auto-reviewed).

### US3 — Author/human can still force a review
As a human, I can always get a draft PR reviewed immediately by sending
`/review` (chat) or `@bot /review` (GitHub), regardless of the flag.

### US4 — Ready-for-review re-triggers automatically
As an operator with the gate enabled, when a deferred draft PR is later marked
ready for review, the bot reviews it on the next `review_requested` re-fire
without any manual workaround.

## Acceptance Criteria

- AC1: With the flag disabled (default), a draft `review_requested` notification
  queues `/review` (regression: current behavior preserved).
- AC2: With the flag enabled, a draft `review_requested` notification does **not**
  queue `/review`, marks the notification read, reports NOOP, and writes neither
  thread-tracker nor cooldown state.
- AC3: With the flag enabled, a **non-draft** `review_requested` notification
  queues `/review` as normal.
- AC4: An explicit `@bot /review` mention on a draft PR queues `/review` even
  with the flag enabled.
- AC5: Malformed config coerces to disabled (no crash).
- AC6: `make lint` and the relevant test files pass; the new key is recognized by
  the config validator (no "unrecognized key" warning).

## Open Questions / Resolved

- **Q: Should the gate also cover post-mission autoreview?** → A: No. The task
  explicitly scopes the flag to "the bot attached as a reviewer." Autoreview is a
  distinct mechanism (self-review of bot-authored PRs) gated by the separate
  per-project `autoreview` flag, and bot-authored PRs are non-draft by default.
- **Q: Track the thread / set cooldown on deferral?** → A: No — soft skip only,
  so the ready-for-review re-fire is processed fresh (FR-004). Hard-tracking
  would permanently suppress the review.
- **Q: Notify on deferral?** → A: Yes, one INFO message (FR-007), mirroring the
  closed/merged skip notification, so a deferred review is not mistaken for a
  dropped one.
