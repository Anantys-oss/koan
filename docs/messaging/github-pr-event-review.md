---
type: doc
title: "Review on PR Events — Automatic /review on Opened and Pushed PRs"
description: "Opt-in per-project mode that queues /review when a PR is opened, reopened, marked ready, or pushed — poll-driven, with optional webhook latency assist."
tags: [messaging]
created: 2026-11-13
updated: 2026-11-13
---

# Review on PR Events

By default Kōan reviews a PR only when asked (`/review`, an @mention, or a review
request). With **review on PR events** enabled, the GitHub poll also queues
`/review <pr-url>` when a PR in the project is **opened**, **reopened**, **marked
ready for review**, or — with `on_push` — **gets new commits**.

## Enable it

```yaml
# config.yaml (global default; requires github.commands_enabled + github.nickname)
github:
  pr_event_review:
    enabled: false
    on_push: true            # re-review each new head SHA; false = first eligible head only
    include_drafts: false
    max_per_cycle: 3         # per repo per scan
    scan_interval_minutes: 5 # per-repo throttle; 0 = every poll

# projects.yaml (per-project; keys override config.yaml one by one)
projects:
  my-toolkit:
    github:
      pr_event_review:
        enabled: true
```

## What gets reviewed

A PR is queued when all of these hold:

- It was updated **after the repo's first scan** and within `github.max_age_hours`.
  The first scan after enabling only records a starting point — existing PRs are
  never reviewed in a burst.
- It is not a draft (unless `include_drafts`) and has no `review_pause_label` label.
- It was not opened by the bot or from a `koan/` (branch-prefix) branch — Kōan's
  own PRs are covered by `/autoreview`.
- Its author passes `github.authorized_users` (per-project override honored;
  `"*"` means "has write access").
- Its head commit has not been reviewed by this feature yet, no `/review` for it
  is already queued, and it is outside the 30-minute review cooldown (a push
  during the cooldown is reviewed once the cooldown ends).

The queued mission looks like
`- [project:my-toolkit] /review https://github.com/my-org/my-toolkit/pull/7 📬`.

## Latency and webhooks

Without webhooks, a new PR is picked up within `scan_interval_minutes`. With the
[webhook receiver](github-webhooks.md) enabled, `pull_request` events for these
actions force an immediate poll that bypasses the throttle (~10s). The webhook
never queues a review itself — a dropped delivery only delays the review to the
next scan. Changing `pr_event_review` takes effect for the webhook filter after a
bridge restart; the scan itself picks it up on its next cycle.

## Cost

Each enabled repo costs one `gh pr list` per scan, plus one Claude review per
queued PR. Use `on_push: false`, `max_per_cycle`, drafts, or the pause label to
limit volume.
