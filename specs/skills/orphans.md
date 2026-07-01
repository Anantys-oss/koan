# Skill Spec — `orphans`

## Command(s)

- **Primary:** `/orphans <project_name>`
- **Aliases:** `orphan`
- **Group:** `pr`

## Purpose

Recover orphan branches (unmerged, no open PR) for a project in one step — rebase
each onto the default branch and open a draft PR. Exists so detected orphans surface
during git sync can be turned into reviewable PRs without manual git work.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| project | command arg | yes | resolved via `resolve_project_from_list`; single-project auto-selects |

## Outputs / side effects

- Per orphan branch: checkout → rebase onto `origin/<default>` → force-push (if rebased) → draft PR via `github.pr_create`.
- Restores the original branch after recovery.
- Recovery hint (`/orphans <project>`) appended to git-sync reports and outbox notifications whenever orphans are detected.

## PR title & description contract

Derived **programmatically from the branch's own commits** (`git_utils.get_commit_messages`,
oldest-first) — no LLM call:

| Commits on branch | Title | Description body |
|---|---|---|
| 0 (read failed / none) | `fix: recover orphan <short-branch>` | generic recovery note |
| 1 | first commit's subject line | that commit's full message |
| ≥2 | first commit's subject line | first three commit messages |

Every body ends with a `---` footer recording the branch name and rebase status
(`Rebased onto` / `Could not rebase onto`). The title is capped at
`PR_TITLE_MAX_LEN` (200) chars and ellipsized so a verbose first-commit subject
can never exceed GitHub's 256-char title limit and fail `gh pr create`; the full
message is always preserved in the body.

## Error cases

| Condition | Behavior |
|---|---|
| no project arg + multi-project | reply listing available projects |
| orphan detection raises | reply `❌ Failed to check orphan branches` |
| rebase conflict | `rebase --abort`; still push as-is and create PR (footer notes "Could not rebase") |
| rebase conflict + abort fails | stop, record broken-state error, no PR |
| checkout / push / `pr_create` failure | record error, continue to next orphan |

## Invariants

- Recovery of one orphan must not abort recovery of the others — errors are per-branch.
- Title/body must never depend on an LLM; commit messages are the source of truth.
- Title length is capped (`PR_TITLE_MAX_LEN`, 200) so recovery never fails on an over-long subject.
- `git fetch --prune` runs before detection so stale refs don't mask orphans.

## Integration hooks

- **Handler:** `skills/core/orphans/handler.py`.
- **Audience:** `bridge` (blocking: git fetch/rebase/push + `gh` PR creation).

## Known debt / watch-outs

- Only the first three commits surface in multi-commit bodies; older commits are omitted.
