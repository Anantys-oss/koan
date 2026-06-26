# Daemon Runtime

This page describes how the long-running Koan daemon is assembled today.

## Startup

`make start` delegates to process management code in `koan/app/pid_manager.py`.
The manager starts the bridge, the agent loop, and optional local-model services
depending on provider configuration. PID files and `fcntl.flock()` prevent
duplicate process instances for the same role.

Startup displays the shared hero banner from `koan/app/banners/koan_hero.txt`
using the terminal mint theme. Banner rendering is cosmetic and must not block
process launch if it fails.

`make run` starts only the agent loop. `make awake` starts only the messaging
bridge. `make stop` asks managed processes to exit and escalates only when a
process does not stop cleanly.

## Bridge Loop

`awake.py` owns user-facing message ingestion. It:

- loads messaging configuration and command registries;
- polls Telegram, Slack, Matrix, GitHub, or Jira integration paths as configured;
- routes slash commands through command handlers and skill dispatch;
- promotes a plain message whose first word names a core skill to its slash form (`time` → `/time`);
- classifies remaining non-command text as chat or mission intent;
- appends missions to `instance/missions.md`;
- drains `instance/outbox.md` back to the messaging provider.

Bridge state that would otherwise create circular imports lives in
`bridge_state.py`. Bridge logging lives in `bridge_log.py`.

### Worker lanes (chat vs background)

The bridge runs heavy work off the messaging poll loop in two independent
daemon-thread lanes (`awake._run_in_worker(fn, lane=...)`):

- **chat** — interactive replies (`handle_chat`). When busy, a second chat
  message is answered with "⏳ Busy with a previous message."
- **bg** — background tasks: worker skills (Claude/API/GitHub calls typed
  in chat, e.g. `/review`, `/rebase`). When busy, additional bg tasks are dropped
  silently (no chat spam). `_run_in_worker` returns `True`/`False`
  (started vs dropped) so callers can tell. Autonomous background work
  ignores the result and stays silent; **user-initiated** worker skills
  (a `/review`, `/implement`, etc. typed in chat) dispatch on the bg lane
  but surface a "⏳ Busy with a previous task" reply when the lane was full,
  so a typed command never vanishes without feedback.

Because the lanes run concurrently, a long-running background task never
blocks an interactive chat reply, and neither blocks the poll loop. One
in-flight task per lane provides back-pressure (no unbounded fan-out). No
extra OS process is forked — the "dedicated chat channel vs bg tasks" split is
realized with threads inside the existing bridge process.

## Agent Loop

`run.py` owns background work. Its loop is split across focused modules:

- `iteration_manager.py` refreshes usage, selects mode, injects recurring work,
  chooses a mission, and resolves the project.
- `mission_runner.py` performs lifecycle transitions, builds the execution
  command, runs the provider or direct skill, parses output, records usage, and
  handles completion, failure, reflection, and auto-merge.
- `loop_manager.py` handles focus, pending-file setup, project validation, and
  interruptible sleeps.
- `quota_handler.py` detects quota exhaustion and writes pause state. Hard
  quota hits requeue the active mission, pause until the provider reset time
  plus 10 minutes, or fall back to a 5-hour pause when no reset time is known.
  Claude Code's structured `rate_limit_event` stream events are matched
  status-aware: only a *rejected* status pauses Koan. The newer CLI also emits
  informational `rate_limit_event`s (status `allowed`) on every session, so
  matching the bare event type would otherwise pause Koan on successful runs.
  The rejected status must co-occur with the event on the same stream-json line
  — an unanchored whole-text match would pair the always-present informational
  event with any unrelated `"status":"exceeded"` JSON elsewhere in the output
  (e.g. CI / check-run payloads that `/ci_check` inspects). The informational
  summary line is rendered as `[cli] rate_limit_ok:` (underscored) so it never
  collides with the loose `rate limit` quota pattern.

Idle actions use the same interruptible sleep path even when `auto_pause` is
disabled. If `interval_seconds` is set to `0`, the runner waits until the next
configured GitHub/Jira notification poll is due, or a small minimum breath when
notification polling is disabled, so always-on instances do not hot-loop.
During those idle waits, the runner only wakes for the run-targeted restart
marker (`.koan-restart-run`); stale legacy `.koan-restart` markers are ignored.

The loop writes real-time state to status files so the bridge, dashboard, and
commands can report progress without directly controlling the runner.

## Runtime Modes And Guards

- Pause mode uses `.koan-pause` state and can be time-bounded.
- Focus mode narrows work to a project or focus area.
- Passive mode keeps Koan alive but blocks execution.
- Restart signaling uses a file so the bridge can ask the runner to restart.
- The stagnation monitor watches provider output, kills stuck subprocess groups,
  and requeues missions up to the configured retry limit.

New daemon behavior should prefer these existing state files and managers over
adding direct process coupling.

## Parallel Sessions

When `max_parallel_sessions` is set to 2 or higher in `config.yaml`, the agent
loop can run multiple missions concurrently. Each session gets its own git
worktree so there are no branch conflicts.

The parallel path has two phases wired into `_run_iteration` in `run.py`:

1. **Reap** (`_parallel_reap_sessions`) — polls active sessions for completion,
   runs the post-mission pipeline, transitions `missions.md` state, and sends
   notifications. Quota exhaustion in any session halts new dispatches.
2. **Dispatch** (`_parallel_dispatch_sessions`) — spawns the primary mission
   plus fills remaining free slots from the pending queue. A same-project guard
   prevents two sessions from running on the same project simultaneously.

Session state is tracked in-memory via `_live_sessions` and persisted via
`SessionRegistry` (`instance/sessions.json`). `session_manager.py` owns
`spawn_session`, `poll_sessions`, and `kill_session`. `worktree_manager.py`
handles git worktree create/teardown.

Skill-dispatched missions (`/rebase`, `/plan`, etc.) always use the sequential
path because they depend on git prep and specialised post-mission handling.

Single-slot installations (`max_parallel_sessions: 1`, the default) skip all
parallel logic with zero overhead.

## CLI stdout memory model

Claude/skill CLI output can reach hundreds of MB per mission, multiplied per
concurrent session when `max_parallel_sessions > 1`. To keep peak RAM bounded,
no path holds the full transcript in a Python list while the mission runs:

- **Main mission path** (`run_claude_task`): the child's stdout is wired
  straight to a temp file (`subprocess.Popen(stdout=out_f)`); no Python-side
  line list is ever built. Downstream consumers (`run_post_mission`, token
  parsing, PR-URL extraction) read that file.
- **Skill-dispatch path** (`_run_skill_mission`): each line is streamed via
  `_pump_skill_stdout` to the same on-disk capture and to `pending.md` (for
  `/live`) as it arrives; only a 200-line tail deque is kept in RAM for timeout
  diagnostics. The full transcript is read back from disk once, at
  end-of-mission, when a caller needs it (`_extract_pr_url`, the `— skipping`
  check, error classification).
- **Provider stream runner** (`run_command_streaming`): the error/max-turns
  accumulator (`raw_lines`) is a bounded `deque`; the assistant-text
  accumulator (`text_lines`) is the actual return value and is intentionally
  unbounded so long sessions never silently lose output.

Operators running on constrained hosts (e.g. Railway) should also pin
`max_parallel_sessions: 1` so per-session cost is not multiplied.
