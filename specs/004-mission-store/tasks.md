# Tasks: Mission Store — SQLite via `MissionStore` port

**Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md) · **Contract**: [contracts/mission-store.md](./contracts/mission-store.md) · **Data model**: [data-model.md](./data-model.md)

**Branch**: `koan/mission-store-impl` (off the merged `main` carrying spec #2295 + constitution v2.0.0 #2296).

One-shot cutover: SQLite is the default and only in-tree backend; `missions.md` becomes a generated read-only export. `[P]` = parallelizable (independent files). Task IDs are stable references.

## Phase 1 — Foundation (port + config + resolver)

- **T001** `koan/app/mission_store/base.py` — `Mission` frozen dataclass (id/text/state/project/sequence/complexity/queued_at/started_at/completed_at), `IngestReport`, `RecoverReport`, and the `MissionStore` ABC with every method from `contracts/mission-store.md` (write/lifecycle, read/query, maintenance, introspection). Docstrings quote the contract's invariants.
- **T002** `koan/app/config.py` — add `get_mission_backend()` (default `"sqlite"`) and `get_mission_export_mode()` (default `"on_demand"`), mirroring `_missions_section()`/`_safe_int`. [P]
- **T003** `koan/app/mission_store/resolver.py` + `__init__.py` — `get_mission_store(instance)`: resolve `sqlite` → `SqliteMissionStore`; a dotted `module:Class` → import it; unknown/failed import → raise a clear startup error. Log the active backend once. Cache per-instance.
- **T004** `koan/tests/test_mission_store_conformance.py` — abstract conformance suite parametrized over backends, incl. an in-repo **in-memory reference adapter** (proves the out-of-tree seam + gives callsite refactors a store-agnostic double). Asserts contract invariants 1–9.

## Phase 2 — SQLite adapter

- **T005** `koan/app/mission_store/sqlite_store.py` — `SqliteMissionStore` implementing `MissionStore`. Schema from `data-model.md` (WAL, `state` CHECK, `sequence`, indexes incl. `(state,sequence)`). Salvage the graceful-degradation + connection handling patterns from #2209's `missions_db.py` (all methods catch `sqlite3.DatabaseError`), but key on the integer PK, add `sequence`, and make faults surface per invariant 7 (no swallow-and-diverge for the authoritative store).
- **T006** `SqliteMissionStore.claim_next` — atomic `BEGIN IMMEDIATE` + earliest-`sequence` pending select + guarded `UPDATE … WHERE id=? AND state='pending'`, retry on lost race. Absorbs `start_mission` stale-flush sanity.
- **T007** `SqliteMissionStore.ingest_from_file` + `is_initialized` — parse `missions.md` via `app.missions.parse_sections`, map per `data-model.md` (verbatim text, project/complexity tags, lifecycle timestamps, monotonic sequence), collect unparseable → `IngestReport`; set the `meta.initialized_at` marker. Only ingest when `not is_initialized()`.
- **T008** `SqliteMissionStore.export_view` — render read-only `missions.md` from the store: bilingual-capable headers, verbatim text, ordering; atomic write. `prune_terminal`, `recover_stale` complete the adapter.
- **T009** Conformance + adapter tests green: `test_mission_store_conformance.py` (both backends), `test_mission_store_ingest.py` (realistic fixtures incl. `###` blocks, code fences, French headers, malformed), `test_mission_store_claim.py` (concurrency: N claimants / M pending → M distinct). `make lint` clean.

## Phase 3 — Startup wiring

- **T010** Boot path (`run.py` startup / `recover.py`): resolve the store, run one-time `ingest_from_file` when uninitialized, log the active backend + ingest report (+ intervention list). Replace `recover.recover_missions` file-rewrite with `store.recover_stale`.
- **T011** Export scheduling: after each finalize (and/or throttled), if `export_mode == continuous` call `export_view`; always available on demand.

## Phase 4 — Callsite migration (route every mission read/write through the port)

*Driven by the callsite map (see PR description). Each subtask: replace the direct `app.missions`/file access with a `get_mission_store()` call.*

- **T012** Writes — lifecycle: `run.py` `_start_mission_in_file`→`claim_next`/`start`, `_update_mission_in_file`→`complete`/`fail`, `_requeue_mission_in_file`→`requeue`, `_prune_missions_history`→`prune_terminal`.
- **T013** Writes — inserts: `utils.insert_pending_mission(s)`, `command_handlers.py`, `github_command_handler.py` (4 sites), `dashboard/missions.py`, `skills/core/idea` → `add_pending`/`add_pending_many`.
- **T014** Reads — picker: `pick_mission.py` + `iteration_manager._fallback_mission_extract`/`_pick_mission` → `claim_next`/`peek_next`/`count_by_state`.
- **T015** Reads — surfaces: `dashboard_service/missions.py`, `dashboard/missions.py`, `api/routes_missions.py` + `api/mission_index.py`, `routes_status.py`, `skills/core/{list,status,brief,report,mission,cancel,abort,priority,journal}` → port reads.

## Phase 5 — Export command + visibility (User Story 3 / FR-015, FR-016)

- **T016** Extend `/list` with an optional state filter (`/list done|failed|all`), default unchanged (pending+in_progress); via `list_by_state`. Add done/failed history to dashboard + REST API. **Do NOT reuse `/done`** (existing `status`-group PR command); if a dedicated command is wanted use a free name (`/failed`/`/history`) verified against the registry.
- **T017** Export command (CLI `make`/subcommand or skill) → `export_view`.

## Phase 6 — Docs/specs reconciliation (the deferred amendment items)

- **T018** Update `specs/components/core.md` (mission-queue contract → `MissionStore`; single-writer invariant; `missions.md` = export), `CLAUDE.md` + `koan/app/CLAUDE.md` (missions.md = read-only export), `docs/architecture/{overview,shared-state,mission-lifecycle}.md`. Run `/brain sync`. Retire/relocate `app.missions` parse/lifecycle usage docs.
- **T019** Full suite green (`KOAN_ROOT=/tmp/test-koan make test`) + `make lint`; update `docs/users/{user-manual,skills}.md` for the `/list` filter + any new command; leak check clean.

## Scope amendment (2026-07-09, from the callsite map)

The migration surface is **~40 files** (see the PR description's migration map), and
`missions.md` holds three sub-populations beyond the four lifecycle states —
handled as **sibling tables in `missions.db`** (`ci_queue`, `ideas`,
`quarantine`; see `data-model.md`). Delivery: **one branch (`koan/mission-store-impl`),
one commit per step** below, single PR.

- **S1 ✅** — Foundation: port + `SqliteMissionStore` + config + resolver + conformance/ingest tests.
- **S2 ✅** — Sibling stores: `CiQueueStore`, `IdeaStore`, `QuarantineStore` + their ingest/export + tests.
- **S3 ✅** — Startup one-time ingest (additive, gated on `is_initialized`).
- **S4 ✅** — Reader migration begins: `reconcile_from_file` helper + first readers (counts).
- **S5 ✅** — Read callsites migrated via `read_sections`/`render_mission_line`/`read_content` (skills, dashboard, API, app-level counts).
- **S6** — Folded into **S8** (rationale below).
- **S7 ✅** — Visibility: `/list [pending|in_progress|done|failed|all]` state filter (done/failed history that used to require reading missions.md).
- **S8 ✅** — The flip. Store is authoritative; `missions.md` is a generated read-only
  export. Chokepoint `utils._locked_missions_rw` round-trips through the store
  (render → transform → `reconcile_all` → export); readers read the store directly;
  one-time `ensure_store_synced` (persisted `s8_synced` marker); CI/Ideas migrated
  via the round-trip; `startup_manager` prune re-syncs the store. Fidelity fixes:
  `### ` block terminator, `insert_mission` `- ` normalization. Verified green across
  ~3,900 mission-touching tests (batches; full suite is CI's job).
- **S9 ✅** — Docs/specs reconciliation: durable contract graduated into
  `specs/components/core.md`; `CLAUDE.md` + `koan/app/CLAUDE.md`,
  `docs/architecture/{mission-lifecycle,shared-state}.md` updated; constitution
  Sync Impact Report marked done.

  _Remaining before merge/deploy: a full-suite CI run (times out locally), and the
  optional S7 visibility command (`/list done|failed`)._

Traps to preserve (from the map): out-of-lock read-then-transactional-write (TOCTOU) in `ci_queue_runner`/parallel dispatch; startup unlocked writes in `startup_manager.py`; direct `read_text`/`atomic_write` on the missions path in `startup_manager.py`/`recover.py`; pure text helpers (`extract_project_tag`, `canonical_mission_key`, …) are NOT store access and stay as-is.

## Notes

- `app.missions` is **retained** but demoted to parse-on-ingest / render-on-export only (not the hot path).
- No in-tree `FileMissionStore`; the in-memory reference adapter lives in tests only.
- `#2209`'s `missions_db.py`/`missions_migrate.py` were never merged — their logic is reconstructed here from the PR diff, not imported.
