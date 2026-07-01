# Skill Spec — `fix`

## Command(s)

- **Primary:** `/fix [--now] <issue-url> [context]` · `/fix <repo-url> [--limit=N]`
- **Group:** `code`

## Purpose

Fix a tracker issue end-to-end (understand → plan → test → implement → draft PR), or
batch-queue fix missions for all open issues in a repo.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| issue URL | command arg | yes (or repo URL) | GitHub or Jira |
| repo URL + `--limit=N` | command arg | alt form | batch all open issues |
| `--now` | flag | no | queue at top |
| trailing context | command arg | no | extra guidance |

## Outputs / side effects

- Queues fix mission(s) (`model_key: mission`); agent opens a draft PR per issue.
- On a PR URL, `/fix` **redirects to `/rebase`** (same intent: address PR concerns),
  preserving `--now` + trailing context.

## Error cases

| Condition | Behavior |
|---|---|
| invalid URL | reply with usage |
| PR URL given | delegated to `rebase/handler.py` with `ctx` untouched |
| batch with no open issues | nothing queued, informative reply |

## Integration hooks

- **Handler:** `handler.py` (delegates to `rebase/handler.py` for PR URLs).
- **GitHub/Jira:** `github_enabled` + `github_context_aware`.

## Invariants

- PR-URL redirect must keep `ctx` intact so `--now` and post-URL context survive.
- Always draft PR on `<prefix>/*`.

## Known debt / watch-outs

- The issue-vs-PR branch is URL-shape-driven; `github_url_parser` is the single
  classifier — don't reimplement URL detection in the handler.
