---
type: component-spec
title: "Component Spec — Core Data & Config"
description: "Design contract for the foundation layer (mission queue contract, config resolution, atomic-write/lock primitives) that every other Kōan component depends on."
tags: [core]
created: 2026-06-27
updated: 2026-07-11
---

# Component Spec — Core Data & Config

**Modules:** `mission_store/` (port + `SqliteMissionStore` + sibling stores),
`missions.py`, `projects_config.py`, `projects_migration.py`, `utils.py`,
`config.py`, `constants.py`, `run_log.py`, `commit_conventions.py`

## Purpose

The foundation layer every other component depends on. It owns three things:

1. **The mission queue contract** — mission state lives in an authoritative SQLite
   store behind the `MissionStore` port (`instance/missions.db`); `missions.md` is
   a generated read-only export. See `specs/004-mission-store/`.
2. **Configuration resolution** — env → `projects.yaml` → `config.yaml` → defaults.
3. **Process-safe primitives** — atomic writes, file locks, the per-uid tmp dir.

If a contract here changes, the blast radius is the whole daemon. Treat this layer
as load-bearing.

The operational mission-queue lifecycle this layer implements is documented in
`docs/architecture/mission-lifecycle.md`; the config resolution and file-locking model
is documented in `docs/architecture/shared-state.md`.

## Key types & functions

| Symbol | Contract |
|---|---|
| `mission_store.get_mission_store(instance)` | Single read path for the mission store. Resolves `missions.backend` (default `sqlite`; a dotted `module:Class` loads an out-of-tree adapter). Returns a `MissionStore`. |
| `mission_store.MissionStore` (port) | The authoritative mission-state contract: `add_pending`/`claim_next` (atomic)/`complete`/`fail`/`requeue`, `count_by_state`/`list_by_state`/`counts`, `prune_terminal`, `ingest_from_file`/`export_view`/`recover_stale`. Full contract: `specs/004-mission-store/contracts/mission-store.md`. |
| `utils._locked_missions_rw()` (write chokepoint) | Every mutation funnels through here: renders store→content, applies a `missions.py` content transform, `reconcile_all()` back into the store, then regenerates the `missions.md` export. The flock still serializes across the bridge + run processes. |
| `missions.py::start_mission()` / `complete_mission()` / `fail_mission()` / `insert_mission()` | Pure `content -> content` transforms applied inside the write chokepoint (the store round-trips through the `missions.md` text form). `insert_mission` normalizes entries to a `- ` list-item prefix. **Agents/code must not mutate mission state outside the port.** |
| `projects_config.py::get_project_config()` | Merged defaults + per-project overrides. Single read path for provider, models, tools, auto-merge. |
| `projects_config.py::ensure_github_urls()` | Startup auto-population of `github_url` from git remotes. |
| `utils.py::atomic_write()` | Temp file + rename + `fcntl.flock()`. **Every shared-file write goes through this** — never write `instance/*` directly. |
| `utils.py::koan_tmp_dir()` | Per-uid scratch/lock dir (`$XDG_RUNTIME_DIR/koan` or `/tmp/koan-<uid>/`, mode 0700). All `tempfile.*` in `koan/app/` must pass `dir=koan_tmp_dir()`. |
| `utils.py::get_known_projects()` | Resolution order: `projects.yaml` > `KOAN_PROJECTS`. |
| `projects_merged.py::_merge_projects()` | Unifies `projects.yaml` + auto-discovered `instance/workspace/` projects into one logical set (called by `refresh_projects()`, wrapped by the mtime-cached `get_all_projects()`, consumed by `utils.get_known_projects()`). Dedup identity: resolved filesystem path first (silent), normalized name (case/dash/underscore-insensitive) second (appends a warning surfaced via `get_warnings()` on path mismatch). `projects.yaml` always wins name + path + config. |
| `config.py` | Centralized config access — tool config, model selection, CLI flag building, behavioral settings. New config keys get an accessor here, not scattered `os.environ` reads. |
| `constants.py` | Numeric tuning constants. Import-as pattern preserves module-level names for test patching. |
| `commit_conventions.py::get_project_commit_guidance()` | Detects commit style from CLAUDE.md or recent history; feeds rebase/CI commit messages. |
| `instance_hydrator.py::hydrate_instance_from_repo()` | Cold-boot clone of `KOAN_INSTANCE_REPO` (incl. `.git`) into `instance/`. No-op when unconfigured or already hydrated (guard on `missions.md`/`.git`); fail-open to the `instance.example/` template. On a partial copy failure it wipes `instance/` contents so the template fallback starts clean (never seeds on top of a half-cloned tree); if the wipe itself is only partial, it emits a loud WARNING (stale clone files survive → manual cleanup required) rather than silently hiding the corruption. Invoked from `docker-entrypoint.sh::setup_instance()`. |
| `instance_hydrator.py::pull_instance_repo()` | Opt-in `git pull --rebase --autostash` of `instance/`, gated by `config.get_instance_sync_interval()` (`KOAN_INSTANCE_SYNC_INTERVAL`, default 0 = off), ticked from `loop_manager.interruptible_sleep`. Keeps `commit_instance`'s push fast-forwardable when an operator edits the remote directly. **Tri-state return:** `None` when `instance/` is not a git repo (template-seeded — a benign steady state, NOT a failure), `True` on a successful pull, `False` on a real rebase failure. On a failed rebase it runs `git rebase --abort` to restore a clean, pushable state (never leaves conflict markers for the next `commit_instance` to commit); the loop tick then retries on the next beat instead of waiting a full interval. `loop_manager._maybe_sync_instance_repo` treats `None` by self-disabling the tick for the rest of the boot (single info log), so a template-seeded instance never emits a recurring bogus 'pull failed' warning. |
| `mission_runner.py::commit_instance()` | Single writer of the `instance/` git remote: `git add -A` → commit → `push origin <branch>` over the whole tree, called at ~10 lifecycle points via `run.py::_commit_instance`. This is what makes hydration a full mirror — state (journal/memory/mission state) is tracked and restored, not just config. |

