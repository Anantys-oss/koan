# Skill Spec — `ci_check`

## Command(s)

- **Primary:** `/ci_check <pr-url>` · `/ci_check --enable` · `/ci_check --disable`
- **Group:** `code`

## Purpose

Check a PR's CI status and fix failures. Also toggles the automatic CI-fix dispatch
(`ci_dispatch.py`) that reacts to CI failures on Kōan-authored PRs.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| PR URL | command arg | yes (for a check) | parsed by `github_url_parser` |
| `--enable` / `--disable` | flag | alt | toggle auto CI-fix dispatch |

## Outputs / side effects

- Fetches check-run status via the GitHub API (`run_gh`/`api`).
- On failure, runs the shared CI-fix loop (`claude_step.run_ci_fix_loop()`), pushes fixes.
- `--enable/--disable` flips the `ci_dispatch` config switch.

## Error cases

| Condition | Behavior |
|---|---|
| invalid PR URL | reply with usage |
| API error fetching checks | `fetch_failing_check_runs()` returns `None` — treat as "unknown", not "green" |
| CI green | report success, no fix loop |

## Integration hooks

- **Handler:** `handler.py`.
- **Auto-dispatch:** shares state with `ci_dispatch.py` (`.ci-dispatch-tracker.json`,
  dedup by PR+SHA+job, cooldown).
- **Fix loop:** `claude_step.run_ci_fix_loop()` with `use_polling`.

## Invariants

- `None` (API error) and `[]` (all green) are distinct — never collapse them.
- Cooldown timer resets only on successful API calls.

## Known debt / watch-outs

- Polling vs single-shot recheck is caller-configured; the auto-dispatch path and the
  manual command share the loop but differ in `use_polling`.
