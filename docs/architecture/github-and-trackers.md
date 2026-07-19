---
type: doc
title: "GitHub And Trackers"
description: "Covers GitHub/Jira notification flow, PR workflows (footer, receiving-code-review protocol), review issue-tracker enrichment, and the instance/ tracker files used to dedupe work."
tags: [architecture]
created: 2026-05-28
updated: 2026-07-19
---

# GitHub And Trackers

Koan integrates with GitHub for notifications, PR workflows, CI feedback, and
issue-style command routing. Jira can be used as an issue tracker while GitHub
remains the code review and PR surface.

## Notification Flow

GitHub and Jira notification modules fetch events, filter authorized users,
parse commands, deduplicate work, and enqueue missions. GitHub mention handling
can react to comments to mark that a command was accepted.

Context-aware skills can receive issue, PR, branch, project, and URL context
from the originating notification.

For Jira issue URLs used by `/plan`, `/fix`, and `/implement`, Koan requires a
resolved Koan project identity before continuing. Resolution order is:
1) explicit `--project-name`/mission project context, then 2) Jira key mapping
from `projects.yaml` (`projects.<name>.issue_tracker.provider: jira` with
`jira_project`). If neither resolves, the runner fails fast with an actionable
error instead of falling back to directory basename heuristics.

## PR Workflows

See `specs/components/git-github.md` for the design contract behind branch and
PR creation (draft-only, `gh`-only transport, fork-awareness invariants).

Koan-created work normally lands in branch-prefixed draft PRs. PR helpers cover
creation, review, rebasing, recreating, squashing, CI fixing, and PR quality
checks. Auto-merge is configurable and should remain guarded by project config,
security review, and sync state.

Controlled PR creation paths append a shared Kōan footer to PR bodies and
review comments. The footer includes best-effort provider/model attribution,
the submitted HEAD SHA, and elapsed runtime when that metadata is available.

When applying reviewer feedback (`/pr`, `/rebase`, `/recreate`), the prompts inject
a shared **receiving-code-review** protocol fragment
(`koan/system-prompts/_partials/receiving-code-review.md`, pulled in via
`{@include receiving-code-review}`). It directs the agent to evaluate each
substantive comment (READ→UNDERSTAND→VERIFY→EVALUATE→RESPOND→IMPLEMENT) instead of
blindly implementing it: verify the suggestion against the current codebase, apply a
YAGNI check, and push back with technical reasoning (surfaced in the summary) when a
request is incorrect — while complying when the human insists. Trivial/mechanical
feedback takes a fast-path. The review-learning extraction additionally records
pushback outcomes (validated vs. overridden) so the agent learns which pushbacks to
trust.

## Mission status indicators (`koan/mission`)

While a GitHub-linked mission runs, Kōan surfaces a live "Running" indicator on
GitHub with no GitHub App — a `koan:working` issue label plus a `koan/mission`
commit status — reusing the existing `gh` auth. See
`specs/components/git-github.md` (Mission status indicators) for the contract
and [GitHub commands](../messaging/github-commands.md#running-indicator-koanmission)
for the user-facing config.

The orchestration lives in `koan/app/mission_status.py` and hangs off the
mission lifecycle:

1. **Start** — `_start_mission_in_file` (`run.py`) confirms the Pending→In
   Progress transition, then `start_indicator` resolves the linked issue/repo
   (from an issue URL in the mission text, else the project's `github_url`),
   adds the `koan:working` label, and records a tracker entry.
2. **First push** — `on_branch_pushed` (called from `pr_submit.py` right after
   the branch is pushed) fills the head SHA into the tracker and posts the
   `pending` commit status. The commit status is complementary: koan pushes
   late, so the label is the primary live signal.
3. **Finalize** — `resolve_indicator` (called from `_finalize_mission`) posts
   the final `success`/`failure` status and removes the label on every terminal
   path. A **stagnation requeue** deliberately leaves the indicator up (the
   mission returns to Pending and is still "running").
4. **Crash recovery** — a hard crash skips finalize, stranding a yellow
   `pending`. `startup_manager.reconcile_running_indicators` (run right after
   crash recovery) resolves any tracked mission no longer Pending/In Progress
   as `error` and removes its label.

Cross-stage state lives in `instance/.running-indicator.json`, keyed by mission
title (mirroring `.stagnation-retries.json`). It carries `{repo, issue, sha,
branch, project}` across the start → push → finalize gap. Local-only missions
(no issue URL, no `github_url`) write nothing. Every entrypoint is best-effort:
a `gh` failure is logged and never blocks the mission.

## Review Issue-Tracker Enrichment

See `specs/components/issue-tracking.md` for the design contract behind the
provider-neutral tracker abstraction this enrichment sits on top of.

When `/review` builds a PR review prompt, it can enrich the prompt with the
referenced tracker issue so Claude reviews the change against its stated intent.
References are parsed out of the PR body:

- **Jira** — keys like `PROJ-123`. Fetched via the existing Jira credentials in
  `config.yaml` (`jira:` section). Only runs for projects whose `issue_tracker`
  in `projects.yaml` maps a `jira_project`.
- **GitHub** — cross-repo refs like `owner/repo#123`, fetched via the existing
  `gh` CLI auth. In-repo `#123` refs are intentionally ignored (ambiguous
  without the current repo).

The backend is selected by the project's `issue_tracker.provider`. Output is
best-effort (any failure is silently skipped), with per-ticket excerpts capped
at 500 chars and the whole injected block at 1000 chars. At most the first 5
references are fetched (each costs a network/subprocess round-trip), so a PR
body listing dozens of tickets cannot balloon review latency or burn API quota.
Disable globally with `review_issue_context.enabled: false` in `config.yaml`
(default enabled). The fetched block — third-party text from possibly unrelated
repos/tickets — is wrapped with `fence_external_data()` (injection scanning on)
before being injected into the standard `review` prompt as `{ISSUE_CONTEXT}`, so
the reviewer agent treats it as data, not instructions. Implemented in
`koan/app/issue_tracker/enrichment.py`.

## Trackers

Tracker files in `instance/` prevent duplicate work across daemon iterations.
Examples include:

- GitHub notification and reaction tracking.
- Review comment dispatch fingerprints (`.review-dispatch-tracker.json`).
  Fetch failures (timeout/OS/`gh` error) return `None` rather than `[]`, so
  the dispatch loop skips that PR without clearing its fingerprint — a
  transient GitHub timeout must not re-dispatch the same unresolved comments
  as a duplicate mission on the next successful poll.
- CI dispatch fingerprints keyed by PR, SHA, and job.
- Remote rename and default-branch tracking.
- Burn-rate and quota-related state.
- Running-indicator state (`.running-indicator.json`), keyed by mission title,
  carrying the linked issue/repo/SHA across a mission's lifecycle. Stale entries
  from a crashed run are reconciled at startup.

Use the existing tracker module for a behavior when one exists. If a new tracker
is needed, keep its state local to `instance/`, make keys stable, and document
the deduplication rule.

User setup lives in [GitHub commands](../messaging/github-commands.md) and
[Jira integration](../messaging/jira-integration.md).
