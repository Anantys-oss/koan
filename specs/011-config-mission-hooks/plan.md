# Implementation Plan: Config-driven mission hooks (`.koan/config.yaml` pre/post hooks)

**Branch**: `koan.atoomic/config-mission-hooks` | **Date**: 2026-07-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/011-config-mission-hooks/spec.md`

## Summary

Let a target repo declare shell commands to run **before** (`pre_hooks`) and
**after** (`post_hooks`) a mission in its checked-in `.koan/config.yaml`, keyed by
mission type with a `default` fallback. A mission-type section replaces the
`default` list for that phase (replace, not merge); commands within a list run in
order; post-hooks run on both success and failure with `KOAN_MISSION_STATUS` /
`KOAN_MISSION_TYPE` in the environment. Execution is best-effort (a failing/timing-out
hook never aborts the mission). Because these commands come from a repo-controlled
file, the whole capability is **disabled by default** behind an operator opt-in
(`mission_hooks.enabled` in `instance/config.yaml`, with an optional per-project
override), mirroring `review_dispatch` / `ci_dispatch`.

The work is a thin additive layer: a pure reader/resolver in `project_koan.py`, a
new `mission_hooks.py` executor+gate module, config accessors in `config.py` /
`projects_config.py`, and three explicit call sites at the mission-execution
chokepoints (skill-dispatch pre+post; agent-loop pre; agent-loop post).

### Context (docs/specs consulted)

Consulted via `/brain ask` (index-first). Findings that shaped this plan:

- `docs/architecture/hooks.md` → the existing **Python lifecycle-hook** system
  (`koan/app/hooks.py`, `HookRegistry`). Events `pre_mission` / `post_mission` fire
  from `mission_executor.py` / `mission_runner.py` but **only for agent-loop
  missions** — skill-dispatched missions return before the `pre_mission` fire. Trust
  model: those hooks are Python in operator-controlled `instance/`, "effectively
  trusted code." This feature is deliberately **separate**: it executes shell from a
  **repo-controlled** file, so it gets its own module and its own opt-in gate rather
  than piggy-backing on the trusted-instance hook path.
- `docs/users/koan-md.md` + `specs/components/skills.md` ("Repo config file
  (.koan/config.yaml)") → the `.koan/config.yaml` reader contract from spec 010:
  `project_koan.read_koan_config()` is fail-safe (returns `{}` on
  absent/empty/unreadable/unparseable/non-mapping). This feature adds a sibling typed
  accessor with the same fail-safe discipline and extends the docs page + sample.
- `specs/components/agent-loop.md` → documents `project_koan` as the repo `.koan/`
  reader home and the mission-executor/runner pipeline. The new call sites and the
  `mission_hooks` module belong to this component's contract.
- `docs/security/threat-model-agent-disalignment.md` → the operator-trust boundary
  and human-PR-review-as-primary-control framing; the new RCE surface and its
  default-off gate are documented against this model (new `docs/security/` page).
- `wiki/index.md` had **no** page describing repo-driven shell execution around
  missions → confirmed missing coverage; this feature adds it.

## Technical Context

**Language/Version**: Python 3.11+ (constitution constraint; no 3.12+ syntax).

**Primary Dependencies**: existing `app.project_koan.read_koan_config` (repo
`.koan/` reader), `app.skill_dispatch.mission_command_name` (mission-type
resolution), `app.config` (operator settings), `app.projects_config`
(per-project overrides), `app.run_log.log_safe` (logging). Standard-library
`subprocess`, `os`, `shlex`/shell for command execution. No new third-party
dependency (PyYAML already present).

**Storage**: N/A — no new persistent state. Repo config is read per-mission;
nothing is written except log lines.

**Testing**: pytest with `KOAN_ROOT` set, never invoking the Claude subprocess.
New unit tests for: the resolver (`test_project_koan.py` — precedence, caps,
validation, fail-safe); the executor+gate (`test_mission_hooks.py` — enablement,
ordering, timeout, status env, failure isolation) with the actual subprocess calls
either run against trivial shell (`true`/`false`/`sleep`) or mocked at the
`subprocess.run` boundary; the config accessors (`test_config.py` /
`test_projects_config.py`); wiring smoke tests that patch `mission_hooks.run_*` to
assert the call sites invoke them with the right (mission_type, phase, status).

**Target Platform**: Linux/macOS daemon.

**Project Type**: Single Python package (`koan/`) — agent-loop pipeline + `.koan/`
reader + a new small executor module.

**Performance Goals**: N/A — pre/post hooks add only the wall-clock of the operator-
authorized commands themselves; resolution is O(configured patterns), tiny and
hard-capped. When disabled or unconfigured the added cost is one dict lookup.

**Constraints**: fail-safe on all untrusted repo config (Principle V); no inline
prompts (none added); must pass `ruff`/`make lint`; functions ≤ ~30 lines, files ≤
~600 lines (split via helpers). The opt-in gate MUST default off. Best-effort
execution MUST never abort a mission or crash the daemon.

**Scale/Scope**: one reader function in `project_koan.py`; one new module
`mission_hooks.py` (~120 lines: gate + executor + helpers); two config accessors;
three call-site edits; docs (koan-md.md, sample config, new security page); tests.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Human Authority**: PASS — hooks run only commands the **operator explicitly
  opted into** running (default off); they do not merge, push to `main`, or expand
  the agent's autonomy. They are operator-authorized automation around the existing
  loop, in the same spirit as `git_auto_merge` (optional, visible, gated).
- **II. Specs Are the Source of Truth**: **ARCHITECTURAL CHANGE — declared.** This
  extends two durable contracts, contract-first: (a) `specs/components/skills.md`'s
  ".koan/config.yaml" reader contract gains a `pre_hooks`/`post_hooks` surface and a
  typed resolver; (b) `specs/components/agent-loop.md`'s mission-execution contract
  gains a new pre/post shell-hook step at the skill-dispatch and agent-loop
  chokepoints, plus the `mission_hooks` module and the operator gate. The spec edits
  land in this branch **before/with** the code and the PR MUST check the
  "Architectural change" box. No existing behavior changes when the gate is off (the
  default), so there is no breaking change.
- **III. Local Files by Default; Mission State in the Store**: PASS — no mission-
  store interaction; config is a plain file read; no new runtime state files.
- **IV. Provider Isolation**: PASS — provider-agnostic; hooks run around mission
  execution regardless of CLI provider.
- **V. Untrusted Inputs, Audited Outputs**: PASS and **central**. A repo's
  `.koan/config.yaml` is semi-untrusted; executing shell from it is a real RCE
  surface. Mitigations: (1) **default-off operator gate** — nothing runs unless the
  operator opts in; (2) fail-safe parsing (type-checked to `list[str]`, per-command
  and per-list caps, blank/non-string entries dropped); (3) bounded per-command
  timeout; (4) all hook lifecycle + output is **audited to the run log**; (5) a
  documented threat model in `docs/security/`. Hooks can only run what the operator
  authorized for repos the operator chose to add to `projects.yaml`.
- **VI. Single Writer, Single Read Path**: PASS — repo config is read through the
  single `project_koan.read_koan_config()` helper (as `always_check` already is);
  the resolver is the one place precedence is computed; the gate is resolved in one
  place (`mission_hooks.hooks_enabled`).
- **VII. Simplicity and Honest Reporting**: PASS — no new dependency; one small
  module rather than a parallel framework; ships only pre/post hooks (YAGNI) while
  *documenting* future extensions (configurable timeouts, merge mode). Logs report
  honestly: skipped-because-disabled, per-command exit/timeout, and dropped-by-cap
  are all surfaced.

**Result**: PASS with one **declared architectural change** (the `.koan/` reader
contract + the agent-loop mission-execution contract). Recorded in Complexity
Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/011-config-mission-hooks/
├── plan.md              # This file
├── spec.md              # Feature spec
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── contracts/           # Phase 1 output (config schema + function contracts)
│   ├── config-schema.md
│   └── functions.md
├── quickstart.md        # Phase 1 output
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
koan/app/
├── project_koan.py        # + get_mission_hooks(project_path, mission_type, phase) -> list[str]
│                          #   (pure reader/resolver: precedence, caps, validation; fail-safe)
├── mission_hooks.py       # NEW: gate + executor
│                          #   hooks_enabled(project_name) -> bool
│                          #   run_pre_hooks(project_path, project_name, mission_type)
│                          #   run_post_hooks(project_path, project_name, mission_type, success)
├── config.py              # + is_mission_hooks_enabled() (instance config mission_hooks.enabled, default False)
├── projects_config.py     # + get_project_mission_hooks(name) -> Optional[bool] (per-project override)
├── mission_executor.py    # wire: pre+post around _run_skill_mission in _handle_skill_dispatch;
│                          #       pre for agent-loop missions near the pre_mission fire
└── mission_runner.py      # wire: post for agent-loop missions in _fire_post_mission_hook path

koan/tests/
├── test_project_koan.py   # resolver: precedence, caps, validation, fail-safe
├── test_mission_hooks.py  # NEW: gate + executor (ordering, timeout, status env, isolation)
├── test_config.py         # is_mission_hooks_enabled default + override
├── test_projects_config.py# per-project override resolution
├── test_mission_executor.py # skill-dispatch pre/post call-site wiring (patched executor)
└── test_mission_runner.py # agent-loop post call-site wiring (patched executor)

docs/
├── users/koan-md.md                    # document pre_hooks/post_hooks + gate + precedence
├── reference/koan-config.sample.yaml   # annotated sample: add pre_hooks/post_hooks + gate note
└── security/mission-hooks.md           # NEW: threat model + safe-use guidance

specs/components/skills.md      # contract-first: extend .koan/config.yaml reader contract
specs/components/agent-loop.md  # contract-first: mission_hooks module + pre/post call sites + gate
```

