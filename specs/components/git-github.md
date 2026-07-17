---
type: component-spec
title: "Component Spec — Git & GitHub"
description: "Design contract for everything touching git history or the GitHub API: branch/PR creation, sync, webhook/notification handling, and rebase/recreate/CI-fix workflows."
tags: [git-github]
created: 2026-06-27
updated: 2026-07-16
---

# Component Spec — Git & GitHub

**Modules:** `git_sync.py`, `git_auto_merge.py`, `github.py`, `github_url_parser.py`,
`github_skill_helpers.py`, `github_config.py`, `github_notifications.py`,
`github_command_handler.py`, `github_webhook.py`, `rebase_pr.py`, `recreate_pr.py`,
`claude_step.py`, `remote_rename_detector.py`, `head_tracker.py`, `git_prep.py`

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
| `github_command_handler.py` | @mention → mission: validate → permission check → react → create mission. Also the assignment fallback (`process_single_notification` processes @mentions first, then `_try_assignment_notification`): `review_requested` → `/review`, `assign` → `/implement`. When `review_draft_skip.enabled` is true, a `review_requested` on a **draft** PR is a soft skip (mark read, no thread/cooldown tracking): because no dedup state is written, any re-surfaced request is re-evaluated fresh. An explicit `/review` once the PR is ready is the remedy — the gate does **not** rely on automatic resume (GitHub does not reliably re-fire `review_requested` on the draft→ready transition), so an info notification is sent on deferral to avoid silent loss; explicit `/review` mentions are never gated. When `review_pause_label` is a non-empty string and a `review_requested` subject's labels include that exact name, soft-skip (mark read, no thread/cooldown, INFO notify). Empty `review_pause_label` disables the check. Labels are fetched free on the existing `_fetch_subject_info` call. |
| `claude_step.py::run_ci_fix_loop()` | Shared CI-fix loop; `use_polling` toggles polling vs single-shot recheck; caller supplies `prompt_builder`. |
| `claude_step.py::_rebase_onto_target()` | Strict PR rebase: target is **only** `{base_remote}/{base}` (the remote matching the PR's base repo, resolved by `rebase_pr._find_remote_for_repo`); fails closed when no remote matches or the target fetch fails; always a plain `git rebase` (never `--onto`); post-rebase sanity gate before any push. Structured failure codes via `result_meta` (`no_base_remote` / `fetch_failed` / `rebase_failed` / `sanity_check_failed`). |
| `claude_step.py::_verify_rebase_result()` | Post-rebase gate: branch must sit on the target's current tip and its unique-commit count (`rev-list --count target..HEAD`) must not grow vs the pre-rebase baseline. On violation the branch is hard-reset to its pre-rebase commit and the rebase reported failed — nothing is pushed. |
| `head_tracker.py` | Detects remote HEAD change (master→main), throttled 12h, state in `.head-tracker.json`. |
| `github_url_parser.py` | Single PR/issue URL parsing path. |
| `git_prep.py::prepare_project_branch()` | Pre-mission: fetch → **self-heal interrupted merge/rebase** → stash → checkout base → ff-only/reset to `<remote>/<base>`. Non-fatal; returns `PrepResult`. |

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
- **Rebases target the PR's base repo remote, freshly fetched, or fail.** No fallback
  to other remotes (a fork's `main` is stale by construction), no proceeding on a
  failed fetch, no `--onto` with a fork branch as cut point, and no push when the
  post-rebase sanity gate flags a stale base or commit-count growth. A failed rebase
  mission is recoverable; a polluted force-push is not (incident: PR #2309 — a rebase
  onto a 4-days-stale ref with the fork's 1,889-commits-behind `main` as cut point
  resurrected 33 already-merged commits and force-pushed them unchecked).
- **Self-heal precedes stash.** An interrupted merge/rebase/cherry-pick/revert
  (or bare unmerged paths / stale `index.lock`) is auto-aborted before stashing,
  because git cannot stash a conflicted tree and the next step resets to the
  remote base anyway. A **conflict-free** dirty tree is never discarded — the
  stash data-loss guard still holds for genuine uncommitted work.

### Mission status indicators (koan/mission)

While a GitHub-linked mission runs, Kōan surfaces a live indicator with no
GitHub App:

- **Issue label** `koan:working` — toggled on the linked issue for the whole
  run (primary live signal; the issue is known at mission start).
- **Commit status** `context=koan/mission` — posted `pending` on the pushed
  branch head at first push, resolved `success`/`failure`/`error` at finalize.

**Invariants**

- All writes go through `github.run_gh()`/`github.api()` (gh-only transport),
  honoring the existing `gh`-only invariant above.
- Indicators are best-effort: a write failure logs and degrades, never blocks
  the mission.
- Every terminal path (success, failure, abort, stagnation-cap, crash
  recovery) resolves the commit status and removes the label. The two writes
  are independent (one failing never skips the other or orphans the label), and
  the tracker entry is dropped **only when both required writes succeed** — a
  failed teardown retains the entry so the next startup reconcile retries it,
  guaranteeing no stale `koan/mission` status or `koan:working` label is left on
  GitHub. A hard crash is likewise reconciled at next startup
  (`mission_status.reconcile_stale_indicators`).
- Cross-stage state lives in `instance/.running-indicator.json`, keyed by
  mission title; local-only missions (no issue URL, no `github_url`) write
  nothing.
- Gated by a global `running_indicator` config block plus a per-project
  override; on by default, opt-out via `running_indicator.enabled: false`.

### @mention intent resolution (natural language)

When rigid parse (word-0 = github-enabled command) misses **and**
`natural_language` is enabled, the bridge resolves intent via a strict ladder
before any free-form fallback:

1. **Keyword** — whole-word scan of the first N tokens (default 5) against
   github-enabled skill names + aliases (excluding `gh_request`/`help`, and
   `ask` for keyword only). Exactly one distinct skill hit **in an actionable
   position** (token 0, or preceded within the window by an imperative lead-in
   such as `do`/`can`/`please`) ⇒ promote (`confidence = 1.0`). Zero or ≥2
   distinct hits, or a lone hit in a non-actionable position (an incidental noun
   like `the review looks good`), ⇒ escalate. This precision gate keeps a bare
   keyword from auto-dispatching a skill on incidental common-English words.
2. **Model** — the `lightweight` classifier returns `{command, context,
   confidence}`. Promote only when `command` is github-enabled,
   `confidence ≥ min_confidence` (default 0.75), and the command's required
   URL type matches the subject (PR vs issue).
3. **Free-form** — residue keeps the `/gh_request` compatibility route.

**Invariants**

- Rigid word-0 matches never invoke the ladder.
- A promoted intent dispatches the real skill directly (same URL/context/
  reaction/ack as a rigid command) and never hops through `/gh_request`.
- Classification fails open to free-form — a mention is never dropped.
- One classifier implementation (`github_intent.resolve_github_intent`) is
  shared by the bridge and `/gh_request`; the URL-type guard lives only in
  `github_intent._url_type_ok` (never duplicated in the skill handler).
- Missing/invalid model `confidence` fails closed to `0.0` (→ free-form).

## Integration points

- Notifications wired into `loop_manager.process_github_notifications()`, which also
  drives `review_comment_dispatch.py` and `ci_dispatch.py`.
- The unregistered-repo @mention alert (`loop_manager._warn_unregistered_mention_repos`)
  is deduped per repo in the in-memory `_warned_unregistered_repos` set and suppressed
  entirely when `enable_multiple_instances` is set. Invariant: a warned repo is pruned
  from the set once it appears in `known_repos`, so registering then later removing a
  repo warns again rather than staying silently suppressed by a stale entry.
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
