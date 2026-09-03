---
type: doc
title: "Memory footprint: process RSS vs cgroup memory.current"
description: "Why the container memory graph plateaus high after missions (page cache + slab, not a leak), the /tmp leftovers that inflate it, the post-mission sweep, the per-mission cgroup scope that kills leaked build daemons, and the anon-first triage rule."
tags: [operations, memory, cgroup]
created: 2026-07-13
updated: 2026-09-03
---

# Memory footprint: process RSS vs cgroup memory.current

The deployment memory graph plateauing at ~1.4 GB after missions is **not** a
Python process leak. Validated on the production container after 14h uptime.

## The two numbers

- **Per-process RSS** — the real memory a process holds. Measured live:
  `run.py` ~67 MB, `awake.py` ~40 MB, supervisord ~30 MB → total ≈ 150 MB.
- **cgroup `memory.current`** — what the platform graph tracks. It counts
  reclaimable page cache and kernel slab on top of anonymous memory. From
  `memory.stat` on the same container: `anon` 294 MB (real process memory),
  `file` 1018 MB (page cache, reclaimable), `slab` 413 MB (kernel dentry/inode
  caches), `shmem` 0.

Missions do heavy file I/O (git operations, pytest runs, CLI session files).
The kernel keeps those pages warm because there is no memory pressure, so
`memory.current` never returns to baseline even though no process grew. It is
not an OOM risk while `anon` stays low — but it **is** billed.

## Root cause of the leftovers

The per-mission `TMPDIR` reaper (`create_mission_tmp_dir` / `cleanup_mission_tmp_dir`)
only covers `$TMPDIR` itself. Test suites run by missions write **outside** it:

- pytest's tmp factory → `/tmp/pytest-of-<user>` (491 MB observed)
- koan's own test runs → `/tmp/test-koan*` (KOAN_ROOT test dirs, including
  `pytest-xdist` `gw*` workers)
- jest → `/tmp/jest_rs`

These accumulate across missions forever. Empirically proven:
`rm -rf /tmp/pytest-of-* /tmp/test-koan* /tmp/jest_rs` on the live container
dropped `memory.current` from 1.53 GB to 952 MB instantly (−580 MB).

> On some platforms `/sys/fs/cgroup/memory.reclaim` is mounted **read-only**, so
> active cgroup reclaim is not available. Do not build anything relying on it.

## Mitigations (#2354 follow-up)

1. **Prevent at the source.** Mission subprocesses are launched with
   `PYTEST_ADDOPTS="… --basetemp=$TMPDIR/pytest"` (appended, never clobbered —
   `utils.pytest_addopts_with_basetemp`), so pytest tmp trees land inside the
   already-reaped per-mission dir. Nested invocations (`make test`) inherit it.
2. **Sweep as a safety net.** After each mission `utils.sweep_stray_tmp_dirs`
   removes well-known stray trees not covered by `$TMPDIR`. The glob list is
   configurable via `cleanup.extra_tmp_globs` (defaults:
   `/tmp/pytest-of-*`, `/tmp/test-koan*`, `/tmp/koan-*`, `/tmp/jest_rs`). Only
   paths directly under `/tmp` matching a glob are removed; symlinks are never
   followed, the live `koan_tmp_dir()` scratch/lock dir is never touched even
   though it matches `/tmp/koan-*`, and dirs owned by another uid are skipped.
   The sweep is also **age-gated** (`cleanup.min_tmp_age_seconds`, default
   600s): a tree is spared if anything inside it — the whole subtree is scanned,
   not just the top-level dir — was touched within the window. This protects a
   concurrently-running **parallel session** (`session_manager.spawn_session`)
   that is mid-`make test` on the koan repo itself: its `/tmp/test-koan*`
   (KOAN_ROOT) tree is same-uid and not the live scratch dir, so only the age
   gate keeps the post-mission sweep from `rmtree`-ing it out from under the
   running session (which would cause spurious test failures and a wrong verdict
   — the koan-on-koan-with-parallel-sessions case). Set `extra_tmp_globs: []` to
   disable the sweep, or `min_tmp_age_seconds: 0` to disable just the age gate.

