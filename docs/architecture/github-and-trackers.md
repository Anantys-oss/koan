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

## Forge Abstraction

Git-hosting platforms are abstracted behind `ForgeProvider` (`koan/app/forge/`),
mirroring the CLI-provider pattern. Each platform subclasses `ForgeProvider` and
implements the operations it supports; unsupported operations raise
`NotImplementedError` and callers check `supports()` (or branch on
`forge.name`) before using optional features.

- `forge/base.py` — abstract base + `FEATURE_*` flags.
- `forge/github.py` — `GitHubForge`, a thin delegation wrapper over
  `app.github` (the canonical `gh`-CLI implementation). Zero behavior change.
- `forge/gogs.py` — `GogsForge`, talking to the Gogs REST API v1 directly via
  `urllib` (host/token from `KOAN_GOGS_HOST` / `KOAN_GOGS_TOKEN`). Gogs supports
  PR and issue operations; it has no draft PRs, CI status, reactions, or rich
  PR-review-comment API.
- `forge/registry.py` — maps the `forge:` type string to a provider class.
- `forge/__init__.py` — `get_forge(project_name)` resolves the provider from the
  project's `forge:` field (or `forge_url` / `github_url` domain), defaulting to
  GitHub. `get_forge_for_path(project_path)` is the convenience form for callers
  that only have a checkout path (project name = directory basename).

### Resolution

`get_forge()` reads `projects.<name>.forge` from `projects.yaml` and falls back
to GitHub for any unconfigured or unknown project, so existing GitHub setups are
unaffected. A self-hosted Gogs project is declared with `forge: gogs` (and may
set `forge_url`); the bot reads `KOAN_GOGS_HOST` and `KOAN_GOGS_TOKEN` from the
environment for API access.

### Caller wiring

Loop-critical PR paths route through the forge so non-GitHub projects function
end-to-end instead of failing on `gh` and accumulating un-PR'd branches until
they hit branch-saturation:

- PR creation, existing-PR detection, and fork/target resolution
  (`pr_submit.py`).
- Merged-branch detection (`git_sync.get_github_merged_branches`) and open-PR
  detection (`branch_limiter`) — both feed the saturation accounting, so a
  non-GitHub forge can recognise merged/open work and free up branch budget.
- Post-mission PR verification (`mission_verifier.check_pr_created`).

The GitHub code path in each of these is kept byte-for-byte identical (selected
when `forge.name == "github"`); the forge branch is taken only for other forges.

GitHub-only enrichments with no forge-neutral equivalent — merge-velocity
analytics (`pr_feedback`), deep-research issue/PR fetching (`deep_research`), and
PR-body/comment mutation (`pr_quality`) — degrade quietly (skip) on non-GitHub
forges rather than erroring every iteration.

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
