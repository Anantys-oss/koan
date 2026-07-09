# Contract — `MissionStore` port

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

The interface every mission-storage backend implements and every mission consumer depends on. It is a **cross-boundary contract**: the in-tree `sqlite` adapter and any out-of-tree adapter (a networked/relational store, or a file-based store for whoever prefers files) all implement it, validated by the shared conformance suite (FR-013). It speaks only in **domain terms** (NFR-001) — no `sqlite3.Row`, no SQL, no `missions.md` line strings cross it.

---

## Resolution (single read path — Constitution VI)

```python
# app/mission_store/__init__.py
def get_mission_store(instance: str | Path) -> MissionStore: ...
```

Resolves once at startup from the `missions.backend` config accessor:

| `missions.backend` | Resolves to |
|---|---|
| unset / `sqlite` | `SqliteMissionStore` (default, in-tree; requires the ratified Principle III amendment) |
| `module.path:ClassName` | an **out-of-tree** adapter — e.g. a networked/relational store, or a file-based store for whoever prefers files. `importlib.import_module(...).ClassName`. |

- Unknown-but-dotted → imported (FR-004). Import/resolution failure → **abort startup** with an actionable error (FR-005); never silent fallback.
- The active backend is logged at startup (like provider resolution). Immutable for the run.

---

## The `Mission` domain object

The value that crosses the boundary. See [data-model.md](../data-model.md) for field semantics.

```python
@dataclass(frozen=True)
class Mission:
    id: str            # stable identity owned by the store (FR-011), independent of text
    text: str          # full verbatim mission body (the agent payload) — untrusted DATA
    state: str         # "pending" | "in_progress" | "done" | "failed"
    project: str       # resolved project tag, or "default"
    sequence: int      # explicit queue order (FR-010); lower = earlier. Replaces file line-order.
    complexity: str | None
    queued_at:    str | None
    started_at:   str | None
    completed_at: str | None
```

---

## Operations

Grouped by concern. Every method is defined on the `MissionStore` ABC; the in-tree `sqlite` adapter implements all of them and passes the conformance suite, which is the portable definition for any out-of-tree adapter. The "Replaces (today)" column is the migration map from the current file world.

### Write / lifecycle

| Method | Contract | Replaces (today) |
|---|---|---|
| `add_pending(text, *, project="default", complexity=None, urgent=False) -> Mission` | Append a new pending mission; `urgent=True` puts it at the front of the queue (`--now`). Returns the created `Mission` (with assigned `id`/`sequence`). Dedup policy is the store's (e.g. the existing `is_duplicate_mission` rule for URL-bearing missions). | `utils.insert_pending_mission` |
| `add_pending_many(texts, ...) -> list[Mission]` | Atomic multi-insert preserving order. | `utils.insert_pending_missions` |
| `claim_next(*, projects=None) -> Mission | None` | **Atomic** (FR-007): select the earliest-`sequence` pending mission (optionally filtered to `projects`), transition it to `in_progress`, stamp `started_at`, and return it — as one indivisible operation. Two concurrent callers MUST NOT receive the same mission. Returns `None` if none pending. Absorbs `start_mission`'s stale-flush sanity. | `pick_mission` + `start_mission` (two steps today) |
| `complete(id) -> bool` | `in_progress → done`, stamp `completed_at`. Only sanctioned success exit. Returns whether a live row transitioned. | `complete_mission` |
| `fail(id) -> bool` | `in_progress → failed`, stamp `completed_at`. Only sanctioned failure exit. | `fail_mission` |
| `requeue(id) -> bool` | `in_progress → pending`, restore to front of queue (crash-recovery / stagnation requeue), without duplicating. | `requeue_mission` |
| `set_complexity(id, complexity) -> bool` | Tag an existing mission; MUST NOT create a second row/identity. | `tag_complexity_in_pending` |

### Read / query — *these back the visibility surfaces (User Story 3 / FR-015, FR-016)*

