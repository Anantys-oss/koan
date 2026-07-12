# Implementation Plan: Mission Store — SQLite by default, behind a `MissionStore` port

**Branch**: `koan/mission-store-spec` | **Date**: 2026-07-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-mission-store/spec.md`

## Summary

Migrate mission state to a single **SQLite store**, reached through a `MissionStore` **port**, in a **one-shot cutover**. Ship only the `sqlite` adapter in-tree and make it the default; the port exists so an alternative backend (a networked/relational store, or a file-based store for anyone who prefers files) can be selected by config — `missions.backend: module:Class` resolves an out-of-tree adapter without editing Kōan. Exactly one store is authoritative per run (no mirror, no dual-truth), which deletes the #2209 divergence class and dissolves the identity problem (store-owned primary key). `missions.md` survives only as a generated read-only export (on demand). Because SQLite-by-default conflicts with **Constitution Principle III**, a **blocking, likely-MAJOR amendment** must be ratified first.

## Sequencing (two PRs; this is PR 1 of 2, design only)

| PR | Contents | Gate |
|---|---|---|
| **1 (this)** | This spec set (`spec.md`, `plan.md`, `data-model.md`, `contracts/mission-store.md`). No code. | — |
| **2** | The **one shot**: ratified Principle III/VI amendment + `MissionStore` port + `SqliteMissionStore` (salvaged from #2209) + the abstract conformance suite + config accessor & dotted-path resolution + one-time ingestion + read-only export + `export` command; route **all ~55 mission callsites** through the port. | Blocked on the amendment |

> **Why not three PRs?** The earlier plan kept a `FileMissionStore` so callsites could migrate through a byte-for-byte no-behavior-change step first. The core contributor chose SQLite-only in-tree (one backend to maintain; schema evolution is a one-place SQL change). That removes the file adapter, so "route through the port" and "switch to SQLite" are now inseparable — a genuine **one-shot**. The lost no-change intermediate is replaced by **NFR-004**'s safety net: the conformance suite, a thorough ingest/migration test over realistic fixtures, and an **in-test** reference adapter that proves the callsite refactor is store-agnostic.

## Technical Context

**Language/Version**: Python 3.11+ (no post-3.11 syntax/stdlib — constitution constraint).

**Primary Dependencies**: Existing stack only, no new third-party deps. Salvages `app.missions_db` (schema, WAL, primitives) and `app.missions_migrate` (dry-run + intervention list) from #2209 as the adapter's internals. Reuses `app.config` (accessor pattern), `app.utils` (atomic writes for the export), and the existing `app.missions` parsing **only inside `ingest_from_file`/`export_view`** (parse-on-import, render-on-export) — not on the hot path.

**Storage**: `instance/missions.db` (WAL) becomes the source of truth for missions — this is exactly what the Principle III amendment authorizes. `missions.md` becomes a generated read-only export.

**Testing**: `pytest` with `KOAN_ROOT=/tmp/test-koan`; never call the Claude subprocess. Mandatory (NFR-004): an **abstract conformance suite** parametrized over backends; an **ingest/migration** test over realistic `missions.md` fixtures (multi-line `###`, code fences, tags, French headers, malformed entries); a **`claim_next` concurrency** test; an **in-test reference adapter** exercising the out-of-tree seam. Test behavior, not source text.

**Target Platform**: Same Kōan daemon host (macOS/Linux).

**Project Type**: Library/daemon extension — one new port + one in-tree adapter + a resolver accessor + one-time migration + export.

**Performance Goals**: Turn O(file) parses into indexed queries for counts and per-project/state listings across all consumers. Removing the file as a live artifact also removes per-transition full-file rewrites; export is on-demand.

**Constraints**:
- Constitution III (Local Files) — **hard conflict**; see Constitution Check + amendment (blocking).
- Constitution VI (Single Writer, Single Read Path) — the port *strengthens* this; one accessor for backend selection.
- Constitution VII (Simplicity/YAGNI) — one in-tree adapter; the out-of-tree seam is generic; nothing speculative built.
- Constitution V + CLAUDE.md — no private/product/topology identifiers in any artifact.

**Scale/Scope**: PR 2 touches ~55 mission callsites (migrated to the port), adds the `mission_store/` package (port + sqlite adapter + resolver), the config accessors, ingestion + export, and the test suite; plus the amendment and the `specs/components/core.md` update. Out of scope: any file/relational/networked backend in-tree, an ORM/dialect layer, and other epic #2147 artifacts (memory/journal/outbox).

## Constitution Check

*GATE: Must pass (or record a justified, ratified deviation) before implementation.* Checked against `.specify/memory/constitution.md` v1.0.0.

| # | Principle | Verdict | Notes |
|---|---|---|---|
| I | Human Authority | ✅ PASS | Draft PRs, `koan/*` branches, never merge. Humans decide via Telegram/dashboard/API; the amendment itself is human-ratified. |
| II | Specs Are the Source of Truth | ✅ PASS | This spec set precedes code; the durable contract graduates into `specs/components/core.md` when PR 2 lands. |
| III | **Local Files, Atomic State** | 🔴 **HARD CONFLICT → BLOCKING AMENDMENT** | III says state lives in files "**never in a database**." SQLite-by-default **redefines** this (not an opt-in carve-out), so prior compliance no longer holds → **candidate MAJOR** amendment. Must be ratified before PR 2. |
| IV | Provider Isolation | ✅ PASS | Storage is pluggable behind the port exactly as CLI providers/bridges are; the loop never branches on which store is active. |
| V | Untrusted Inputs, Audited Outputs | ✅ PASS | Mission text stays untrusted DATA across the port (NFR-001). No private/product identifiers (FR-014). |
| VI | **Single Writer, Single Read Path** | ✅ PASS (re-specified) | The port becomes *the* single authority + access path — a stronger VI. VI's wording (which names `missions.md` lifecycle functions) is restated in terms of the port by the amendment. |
| VII | Simplicity & Honest Reporting | ✅ PASS | One in-tree backend (simpler than two); rejects the mirror's dual-truth complexity; documents the one-shot risk honestly (NFR-004). |

