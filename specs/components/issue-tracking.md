# Component Spec — Issue Tracking

**Package:** `koan/app/issue_tracker/` (`base.py`, `config.py`, `github.py`, `jira.py`,
`types.py`, `enrichment.py`, `__init__.py`) + `issue_cli.py`, `notification_config.py`

## Purpose

A provider-neutral abstraction over issue trackers so the rest of Kōan never branches on
"GitHub vs Jira". Skills and prompts call one service layer; routing to the right backend
is config-driven per project.

## Architecture

```
issue_tracker/__init__.py  → service layer: fetch_issue(), add_comment(),
       │                      create_issue(), find_existing_plan_issue()
       ├─ base.py    → IssueTracker ABC (fetch/comment/create contract)
       ├─ config.py  → get_tracker_for_project(), Jira-key→project map, repo resolution
       ├─ github.py  → GitHubIssueTracker (gh CLI backend)
       ├─ jira.py    → JiraIssueTracker (REST API backend)
       ├─ types.py   → IssueRef, IssueContent
       └─ enrichment.py → PR-review {ISSUE_CONTEXT} block from tracker refs
issue_cli.py          → CLI entry point (fetch/comment/create) used by prompts/subprocesses
```

## Key types & functions

| Symbol | Contract |
|---|---|
| `IssueTracker` (ABC) | The provider-neutral contract. New backends subclass this. |
| `__init__.fetch_issue/add_comment/create_issue` | **Callers use these, not the backends.** No `gh issue create` / raw Jira calls in skill code. |
| `config.get_tracker_for_project()` | Routes a project to its configured tracker (`tracker:` section in `projects.yaml`). |
| `enrichment.py` | Parses `PROJ-123` (Jira) / `owner/repo#123` (GitHub) refs out of a PR body, fetches a capped summary, returns `{ISSUE_CONTEXT}`. Best-effort: every path returns `""` on failure. Gated by `review_issue_context.enabled`. |
| `issue_cli.py` | The subprocess/prompt-facing CLI. Agents create tracker issues via `python3 -m app.issue_cli create ...`, never `gh issue create` directly. |

## Invariants

- **Provider neutrality is the whole point.** Code outside `issue_tracker/` must not know
  whether a project uses GitHub or Jira. Branching on provider type is a design smell.
- **Tracker writes go through the service layer / `issue_cli`**, so routing, fork
  awareness, and Jira-key mapping are applied uniformly.
- **Enrichment is non-fatal.** Issue-context fetching is best-effort and must degrade to
  `""` — it must never block or fail a review.

## Integration points

- `__init__.create_issue` backs `audit`, `security_audit`, `plan`, `brainstorm`, `fix`.
- `enrichment.py` wired into `review_runner.build_review_prompt()`.
- Polling cadence resolved via `notification_config.py` (shared GitHub/Jira interval).
- Project routing from `projects_config` (`tracker:` override).

## Known debt / watch-outs

- Jira and GitHub have different identity/permission models; `config.py` carries the
  mapping glue (Jira key → project, code-repo resolution) — keep it the single source.
- `enrichment.py` caps context size; raising the cap risks prompt bloat in reviews.

## Change protocol

A new tracker backend subclasses `IssueTracker`, registers in `config.py` routing, and
updates this spec + a `docs/messaging/` page. Service-layer signature changes ripple to
every skill that creates/fetches issues — review all callers.