**Structure Decision**: Single Python package. Reader/resolver stays in
`project_koan.py` (the `.koan/` reader home, matching `get_review_always_check`);
execution + gate go in a **new** `mission_hooks.py` because `project_koan.py` is
"raw-content readers only" (its docstring) and side-effecting subprocess execution
is a distinct concern. Kept separate from `hooks.py` (Python lifecycle hooks) on
purpose — different trust model (repo-controlled vs operator-controlled) and
different mechanism (shell vs Python).

## Key design decisions (from research.md)

1. **Two reader/executor split**: `project_koan.get_mission_hooks()` is pure and
   fail-safe; `mission_hooks.run_*` handles the gate, subprocess, timeout, env, and
   logging. This keeps the untrusted-parse logic testable in isolation from the
   side-effecting executor.
2. **Precedence = replace, per phase**: `get_mission_hooks(path, mission_type,
   phase)` returns the `<mission_type>.<phase>` list if that section defines a list
   for that phase, else the `default.<phase>` list, else `[]`. Pre and post are
   resolved independently.
3. **Mission-type key** = `skill_dispatch.mission_command_name(mission_title)`
   (canonical, alias-resolved, trigger-agnostic). Empty string ⇒ only `default`
   applies. `fix` stays `fix` for both PR and issue targets.