Expected steady state after this ships: ~500–700 MB (`anon` ~300 MB + slab +
incompressible cache) instead of 1.4–1.5 GB.

## Per-mission containment: the cgroup scope (`mission_limits`)

The sweeps above return *files* and *page cache*. They never returned
**processes** — until this section shipped, Kōan had no process sweep on the
mission success path at all. `run_claude_task`'s `finally` closed fds, cleared
`_sig.claude_proc`, reset the terminal and restored the git branch; `killpg`
only fired on the abort/timeout branches. So a mission that left a daemon
running leaked it into the host's idle baseline.

Observed on a 4 vCPU / 7.75 GiB fleet host with **no swap** after a Java/Gradle
mission: git-fetch timeouts, a 38 s page-cache reclaim, `gh api` timeouts.
Recovery was a manual `killall java`. The chain:

1. Gradle's build daemon persists by design (3-hour idle timeout) and **detaches
   to `PPID 1` with its own session** — 766 MB RSS idle for 26 minutes.
2. In that project the test postgres is a Gradle **build service**, so the
   container is started *inside the Gradle daemon JVM*, not in a test fork.
3. So the daemon — not a test fork — holds the Testcontainers `ryuk` client
   socket (`ss -tanp`: `java pid=40407 fd=444` → ryuk port 32772). Ryuk reaps
   only once its client disconnects, so the container's reap timer was scoped to
   the daemon's 3-hour life and the postgres container was never removed.
4. **Kill the Gradle daemon and ryuk reaps the containers itself.** One lever
   fixes the whole chain.

**Why a process group is not sufficient.** Because the daemon re-parents to
PID 1 with its own session, it is out of the mission's process group by the time
the mission ends — `os.killpg` can never reach it. A cgroup can, because it
catches every descendant regardless of how many times it double-forks.

