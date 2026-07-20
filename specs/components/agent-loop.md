---
type: component-spec
title: "Component Spec — Agent Loop Pipeline"
description: "Design contract for the core mission pipeline (iteration manager, mission executor/runner, quota handling, stagnation monitor) that pulls missions, invokes the CLI provider, and finalizes lifecycle state."
tags: [agent-loop]
created: 2026-06-27
updated: 2026-07-17
---

# Component Spec — Agent Loop Pipeline

**Modules:** `run.py`, `iteration_manager.py`, `mission_executor.py`,
`mission_runner.py`, `loop_manager.py`, `contemplative_runner.py`, `quota_handler.py`,
`prompt_builder.py`, `event_scheduler.py`, `stagnation_monitor.py`, `hooks.py`,
`devcontainer.py`

## Purpose

The beating heart: a pure-Python loop that pulls a mission, builds a prompt, invokes
the CLI provider as a subprocess, monitors it, and finalizes the mission's lifecycle
state. Everything else exists to feed or observe this loop.

See `docs/architecture/daemon.md`'s Agent Loop section for how this pipeline is wired
into the running daemon (startup, quota pause, parallel sessions).

## Execution flow (one iteration)

```
iteration_manager._decide()        # usage refresh, mode (REVIEW/IMPLEMENT/DEEP/WAIT),
                                    # recurring injection, mission pick, project resolve
        │
mission_executor._run_iteration()  # orchestration: pick → dispatch → execute → finalize
        │
        ├─ skill mission?  → _handle_skill_dispatch()  → skill_dispatch runners
        │                                                (bypass the Claude agent)
        └─ normal mission? → run.run_claude_task()      # CLI subprocess + monitoring
        │
run._finalize_mission()            # lifecycle state machine: Done / Failed / requeue
        │
mission_runner (post-processing)   # usage tracking, pending.md archival, reflection,
                                    # auto-merge
```

## Key types & functions

| Symbol | Contract |
|---|---|
| `run.run_claude_task()` | CLI subprocess invocation + monitoring host. Wires in the stagnation monitor and timeout watchdog. |
| `run._finalize_mission()` | The lifecycle authority — decides Done vs Failed vs requeue. All exits from In Progress funnel here. |
| `run._classify_and_handle_cli_error()` | Maps CLI error text → action. `trust_stdout` flag distinguishes raw CLI output from skill transcripts (skill stdout is DATA, not error signal). |
| `run._probe_exit0_quota()` | False-success detection: exit 0 but the run actually hit quota. |
| `mission_executor._run_iteration()` | Full per-iteration orchestration. |
| `mission_executor._maybe_retry_mission()` | Single transient-error retry. **Any new mission-terminating pathway must add a guard here** (see stagnation retry gap). |
| `mission_runner.build_mission_command()` | CLI prompt + flags assembly. |
| `mission_runner.parse_claude_output()` | JSON → text extraction from `--output-format json` / stream-json. |
| `run._is_ci_check_mission()` | Classifies a mission title as CI-related (`/ci_check …`, ci_dispatch `Fix CI failure: …`). |
| `run._mission_fail_icon()` | **The single source of truth for the emoji prefix on a mission-failure notification** — 🚦 for CI missions, ❌ otherwise. Every failure-notification site MUST call this, never hardcode ❌. |
| `iteration_manager._downgrade_if_burning_fast()` | Burn-rate-driven mode downgrade, next to affordability downgrade. |
| `iteration_manager._maybe_decompose_mission()` | Decomposition gate: classifies an eligible mission and, on a composite verdict, injects `[group:ID]` sub-missions + retags the parent `[decomposed:ID]`. Runs after the passive/CLI-unavailable gates. |
| `iteration_manager._sweep_decomposed_parents()` | Group-completion sweep: a distinct Pending→terminal parent transition (no CLI run, no `_maybe_retry_mission` guard). Alerts the operator after `_SWEEP_FAILURE_NOTIFY_THRESHOLD` stuck sweeps. |
| `stagnation_monitor` | Daemon thread hashing last-N stdout lines; kills the subprocess group after K identical hashes; requeues up to `max_retry_on_stagnation`. |
| `quota_handler` | Parses quota exhaustion from CLI output, writes pause state + journal entry. `extract_reset_info` is **bounded** — it stops at JSON/structural delimiters so a single-line CLI result object can't leak its JSON tail into `reset_display`. `quota_debug_snippet` returns a capped, reset-centered window of the raw output for chat debug blocks. |
| `hooks.py` | Lifecycle events: `session_start`, `session_end`, `pre_mission`, `post_mission`, `post_review`, each error-isolated. |
| `prompt_builder._get_koan_md_section()` | Delegates reading to `project_koan.read_general_koan_md()` (root `KOAN.md` + `.koan/KOAN.md`, combined cap `_MAX_KOAN_MD_CHARS` 16k), frames via the `koan-md` template. Returns `""` for absent/blank/unreadable. |

