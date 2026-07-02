# Skill Spec — `review`

## Command(s)

- **Primary:** `/review [--now] <pr-or-issue-url> [more urls] [context] [flags]`
  or `/review <repo-url> [--limit=N]`
- **Aliases:** `rv`
- **Group:** `code`

## Purpose

Queue a code-review mission for one or more GitHub PRs/issues. The agent reviews the
diff and posts findings as a review comment. The default review can be sharpened with
focus passes (architecture, silent-failure hunting, comment quality, plan alignment).

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| PR/issue URL(s) | command arg | yes (or repo URL) | multiple allowed; parsed by `github_url_parser` |
| repo URL + `--limit=N` | command arg | alt form | batch-review N open PRs |
| `--now` | flag | no | queue at top |
| `--architecture` | flag | no | SOLID/layering focus |
| `--errors` | flag | no | silent-failure-hunter pass |
| `--comments` | flag | no | comment-quality pass |
| `--plan-url <issue-url>` | flag | no | check PR against its plan |
| `--force` | flag | no | review even if closed/merged |
| trailing context | command arg | no | extra reviewer guidance |

## Outputs / side effects

- Queues a review mission (one per URL); the agent loop runs it.
- Posts a review comment to the PR with a branded footer (`pr_footer.py`).
- Review prompt is enriched with `{ISSUE_CONTEXT}` from `issue_tracker/enrichment.py`.

## Error cases

| Condition | Behavior |
|---|---|
| invalid/missing URL | reply with usage |
| closed/merged target | skipped unless `--force` |
| unresolved project | alias resolution then skip if unknown |

## Integration hooks

- **Handler:** `handler.py`. **GitHub:** `github_enabled` + `github_context_aware`.
- **Combo member:** part of `review_rebase` (`/rr`) and `ultrareview`.
- **Async:** runs as a queued agent-loop mission.

## Invariants

- Multi-URL queues preserve order via a single atomic locked insert.
- Findings are advisory comments — `/review` never merges or pushes code.

## Known debt / watch-outs

- Focus flags compose; stacking many passes multiplies token cost.
- Plan-alignment (`--plan-url`) depends on the tracker being reachable.