`app/mission_scope.py` is the single containment primitive. All **three** mission
spawn sites go through it — `run.run_claude_task` (the generic path, via
`popen_cli`'s `launcher` prefix, applied *after* the prompt rewrite),
`run._run_skill_mission` (the `/review`, `/fix`, `/implement` dispatch path), and
`session_manager.spawn_session` (parallel sessions, same `popen_cli` launcher
prefix) — and `teardown()` runs in the `finally` blocks that already reap
`TMPDIR` and drop page cache, so it fires on **every** exit path including
success. For parallel sessions there is no single `finally`, so the teardown sits
on both exit paths instead: `poll_sessions()` when the session completes and
`kill_session()` when it is aborted. The inner `provider/__init__.py` spawn is
deliberately left alone: it shares `review_runner`'s process group and so
inherits the same cgroup.

Parallel sessions were **missed** by the first cut of this and confirmed leaking
in production on a fleet host: a parallel `implement` mission logged
`[parallel] Spawned bbf2bd38511f` while `systemctl list-units 'koan-mission-*'`
listed nothing, and its Gradle daemon showed up as `pid=97020 ppid=1 rss=822MB`
in `/user.slice/user-0.slice/session-68.scope` — Kōan's own SSH login scope, not
a mission scope. Host free memory fell 4653 → 3751 MB in two minutes on a
7.75 GiB swapless box. The lesson generalises: *every* new spawn path has to go
through `launch_scoped`, because nothing else in the process tree will catch a
daemon that has already re-parented to PID 1.

- **Boundary:** `systemd-run --scope --collect --quiet
  --unit=koan-mission-<uuid>.scope
  --property=MemoryMax=<n> --property=MemoryHigh=<90% of n>`. `MemoryHigh` gives
  reclaim back-pressure before `MemoryMax` becomes an OOM kill. Non-root uses the
  per-user manager (`--user`); the system manager needs polkit, which fails
  non-interactively.
- **Teardown** (`mission_scope.stop_scope_unit`, shared with `make stop` so both
  judge a manager failure the same way): `systemctl stop <unit>`, then verify the
  cgroup is empty via `cgroup.events`, escalating to `systemctl kill -s SIGKILL`
  if it is not. `_systemctl` runs with `check=False`, so a refusal arrives as a
  **non-zero result, not an exception** — accepting it would report containment
  that never happened. A non-zero `stop` is therefore disambiguated with
  `systemctl show -p LoadState`: `not-found` means `--collect` already reaped the
  scope (the ordinary clean-mission ending, and quiet), anything else — or an
  unreachable manager — is a real failure and escalates. The escalation targets
  the *unit*, not the process group: on the success path the mission process has
  already exited so `kill_process_group` signals nothing, and a re-parented
  daemon has left the group anyway. The group is the last resort, pulled only
  when nothing on the systemd side can confirm the scope is gone.
- **Cap:** `max(memory_min, MemTotal - memory_reserve)`, with an explicit
  `memory_max` winning verbatim. A reserve **with a floor**, not a percentage of
  RAM: the fleet spans 1.9 GiB to 7.7 GiB with no swap anywhere, and Kōan's own
  baseline is roughly constant (~500 MB: python ~97 MB + CLI ~275 MB +
  mcp-atlassian ~129 MB), so a percentage under-reserves on the small hosts.
- **Cap hit:** reported as a distinct mission result — `memory_cap_exceeded` /
  `memory_cap_detail` in the `post_mission` hook context, reading e.g.
  `exceeded memory cap (5.9G of 5.75G)` (peak from `memory.peak`, falling back to
  `read_cgroup_memory_stat`'s `anon`). **Never retried**: re-running an
  oversubscribed build only repeats the meltdown. The phrase is *passed into*
  `run_post_mission` by whoever owns the mission — the sequential loop hands
  over `app.run._last_mission_memory_cap`, a parallel session hands over its own
  `ScopedProcess.cap_message()` (carried on `SessionResult`). The pipeline never
  reaches for the global: a session reaped in a later iteration would otherwise
  inherit a previous sequential mission's cap flag and never report its own.
  Evidence that cannot be read (`memory.events` gone with the collected cgroup,
  an unreachable manager) is *unknown*, not "the mission fit": it is logged, and
  deliberately not reported as a cap hit, because on a clean mission the
  collected cgroup is unreadable by definition and believing the unknown would
  mark every ordinary mission as capped — permanently disabling the retry path.
  The mirror of that rule matters just as much: a readable `oom_kill 0` is the
  kernel saying the cap did **not** fire, so the exit-status heuristic is gated
  on `oom_kills is None` and never overrides it. That evidence is readable in
  exactly the case this feature targets (a leaked daemon keeps the scope
  populated, so `--collect` has not reaped the cgroup), and it is what separates
  the cap from a co-tenant exhausting RAM and the kernel's *global* OOM killer
  taking the CLI. Kills Kōan issues itself are excluded by
  `koan_initiated_kill`, the double-tap CTRL-C included — `_on_sigint` records
  it as an abort exactly as `/abort` does, so its own SIGKILL is not read as the
  cap firing and then refused a retry.
- **No container sweep.** Containers are children of the Docker daemon, not of
  the mission, so nothing Kōan can observe distinguishes one this mission
  started from a co-tenant's: a creation timestamp inside the mission's window
  proves *overlap*, never ownership, and a long mission overlaps everything a
  shared host starts while it runs. A time-gated `docker rm -f` would therefore
  destroy a live co-tenant workload's container and its state, which no default
  and no documentation makes acceptable — so the sweep was removed rather than
  merely disabled. Killing the scope is what handles containers: it drops the
  ryuk client socket, and ryuk removes the containers that client owned. That is
  the same lever the whole feature rests on. No `docker prune`, ever. The
  hand-off does depend on ryuk being enabled: a project that sets
  `TESTCONTAINERS_RYUK_DISABLED=true` owns its own container cleanup, and Kōan
  will not (and cannot safely) do it for them.
- **Fallback:** where `systemd-run` cannot create a scope (macOS, no manager, no
  user manager) Kōan spawns with `start_new_session=True`, tearing down the
  process group captured at launch (SIGTERM → 3 s → SIGKILL → 5 s). The *pgid*
  is what is signalled, not the `Popen`: `kill_process_group()` returns at its
  `poll()` guard once the mission process is reaped, so on the success path it
  would signal nothing while descendants left in the group keep running. Every
  host must still be able to run missions. The same "cannot tell is never
  contained" rule applies here — only a `ProcessLookupError` from `killpg`
  proves the group is empty, so an EPERM refusal (a descendant that changed
  credentials), a group that outlived SIGKILL, or a pgid that was never captured
  keeps its registry record for `make stop` instead of reporting a clean sweep.
  The **warning** is once per process for the *probe* verdict (a host does not
  grow a `systemd-run` mid-run, so repeating it says nothing new) but logged on
  **every** occurrence of a scope that fails to *start*: a user manager that
  restarts mid-run leaves each later mission uncontained, and sharing one
  one-shot budget hid exactly that — an empty
  `systemctl list-units 'koan-mission-*'` and a clean log while daemons
  accumulate.
- **`make stop`:** `pid_manager.stop_processes` stops the live mission scope
  first (registered under `$KOAN_ROOT/.koan-mission-scopes/`, one file per scope
  so there is no read-modify-write to race on), then signals each daemon's
  *process group* rather than the bare PID from `.koan-pid-*` — the single-PID
  SIGTERM left every descendant running. A registry entry is unlinked only once
  its scope is **confirmed** stopped: this is the retry mechanism, so discarding
  a record it could not act on would destroy the only handle on a live scope.
  `ScopedProcess.teardown` follows the same rule for scope records. A fallback
  `pid-<n>` record is dropped rather than retried, because a PID — unlike a uuid4
  unit name — can be recycled; and for the same reason it is **signalled only
  after** the PID's real start time (`/proc/<pid>/stat` + `btime`, or `ps -o
  etime=` off Linux) matches the `started_at` in the record. Unlinking a stale
  record afterwards would not undo a SIGKILL already sent to a stranger's process
  group. A retained scope record self-heals: once the scope really is gone the
  next `make stop` sees `LoadState=not-found` and unlinks it.
- **Startup reconciliation:** `teardown()` covers every ordinary mission exit,
  but a hard crash or SIGKILL of `run.py` never runs it — the case the registry
  exists for. `startup_manager` therefore sweeps it (`Leaked mission scope
  sweep`, next to the stale-`TMPDIR` sweep) so a scope from a previous
  incarnation is not left holding a 766 MB daemon until someone runs `make
  stop`. A record under this `KOAN_ROOT` can only be this instance's, the unit
  name is a uuid4 that cannot collide, and a fallback `pid-<n>` record is
  start-time-verified before it is signalled.
- **Config:** `mission_limits: { enabled, memory_reserve, memory_min,
  memory_max }` — see `instance.example/config.yaml`. Default on.

## Triage rule: anon first

When assessing a suspected leak, **`anon` (or per-process RSS) is the signal,
not `memory.current`.** Where cheap, the cgroup breakdown is surfaced:

- `get_memory_status()` adds a `cgroup` block (`anon_mb`/`file_mb`/`slab_mb`)
  when `/sys/fs/cgroup/memory.stat` is readable — visible on the dashboard
  `/health` endpoint.
- `health_check.py` prints the same breakdown, tagging `anon` as the leak
  signal.

## Page-cache reclaim (#2374)

Railway bills the cgroup's `memory.current`, which includes the kernel page
cache (`file`), not just process RSS (`anon`). Missions do heavy file I/O and
the kernel keeps those clean pages warm absent memory pressure, so the billed
baseline ratchets up (~550 MB → ~820 MB in a day) even though `anon` is flat.
`/sys/fs/cgroup/memory.reclaim` is read-only on Railway, so Kōan uses
unprivileged `posix_fadvise(POSIX_FADV_DONTNEED)` to drop clean pages
(`app/page_cache.py`).

- **When it runs:** after every mission (in `run_claude_task`'s `finally`, once
  the CLI subprocess has exited) and periodically while idle
  (`page_cache_reclaim.idle_interval_s`, default 900s; `0` disables the idle tick).
  The idle hook lives *inside* `loop_manager.interruptible_sleep()` — the one
  sleep primitive every idle path shares (between-runs sleep, contemplative
  sleep, and all `_IDLE_WAIT_CONFIG` states: `focus_wait`, `passive_wait`,
  `schedule_wait`, `exploration_wait`, `pr_limit_wait`,
  `branch_saturated_wait`) — so new idle states inherit it automatically.
  Do not wire it at individual call-sites: that is how `focus_wait` initially
  shipped with no reclaim at all (850 MB flat billed `memory.current` on an
  idle focus-mode instance, observed 2026-07-14).
- **What it touches:** the small, high-value roots first — `instance/`, the venv,
  the scratch dir — then project workdirs, then `extra_roots`, so a budget-truncated
  sweep still reclaims the cheap roots instead of burning the whole budget on one
  large project tree. Read-only; regular files only; symlinks and special files
  skipped (`os.walk(followlinks=False)` + `os.lstat` + `stat.S_ISREG`); a soft
  `time_budget_s` (default 10s) bounds each sweep so it never stalls the loop.
- **Reading the numbers:** `read_cgroup_memory_stat()` returns `anon_mb`/`file_mb`.
  Watch `file_mb` drop toward the hot-set floor post-mission; `anon` is the leak
  signal. A single `Page cache reclaim: file X→Y MB (…)` health log fires when the
  reclaimed delta is meaningful (≥3 MB), the sweep was truncated (`budget hit` —
  raise `time_budget_s` or trim `extra_roots`), or per-file errors dominated (`N
  errors`, i.e. systemic `os.open` denial). A small-delta success is the only case
  suppressed, so no-op reclaims never hide behind silence.
- **Platform:** no-op where `os.posix_fadvise` is absent (macOS dev boxes) —
  every hook returns `ReclaimStats(supported=False)` and touches zero files.
- **Config:** `page_cache_reclaim: { enabled, idle_interval_s, time_budget_s,
  extra_roots }` — see `instance.example/config.yaml`. Default on. `idle_interval_s`
  defaults to **180s** — short enough that boot/between-tick page cache never sits
  billed for long.

## Out-of-root residuals: the stray `/tmp` trees

The reclaim only drops pages for files **under its roots**. Mission subprocesses
read large files into the page cache from trees the standard roots
(`instance/` + venv + scratch + project workdirs) never cover — chiefly the stray
`/tmp` mission trees (`pytest-of-*`, `test-koan*`, `koan-*`, `jest_rs`). Those
pages stay billed until the underlying files are **deleted** by the age-gated
post-mission sweep, which can be hours away.

Observed live (2026-07-19, fresh idle instance): `anon` 78 MB / per-process RSS
~124 MB (healthy), but billed `memory.current` sat at ~600–900 MB. The reclaim
log showed two floors — `file` dropping to **~95 MB** (everything reclaimed) or
plateauing at **~663 MB** (a mission tree pinning ~570 MB of out-of-root cache).
The high floor held **10:44→15:16 (~4.5h)** — this is the "stable at ~1 GB for
hours" an operator sees — then fell to ~98 MB the instant the file was deleted.

**Fix:** `default_reclaim_roots()` now also sweeps the same stray-`/tmp` trees the
post-mission tmp sweep targets (`cleanup.extra_tmp_globs`, **own-uid dirs only** so
it never churns on another user's `/tmp`). Reclaiming their clean pages decouples
the billed baseline from the sweep's deletion latency. If a large residual persists
elsewhere, add its dir to `page_cache_reclaim.extra_roots`; the ultimate backstop
for *any* out-of-root cache is a **cgroup memory limit** on the service (Railway
Resources), which forces the kernel to evict reclaimable page cache **and** slab
under pressure — `fadvise` cannot touch slab.

See also: [bridge-memory](../architecture/bridge-memory.md),
[memory-watchdog](memory-watchdog.md).
