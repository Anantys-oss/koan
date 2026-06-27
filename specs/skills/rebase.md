# Skill Spec — `rebase`

## Command(s)

- **Primary:** `/rebase [--now] <pr-url> [context]`
- **Aliases:** `rb`
- **Group:** `pr`

## Purpose

Rebase a PR onto current base and address review concerns — the standing workflow for
keeping a Kōan PR current and merge-ready. `/fix` on a PR URL redirects here.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| PR URL | command arg | yes | parsed by `github_url_parser` |
| `--now` | flag | no | queue at top |
| trailing context | command arg | no | threaded into the queued mission |

## Outputs / side effects

- Queues a rebase mission (`model_key: mission`); runs via `rebase_pr.py`.
- Updates the PR branch (force-push with multi-account token resolution if needed).
- Commit messages shaped by `commit_conventions.py`.

## Error cases

| Condition | Behavior |
|---|---|
| invalid PR URL | reply with usage |
| force-push 403 (fork owned by other account) | recovery via `claude_step._force_push` using `gh auth token --user <owner>` |

## Integration hooks

- **Handler:** `handler.py` (also the redirect target of `fix`).
- **GitHub:** `github_enabled` + `github_context_aware`.
- **Combo:** second leg of `review_rebase` (`/rr`).

## Invariants

- Post-URL context must thread into the queued mission.
- Multi-account pushes resolve the remote owner's token; tokens redacted in logs.

## Known debt / watch-outs

- Order-sensitive combo `/rr` (review→rebase) must insert both sub-missions in one
  atomic locked write to preserve order and avoid TOCTOU.
