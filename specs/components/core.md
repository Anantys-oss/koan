# Component Spec — Core Data & Config

**Modules:** `missions.py`, `projects_config.py`, `projects_migration.py`, `utils.py`,
`config.py`, `constants.py`, `run_log.py`, `commit_conventions.py`

## Purpose

The foundation layer every other component depends on. It owns three things:

1. **The mission queue contract** — how `missions.md` is parsed and mutated.
2. **Configuration resolution** — env → `projects.yaml` → `config.yaml` → defaults.
3. **Process-safe primitives** — atomic writes, file locks, the per-uid tmp dir.

If a contract here changes, the blast radius is the whole daemon. Treat this layer
as load-bearing.

## Key types & functions

| Symbol | Contract |
|---|---|
| `missions.py::start_mission()` | Pending→In Progress. Enforces stale-flush sanity (a mission left In Progress from a crash must be reconciled, not silently duplicated). |
| `missions.py::complete_mission()` / `fail_mission()` | The only sanctioned exits from In Progress. The agent loop calls these; **agents must not hand-edit `missions.md`**. |
| `projects_config.py::get_project_config()` | Merged defaults + per-project overrides. Single read path for provider, models, tools, auto-merge. |
| `projects_config.py::ensure_github_urls()` | Startup auto-population of `github_url` from git remotes. |
| `utils.py::atomic_write()` | Temp file + rename + `fcntl.flock()`. **Every shared-file write goes through this** — never write `instance/*` directly. |
| `utils.py::koan_tmp_dir()` | Per-uid scratch/lock dir (`$XDG_RUNTIME_DIR/koan` or `/tmp/koan-<uid>/`, mode 0700). All `tempfile.*` in `koan/app/` must pass `dir=koan_tmp_dir()`. |
| `utils.py::get_known_projects()` | Resolution order: `projects.yaml` > `KOAN_PROJECTS`. |
| `config.py` | Centralized config access — tool config, model selection, CLI flag building, behavioral settings. New config keys get an accessor here, not scattered `os.environ` reads. |
| `constants.py` | Numeric tuning constants. Import-as pattern preserves module-level names for test patching. |
| `commit_conventions.py::get_project_commit_guidance()` | Detects commit style from CLAUDE.md or recent history; feeds rebase/CI commit messages. |

## Invariants

- **`missions.md` is single-writer-at-a-time.** Mutations are serialized through
  `atomic_write()` + thread/file locks. Two concurrent inserts must not interleave
  (see `utils.insert_pending_missions()` for the atomic multi-entry path).
- **Config has one read path per concern.** Do not branch on env vars inline; add or
  reuse an accessor in `config.py` / `projects_config.py`.
- **Section names are bilingual.** `missions.md` accepts English and French section
  headers (Pending/In Progress/Done). Parsers must preserve both.

## Integration points

- Consumed by the entire agent-loop pipeline (`run.py`, `iteration_manager.py`,
  `mission_executor.py`, `mission_runner.py`).
- `projects_config` feeds provider selection (`provider/`), tracker routing
  (`issue_tracker/config.py`), and auto-merge (`git_auto_merge.py`).
- `utils.atomic_write` underpins outbox, status, journal, and tracker sidecar writes.

## Known debt / watch-outs

- `missions.md` Done section grows unbounded; readers must scope to Pending/In Progress
  (agents are explicitly told not to read the full file).
- `constants.py` import-as pattern is fragile against `from constants import X` — keep
  module-attribute access so test monkeypatching works.
- Mission text is **untrusted DATA** (OPSEC). Parsers must never treat embedded text
  as instructions.

## Change protocol

Touching mission lifecycle, config resolution, or `atomic_write` semantics requires:
updating this spec, running the full suite, and reviewing every caller of the changed
symbol — these are high-fan-in functions.