4. **Three call sites** (no double-fire — skill dispatch returns before the
   agent-loop `pre_mission` fire):
   - `mission_executor._handle_skill_dispatch`: `run_pre_hooks` just before the
     `_run_skill_mission` `try`; `run_post_hooks(..., success=exit_code==0)` in the
     existing `finally` so it fires on every exit path (success, failure, exception).
   - `mission_executor._run_iteration` (agent-loop branch): `run_pre_hooks` at the
     existing `pre_mission` fire site.
   - `mission_runner._fire_post_mission_hook`: `run_post_hooks(..., success=exit_code
     ==0)` alongside the Python `post_mission` fire.
5. **Gate**: `mission_hooks.hooks_enabled(project_name)` = per-project override if
   set (bool in `projects.yaml`), else `config.is_mission_hooks_enabled()` (instance
   `mission_hooks.enabled`, default False). Both `run_pre_hooks`/`run_post_hooks`
   check the gate first and no-op (with one diagnostic on the first skip) when off.
6. **Executor**: run each command with `subprocess.run(cmd, shell=True,
   cwd=project_path, env={**os.environ, KOAN_MISSION_STATUS, KOAN_MISSION_TYPE},
   timeout=<cap>, capture_output=True, text=True)`, wrapped per-command in
   try/except so one failure never blocks the rest; log start/exit/duration/timeout
   and bounded output. Never raise to the caller.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| Declared architectural change to the `.koan/` reader contract + the agent-loop mission-execution contract (`specs/components/skills.md`, `agent-loop.md`) | The feature adds a genuinely new capability — repo-config-driven shell execution around missions — which changes the mission-execution contract (a new pre/post step) and the repo-config reader contract (a new executable surface). Per Principle II that must be declared and landed contract-first. | Piggy-backing on the existing Python `hooks.py` was rejected: it fires only for agent-loop missions (skill dispatch bypasses it), and it would blur the operator-trusted-Python vs repo-untrusted-shell trust boundary — precisely the boundary the security design turns on. |
| New `mission_hooks.py` module + operator opt-in gate | Shell from a repo file is an RCE surface; a default-off gate is the load-bearing control (Principle V). A separate module keeps the untrusted-execution concern isolated and independently testable. | Hardcoding hooks into `mission_executor.py` was rejected: it would spread subprocess/gate logic across two large files and make the security boundary harder to audit. Reusing `always_check`'s reader-only pattern alone is insufficient because execution is side-effecting and gated. |
