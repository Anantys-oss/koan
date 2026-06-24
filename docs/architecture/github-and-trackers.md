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

Koan-created work normally lands in branch-prefixed draft PRs. PR helpers cover
creation, review, rebasing, recreating, squashing, CI fixing, and PR quality
checks. Auto-merge is configurable and should remain guarded by project config,
security review, and sync state.

Controlled PR creation paths append a shared Kōan footer to PR bodies and
review comments. The footer includes best-effort provider/model attribution,
the submitted HEAD SHA, and elapsed runtime when that metadata is available.

## Review Issue-Tracker Enrichment

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
- Review comment dispatch fingerprints.
- CI dispatch fingerprints keyed by PR, SHA, and job.
- Remote rename and default-branch tracking.
- Burn-rate and quota-related state.

Use the existing tracker module for a behavior when one exists. If a new tracker
is needed, keep its state local to `instance/`, make keys stable, and document
the deduplication rule.

User setup lives in [GitHub commands](../messaging/github-commands.md) and
[Jira integration](../messaging/jira-integration.md).
