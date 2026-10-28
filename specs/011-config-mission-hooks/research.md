# Phase 0 Research: Config-driven mission hooks

## R1 — Where to read/resolve hooks

**Decision**: Add `get_mission_hooks(project_path, mission_type, phase) -> list[str]`
to `koan/app/project_koan.py`, built on the existing `read_koan_config()`.

**Rationale**: `project_koan.py` is the single home for `.koan/` readers and already
hosts `read_koan_config()` (fail-safe `{}` on absent/empty/unreadable/unparseable/
non-mapping) and `get_review_always_check()`. Reusing it keeps the "single read path"
(Principle VI) and inherits the fail-safe contract for free. The resolver stays
**pure** (no side effects) so precedence/caps/validation are unit-testable without a
subprocess.

**Alternatives considered**: a brand-new reader module (rejected — duplicates the
fail-safe read + violates single-read-path); putting resolution inside the executor
(rejected — couples untrusted-parse logic to side-effecting code, harder to test).

## R2 — Where to execute + gate

**Decision**: New module `koan/app/mission_hooks.py` with `hooks_enabled(project_name)`,
`run_pre_hooks(...)`, `run_post_hooks(...)`.

**Rationale**: `project_koan.py`'s docstring scopes it to "raw-content readers only";
subprocess execution, the opt-in gate, env injection, timeout, and log capture are a
distinct, side-effecting concern. A dedicated module isolates the RCE surface for
audit and keeps each file within the ~600-line guidance.

**Alternatives considered**: extend `hooks.py` (rejected — it is the operator-trusted
Python-hook system that does not fire for skill dispatch; mixing repo-untrusted shell
into it blurs the exact trust boundary this design depends on).

## R3 — Precedence semantics

**Decision**: **Replace, per phase.** `get_mission_hooks(path, type, phase)` returns
`config[type][f"{phase}_hooks"]` if that is a non-empty resolved list; else
`config["default"][f"{phase}_hooks"]`; else `[]`. `pre` and `post` are resolved
independently, so a type may override pre while inheriting post's default.

**Rationale**: Matches the user's explicit statement ("the custom mission takes
precedence" — replace, not merge). Per-phase independence is the least surprising
reading and keeps the resolver a trivial two-key lookup.

**Alternatives considered**: merge/append default+type (rejected for v1 — the user
asked for precedence/replacement; append is documented as a possible future mode).

## R4 — Mission-type key

**Decision**: `mission_type = skill_dispatch.mission_command_name(mission_title)`.

**Rationale**: `mission_command_name()` already normalizes across every trigger path
(Telegram `/review`, GitHub `/core.rebase`, SKILL.md aliases, `[project:x]` prefix,
requeue metadata) to a canonical command name, and falls back to the skill registry.
It returns `""` for non-skill missions → only `default` hooks apply. `fix` resolves
to `fix` for both PR and issue targets (the runner-internal PR→rebase redirect in
`_build_fix_cmd` does not change the mission's command name), satisfying the "fix
applies to both PR and issues" requirement.

**Alternatives considered**: a bespoke type map (rejected — duplicates existing
canonicalization and would drift from `_CANONICAL_RUNNERS`/aliases).

## R5 — Call sites (both execution paths, no double-fire)

**Decision**: Three explicit call sites.

| Path | Pre | Post |
|---|---|---|
| Skill dispatch (review/fix/plan/rebase/implement/…) | `_handle_skill_dispatch`, just before the `_run_skill_mission` `try` | same function's existing `finally`, `success = exit_code == 0` |
| Agent-loop (refactor / free-form / autonomous) | `_run_iteration`, at the existing `pre_mission` `fire_hook` site | `mission_runner._fire_post_mission_hook`, `success = exit_code == 0` |

**Rationale**: `_handle_skill_dispatch` returns `handled=True` **before** the agent-
loop `pre_mission` fire, so the two pre sites are mutually exclusive — no double-fire.
Skill missions never reach `mission_runner`'s post-mission pipeline, so the two post
sites are likewise exclusive. Putting the skill post-hook in the existing `finally`
guarantees it runs on success, failure, quota-classification early-return, and
`KeyboardInterrupt`.

**Alternatives considered**: a single wrapper around all mission execution (rejected —
the two paths finalize very differently; a unifying wrapper would be a larger,
riskier refactor than the constitution's "simplicity" principle warrants).

## R6 — Executor mechanics

**Decision**: For each command, `subprocess.run(command, shell=True,
cwd=project_path, env={**os.environ, "KOAN_MISSION_STATUS": status,
"KOAN_MISSION_TYPE": mission_type}, capture_output=True, text=True,
timeout=MISSION_HOOK_TIMEOUT)`. `KOAN_MISSION_STATUS` is set on post-hooks only
(`success`/`failure`); on pre-hooks it is unset (or `pending`) — decided in
data-model. Wrap each command in its own try/except (catch `subprocess.
TimeoutExpired` and generic `Exception`); log `[mission_hooks] pre/post <type>
cmd N/M exit=… dur=…s` plus bounded stdout/stderr; continue to the next command
regardless.

**Rationale**: `shell=True` honors the "shell/bash command" framing (pipelines,
`&&`). Per-command isolation implements best-effort/fire-and-forget (FR-007). The
timeout (FR-008) bounds a hung command. Bounded log capture (FR-009) prevents flood.

**Security note**: `shell=True` on repo-supplied strings is intentional and safe
**only** because the whole path is gated behind the operator opt-in (R7). This is
called out in the code comment and the threat-model doc.

**Alternatives considered**: `shell=False` with `shlex.split` (rejected — breaks
pipelines/`&&` operators the user explicitly wants; provides no real safety benefit
once execution is already operator-authorized).

## R7 — Operator opt-in gate

**Decision**: `mission_hooks.hooks_enabled(project_name)`:
1. per-project override — `projects_config.get_project_mission_hooks(name)` returns
   `Optional[bool]` from the project's `mission_hooks:` key in `projects.yaml`; if not
   `None`, use it;
2. else global — `config.is_mission_hooks_enabled()` reads
   `mission_hooks.enabled` from `instance/config.yaml`, **default `False`**.

**Rationale**: Mirrors the established `review_dispatch`/`ci_dispatch` `enabled: true`
opt-in and the existing per-project override mechanism (cli_provider, models, tools,
git_auto_merge). Default-off is the load-bearing security control (Principle V).

**Alternatives considered**: env-var-only gate (rejected — inconsistent with the
config-section pattern); always-on with a blocklist (rejected — fails safe-by-default).

## R8 — Caps & validation

**Decision**: In `get_mission_hooks`: value must be a `list`; keep only non-blank
`str` items (`.strip()`); drop entries longer than `_MAX_HOOK_CMD_LEN` (e.g. 1000
chars) and cap the list at `_MAX_HOOKS_PER_LIST` (e.g. 20), logging one diagnostic
when anything is dropped. Mirrors `get_review_always_check`'s cap style.

**Rationale**: Bounds worst-case execution time and log volume from a pathological or
malicious config (FR-011). Fail-safe: any malformed shape → `[]`.

## Constitution re-check (post-design)

No new violations surfaced during design. The single declared architectural change
(R1/R2 contracts) stands; the default-off gate (R7) preserves Human Authority and
Untrusted-Inputs principles. PASS.
