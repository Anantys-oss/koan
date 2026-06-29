# Component Spec — Agent Loop Pipeline

**Modules:** `run.py`, `iteration_manager.py`, `mission_executor.py`,
`mission_runner.py`, `loop_manager.py`, `contemplative_runner.py`, `quota_handler.py`,
`prompt_builder.py`, `event_scheduler.py`, `stagnation_monitor.py`, `hooks.py`,
`devcontainer.py`

## Purpose

The beating heart: a pure-Python loop that pulls a mission, builds a prompt, invokes
the CLI provider as a subprocess, monitors it, and finalizes the mission's lifecycle
state. Everything else exists to feed or observe this loop.

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
| `iteration_manager._downgrade_if_burning_fast()` | Burn-rate-driven mode downgrade, next to affordability downgrade. |
| `stagnation_monitor` | Daemon thread hashing last-N stdout lines; kills the subprocess group after K identical hashes; requeues up to `max_retry_on_stagnation`. |
| `quota_handler` | Parses quota exhaustion from CLI output, writes pause state + journal entry. `extract_reset_info` is **bounded** — it stops at JSON/structural delimiters so a single-line CLI result object can't leak its JSON tail into `reset_display`. `quota_debug_snippet` returns a capped, reset-centered window of the raw output for chat debug blocks. |
| `hooks.py` | Lifecycle events: `session_start`, `session_end`, `pre_mission`, `post_mission`, each error-isolated. |

## Invariants

- **`run.py` never commits to main and never merges.** This is a hard safety boundary
  enforced by prompt + convention; the loop's job is to host the subprocess, not to
  alter git state itself.
- **Skill-dispatch stdout is DATA, not CLI error output.** `_classify_and_handle_cli_error`
  is called with `trust_stdout=False` for skill dispatches so a transcript is not
  mistaken for a quota/auth message. Keep that default for new dispatch pathways.
- **Every termination pathway needs a retry guard.** Stagnation kill, timeout kill,
  and CLI error all route through `_maybe_retry_mission`'s RETRYABLE check.
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
