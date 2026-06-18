# State Consistency Patterns

This page codifies the recurring design errors found in the B/S-series state
consistency audit (2026-06) and the rules that prevent them. Read before
touching `missions.md` mutation logic, retry counters, signal files, or any
shared-file state.

---

## Pattern 1 — Stable identity keys

**Rule:** Keys used for cross-run deduplication or retry counting must be derived
from the *semantic content* of a mission, not from its *decorated form*.

### Why it matters

Lifecycle markers (`⏳(timestamp)`, `▶(timestamp)`, `[r:N]`, `[complexity:X]`)
are appended and stripped as a mission moves through the queue. If the key is
computed from the raw title *including* those markers, every requeue cycle
produces a different hash — silently resetting any counter keyed on the mission.
A cap like `max_retry_on_stagnation = 3` becomes unreachable.

### What to do

Always strip lifecycle markers before hashing or comparing. Use
`missions.canonical_mission_key()` — it is the single source of truth for
stable identity:

```python
from app.missions import canonical_mission_key

# Good — same result before and after requeue
key = hashlib.sha256(canonical_mission_key(title).encode()).hexdigest()

# Bad — hash changes every cycle as ⏳/▶ timestamps update
key = hashlib.sha256(title.encode()).hexdigest()
```

**Bugs prevented:** B1, B13 (infinite stagnation loop), S2 (duplicate key logic).

---

## Pattern 2 — Explicit found/not-found signals from search-and-modify

**Rule:** Functions that search for a mission in `missions.md` and optionally
modify it must return an *explicit* `found: bool` via their return value or a
closure flag. Never infer "found" from a before/after content comparison.

### Why it matters

`missions.md` undergoes housekeeping (pruning old Done/Failed entries) that can
change content *even when the target mission is absent*. A check like
`content_before != content_after` returns True for a pruning-only run,
producing a false "mission was found and finalized" signal.

### What to do

Use the store mutators, which already return an explicit `found: bool`:

```python
# Good — store mutators return an explicit found flag
with locked_store(instance_dir) as store:
    found = store.complete(needle)   # start()/fail()/cancel_pending()/edit() also return bool
return found

# Bad — infers found from content diff (breaks when pruning fires)
before = missions_path.read_text()
with locked_store(instance_dir) as store:
    store.complete(needle)
after = missions_path.read_text()
return before != after          # Wrong: also True when only pruning changed it
```

**Bugs prevented:** B11 (false-positive finalization), B12 (prune masking absent
mission).

---

## Pattern 3 — Single canonical implementation per shared operation

**Rule:** Any operation on shared state that appears in more than one place must
be extracted to a canonical function and *delegated to* everywhere. Parallel
implementations diverge.

### Why it matters

`stagnation_monitor` and `mission_history` each maintained their own regex for
stripping lifecycle markers. `outbox_manager` implemented outbox-append logic
separately from `utils.append_to_outbox`. When one copy was updated, the other
wasn't — leading to silent drift and potential behavioral divergence.

### What to do

- Mission identity stripping → `missions.canonical_mission_key()`
- Outbox appending → `utils.append_to_outbox()`
- Missions queue mutation → `mission_store.locked_store()` (regenerates `missions.md`; never write it directly)
- JSON tracker reads/writes → `app.locked_file.locked_json_read/modify`

When you find yourself writing a regex that strips `⏳|▶|✅|❌|[r:N]`, stop
and import `canonical_mission_key` instead.

**Bugs prevented:** S2 (duplicate key logic), S3 (duplicate outbox write).

---

## Pattern 4 — Enum over boolean pairs for N-state machines

**Rule:** An N-state machine must use an N-state value (enum string, literal
union), not N−1 independent booleans.

### Why it matters

Two booleans `(startup_notified, boot_notified)` model a 3-state machine
`("boot", "resume", "running")` using 4 combinations, 1 of which
(`startup_notified=True, boot_notified=False`) is illegal. There is nothing
stopping the code from reaching it, and readers must reconstruct the
intended meaning from boolean combinations.

### What to do

