# Skill Spec — `implement`

## Command(s)

- **Primary:** `/implement <issue-url> [additional context]`
- **Aliases:** `impl`
- **Group:** `code`

## Purpose

Queue an implementation mission for a tracker issue (GitHub or Jira): the agent reads the
issue, plans, implements, tests, and opens a draft PR. The end-to-end "build the thing"
skill.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| issue URL | command arg | yes | GitHub or Jira; routed by `issue_tracker` |
| trailing context | command arg | no | extra guidance folded into the mission |

## Outputs / side effects

- Queues an implementation mission (`model_key: mission` → heavier model tier).
- Agent creates a `<prefix>/implement-<issue_number>` branch and a draft PR.
- PR may flow through `security_review.py` and auto-merge per project config.

## Error cases

| Condition | Behavior |
|---|---|
| invalid issue URL | reply with usage |
| issue assigned to someone else (autonomous pick) | skip per GitHub-issue-selection rules |
| open PR already addresses it | skip to avoid duplicate work |

## Integration hooks

- **Handler:** `handler.py`. **GitHub/Jira:** `github_enabled` + `github_context_aware`.
- **Tracker:** fetches via `issue_tracker.fetch_issue()`.
- **Async:** queued agent-loop mission.

## Invariants

- Always a draft PR on a `<prefix>/*` branch; never commits to main, never merges.
- `_work_landed()` must detect the landed branch even when the agent checks out main
  after pushing (fallback checks the expected `{prefix}implement-{issue}` branch).

## Known debt / watch-outs

- HEAD-only landing checks miss work pushed to a feature branch when HEAD is back on
  base — keep the branch fallback.
