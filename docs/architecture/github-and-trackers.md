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

Generic missions push their branch from the provider session, then the Python
post-mission pipeline creates the draft PR. That path reuses the same submission
helper as skill-dispatched work, so empty-diff guards, fork/upstream targeting,
existing-PR detection, tracker cross-links, and PR metadata footers stay
consistent across mission types.

Controlled PR creation paths append a shared Kōan footer to PR bodies and
review comments. The footer includes best-effort provider/model attribution,
the submitted HEAD SHA, and elapsed runtime when that metadata is available.

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
