# SQLite mission mirror (transitional)

Mission state lives in `instance/missions.md` — a human-readable, human-editable
Markdown file parsed by `app/missions.py`. Parsing runs many times per mission
cycle and the regex logic is fragile to code fences, multi-line `###` blocks, and
French headers. To make hot queries (e.g. "count pending") constant-time and to
gain a project/state index, Kōan maintains a SQLite projection at
`instance/missions.db` alongside the file.

This is a **transitional** design: `missions.md` remains the source of truth.

## What the DB holds

`instance/missions.db` (WAL mode, sibling of `memory/memory.db`) has one table,
`missions`, with a `CHECK` constraint on `state IN ('pending', 'in_progress',
'done', 'failed')`, plus `project`, lifecycle timestamps
(`queued_at`/`started_at`/`completed_at`), and a `complexity` tag. Rows are keyed
on `missions.canonical_mission_key()` so the same logical mission maps to one row
across requeue and crash-recovery cycles. Indexes on `state` and `project` back
the scoped queries. See `app/missions_db.py`.

## Read order (dual-read)

Reads consult the DB opportunistically, with a file fallback:

- **Counts** (`iteration_manager._fallback_mission_extract`) read
  `mission_count_by_state(instance, "pending")`. The DB count is authoritative
  **only when `> 0`**; during the dual-write window a lagging mirror could read 0
  while the file still has work, so a 0 result falls back to `count_pending()` on
  the file and triggers a `reconcile()` so the DB self-heals.
- Any module may fall back to `missions.parse_sections()` on the file when the DB
  is missing or returns empty.

## Write path (mirror)

The pure transition functions in `missions.py` (`start_mission`,
`complete_mission_checked`, `fail_mission_checked`, `insert_mission`) stay
`content -> content` with no I/O. The actual file writes happen in their callers,
which call `missions_db.mirror_transition()` **after** the locked
`missions.md` commit:

- `run._start_mission_in_file()` — Pending → In Progress
- `run._update_mission_in_file()` — In Progress → Done / Failed
- `utils.insert_pending_mission()` — new Pending insert

Every mirror call is **best-effort and non-fatal**: a `DatabaseError` is logged
and swallowed so it can never roll back or abort the `missions.md` transition
that already committed.

`mirror_transition()` is resilient to write paths that bypass it: a finalize for
a mission with no live DB row inserts the row in its target state rather than
updating zero rows, and a transition never rewrites a terminal (`done`/`failed`)
row — so a re-run of an identical past mission gets its own fresh row instead of
flipping the historical one. The In Progress → Pending requeue path is mirrored
too.

## Startup reconcile

Crash recovery (`recover.recover_missions()`) rewrites `missions.md` (moving stale
In Progress entries back to Pending or escalating to Failed). Right after that
rewrite it calls `missions_db.reconcile(instance)`, which truncates the table and
rebuilds it from the recovered file so the DB and file agree at the start of every
run. `reconcile()` is idempotent and also the divergence-recovery tool.

## Migration

`app/missions_migrate.py` is a one-shot CLI (`python3 -m app.missions_migrate
[instance] [--apply]`). It defaults to `--dry-run`: it reports per-state counts
and lists unparseable entries for manual intervention. `--apply` runs
`reconcile()` to populate the DB.

## Freeze plan

`missions.md` writes remain authoritative until two stable releases confirm zero
divergence between the file and the DB. Only then does the DB become the source of
truth (`missions.md` would then be generated as a read-only export). Until that
freeze, deleting `missions.db` is safe — the next `reconcile()` rebuilds it from
the file.
