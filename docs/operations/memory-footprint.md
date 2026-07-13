---
type: doc
title: "Memory footprint: process RSS vs cgroup memory.current"
description: "Why the container memory graph plateaus high after missions (page cache + slab, not a leak), the /tmp leftovers that inflate it, the post-mission sweep, and the anon-first triage rule."
tags: [operations, memory, cgroup]
created: 2026-07-13
updated: 2026-07-13
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
   Set `extra_tmp_globs: []` to disable.

Expected steady state after this ships: ~500–700 MB (`anon` ~300 MB + slab +
incompressible cache) instead of 1.4–1.5 GB.

## Triage rule: anon first

When assessing a suspected leak, **`anon` (or per-process RSS) is the signal,
not `memory.current`.** Where cheap, the cgroup breakdown is surfaced:

- `get_memory_status()` adds a `cgroup` block (`anon_mb`/`file_mb`/`slab_mb`)
  when `/sys/fs/cgroup/memory.stat` is readable — visible on the dashboard
  `/health` endpoint.
- `health_check.py` prints the same breakdown, tagging `anon` as the leak
  signal.

See also: [bridge-memory](../architecture/bridge-memory.md),
[memory-watchdog](memory-watchdog.md).
