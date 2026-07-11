---
type: skill-spec
title: "Skill Spec — ci_check"
description: "Specifies the `/ci_check` skill, which checks a PR's CI status, runs the shared CI-fix loop on failures, and toggles automatic CI-fix dispatch."
tags: [skill]
created: 2026-06-27
updated: 2026-07-10
---

# Skill Spec — `ci_check`

## Command(s)

- **Primary:** `/ci_check <pr-url>` · `/ci_check --enable` · `/ci_check --disable`
- **Group:** `code`

## Purpose

Check a PR's CI status and fix failures. Also toggles the automatic CI-fix dispatch
(`ci_dispatch.py`) that reacts to CI failures on Kōan-authored PRs.

See `docs/users/skills.md` for the end-user `/ci_check` reference and
`docs/users/user-manual.md` for the fuller walkthrough.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| PR URL | command arg | yes (for a check) | parsed by `github_url_parser` |
| `--enable` / `--disable` | flag | alt | toggle auto CI-fix dispatch |

## Outputs / side effects

- Fetches check-run status via the GitHub API (`run_gh`/`api`).
- On failure, runs the shared CI-fix loop (`claude_step.run_ci_fix_loop()`), pushes fixes.
- `--enable/--disable` flips the `ci_dispatch` config switch.

## Queue-safety model (async check, non-blocking fix)

The system has two halves that must stay decoupled:

1. **Async monitor** — `ci_queue_runner.drain_one()`, called each iteration (throttled to
   `CI_QUEUE_SLEEP_INTERVAL`) from the interruptible sleep loop. One non-blocking status check
   per `## CI` PR. On failure it injects a `/ci_check <url>` fix mission **non-urgently**
   (FIFO), so CI fixing never jumps ahead of `/rebase` / `/review` / `/implement` backlog work.
2. **Fix mission** — the injected `/ci_check <url>` runs in the single mission slot. It performs
   **at most `get_ci_check_max_fix_attempts()` internal Claude fix steps (default 1)** and then
   returns, yielding the queue. The `## CI` item's `max_attempts` (config `ci_fix_max_attempts`,
   default 5) is the counter `drain_one` increments per re-injection and gives up at — it is NOT
   the per-mission internal loop count. (The counter resets to 0 when a fix lands a fresh *pending*
   CI run — `add_ci_item` treats that as a new run to evaluate — so a genuinely-progressing PR
   keeps going while a repeatedly fast-failing one is bounded.) Each fix step runs under
   `get_ci_check_step_timeout()` (config `ci_check.timeout`, default 3600s) plus a
   `get_ci_check_idle_timeout()` idle guard (config `ci_check.idle_timeout`, defaulting to
   `first_output_timeout`), not the 2-hour `skill_timeout`.

This preserves the original intent (fix on failure, give up after N attempts with the 🚦
notification) while guaranteeing one failing PR cannot monopolize the queue for hours.

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
- The auto-injected fix mission MUST be inserted non-urgent; per-mission fix attempts MUST be
  bounded by `get_ci_check_max_fix_attempts()` (decoupled from the `## CI` total budget); and the
  fix step MUST run under `ci_check.timeout` + idle guard, never the raw `skill_timeout`.

## Known debt / watch-outs

- Polling vs single-shot recheck is caller-configured; the auto-dispatch path and the
  manual command share the loop but differ in `use_polling`.
- Manual `/ci_check <url>` (no `## CI` entry) does a single bounded attempt; if the fix is pushed
  and CI goes pending it re-enqueues for monitoring, otherwise it reports "still failing" as a
  one-shot. Auto retries are driven by the `## CI` budget, not the manual path.
- `ci_check` config accepts both a bare bool (`ci_check: true`) and the dict form
  (`ci_check: {enabled, timeout, max_fix_attempts_per_mission, idle_timeout}`); both are honored
  and validated (the strict startup validator also accepts the bool form).