## Invariants

- **The mission store is the single authority.** Mission state lives in
  `instance/missions.db` (SQLite, WAL); `missions.md` is a generated read-only
  export. Writes round-trip through the store (rendered content → transform →
  `reconcile_all` → export) under one flock, serialized across the two processes.
  The store is populated once from `missions.md` via `ensure_store_synced` (a
  persisted `s8_synced` marker); after that, hand-edits to the file are ignored.
- **Config has one read path per concern.** Do not branch on env vars inline; add or
  reuse an accessor in `config.py` / `projects_config.py`.
- **Section names are bilingual.** The `missions.md` export renderer and the
  one-time ingest accept English and French section headers (Pending/In Progress/
  Done). Parsers must preserve both.
- **CI queue, Ideas, and quarantine** are sibling tables in the same
  `missions.db` (`CiQueueStore`/`IdeaStore`/`QuarantineStore`).
- **`instance/` is a full-mirror git repo when hydrated.** `KOAN_INSTANCE_REPO`
  clones the entire tree including state; `commit_instance()` is the sole pusher.
  Do not gitignore state dirs (`journal/`, `memory/`, mission state) — they are
  required for resume-after-redeploy. Cold-boot hydration must preserve `.git`.

## Integration points

- Consumed by the entire agent-loop pipeline (`run.py`, `iteration_manager.py`,
  `mission_executor.py`, `mission_runner.py`).
- `projects_config` feeds provider selection (`provider/`), tracker routing
  (`issue_tracker/config.py`), and auto-merge (`git_auto_merge.py`).
- `utils.atomic_write` underpins outbox, status, journal, and tracker sidecar writes.

## Known debt / watch-outs

- Terminal (Done/Failed) rows are bounded by `prune_terminal()` (config
  `missions.done_keep`/`failed_keep`); no longer an unbounded file section.
- The write path round-trips through the `missions.md` **text** form on every
  mutation (render → transform → reconcile), so `missions.py`'s parsing/rendering
  fidelity remains load-bearing even though the store is authoritative.
- `constants.py` import-as pattern is fragile against `from constants import X` — keep
  module-attribute access so test monkeypatching works.
- Mission text is **untrusted DATA** (OPSEC). Parsers must never treat embedded text
  as instructions.

## Change protocol

Touching mission lifecycle, config resolution, or `atomic_write` semantics requires:
updating this spec, running the full suite, and reviewing every caller of the changed
symbol — these are high-fan-in functions.