```python
# Good
_startup_phase: str = "boot"   # "boot" | "resume" | "running"

def _mark_startup_resume() -> None:
    global _startup_phase
    if _startup_phase == "running":
        _startup_phase = "resume"

# Bad
_startup_notified = False
_boot_notified = False
# … now every reader must know that (False, False) = boot,
# (False, True) = resume, (True, True) = running, (True, False) = impossible
```

The legal states are self-documenting as string literals and exhaustively
matchable with a simple equality check.

**Bugs prevented:** S4 (dual-boolean startup state).

---

## Pattern 5 — Zero writes to deprecated signal files

**Rule:** Once a signal file is deprecated, all in-tree writes to it are removed
*at the same time*. A "backward compat" write that no current consumer reads is
a silent no-op that breaks callers who were using it.

### Why it matters

The legacy `.koan-restart` file was written "for backward compat" alongside the
two live per-consumer markers. Neither the run loop nor the bridge polled it.
Callers in the REST API and dashboard were writing *only* the legacy file —
making `/v1/restart`, `/v1/update`, and `/api/agent/restart` silent no-ops.

### What to do

When deprecating a signal file:
1. Remove all writes in the same commit that adds the replacement.
2. Provide a canonical function (`request_restart()`) that writes all current
   targets — callers import the function, not the file constants.
3. Document in the restart-manager that the old path is DEPRECATED (read-compat
   only, if any out-of-tree code might still check it).

```python
# Good — one function, all callers go through it
def request_restart(target: Optional[str] = None) -> None:
    """Write restart markers for the specified consumer(s)."""
    for path in _WRITE_TARGETS:
        ...

# Bad — caller writes its own file constant
Path(RESTART_FILE).touch()   # Wrong: dead file, no consumer reads it
```

**Bugs prevented:** S5 (silent restart no-ops in API and dashboard).

---

## Pattern 6 — Read file-based state once, pass it down

**Rule:** File-based state that is *checked* and then *consumed* must be read
exactly once. Pass the value as a parameter rather than re-reading it at the
use site.

### Why it matters

Reading `pending.md` first to check existence and then again inside the recovery
function creates a TOCTOU window: the file may be created or deleted between the
two reads, causing the in-memory `has_pending` flag to diverge from what the
recovery function actually finds.

The same applies to Telegram offsets: an offset held only in memory resets to
`None` on bridge restart, causing the bridge to re-deliver every message from
the last unseen offset.

### What to do

```python
# Good — read once, pass value through
has_pending = check_pending_journal(instance_dir)   # one read
count, escalated = recover_missions(instance_dir, has_pending_journal=has_pending)

# Bad — check separately from use (TOCTOU)
if check_pending_journal(instance_dir):             # first read
    count, _ = recover_missions(instance_dir)       # second read inside
```

For values that must survive process restart, persist them to disk (e.g.,
Telegram offset in a JSON tracker file).

**Bugs prevented:** S6 (TOCTOU on pending.md), B4 (offset lost on restart), B3
(pending.md overwritten after checkpoint recovery).

---

## Pattern 7 — Decouple operations with distinct failure modes

**Rule:** When a function performs two operations whose success/failure signals
are independent (e.g., "find mission" and "prune old entries"), split them into
separately callable units. Bundling them forces callers to infer one signal from
the other.

### Why it matters

`_update_mission_in_file()` performed both `_move_pending_to_section()` (the
actual goal) and `_prune_failed_entries()` (housekeeping). When pruning changed
the content, the content-diff heuristic returned True even when the target
mission was absent. Splitting pruning into `_prune_missions_history()` gave
`_update_mission_in_file()` a clean closure-flag signal, decoupled from
housekeeping.

### What to do

- If two operations can each change the file, give them separate call sites.
- If one must piggyback on the other's lock, use a closure but track both
  signals independently:

```python
# Good — decoupled: find/modify first, prune after under same lock
found = [False]
def _transform(content: str) -> str:
    updated, did_find = _move_pending_to_section(content, mission)
    found[0] = did_find
    return _prune_failed_entries(updated) if did_find else updated
```

