---
type: component-spec
title: "Component Spec — Git & GitHub"
tags: [git-github]
created: 2026-06-27
updated: 2026-06-27
---

# Component Spec — Git & GitHub

**Modules:** `git_sync.py`, `git_auto_merge.py`, `github.py`, `github_url_parser.py`,
`github_skill_helpers.py`, `github_config.py`, `github_notifications.py`,
`github_command_handler.py`, `github_webhook.py`, `rebase_pr.py`, `recreate_pr.py`,
`claude_step.py`, `remote_rename_detector.py`, `head_tracker.py`

## Purpose

Everything that touches git history or the GitHub API. Kōan's output is branches and
PRs; this layer creates them safely, syncs branch state, reacts to GitHub events
(@mentions, review comments, push webhooks), and runs the rebase/recreate/CI-fix
workflows.

## Key types & functions

| Symbol | Contract |
|---|---|
| `github.py::run_gh()` | **The only sanctioned `gh` invocation path.** Wraps retry/backoff. Callers mock at this level, never at `subprocess.run`. |
| `github.py::pr_create()` / `issue_create()` | Centralized PR/issue creation (always draft PRs). |
| `git_auto_merge.py` | Configurable per-project auto-merge; runs after `security_review.py`. |
| `git_sync.py` | Branch tracking, sync awareness, time-throttled cleanup (24h/project), orphan-branch detection → outbox. |
| `github_webhook.py::maybe_start_from_config()` | Opt-in HMAC-verified push receiver; writes `.koan-check-notifications` to collapse poll latency 60-180s → ~10s. Polling remains the fallback. |
| `github_command_handler.py` | @mention → mission: validate → permission check → react → create mission. Also the assignment fallback (`process_single_notification` processes @mentions first, then `_try_assignment_notification`): `review_requested` → `/review`, `assign` → `/implement`. When `review_draft_skip.enabled` is true, a `review_requested` on a **draft** PR is a soft skip (mark read, no thread/cooldown tracking): because no dedup state is written, any re-surfaced request is re-evaluated fresh. An explicit `/review` once the PR is ready is the remedy — the gate does **not** rely on automatic resume (GitHub does not reliably re-fire `review_requested` on the draft→ready transition), so an info notification is sent on deferral to avoid silent loss; explicit `/review` mentions are never gated. |
| `claude_step.py::run_ci_fix_loop()` | Shared CI-fix loop; `use_polling` toggles polling vs single-shot recheck; caller supplies `prompt_builder`. |
| `head_tracker.py` | Detects remote HEAD change (master→main), throttled 12h, state in `.head-tracker.json`. |
| `github_url_parser.py` | Single PR/issue URL parsing path. |

## Invariants

- **Branches are always `<prefix>/*`** (default `koan/`, configurable). Never commit to
  main, never merge — these are hard safety boundaries.
- **PRs are always draft** (`gh pr create --draft`).
- **`gh` is the only GitHub transport.** No `curl`, raw API outside `gh api`, or
  git-based API workarounds (OPSEC: no external network beyond `gh`).
- **Fork-awareness.** When `origin` is a fork, PRs target upstream via `--repo` +
  `--head <fork-owner>:<branch>`. Multi-account pushes resolve the remote owner's token
  (`gh auth token --user <owner>`); tokens are redacted in logs.
- **Mock above `retry_with_backoff`.** Test error handling at `run_gh()`/`api()`, never
  at `subprocess.run` — the latter sleeps 1+2+4s per retry (anti-pattern 6).

## Integration points

- Notifications wired into `loop_manager.process_github_notifications()`, which also
  drives `review_comment_dispatch.py` and `ci_dispatch.py`.
- Webhook receiver started in the bridge (`maybe_start_from_config()`) or standalone
  (`make webhook`).
- Commit messages shaped by `commit_conventions.py`.
- Auto-merge gated by `security_review.py`.

## Known debt / watch-outs

- `fetch_failing_check_runs()` returns `None` on API error vs `[]` on CI-green — callers
  must check `is None` before treating empty as "all passed".
- Webhook is opt-in and off by default; polling must stay correct as the reliability
  fallback.
- Orphan-branch detection only notifies; it never deletes — deletion stays human-driven.

## Change protocol

Changes to branch/PR creation, the `gh` wrapper, or webhook verification update this
spec and a `docs/messaging/` page where user-facing. Never weaken the
draft-PR / no-merge / `gh`-only invariants without explicit human direction.