### Proposed constitutional amendment (candidate MAJOR, 1.0.0 → 2.0.0)

To be ratified in PR 2's branch (human-reviewed, per Principle I + the amendment procedure). This is a **larger** change than the opt-in version would have been, because SQLite is the default, not an opt-in behind a file default:

- **Principle III (amended)** — replace "never in a database" with: *"Mission state is authoritative in `instance/missions.db` (SQLite) reached through the `MissionStore` port; `missions.md` is a generated read-only export. All **other** runtime state (config, outbox, journal, trackers, soul, memory JSONL truth) remains in plain files; a database used as a **derived index** over a file source of truth (e.g. `memory_db` FTS5) remains permitted. Any additional database-authoritative state requires a further amendment."* This narrowly scopes the DB authority to missions and preserves the file-first default everywhere else, while retroactively clarifying the `memory_db` precedent.
- **Principle VI (amended)** — restate the mission clause: *"Mission state has exactly one authority — the active `MissionStore` implementation — reached through one port. Agents/code MUST NOT mutate mission state outside the port. Bilingual-header preservation applies to the `missions.md` export; the single-config-read-path clause is unchanged."*
- **Dependent artifacts to reconcile in the same branch** (amendment procedure step 3): `CLAUDE.md` (two-process/shared-state notes, "missions.md is the task queue" → "generated export"), `specs/components/core.md` (mission-queue contract + invariants), `docs/architecture/{overview,shared-state,mission-lifecycle}.md`, `docs/architecture/missions.md`.

**Gate result**: **BLOCKED pending ratification.** This is surfaced honestly, not shipped as a silent exception (governance: "resolved by amendment — not by exception"). PR 1 (this spec) is unaffected; it *is* the proposal.

## Project Structure

### Documentation (this feature)

```text
specs/004-mission-store/
├── spec.md                     # why/requirements/success criteria + governance gate
├── plan.md                     # This file
├── data-model.md               # Mission domain object, states, sqlite schema, ingestion
└── contracts/
    └── mission-store.md        # The MissionStore port — the operations
```

### Source Code (repository root) — *PR 2, not this PR*

```text
koan/app/
├── mission_store/                  # NEW: the port + the in-tree sqlite adapter + resolver
│   ├── __init__.py                 #   get_mission_store(instance) -> MissionStore  (single read path)
│   ├── base.py                     #   MissionStore ABC + Mission dataclass (domain terms only)
│   ├── sqlite_store.py             #   SqliteMissionStore — salvaged missions_db internals + sequence/identity
│   └── resolver.py                 #   sqlite | dotted-path resolution; startup log; abort-on-bad-import
├── config.py                       #   + get_mission_backend() / get_mission_export_mode() accessors
├── missions.py                     #   retained ONLY for parse-on-import / render-on-export (not the hot path)
├── missions_db.py                  #   folded into sqlite_store.py (salvaged)
└── missions_migrate.py             #   folded into ingest_from_file (salvaged)
koan/tests/
├── test_mission_store_conformance.py   # abstract suite parametrized over backends (incl. in-test reference adapter)
├── test_mission_store_ingest.py        # one-shot migration over realistic fixtures
└── test_mission_store_claim.py         # claim_next concurrency
```

**Structure Decision**: A new `koan/app/mission_store/` package (mirrors `provider/` and `issue_tracker/`) so the port, the sqlite adapter, and the resolver live together and the ~55 callsites import one thing: `get_mission_store()`. `app.missions` is demoted from "the mission engine" to "the parser/renderer used only at import/export boundaries," which is the crux of the one-shot: the hot path no longer parses markdown.

## Complexity Tracking

The contributor's decision **removes** complexity rather than adding it: one in-tree backend instead of two, no permanent two-backend test matrix, and schema evolution as a one-place SQL migration. The accepted costs are explicit and justified: (1) a **blocking, likely-MAJOR constitutional amendment** (missions leave files) — deliberately surfaced, human-ratified; (2) a **one-shot cutover with no behavior-preserving intermediate** — bounded by NFR-004's mandatory test net; (3) **loss of default `missions.md` hand-editing** — mitigated by the on-demand export and the out-of-tree file-backend option. No simpler alternative delivers indexed, single-authority mission state without these costs; the superseded mirror (#2209) was *more* complex and could never finish.

## Deferred to `/speckit-tasks` (PR 2 task breakdown)

- Freeze the `MissionStore` method set + signatures from `contracts/mission-store.md`.
- Store-initialized marker form (a `meta` table vs. a sentinel file) — data-model notes both; tasks pick one.
- Callsite migration order (counts/picker + dashboard/status/API first; then the long tail).
- `claim_next` transaction shape (`BEGIN IMMEDIATE` + `UPDATE … WHERE state='pending'` retry) and its concurrency-test harness.
- Whether `export` is a new core skill or a `make` target / CLI subcommand; whether `continuous` export is worth shipping at all given no in-tree file readers remain.
- The amendment's exact version bump (MINOR vs MAJOR) — the ratifier decides; this plan recommends MAJOR.
- The reconciliation edits to `CLAUDE.md` / `core.md` / architecture docs (amendment step 3).
