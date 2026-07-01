# Skill Spec — `plan`

## Command(s)

- **Primary:** `/plan [--iterations N] <idea>` · `/plan <project> <idea>` · `/plan <issue-url>`
- **Group:** `code`

## Purpose

Deep-think an idea and produce a structured plan as a tracker issue — or iterate on an
existing issue. Plans become the contract `implement`/`fix` work against.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| idea text | command arg | yes (or issue URL) | free-form |
| project name | command arg | no | scopes the plan |
| issue URL | command arg | alt | iterate on an existing plan |
| `--iterations N` | flag | no | 1–5, default 1; critic→regenerate loop, only final posted |

## Outputs / side effects

- Creates (or updates) a tracker issue via `issue_tracker.create_issue()` /
  `find_existing_plan_issue()`.
- Multi-iteration runs cost ~5× a single plan at `--iterations 3` (token-linear).

## Error cases

| Condition | Behavior |
|---|---|
| no idea/URL | reply with usage |
| unknown project | alias resolution then skip if unknown |
| `--iterations` out of 1–5 | clamp/validate |

## Integration hooks

- **Handler:** `handler.py`. **GitHub/Jira:** `github_enabled` + `github_context_aware`.
- **Combo:** paired with `implement` in `plan_implement` (`/planit`, `/doit`).

## Invariants

- Only the final iteration is posted — intermediate critic passes are internal.
- `find_existing_plan_issue()` is consulted before creating a duplicate plan issue.

## Known debt / watch-outs

- Iteration cost scales linearly; surface the cost expectation to users.
