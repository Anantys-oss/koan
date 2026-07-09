# Feature Specification: Mission Store — SQLite by default, behind a `MissionStore` port

**Feature Branch**: `koan/mission-store-spec`

**Created**: 2026-07-09

**Status**: Draft

**Supersedes**: [#2209](https://github.com/Anantys-oss/koan/pull/2209) (SQLite mission *mirror*) — see "Why this replaces the mirror" below.

**Design issue**: [#2140](https://github.com/Anantys-oss/koan/issues/2140) (part of epic #2147)

**Input**: Operator + core-contributor intent — "Migrate mission state to a SQLite store **in one shot** (not a gradual mirror, not a permanent dual file/DB). Keep a `MissionStore` **port** so an alternative backend (a networked/relational store, or a file-based store for anyone who prefers files) can be selected by config without editing Kōan. Ship **only** the SQLite adapter in-tree — maintaining both a file and a DB backend forever is a tax, and evolving the schema (adding a column) is far easier in one SQLite place than in a parallel markdown format."

---

## Why this replaces the mirror (#2209)

PR #2209 kept `missions.md` as the source of truth and maintained `instance/missions.db` as a **best-effort mirror** written on every committed file transition. Investigation surfaced three structural problems that are properties of the *design*, not the code:

1. **No realized read payoff.** The stated ROI (constant-time counts replacing 15+ full-file regex scans) never materialized: exactly one read was routed through the DB — `iteration_manager._fallback_mission_extract`, a safety-net path that only runs when the normal picker returns empty — and even there the DB count is authoritative "only when `> 0`". Every other reader (dashboard, `/status`, REST API, `/brief`, `/list`, `recover`, `branch_limiter`) still parses the file.
2. **Net-negative on the hot path.** Every *write* transition gained 1–3 SQLite connections (each re-running WAL pragmas + schema) to accelerate a read that is almost never taken.
3. **A migration that cannot finish.** The freeze plan gated on "zero divergence for 2 releases," but `canonical_mission_key()` is deliberately non-unique, so duplicate/recurring missions collapse to one DB row (documented as a *permanent* known-divergence). The freeze gate is therefore unreachable — the design is pinned in dual-write mode forever.

This spec discards the mirror/dual-truth model entirely. Instead, mission state moves to a **single SQLite store**, reached through a `MissionStore` **port**, in a **one-shot cutover**. Exactly one store is authoritative (there is no second file authority to diverge from), which eliminates the mirror's whole problem class and dissolves the identity problem (the store keys on its own primary key, not on canonicalized text).

> The mirror PR's `missions_db.py` schema and `missions_migrate.py` ingestion are **salvaged** as the guts of the SQLite adapter and its one-time import; the mirror wiring (dual-write in `run.py`/`utils.py`, the ">0" read, the freeze plan, and the whole "keep the file as truth" premise) is discarded.

## Why SQLite-only in-tree (design decision)

The `MissionStore` port could host both a `file` and a `sqlite` adapter, but only **`sqlite` ships in-tree**, for reasons the core contributor made explicit:

- **One backend to maintain, not two.** A permanent file adapter + a two-backend test matrix is ongoing tax with no product payoff once SQLite is the committed direction.
- **Schema evolution is a one-place change.** Adding a column (e.g. token cost, risk score, attempt count — cf. issues #2285–#2287) is a small SQL migration. Doing the equivalent in a markdown format means threading a new field through fragile regex parsing, the file writer, and every reader.
- **The port keeps optionality without the tax.** Anyone who genuinely wants a file-backed queue can implement their own `MissionStore` and point config at it (§ User Story 3). Kōan does not carry that weight; the community can. The port + conformance suite are the template.

The cost of "one shot" — no zero-risk file-adapter intermediate, and an up-front constitutional amendment — is accepted deliberately (see the governance gate and `plan.md`).

---

## ⚠️ Governance gate (read first)

Making SQLite the **default source of truth** for missions conflicts head-on with **Constitution Principle III — "Local Files, Atomic State"** ("Runtime state lives in plain, inspectable files under `instance/` … **never in a database**"). Because SQLite is now the *default and only* in-tree store (not an opt-in behind a file default), this is not a small carve-out — it **redefines Principle III in a way that breaks prior compliance**, which the constitution's versioning policy classifies as **MAJOR** (candidate 1.0.0 → 2.0.0; the ratifier may argue MINOR, but the one-shot posture pushes it toward MAJOR). Per governance ("resolved by **amendment**, not by exception"), this amendment is a **blocking precondition** for the implementation PR and is itself human-ratified (Principle I). The amendment is drafted in [`plan.md` → Constitution Check](./plan.md#constitution-check).

Nothing is implemented until the amendment is accepted.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — One-shot migration to the SQLite store (Priority: P1)

On the first start after upgrade, Kōan performs a **one-time ingestion** of the existing `missions.md` into `instance/missions.db`, then treats the store as the sole source of truth. All mission reads/writes flow through the `MissionStore` port; counts and per-project/state queries become indexed. `missions.md` is thereafter a **generated read-only export**, not an input.

**Why this priority**: This is the feature — mission state becomes indexed, observable, and ACID, with the mirror's divergence eliminated by construction.

**Independent Test**: Seed a `missions.md` with N pending + assorted in-progress/done/failed (including multi-line `###` blocks, tags, French headers). Start Kōan; assert the store is populated once (with an unparseable-entry intervention list), a full lifecycle (queue → claim → complete/fail → requeue → prune) runs entirely against the store, and a second start does **not** re-ingest.

**Acceptance Scenarios**:

1. **Given** an existing `missions.md` and an uninitialized store, **When** Kōan first starts, **Then** it ingests the file exactly once, reports per-state counts + any unparseable entries, marks the store initialized, and treats it as authoritative.
2. **Given** an already-initialized store, **When** Kōan restarts, **Then** it does **not** re-ingest (gated on an explicit initialized marker, not a row count).
3. **Given** the store is authoritative, **When** any migrated consumer asks for a pending count or per-project listing, **Then** the answer comes from an indexed query, not a file parse.
4. **Given** a mission lifecycle runs, **When** it completes, **Then** the observable outcomes (which mission is picked, ordering, terminal states, history pruning) match pre-migration behavior — the store change is behavior-preserving for the queue semantics.

---

### User Story 2 — Human-readable export, on demand (Priority: P1)

`missions.md` remains available as a **read-only** artifact so operators keep the "read the queue as text" habit, and as a backup / reversibility path. It is produced by an explicit human action (an `export` command); it is never an input and edits to it are ignored.

**Why this priority**: Preserves human visibility and a non-destructive escape hatch (export the DB back to a runnable file) without reintroducing a second authority.

**Independent Test**: With the SQLite store authoritative, run the export command; assert a fresh, runnable `missions.md` matches the store (verbatim text, bilingual headers, ordering). Hand-edit that file; drive a transition; assert the edit had no effect on the store (read-only) and the next export overwrites the hand-edit.

**Acceptance Scenarios**:

1. **Given** the SQLite store, **When** the operator runs the export command, **Then** a read-only `missions.md` is written from the authoritative store and is itself a valid ingestion input.
2. **Given** a human edits the exported `missions.md`, **When** the loop runs, **Then** the edit is ignored (the file is not read as truth) — mission changes must go through the port via Telegram/dashboard/API/skills.
3. *(Optional)* **Given** `missions.export: continuous`, **When** a transition commits, **Then** the export is refreshed automatically (throttled); default is `on_demand`.

---

### User Story 3 — Full queue visibility via commands, not the file (Priority: P1)

Everything an operator could previously learn by reading `missions.md` — the pending queue, what is in progress, what **executed** (done), and what **failed** (with reason) — MUST be visible through commands and UI instead: chat skills, the web dashboard, and the REST API, all reading through the port. Removing the file as a live artifact MUST NOT reduce operator visibility.

**Why this priority**: This is a **migration precondition**, not a follow-up. The file is currently the only window into done/failed *history* — `/list` deliberately shows just pending + in-progress, and the REST API is pending-centric. Cutting the file without an equivalent command surface is a UX regression, so it gates the one-shot PR.

**Independent Test**: With the SQLite store authoritative, assert an operator can — via chat commands, the dashboard, and the API — list pending, list in-progress, list **executed (done)**, and list **failed** missions, each scoped by project, and that every view is served by a port read (`list_by_state`/`counts`), not a file parse.

**Acceptance Scenarios**:

1. **Given** the store is authoritative, **When** an operator asks to see failed (or executed) missions, **Then** a command returns them — with failure reason / timestamps where available — matching or exceeding what `missions.md`'s Failed/Done sections showed.
2. **Given** the existing surfaces (`/list`, `/status`, `/brief`, `/report`, dashboard, REST API), **When** the migration lands, **Then** each is backed by the port and, collectively, they cover all four states + per-project scoping — extending `/list`'s state filter and adding done/failed history where it is missing today.
3. **Given** a mission that failed, **When** it is viewed, **Then** its terminal status + reason are available (dovetails with #2285–#2287 — authoritative terminal status, failure reason, and structured result on the record become natural store columns).

---

### User Story 4 — Someone who wants files ships their own backend (Priority: P2)

An operator who dislikes SQLite (or wants a different store) provides their own `MissionStore` implementation — e.g. a file-based adapter — in an out-of-tree package, and points `missions.backend` at a dotted import path (or an entry point). Kōan loads and uses it **without any change to Kōan's code** and without Kōan depending on it.

**Why this priority**: This is what makes "SQLite-only in-tree" acceptable — the port keeps the door open for alternatives (a file store, a relational/networked store) that their authors maintain. Same extensibility grain Kōan already uses for CLI providers, bridges, and hooks.

**Independent Test**: Register an in-repo in-memory (or file) test-double `MissionStore` via a dotted path in config; assert Kōan resolves and uses it and that it passes the shared conformance suite (FR-013). No Kōan source file is edited to introduce it.

**Acceptance Scenarios**:

1. **Given** `missions.backend: some.module:SomeStore`, **When** Kōan starts, **Then** it imports and uses that class through the port; a failed import **aborts startup** with a clear, actionable error (no silent fallback that would mask a misconfiguration).
2. **Given** any conforming backend, **When** the shared conformance suite runs against it, **Then** it passes — the suite is the portable, executable definition of "a valid backend."

---

### Edge Cases

- **Empty vs. absent store**: "uninitialized" (ingest) is distinguished from "initialized but drained to zero" (do not ingest) via an explicit initialized marker, not a row count — otherwise an operator who legitimately empties their queue would trigger a spurious re-ingest.
- **Re-ingesting the exported file**: switching to an out-of-tree backend, or restoring from a `missions.md` backup, is a deliberate operator action (import), never an automatic round-trip — this "no silent round-trip" rule is what keeps the mirror's divergence class dead.
- **Concurrent access (bridge + run loop)**: both processes reach the store through the port. The store MUST make "claim the next pending mission" **atomic** (FR-007) so the two processes never claim the same one — the port exposes a single `claim_next()`, never a read-then-mark race.
- **Store unavailable/corrupt at runtime**: an authoritative store that errors is a real fault, not something to swallow. The port surfaces it and the loop's existing error handling decides (pause/retry). Unlike the mirror, there is no file to silently diverge into. (Backup/restore is the `export`/import path.)
- **Unparseable file entries during ingestion**: reported in an intervention list (never silently dropped), reusing #2209's `migrate_md_to_sqlite` behavior.
- **Rich/bilingual text**: ingestion and export preserve full verbatim mission text (multi-line `###` blocks, code fences, `[project:]`/`[complexity:]`/`[r:N]` tags, lifecycle markers) and bilingual section headers — the text is the agent payload and untrusted DATA.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Kōan MUST expose a `MissionStore` **port** through which *all* mission reads and mutations flow. The operation set is the contract in [`contracts/mission-store.md`](./contracts/mission-store.md).
- **FR-002**: Kōan MUST ship exactly one in-tree adapter — `sqlite` — and MUST make it the default. No file adapter is shipped or maintained in-tree.
- **FR-003**: The active backend MUST be selected by a single config accessor reading `missions.backend` (`config.py`), resolved at startup, immutable for the run, and logged at startup (mirroring provider resolution). Default is `sqlite`. One read path (Constitution VI).
- **FR-004**: When `missions.backend` is not the known in-tree name (`sqlite`), Kōan MUST treat it as a dotted import path (`module:Class`) and load that class as the backend. Kōan MUST NOT enumerate a hardcoded list of external backends anywhere (mechanism, not enumeration).
- **FR-005**: A failed backend import/resolution MUST abort startup with a clear, actionable error naming the offending value — never a silent fallback.
- **FR-006**: On first start with an **uninitialized** store, Kōan MUST perform a one-time ingestion from `missions.md`, produce a per-state count report and an **unparseable-entry intervention list**, and mark the store initialized. Ingestion MUST NOT run again while the store remains initialized.
- **FR-007**: The port MUST expose an **atomic** "claim the next pending mission in queue order and mark it in-progress" operation (`claim_next()`). A store shared by more than one process MUST guarantee two claimants never receive the same mission. The interface MUST NOT require a read-list-then-separately-mark sequence.
- **FR-008**: `missions.md` MUST become a **generated read-only export**, never an input. Agents and code MUST NOT treat it as writable; human queue changes (add/edit/reorder/delete) route through the port via the existing Telegram/dashboard/API/skill entry points, which mutate the store.
- **FR-009**: An **export command** (CLI/skill) MUST write a fresh, runnable `missions.md` from the authoritative store on demand (the human-visibility + reversibility path). Optionally, `missions.export: continuous` refreshes it per-transition (throttled); default `on_demand`.
- **FR-010**: Queue **priority/order** MUST be modeled explicitly in the store (a sequence/priority column), not implicit line-order — so `--now`, `/priority`, requeue-to-top, and FIFO picking behave identically to today.
- **FR-011**: Mission **identity** MUST be a stable primary key owned by the store, independent of mission text — eliminating the canonical-key-collapse divergence documented in #2209.
- **FR-012**: The one-time ingestion MUST preserve each mission's full verbatim text and extract lifecycle timestamps/tags where present, reporting anything it cannot parse (reusing #2209's `migrate_md_to_sqlite` semantics).
- **FR-013**: Kōan MUST ship an **abstract conformance test suite** that any `MissionStore` implementation (the in-tree `sqlite` adapter or any out-of-tree one) can run against itself. The suite is the portable, executable definition of the contract; the `sqlite` adapter MUST pass it.
- **FR-014**: No feature artifact (code, comments, docs, tests, config examples, commit messages) may reveal or presuppose any specific out-of-tree backend, deployment topology, or private product — the seam is documented in **generic** extensibility terms only (Constitution V; CLAUDE.md leak rules).
- **FR-015**: Every category of mission state formerly visible in `missions.md` — pending, in-progress, executed (done), failed (with reason where available), scoped by project — MUST be accessible through chat commands, the web dashboard, and the REST API. The one-shot PR MUST close any gap so the migration is not a visibility regression — notably a first-class way to view **done** and **failed** history (which `/list` does not provide today) and a REST read surface beyond the pending queue.
- **FR-016**: All mission read surfaces (skills, dashboard, API) MUST obtain state through the `MissionStore` port (`count_by_state`/`list_by_state`/`counts`/`get`), never by parsing a file. New or extended read commands slot into the existing `missions` help group; `/list` gains an optional state filter while keeping its current pending+in-progress default.

### Non-Functional / Design Requirements

- **NFR-001 (Interface in domain terms)**: The port MUST speak in domain objects (a `Mission` value with id/text/state/project/timestamps/sequence) and verbs (`add_pending`, `claim_next`, `transition`, `count_by_state`, `list_by_state`, `prune_terminal`, …). It MUST NOT leak storage types (`sqlite3.Row`, SQL, file line strings) across the boundary — otherwise an out-of-tree backend cannot implement it.
- **NFR-002 (Versioned contract)**: The port is a cross-boundary contract. Changes to it MUST update this spec + the contract doc, MUST be treated as potentially breaking for out-of-tree implementers, and are validated by the conformance suite (FR-013).
- **NFR-003 (YAGNI)**: Only the `sqlite` adapter ships in-tree. No relational/networked/shared backend, no file adapter, and no engine-portability/ORM layer is built in Kōan. The port + dotted-path resolution is the entire extensibility investment (Constitution VII).
- **NFR-004 (One-shot risk is bounded by tests)**: Because there is no behavior-preserving file-adapter intermediate, the cutover's safety rests on (a) the conformance suite, (b) a thorough ingest/migration test over realistic `missions.md` fixtures, and (c) an in-*test* reference adapter proving the callsite refactor is store-agnostic. These are mandatory, not optional.

### Key Entities

- **`MissionStore` (port)**: the single authority + single access path for mission state (Constitution VI). One implementation is live per run.
- **`Mission` (domain object)**: id, verbatim text, state (`pending`/`in_progress`/`done`/`failed`), project, sequence/priority, lifecycle timestamps, complexity tag. The unit crossing the port boundary. Full shape in [`data-model.md`](./data-model.md).
- **Backend resolution**: `config.py` accessor → `sqlite` (default) | dotted-path. One read path.
- **Store initialization marker**: the explicit signal distinguishing "never ingested" from "ingested and later drained," gating the one-time import (FR-006).
- **Exported view**: the read-only `missions.md` produced on demand from the store. A projection, never an input.

## Success Criteria *(mandatory)*

- **SC-001**: After the one-shot migration, every existing mission from `missions.md` is present in the store with correct state/project/timestamps (or surfaced in the intervention list); the full lifecycle produces the same *observable* queue behavior (pick order, terminal states, pruning) as pre-migration `main`.
- **SC-002**: Pending counts and per-project/state listings are answered by an indexed query (no full-file parse) for **every** migrated consumer — dashboard, `/status`, REST API, picker, `/brief`, `/list` — not a single fallback path.
- **SC-003**: At no point are there two authorities; there is no dual-write, no reconcile-on-divergence, and no "authoritative only when > 0" logic anywhere in the design.
- **SC-004**: A first start ingests `missions.md` exactly once (with an intervention list for unparseable entries); a second start ingests zero.
- **SC-005**: Two concurrent processes issuing `claim_next()` against one store never receive the same mission (verified by a concurrency test).
- **SC-006**: An in-repo test-double backend, referenced only by config, is loaded and passes the conformance suite — with zero edits to Kōan source — demonstrating the out-of-tree seam.
- **SC-007**: The exported `missions.md` is a faithful, runnable projection of the store and is ignored as an input (hand-edits do not affect the store).
- **SC-008**: No public artifact references any out-of-tree backend, topology, or product; the leak check (`.leak-patterns` diff filter) is clean.
- **SC-009**: Post-migration, an operator can view pending, in-progress, done, and failed missions (per-project) via commands/UI with parity-or-better vs. reading `missions.md`; every such view is served by a port read, not a file parse.

## Assumptions

- **Single authoritative store, no dual-truth.** Exactly one store is authoritative per run. The mirror's dual-write/dual-read model is explicitly rejected.
- **SQLite is the default and only in-tree backend.** Kōan becomes a SQLite-backed daemon for missions out of the box. A file-backed (or other) store is an **out-of-tree** option, maintained by whoever wants it, loaded via config — not shipped by Kōan.
- **`missions.md` survives only as a generated read-only export**, produced on demand (human action) for visibility and reversibility. It is never an input; hand-editing is not a supported affordance in the shipped configuration (operators who require it ship their own file backend).
- **The constitutional amendment is a blocking precondition**, and is likely MAJOR (redefining Principle III), not a minor opt-in carve-out — this is the accepted cost of one-shot SQLite-by-default.
- **One-shot has no behavior-preserving intermediate.** Routing callsites through the port and switching to SQLite happen together; safety rests on the conformance suite + migration tests + an in-test reference adapter (NFR-004). This risk is accepted for the maintenance simplicity of a single backend.
- **The out-of-tree seam is generic extensibility**, justified exactly like pluggable CLI providers/bridges/hooks — it presupposes no particular external backend and names none.
- **Atomic `claim_next` is designed now** even though a single-process SQLite store barely needs it, because retrofitting atomicity into the port later is a breaking change for any implementer.
- **Salvage, don't rebuild.** #2209's `missions_db.py` schema and `missions_migrate.py` become the SQLite adapter's storage + one-time ingestion; only the mirror wiring and the "file-as-truth" premise are discarded.
- **Specs-directory layout**: this feature's speckit trio lives at `specs/004-mission-store/`, coexisting with `specs/components/` and `specs/skills/` (RESOLVED `SPECS_DIR_COLLISION`). The durable contract graduates into `specs/components/core.md` (mission-queue section) when the code ships.

## Out of Scope

- A shipped file adapter, or any relational/networked backend, inside Kōan (NFR-003). Only the port + `sqlite` + dotted-path resolution ship here.
- An engine-portability/dialect abstraction (e.g. an ORM). An out-of-tree backend owns its own storage concerns.
- Migrating memory/journal/outbox to stores (other epic #2147 items) — this spec is missions only.
- Auto-merge / autonomy / mission-selection-policy changes. Mission *storage* only.