### KOAN.md injection

`prompt_builder._get_koan_md_section(project_path)` delegates file reading to
`project_koan.read_general_koan_md(project_path)`, which reads **both**
`<project>/KOAN.md` and `<project>/.koan/KOAN.md` (root first, `.koan/KOAN.md`
behind a `# .koan/KOAN.md` marker), strips and concatenates them, and caps the
*combined* length at `_MAX_KOAN_MD_CHARS`. When the result is non-empty it is
appended (framed via the `koan-md` system-prompt template) as a **Tier-1 stable
system-prompt section** — placed right after the submit-PR section so the
prompt-cache prefix stays intact. Root `KOAN.md` stays fully backward-compatible:
a project with no `.koan/` sees byte-identical output. `build_agent_prompt_parts()` /
`build_agent_prompt()` take an optional `host_project_path`; the reader uses it
in preference to `project_path` so the on-disk files are read from the host even
when `project_path` is the devcontainer workspace. Invariant: both sources
absent/blank leaves the system prompt unchanged. KOAN.md is koan-only — Claude
Code auto-loads `CLAUDE.md` but never `KOAN.md`, so interactive sessions never
see it.

## Invariants

- **One-shot headless invocation, in-turn completion.** Missions run via
  `claude -p --output-format json` — a single non-interactive turn with no
  post-turn event loop. Deferred re-invocation (background monitors, scheduled
  wake-ups, "report later") is NOT available; such work is dropped and the child
  is killed. Result-bearing work MUST complete before the model ends its turn —
  enforced at the prompt layer (`_partials/cli-execution-model.md`) and supported
  by a raised Bash foreground timeout (`get_bash_foreground_timeout_ms()`,
  injected into the mission subprocess env as `BASH_DEFAULT_TIMEOUT_MS` /
  `BASH_MAX_TIMEOUT_MS` for the Claude provider only, clamped below
  `mission_timeout`). `max_turns` is
  orthogonal: default missions impose no `--max-turns` cap (`build_mission_command`
  passes `0` unless `complexity_routing` assigns a tier), and a cap-hit is
  classified as failure (`subtype: "error_max_turns"`), not a clean success.