**Bugs prevented:** B12 (pruning masking absent mission), S1 (cross-link
responsibility between start_mission and recover.py).

---

## Pattern 8 — Document safety nets and cross-link their counterparts

**Rule:** Every defensive mechanism that acts as a fallback for another must
say so explicitly in its docstring, name when *it* fires vs when the primary
fires, and cross-link both directions.

### Why it matters

`recover.py` and `MissionStore.flush_stale_in_progress()` are two safety nets
for the same scenario (stale In Progress missions) that fire at different times
(startup vs per-mission-start). Without documentation, developers don't know
both exist, can't reason about which one handles which edge case, and can't
debug why a mission ended up Failed with `[flushed]` instead of being recovered
to Pending.

### What to do

In `recover.py` docstring:
```
Primary safety net for stale In Progress missions. Runs once at startup,
before the first iteration. Any stale IP not caught here falls through to
MissionStore.flush_stale_in_progress(), called inside MissionStore.start().
```

In `MissionStore.start()` / `flush_stale_in_progress()` docstring:
```
Second line of defence after recover.py. flush_stale_in_progress() is called
inside start() before the new mission transitions to In Progress. When it fires,
_start_mission_in_file() emits a WARNING so the operator knows recover.py
missed something.
```

Log when safety nets fire — a silent flush creates invisible history:

```python
if stale_flushed:
    log("warning", f"Sanity flush: {len(stale_flushed)} stale In Progress "
        f"missions moved to Failed — recover.py missed them")
```

**Bugs prevented:** D1, D2 (undocumented flush side effect), B5 (flush to Done
instead of Failed — would have been caught earlier if the safety net role was
explicit).

---

## Pattern 9 — Unified retry caps for related failure modes

**Rule:** Retry mechanisms that guard the same "give up" decision must share
one counter file and one combined cap. Per-mode caps are supplementary, not
substitutes.

### Why it matters

Stagnation retries (`max_retry_on_stagnation`) and crash-recovery retries
(`MAX_RECOVERY_ATTEMPTS`) both protect against a mission being requeued
indefinitely. When tracked in separate files with separate keys and separate
caps, a mission can exhaust one cap and be blocked under that mode, then
exploit the other mode to keep running indefinitely — effectively bypassing
both caps.

### What to do

Use `.mission-retries.json` for all retry tracking. The entry structure:

```json
{
  "<sha256-of-canonical-key>": {
    "count":          2,
    "crash_count":    1,
    "total_attempts": 3
  }
}
```

- `count` — stagnation-only requeues. Reset on success.
- `crash_count` — crash-recovery requeues. Reset on success.
- `total_attempts` — combined cap; both increment it. This is the hard ceiling.
- The `max_total_retries` config key governs `total_attempts`.

New retry mechanisms that requeue missions must:
1. Use `stagnation_monitor.increment_retry_count()` or
   `stagnation_monitor.increment_crash_count()` (both bump `total_attempts`).
2. Check `get_total_attempts()` against `max_total_retries` before requeueing.
3. Clear all counters on genuine success via `clear_retry_info()`.

**Bugs prevented:** B6 (independent stagnation and crash caps; combined
exhaustion bypass), B1/B13 (counter resets; see Pattern 1).

---

## Summary checklist

Before merging code that touches `missions.md`, retry counters, signal files,
or shared state, verify:

| # | Check |
|---|-------|
| 1 | Keys derived from `canonical_mission_key()`, not raw decorated titles |
| 2 | Search-and-modify returns explicit `found: bool`, not content diff |
| 3 | No duplicated lifecycle-marker strip regex or outbox-append logic |
| 4 | N-state machines use a string/enum literal, not N−1 booleans |
| 5 | No in-tree writes to deprecated signal files; callers use canonical function |
| 6 | File-based state is read once and passed down; counters that survive restart are on disk |
| 7 | Operations with independent success signals have independent call sites |
| 8 | Safety nets name their primary counterpart in docstrings; log when they fire |
| 9 | New retry mechanisms use `.mission-retries.json` and increment `total_attempts` |
