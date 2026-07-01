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

If `threshold_mb` is at or below the agent loop's baseline RSS at startup, the
watchdog disables itself for the session (and logs why) instead of restart-looping
forever. Raise `threshold_mb` above baseline to re-enable it.

## How it works

RSS is read from `/proc/self/status` (`VmRSS`, current RSS) with a
`resource.getrusage` fallback — no new dependency. The fallback is peak (not
current) RSS and its units are platform-dependent (KB on Linux, bytes on
macOS/BSD), so it is scaled per-platform. Sampling happens once per
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
`rss_mb`, `threshold_mb`, `watchdog_enabled`, and `source`.
The dashboard runs in its own process, so it resolves the agent loop's (`run`)
PID and reports *that* process's RSS (`source: "agent_loop"`) — the watchdog's
actual subject. If the run PID can't be resolved (agent loop not running) it
falls back to the dashboard's own RSS (`source: "self"`).

`watchdog_enabled`/`threshold_mb` reflect the *runtime* decision, not raw
config. The agent loop writes its actual enabled/disabled state (and reason) to
`instance/.memory-watchdog-status.json` at startup, and the health endpoint
reads that. So a watchdog that config-enables but startup-disables itself
(unreadable baseline, or `threshold_mb` ≤ baseline RSS) correctly reports
`watchdog_enabled: false` — the dashboard never falsely advertises protection.
Only when no runtime status file exists does it fall back to raw config.

If reading the config fails, the block sets `config_error: true` and reports
`watchdog_enabled`/`threshold_mb` as `null` rather than a plausible-looking
disabled state — so consumers can tell a real failure from an intentional
disable. A read failure that takes down the whole snapshot returns the same
`config_error: true` shape from the endpoint itself, not a bare `error` object.

A mid-session RSS read failure (`read_rss_mb` returns `0.0`, meaning "unknown")
does **not** reset the overage counter — treating unknown as below-threshold
would silently disable protection. The counter is held and the failure is
logged to stderr once.