| Method | Contract | Replaces (today) |
|---|---|---|
| `count_by_state(state) -> int` | Count in `state`. Indexed/O(1). | `count_pending` (+ ad-hoc scans) |
| `counts() -> dict[str,int]` | All four state counts in one call (dashboard/`/status`/API). | scattered parses |
| `list_by_state(state, *, project=None, limit=None) -> list[Mission]` | Ordered by `sequence` (pending/in-progress) or recency (done/failed). Optional project filter + limit. **This is what a `/list <state>`, "show failed", or "show executed" command and the dashboard/API history views call.** | `parse_sections` consumers; today has **no** done/failed history command |
| `get(id) -> Mission | None` | Fetch one (detail view). | — |
| `peek_next(*, projects=None) -> Mission | None` | The mission `claim_next` *would* claim, read-only (preview; NOT a substitute for the atomic claim). | `pick_mission` (read-only part) |

### Maintenance / lifecycle-of-store

| Method | Contract | Replaces (today) |
|---|---|---|
| `prune_terminal(done_keep, failed_keep) -> int` | Cap done/failed history, keeping the most recent N per state; returns rows removed. | `enforce_size_bound` / history prune |
| `is_initialized() -> bool` | Whether the store has been set up + ingested (distinct from "has zero missions"). Gates one-time ingestion (FR-006). | n/a (new) |
| `ingest_from_file(missions_md_path) -> IngestReport` | One-time import of `missions.md`: preserve verbatim text, extract timestamps/tags, return `{inserted, by_state, unparseable[]}`; mark initialized. Called only when `not is_initialized()`. | `missions_migrate` / `migrate_md_to_sqlite` (salvaged) |
| `export_view(missions_md_path) -> None` | Render the **read-only** `missions.md` from the authoritative store (FR-008/009): honors bilingual headers + verbatim text; on-demand by default. Also the reversibility/backup path. | n/a (new; replaces the file *being* the store) |
| `recover_stale(...) -> RecoverReport` | Reconcile missions left `in_progress` by a crash (move back to pending / escalate to failed), matching `recover.py` semantics, as a store transaction. | `recover.recover_missions` |

### Introspection

| Method | Contract |
|---|---|
| `backend_name() -> str` | `"sqlite"` or an adapter-supplied label — for the startup log and `/status`. |

---

## Invariants (the conformance suite asserts these on every adapter)

1. **Exclusive authority.** Only this store is consulted for mission state; there is no second concurrent authority and no reconcile-on-divergence path (contrast #2209).
2. **Atomic claim.** `claim_next` is indivisible; concurrent callers never collide (FR-007). *Suite: N concurrent claimants over M pending → exactly M distinct missions claimed, no dupes, no drops.*
3. **Stable identity.** An `id` addresses the same logical mission across `requeue`/recovery/complexity-tag; a re-run of an identical past mission gets a *new* `id` (no collapse — FR-011).
4. **Order fidelity.** `sequence` reproduces file semantics: FIFO pick, `urgent`/requeue to front, stable across restarts (FR-010).
5. **Verbatim payload.** `text` round-trips unchanged (multi-line `###`, code fences, tags, lifecycle markers) — it is the agent payload and untrusted DATA (Constitution V).
6. **Terminal rows are never rewritten by a live transition** (a re-queued mission sharing text with an old done/failed entry must not flip it).
7. **Faults surface, they do not swallow-and-diverge.** Graceful degradation is not a port guarantee: the authoritative store raises real faults to the caller (the loop decides pause/retry). There is no file to silently diverge into (contrast the mirror). Backup/restore is the `export_view`/import path.
8. **Bilingual headers preserved** by `export_view` and by `ingest_from_file` (Constitution VI).
9. **Visibility parity.** Every mission category the file exposed (pending/in-progress/done/failed, per-project) is reachable via `list_by_state`/`counts`/`get` (FR-015) — the read surface is at least as capable as reading `missions.md`.

---

## What the port deliberately does NOT expose

- No `missions.md` path/line access, no `sqlite3` handle, no SQL — callers get `Mission` objects and the verbs above (NFR-001).
- No "read list then mark" primitive — claiming is atomic-only (prevents the two-process race by construction).
- No engine/dialect knobs — an out-of-tree adapter owns its own storage concerns (NFR-003).
