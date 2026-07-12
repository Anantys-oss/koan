# Data Model — Mission Store

**Feature**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md) · **Contract**: [contracts/mission-store.md](./contracts/mission-store.md)

Defines the `Mission` domain object (the boundary value), the state machine, the SQLite schema, the store-initialized marker, and the one-time ingestion mapping. The **domain object is authoritative**; the SQLite schema is the in-tree adapter's realization of it (NFR-001 — storage types never cross the port).

## `Mission` (domain object)

| Field | Type | Semantics |
|---|---|---|
| `id` | `str` | Stable identity owned by the store (FR-011), independent of mission text. `sqlite`: the integer primary key rendered as text. Addresses the same logical mission across requeue/recovery/complexity-tag. |
| `text` | `str` | Full **verbatim** mission body — the payload fed to the agent. Untrusted DATA (Constitution V). Round-trips unchanged (multi-line `###` blocks, code fences, `[project:]`/`[complexity:]`/`[r:N]` tags, lifecycle markers). |
| `state` | `str` enum | `pending` \| `in_progress` \| `done` \| `failed`. |
| `project` | `str` | Resolved `[project:X]` tag, else `"default"`. The sentinel `all` passes through (resolved downstream, as today). |
| `sequence` | `int` | Explicit queue order (FR-010). Lower = earlier. Replaces implicit file line-order; `urgent`/requeue assign a front-of-queue value. |
| `complexity` | `str \| None` | `[complexity:X]` classifier, injected after capture without changing identity. |
| `queued_at` | `str \| None` | ISO-ish minute stamp (`%Y-%m-%dT%H:%M`), from the `⏳` marker on ingest. |
| `started_at` | `str \| None` | From `▶`. Set by `claim_next`. |
| `completed_at` | `str \| None` | From `✅`/`❌`. Set by `complete`/`fail`. |

The object is `frozen` — mutation happens through port verbs, which return fresh `Mission` values.

## State machine

```
                add_pending / add_pending_many
                              │
                              ▼
        ┌───────────────►  pending  ◄───────────── requeue (recover, stagnation)
        │                     │                            ▲
        │            claim_next (ATOMIC)                    │
        │                     ▼                             │
        │               in_progress ──────────────────────►┘
        │                  │      │
        │         complete │      │ fail
        │                  ▼      ▼
        └──── (new id)   done   failed
                          └──────┴──► prune_terminal (keep most-recent N)
```

- `pending → in_progress` happens **only** via atomic `claim_next` (FR-007) — never a read-then-mark pair.
- `in_progress` is exited **only** by `complete` / `fail` / `requeue` (Constitution VI: sanctioned exits).
- A re-run of an identical past mission enters as a **new** `pending` with a **new** `id`; it never reactivates a terminal row (invariant 6). This is the explicit fix for #2209's canonical-key collapse.

## SQLite schema (the in-tree adapter; salvaged + extended from #2209)

```sql
CREATE TABLE IF NOT EXISTS missions (
    id           INTEGER PRIMARY KEY,               -- store-owned identity (FR-011)
    text         TEXT    NOT NULL,                  -- verbatim payload
    state        TEXT    NOT NULL CHECK(state IN ('pending','in_progress','done','failed')),
    project      TEXT    NOT NULL DEFAULT 'default',
    sequence     INTEGER NOT NULL,                  -- explicit queue order (FR-010) — NEW vs #2209
    complexity   TEXT,
    queued_at    TEXT,
    started_at   TEXT,
    completed_at TEXT
    -- future columns land here as a one-line migration: failure_reason, result_json,
    -- token_cost, attempt_count (cf. #2285–#2287) — see "Schema evolution" below
);
CREATE INDEX IF NOT EXISTS idx_missions_state    ON missions(state);
CREATE INDEX IF NOT EXISTS idx_missions_project  ON missions(project);
CREATE INDEX IF NOT EXISTS idx_missions_pending  ON missions(state, sequence);  -- backs claim_next + FIFO
```

