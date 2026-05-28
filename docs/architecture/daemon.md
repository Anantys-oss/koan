# Daemon Runtime

This page describes how the long-running Koan daemon is assembled today.

## Startup

`make start` delegates to process management code in `koan/app/pid_manager.py`.
The manager starts the bridge, the agent loop, and optional local-model services
depending on provider configuration. PID files and `fcntl.flock()` prevent
duplicate process instances for the same role.

`make run` starts only the agent loop. `make awake` starts only the messaging
bridge. `make stop` asks managed processes to exit and escalates only when a
process does not stop cleanly.

## Bridge Loop

`awake.py` owns user-facing message ingestion. It:

- loads messaging configuration and command registries;
- polls Telegram, Slack, Matrix, GitHub, or Jira integration paths as configured;
- routes slash commands through command handlers and skill dispatch;
- classifies non-command text as chat or mission intent;
- appends missions to `instance/missions.md`;
- drains `instance/outbox.md` back to the messaging provider.

Bridge state that would otherwise create circular imports lives in
`bridge_state.py`. Bridge logging lives in `bridge_log.py`.

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
