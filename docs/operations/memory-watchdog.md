# Memory watchdog (#2232)

Kōan's RSS grows over multi-day runs. The watchdog samples RSS each loop
iteration and, after a sustained overage, restarts the agent loop *between
missions* to reclaim memory back to the ~400 MB baseline. Restarts use the
existing `RESTART_EXIT_CODE` re-exec path, so no mission is ever interrupted.

## Enable (config.yaml)

```yaml
memory_monitor:
  enabled: true
  threshold_mb: 1200          # restart when RSS stays at/above this
  sustained_samples: 3        # consecutive over-threshold loop iterations required
  min_runs_before_restart: 1  # don't restart before completing N runs this session
  tracemalloc: false          # set true to diagnose the leak source
```

Defaults: disabled, `threshold_mb: 1200`, `sustained_samples: 3`,
`min_runs_before_restart: 1`, `tracemalloc: false`.

All knobs are read once at agent-loop startup and frozen for the session — a
live config edit takes effect on the next restart, consistently for every knob.

## How it works

RSS is read from `/proc/self/status` (`VmRSS`, current RSS) with a
`resource.getrusage` fallback — no new dependency. Sampling happens once per
loop iteration, at the loop top, never mid-mission. After RSS stays at or above
`threshold_mb` for `sustained_samples` consecutive iterations (and at least
`min_runs_before_restart` runs have completed this session), the loop logs,
journals, notifies via Telegram, and exits with `RESTART_EXIT_CODE` (`42`).

## Diagnosing the leak

With `tracemalloc: true`, each restart appends a record to
`instance/.memory-restarts.jsonl` containing the top allocation sites:

```bash
jq '.top_allocations' instance/.memory-restarts.jsonl | tail
```

Recurring sites across restarts point at the leak. tracemalloc adds CPU and
memory overhead — enable it only while investigating, then turn it off.

## Observability

`GET /api/health` on the dashboard includes a `memory` block with current
`rss_mb`, the configured `threshold_mb`, `watchdog_enabled`, and `source`.
The dashboard runs in its own process, so it resolves the agent loop's (`run`)
PID and reports *that* process's RSS (`source: "agent_loop"`) — the watchdog's
actual subject. If the run PID can't be resolved (agent loop not running) it
falls back to the dashboard's own RSS (`source: "self"`).