Notes:
- **`sequence` is the material addition over #2209.** #2209 relied on insertion `id` order with no explicit priority column, so it could not represent `--now`/requeue-to-front without diverging from the file. `sequence` makes queue order first-class.
- **Identity is the PK, not the text.** #2209 keyed on `canonical_mission_key(text)` (non-unique → collapse). Here the text is a column; identity is the PK — which is why duplicates/recurring missions no longer under-report (invariant 3).
- WAL + `busy_timeout` (salvaged) support the bridge+run two-writer case.
- **`claim_next` (sqlite)**: one transaction — `SELECT id FROM missions WHERE state='pending' [AND project IN (…)] ORDER BY sequence LIMIT 1`, then `UPDATE … SET state='in_progress', started_at=? WHERE id=? AND state='pending'`, retry on lost race. For a future multi-writer/networked adapter the same contract maps to `SELECT … FOR UPDATE SKIP LOCKED` — which is why atomicity lives in the *contract*, not the adapter (FR-007).

### Schema evolution (a core reason for dropping the file backend)

Adding mission metadata is the recurring pressure the file format handles badly. The three open API issues alone want more on the mission record:

- **#2285** — authoritative terminal status + failure reason,
- **#2286** — structured review result (not just a text line),
- **#2287** — per-mission token usage + cost.

In SQLite each is `ALTER TABLE missions ADD COLUMN …` plus one write-site and (optionally) an index — a contained migration. In `missions.md` the same field would have to be encoded into the line/block text, parsed back out by fragile regex, preserved by every writer, and rendered by every reader. Maintaining that parallel evolution for a second (file) backend is exactly the tax the SQLite-only decision removes.

## No in-tree file adapter (out-of-tree option only)

Kōan ships **no** `FileMissionStore`. A file-backed queue is an **out-of-tree** adapter that whoever wants it maintains, selected via `missions.backend: their.module:TheirFileStore`. Such an adapter would realize the port over `missions.md` using the retained parsing helpers in `app.missions` (`parse_sections`, `extract_next_pending`, lifecycle functions, `canonical_mission_key`), derive `sequence` from line order, and no-op `export_view`. The port + the abstract conformance suite are its template — but Kōan neither ships nor tests it as a product. (`app.missions` itself is retained in-tree only for `ingest_from_file` parse-on-import and `export_view` render-on-export, not on the hot path.)

## Additional `missions.md` sub-populations → sibling tables

`missions.md` today holds more than the four lifecycle states. Since it becomes a
read-only export, these move into **sibling tables in the same `missions.db`**
(decision: 2026-07-09) so the file is fully retired. They are managed by small
concrete helpers in the sqlite adapter (`CiQueueStore`, `IdeaStore`,
`QuarantineStore`), sharing the store's connection handling; they are not part of
the `MissionStore` lifecycle port (that stays mission-centric).

```sql
-- ## CI section: get_ci_items / add_ci_item / remove_ci_item / update_ci_item_attempt
CREATE TABLE IF NOT EXISTS ci_queue (
    id         INTEGER PRIMARY KEY,
    pr         TEXT NOT NULL,
    project    TEXT NOT NULL DEFAULT 'default',
    attempts   INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER,
    added_at   TEXT,
    sequence   INTEGER NOT NULL DEFAULT 0
);
-- Ideas section: parse_ideas / insert_idea / delete_idea / promote_idea
CREATE TABLE IF NOT EXISTS ideas (
    id        INTEGER PRIMARY KEY,
    text      TEXT NOT NULL,
    project   TEXT NOT NULL DEFAULT 'default',
    added_at  TEXT,
    sequence  INTEGER NOT NULL DEFAULT 0
);
-- authoritative store for quarantined missions; missions-quarantine.md is a
-- generated read-only export of this table (row-capped at _QUARANTINE_KEEP)
CREATE TABLE IF NOT EXISTS quarantine (
    id        INTEGER PRIMARY KEY,
    text      TEXT NOT NULL,
    reason    TEXT,
    source    TEXT,       -- origin label (e.g. "telegram", "github/@user")
    added_at  TEXT
);
```

### `mission_outcomes` (authoritative terminal-outcome log — issue #2285)