- **Mission scratch is bounded across missions (#2354 follow-up).** A long-lived
  container's memory graph must not ratchet up from test-suite tmp leftovers. Each
  mission subprocess runs with a per-mission `TMPDIR` (reaped in the outer `finally`)
  **and** `PYTEST_ADDOPTS=--basetemp=$TMPDIR/pytest` (appended, never clobbering an
  existing value via `pytest_addopts_with_basetemp`) so pytest tmp trees land inside
  the reaped dir. As a safety net for tools that ignore `$TMPDIR`, the post-mission
  step sweeps stray `/tmp` trees (`sweep_stray_tmp_dirs`, globs from
  `cleanup.extra_tmp_globs`). The sweep MUST only remove paths directly under `/tmp`
  matching a glob, MUST NOT follow symlinks, MUST NOT remove the live `koan_tmp_dir()`
  scratch/lock dir (even though it matches `/tmp/koan-*`), and MUST skip paths owned
  by another uid. Because same-uid `/tmp/test-koan*` (KOAN_ROOT) trees written by a
  concurrent parallel session (`session_manager.spawn_session`) are covered by none of
  those guards, the sweep MUST additionally be **age-gated**
  (`cleanup.min_tmp_age_seconds`, default 600s): a tree is skipped if the newest mtime
  anywhere in it (the whole subtree, not just the top-level dir) is within the window,
  so a session mid-`make test` is never `rmtree`d out from under itself. Triage rule:
  `anon` / per-process RSS is the leak signal, not cgroup
  `memory.current` (which counts reclaimable page cache + slab); the cgroup breakdown
  is surfaced via `get_memory_status`/`health_check` when `/sys/fs/cgroup/memory.stat`
  is readable. See `docs/operations/memory-footprint.md`.
- **Kernel page cache is reclaimed universally, not just RSS (#2374).** Bounding
  `anon` (per-mission `TMPDIR` reap + stray-tmp sweep, above) does not return the
  reclaimable page cache (`file`) that mission file I/O leaves warm, and Railway's
  `/sys/fs/cgroup/memory.reclaim` is read-only. The agent loop therefore runs a
  single reclaim primitive (`app/page_cache.reclaim_page_cache`, over
  `default_reclaim_roots()` = project workdirs + `instance/` + venv + scratch dir +
  the stray per-mission `/tmp` trees matched by `cleanup.extra_tmp_globs`, own-uid
  only — those hold big *out-of-root* page-cache residuals that the standard roots
  never cover, observed live 2026-07-19 as ~570 MB of `file` pinned for ~4.5h until
  the age-gated sweep deleted the files; reclaiming their clean pages decouples the
  billed baseline from that deletion latency)
  at exactly two non-bypassable choke points: the `run_claude_task` outer `finally`
  (post-mission, after the CLI subprocess has exited) and **inside
  `loop_manager.interruptible_sleep()` itself** — the single sleep primitive every
  idle path shares (between-runs sleep, contemplative sleep, and the whole
  `_IDLE_WAIT_CONFIG` family: `focus_wait`, `passive_wait`, `schedule_wait`,
  `exploration_wait`, `pr_limit_wait`, `branch_saturated_wait`) — throttled to
  `page_cache_reclaim.idle_interval_s` (default 180s) by `maybe_reclaim_page_cache_idle()`'s
  module-level timestamp, which makes the call idempotent per tick. Wiring the
  idle hook at individual call-sites instead of inside the primitive is a
  contract violation: it is exactly how `focus_wait` shipped with no reclaim at
  all (observed live 2026-07-14: 850 MB flat billed `memory.current` on an idle
  focus-mode instance). The sweep is `posix_fadvise(DONTNEED)` on
  regular files only — strictly read-only, symlink- and non-regular-file-skipped,
  time-budgeted (`time_budget_s`) so it never stalls the loop, and a no-op where
  `os.posix_fadvise` is absent (macOS). It reuses `read_cgroup_memory_stat()` for
  the before/after `file` delta; no new cgroup parser. No per-feature opt-in exists
  by construction — a new provider or mission type inherits both hooks. Default on
  (`page_cache_reclaim.enabled: true`); `idle_interval_s: 0` keeps only the
  post-mission hook. See `docs/operations/memory-footprint.md`.
- **`run.py` never commits to main and never merges.** This is a hard safety boundary
  enforced by prompt + convention; the loop's job is to host the subprocess, not to
  alter git state itself.
- **A missing CLI binary at startup enters an in-memory degraded (no-mission) mode.**
  `startup_manager.check_cli_binary()` probes the primary provider once via
  `cli_health.check_primary_cli()` (which wraps `CLIProvider.is_available()` /
  `shutil.which(binary())`, honoring absolute / bare-PATH / `KOAN_ROOT`-relative paths and
  the `KOAN_CLAUDE_CLI_PATH` / `cli.<role>` overrides). On a miss it logs, sends ONE ⚠️
  operator warning (all messaging backends, via `send_telegram`), and sets the in-memory
  `cli_health` flag — **never a hard stop** (chat/inbox must keep working) and **no on-disk
  signal**: the flag lives for the process lifetime and clears only on restart (PATH must
  be fixed properly). `iteration_manager.plan_iteration` gates on `cli_health.is_unavailable()`
  **before** mission/autonomous/contemplative selection (next to the passive gate),
  returning the `cli_unavailable_wait` idle action so **no** execution starts, missions stay
  Pending, and GitHub/Jira notification polling (which runs before planning) still queues
  work. The loop reminder is throttled (`cli_health.should_warn`/`mark_warned`, ~6h) so the
  operator is never flooded. Defense-in-depth for a mid-session vanish: `run.run_claude_task`
  converts a provider-binary `FileNotFoundError` into an actionable exit-127 failure (shared
  `provider.missing_binary_message`, also used by `run_command_streaming`), routing through
  the normal failure/fallback path rather than crashing the loop. `cli_unavailable_wait` sets
  `wake_on_mission=False` (like `passive_wait`) so a queued mission cannot tight-loop the gate.
- **Skill-dispatch stdout is DATA, not CLI error output.** `_classify_and_handle_cli_error`
  is called with `trust_stdout=False` for skill dispatches so a transcript is not
  mistaken for a quota/auth message. Keep that default for new dispatch pathways.
- **Every termination pathway needs a retry guard.** Stagnation kill, timeout kill,
  and CLI error all route through `_maybe_retry_mission`'s RETRYABLE check.
- **Mission decomposition adds a distinct Pending→terminal transition that bypasses
  the CLI/`_finalize_mission` path.** A natural-language mission tagged `[decompose]`
  (or any eligible mission when `decompose.auto` is on) is classified by a lightweight
  model at the **decomposition gate** (`_maybe_decompose_mission`), which runs *after*
  the passive and CLI-unavailable gates because it invokes the classifier CLI and
  mutates the store. A composite verdict injects `[group:ID]` sub-missions into Pending
  and retags the parent `[decomposed:ID]`; the picker skips `[decomposed:]` parents. A
  **group-completion sweep** (`_sweep_decomposed_parents`, also after the read-only
  gates) transitions a parent out of Pending — completed if any sub-task succeeded,
  failed if all did — once no `[group:ID]` sub-mission remains in Pending/In Progress.
  This parent transition is a store mutation, **not** a CLI run: the parent never enters
  In Progress and never spawns a subprocess, so it needs **no** `_maybe_retry_mission`
  guard (the retry-guard invariant covers subprocess-terminating pathways only). The
  sweep short-circuits before taking the store lock when no `[decomposed:]` parent sits
  in Pending, logs (not swallows) an unreadable-store probe, and counts per-parent
  transition no-ops so a parent stuck in Pending trips an operator alert after
  `_SWEEP_FAILURE_NOTIFY_THRESHOLD` consecutive failures.
- **Mission-failure notifications route their emoji through `_mission_fail_icon()`.**
  CI-related missions (`/ci_check`, ci_dispatch `Fix CI failure:`) surface 🚦 — a
  status signal, not an alarm, per operator preference; all other failures surface ❌.
  This was scattered across call sites and kept regressing (start-transition and
  devcontainer-setup failures still hardcoded ❌). New failure-notify sites must call
  `_mission_fail_icon(mission_title)`, never inline the emoji.
- **Contemplative failures must surface, not swallow.** `_handle_contemplative`
  captures the CLI exit code and runs `_notify_contemplative_failure`, which classifies
  the outcome (529 overload, quota, auth, transient, exit-code) and sends ONE throttled
  message per outage episode (`.contemplative-failure-notify.json`, 6h cooldown). Without
  it a failed contemplative session is invisible and the agent emits generic
  "Run failed / went sideways" text. The contemplative path does NOT retry — it sleeps
  and the next iteration retries naturally.
- **Provider gateway overloads (HTTP 5xx via `API Error: NNN`) are RETRYABLE.**
  OpenAI-compatible gateways behind the Claude CLI surface 529 as
  `API Error: 529 [..][The service may be temporarily overloaded...]`; `cli_errors`
  matches `api error: 5\d\d` and `temporarily overloaded` so these classify as
  RETRYABLE, not UNKNOWN.
- **Quota signals come from the summary stream, not assistant text.** Clean `output`
  must never carry quota signals; read `stream_summary` (`cli_runtime_quota_signal`).
- **`reset_display` is shared by the chat warning, `.koan-pause` (`/status`), and the
  journal — it must stay clean.** `_RESET_RE` is bounded so a one-line CLI JSON result
  can't dump its tail into it; `parse_reset_time` handles minute-precision times (`8:40am`).
  Quota chat warnings go through `_notify_raw` (`_notify_quota_warning`) with the raw
  output fenced in a code block — `_notify` runs the Claude reformatter, which strips
  markdown fences, so a code block would never render that way.

## Integration points

- Reads missions via `missions.py`; writes status to `.koan-status`.
- Mode + affordability from `usage_tracker.py` / `burn_rate.py`.
- Provider invocation through `provider/` (subprocess, lock under `koan_tmp_dir()`).
- Skill missions handed to `skill_dispatch.py`.
- Post-mission: `git_auto_merge.py`, `security_review.py`, memory + journal writes.

## Known debt / watch-outs

- **Silent timeouts are the dominant failure mode** — CLI can hang with zero stdout
  until the 7200s watchdog. A resettable-deadline timer would catch stuck sessions far
  faster than post-kill JSON-completeness checks.
- Retry-guard gaps: introducing a new kill/abort mechanism without a `_maybe_retry_mission`
  guard silently drops retryable missions.
- `_run_iteration` is large; the dispatch layer was extracted to `mission_executor` to
  keep `run.py` focused on the execution host. Resist re-merging them.

## Change protocol

Changes to the lifecycle state machine, error classification, or subprocess monitoring
must update this spec and add tests via `test_run.py` (drives `run._run_iteration`) plus
`mission_executor` patch points (`app.skill_dispatch.*`, `app.run.*`).
