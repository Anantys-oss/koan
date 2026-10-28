# Contract: function surface

## `project_koan.get_mission_hooks(project_path, mission_type, phase) -> list[str]`

Pure, fail-safe resolver. Reads `<project_path>/.koan/config.yaml` via
`read_koan_config()`, applies per-phase precedence (mission-type over `default`,
replace-not-merge), validation, and caps.

- `project_path: str` — target repo root. Empty ⇒ `[]`.
- `mission_type: str` — canonical command name (e.g. `"review"`), or `""`.
- `phase: str` — `"pre"` or `"post"`.
- **Returns** the ordered, validated command list (possibly empty). NEVER raises.

Behavior: `""` mission_type ⇒ only `default.<phase>_hooks`. Non-list value,
absent key, or fail-safe read ⇒ `[]`. Blank/non-string/over-long entries dropped;
list capped; one diagnostic on any drop/cap.

## `config.is_mission_hooks_enabled() -> bool`

Reads `mission_hooks.enabled` from the operator's `instance/config.yaml`.
**Default `False`.** Fail-safe: any read/parse issue ⇒ `False`.

## `projects_config.get_project_mission_hooks(project_name) -> Optional[bool]`

Per-project override from the project's `mission_hooks:` key in `projects.yaml`.
Returns `None` when unset (⇒ fall back to global), else the bool.

## `mission_hooks.hooks_enabled(project_name) -> bool`

`get_project_mission_hooks(name)` if not `None`, else
`is_mission_hooks_enabled()`. The single decision point for whether any repo hook
may run.

## `mission_hooks.run_pre_hooks(project_path, project_name, mission_type) -> None`

Gate-checked, best-effort. If `not hooks_enabled(project_name)` ⇒ no-op (one
"skipped (not enabled)" diagnostic on first skip). Else resolves
`get_mission_hooks(project_path, mission_type, "pre")` and runs each command in
order with `KOAN_MISSION_TYPE` set, `cwd=project_path`, per-command timeout,
bounded log capture. Per-command errors/timeouts are logged and swallowed. NEVER
raises to the caller.

## `mission_hooks.run_post_hooks(project_path, project_name, mission_type, success) -> None`

As `run_pre_hooks`, for the `"post"` phase, additionally setting
`KOAN_MISSION_STATUS = "success" if success else "failure"`. Runs on both success
and failure. NEVER raises.

## Call-site contract (wiring)

| File / function | Insert |
|---|---|
| `mission_executor._handle_skill_dispatch` | `run_pre_hooks(project_path, project_name, mtype)` before the `_run_skill_mission` `try`; `run_post_hooks(project_path, project_name, mtype, exit_code == 0)` in the existing `finally`. `mtype = mission_command_name(mission_title)`. |
| `mission_executor._run_iteration` (agent-loop branch) | `run_pre_hooks(...)` at the existing `pre_mission` `fire_hook` site (only reached when the mission was NOT skill-dispatched). |
| `mission_runner._fire_post_mission_hook` | `run_post_hooks(project_path, project_name, mission_command_name(mission_title), exit_code == 0)` beside the Python `post_mission` fire. |

Invariants preserved: no double-fire (skill dispatch returns before the agent-loop
pre site; skill missions never reach `_fire_post_mission_hook`); every wiring call
is wrapped so a hook subsystem error never disturbs mission finalization.