```sql
-- append-only audit trail of terminal Done/Failed transitions, keyed by
-- canonical_mission_key so it survives requeue/recovery and the per-write
-- reconcile_all DELETE+re-INSERT of the missions table. NOT part of any
-- missions.md export; read by the REST API (GET /v1/missions/{id}.outcome).
CREATE TABLE IF NOT EXISTS mission_outcomes (
    id              INTEGER PRIMARY KEY,
    key             TEXT NOT NULL,   -- canonical_mission_key(text)
    status          TEXT NOT NULL,   -- "done" | "failed"
    reason_category TEXT,            -- quota|timeout|tool_error|agent_error|cancelled|stagnation
    detail          TEXT,            -- free-text context, capped at 500 chars
    recorded_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_outcomes_key ON mission_outcomes(key);
```

Owned by `OutcomeStore` (alongside `QuarantineStore`). The agent loop writes one
row at the authoritative Done/Failed transition (`run._finalize_mission`);
`OutcomeStore.latest(text)` returns the newest row for a key. Row-capped at
`OutcomeStore.KEEP` (newest retained). Critically, because `reconcile_all`
DELETE+re-INSERTs the `missions` table on every write (churning mission-row
ids), terminal outcomes must live in a sibling table it never rebuilds — the
same durability guarantee `QuarantineStore` relies on. The REST API prefers this
log over the `missions.md` section scan for terminal status, eliminating the
absence-inference heuristic that mis-reported pruned/renamed/crashed missions as
`done`.

The one-time ingest imports the `## CI` and Ideas sections (and the separate
`missions-quarantine.md`) into these tables; `export_view` renders CI + Ideas back
into the read-only `missions.md`. Quarantine is authoritative in its table too: the
ongoing `quarantine_mission()` write path records to `QuarantineStore` and then
regenerates `missions-quarantine.md` as its own read-only export (it is not part of
the `missions.md` export). Exact column shapes are finalized against the current
`get_ci_items`/`parse_ideas`/`quarantine_mission` signatures during implementation.

## Store-initialized marker (gates one-time ingestion — FR-006)

"Uninitialized" (ingest) MUST be distinguished from "initialized but drained to zero" (do not ingest), or an operator who legitimately empties their queue would trigger a spurious re-import. Two candidate realizations (tasks.md picks one):

- **`meta` table** inside the store: `CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)` with `initialized_at`. Self-contained; travels with the DB. *(preferred)*
- **Sentinel file** `instance/.missions-store-initialized`. Simpler but a second artifact to keep atomic.

`is_initialized()` reads this marker; `ingest_from_file()` sets it. Ingestion is attempted at startup only when `not is_initialized()`.

## One-time ingestion mapping (`ingest_from_file` — salvaged from `migrate_md_to_sqlite`)

Per `parse_sections(missions.md)` entry:

| Source (missions.md) | → Mission field |
|---|---|
| verbatim `- …` line or `### …` block | `text` (full block preserved) |
| section (Pending/In Progress/Done/Failed, EN or FR) | `state` |
| `[project:X]` | `project` (else `default`) |
| `[complexity:X]` | `complexity` |
| line order within section | `sequence` (monotonic) |
| `⏳(…)` | `queued_at` |
| `▶(…)` | `started_at` |
| `✅(…)` / `❌(…)` | `completed_at` |
| entry with no `- `/`### ` key line | → `IngestReport.unparseable[]` (manual intervention; never dropped) |

## Reports

```python
@dataclass
class IngestReport:
    inserted: int
    by_state: dict[str, int]
    unparseable: list[str]         # up to first ~120 chars per entry, for the intervention list

@dataclass
class RecoverReport:
    requeued: int                  # in_progress → pending
    escalated: list[str]           # in_progress → failed (exceeded recovery budget)
```

## Relationship to `specs/components/core.md`

When PR 2 lands, the durable contract graduates into `specs/components/core.md`'s mission-queue section: the "Key types & functions" table replaces the `missions.py` lifecycle rows with `mission_store.get_mission_store()` + the `MissionStore` port; the Invariants section restates "single-writer-at-a-time" in terms of the port (per the Principle VI amendment) and records that `missions.md` is now a generated read-only export; "Known debt" drops the unbounded-Done note (bounded by `prune_terminal`). The speckit folder remains as planning history (RESOLVED `SPECS_DIR_COLLISION`).
