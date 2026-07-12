---
type: doc
title: "Mission-queue break-glass CLI"
description: "Terminal commands (make missions / make mission-rm, or python -m app.mission_ctl) to inspect and edit the SQLite mission store directly when the Telegram bridge is unresponsive."
tags: [operations]
created: 2026-07-11
updated: 2026-07-11
---

# Mission-queue break-glass CLI

When the agent loop gets stuck on a mission, the Telegram bridge can stop
answering commands like `/list`, `/cancel`, and `/abort` — exactly when you most
need to see and unstick the queue. `app.mission_ctl` is an out-of-band CLI that
talks **straight to the authoritative mission store** (`instance/missions.db`),
independent of both long-running processes, so you can inspect and edit the queue
from a shell.

It is safe to run while the daemons are alive: every edit goes through the same
flock-protected write chokepoint (`utils.modify_missions_file`) the daemons use,
so the store and the `missions.md` read-only export stay consistent. See
[Mission Lifecycle](../architecture/mission-lifecycle.md) and
`specs/components/core.md` for the store contract.

## Commands

Run from the repo root (the `make` targets set `KOAN_ROOT` and `PYTHONPATH` for
you; run as the account that owns the daemon):

```bash
make missions                 # list active queue (in-progress + pending)
make missions state=pending   # just pending
make missions state=failed    # recently failed
make missions state=all       # every section

make mission-rm sel=i1        # abort in-progress mission #1 (→ Failed)
make mission-rm sel=p2        # remove pending mission #2 from the queue
make mission-rm sel=auth      # match by keyword (in-progress first, then pending)
```

Equivalent direct invocation (any shell with the venv and `KOAN_ROOT` set):

```bash
cd koan && KOAN_ROOT=<koan-root> PYTHONPATH=. ../.venv/bin/python -m app.mission_ctl list
cd koan && KOAN_ROOT=<koan-root> PYTHONPATH=. ../.venv/bin/python -m app.mission_ctl delete i1
```

### `list [active|pending|in_progress|done|failed|all]`

Default is `active` (in-progress + pending). Each mission is printed with a
**selector** you pass to `delete`:

```
IN PROGRESS (1):
  i1	[api] refactor the auth middleware
PENDING (2):
  p1	[web] add dark mode toggle
  p2	write integration tests
```

### `delete <selector>` (alias `rm`)

Selectors: `i<N>` (in-progress #N), `p<N>` (pending #N), or a keyword substring
of the mission text (in-progress is searched first — the usual stuck target).

- **Pending** mission → removed from the queue entirely (it never ran).
- **In-progress** mission → **aborted**: moved to Failed with an `[aborted]`
  tag. This takes it out of the active queue and ensures crash-recovery will not
  re-run it on the next start.

## Unsticking a hung loop

Editing the queue does **not** kill a hung agent process — if the run loop is
blocked in a stuck mission's subprocess, abort the mission and then restart the
loop so a fresh process picks up clean:

```bash
make missions                 # find the stuck mission, e.g. i1
make mission-rm sel=i1        # abort it (→ Failed)
make stop && make start       # restart; recovery skips the aborted mission
```

If `make stop` reports processes stopped but `pgrep -fl app/run.py` still shows a
`run.py`, you have an orphaned daemon (often owned by a dedicated bot account) —
kill it from that account before `make start`. See
[Troubleshooting → Agent stuck on a mission](troubleshooting.md).

## Notes & limits

- **Complex `### ` missions** (multi-step) are not simple line-items and cannot be
  removed with `delete`; the command reports this rather than silently no-op'ing.
- The CLI requires `KOAN_ROOT` to be set (the `make` targets handle it) and reads
  the same `instance/` the daemons use.
- For everyday queue management prefer the Telegram/GitHub commands (`/list`,
  `/cancel`, `/abort`); this CLI is the break-glass fallback for when the bridge
  is unavailable.
