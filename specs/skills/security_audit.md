# Skill Spec — `security_audit`

## Command(s)

- **Primary:** `/security_audit <project-name> [extra context] [limit=N]`
- **Aliases:** `security`, `secu`
- **Group:** `code`

## Purpose

Security-focused SDLC audit of a project codebase: find up to 5 critical vulnerabilities
and create a tracker issue for each. The actionable counterpart to read-only audits.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| project name | command arg | yes | alias-resolved before use |
| `limit=N` | command arg | no | cap on findings (default 5) |
| extra context | command arg | no | scope hints |

## Outputs / side effects

- Runs as a `worker: true` background thread (blocking Claude call).
- Creates one tracker issue per finding via `issue_tracker.create_issue()` (GitHub/Jira,
  provider-neutral).

## Error cases

| Condition | Behavior |
|---|---|
| unknown project / unresolved alias | reply, do not run |
| no tracker configured | findings stay local (see `private_security_audit` for local-only) |
| provider/quota failure | abort, notify |

## Integration hooks

- **Handler:** `handler.py`, `worker: true`. **GitHub/Jira:** `github_enabled` +
  `github_context_aware`.
- **Tracker:** `issue_tracker` service layer (never `gh issue create` directly).
- **Sibling:** `private_security_audit` keeps findings journal-only (never posted).

## Invariants

- Findings are written via the provider-neutral tracker service, never raw `gh`/Jira.
- Up to `limit` critical findings — quality over exhaustiveness.
- A discovered vulnerability is flagged prominently (journal + issue), even if also fixed.

## Known debt / watch-outs

- Alias must be resolved before building the project tag (regression-prone across audit
  handlers).
