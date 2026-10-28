# Phase 1 Data Model: Config-driven mission hooks

No persistent storage. These are in-memory shapes read per-mission from the target
repo's `.koan/config.yaml` and the operator's config.

## Repo config surface (`<repo>/.koan/config.yaml`)

```yaml
# Section per mission type, plus a `default` fallback. All optional.
default:
  pre_hooks:  [<command string>, ...]   # run before ANY mission (fallback)
  post_hooks: [<command string>, ...]   # run after ANY mission (fallback)

review:                                 # well-known types: review, fix, plan,
  pre_hooks:  [<command string>, ...]   # rebase, refactor, implement, recreate,
  post_hooks: [<command string>, ...]   # squash, … (any resolvable command name)

# review.always_check (spec 010) and other keys coexist untouched.
```

- **Command string**: a single shell command line (run via `shell=True`), e.g.
  `"docker compose up -d db"` or `"npm ci && npm run build"`.
- **List order = execution order.** Commands run top-to-bottom, sequentially.

### Validation / normalization (in `get_mission_hooks`)

| Input shape | Result |
|---|---|
| section or `*_hooks` key absent | `[]` |
| value not a `list` | `[]` (one diagnostic) |
| item not a `str` | dropped |
| item blank / whitespace-only | dropped |
| item length > `_MAX_HOOK_CMD_LEN` (1000) | dropped (one diagnostic) |
| list length > `_MAX_HOOKS_PER_LIST` (20) | truncated (one diagnostic) |
| whole file absent/empty/unparseable/non-mapping | `[]` (via `read_koan_config`) |

### Precedence (per phase, independent)

```
get_mission_hooks(path, mission_type, phase):
    cfg = read_koan_config(path)                    # {} on any failure
    key = f"{phase}_hooks"                           # "pre_hooks" | "post_hooks"
    typed   = _normalize(cfg.get(mission_type, {}).get(key))
    default = _normalize(cfg.get("default", {}).get(key))
    return typed if typed else default               # replace, not merge
```

`mission_type == ""` (non-skill mission) ⇒ `typed` is always empty ⇒ `default`
applies. `phase ∈ {"pre", "post"}`.

## Operator gate

```yaml
# instance/config.yaml (KOAN_ROOT) — operator-controlled
mission_hooks:
  enabled: false        # DEFAULT. Must be true for any repo hook to run.
```

```yaml
# projects.yaml — optional per-project override
projects:
  - name: my-toolkit
    path: /path/to/my-toolkit
    mission_hooks: true    # or false; overrides the global setting for this project
```

Resolution (`mission_hooks.hooks_enabled(project_name)`):

```
override = get_project_mission_hooks(name)   # Optional[bool]
return override if override is not None else is_mission_hooks_enabled()
```

## Environment exposed to hook commands

| Variable | pre_hooks | post_hooks | Value |
|---|---|---|---|
| `KOAN_MISSION_TYPE` | ✅ | ✅ | resolved command name (e.g. `review`), or `""` |
| `KOAN_MISSION_STATUS` | unset | ✅ | `success` \| `failure` |

Plus the inherited process environment (`{**os.environ, ...}`). `cwd` = the target
project's working directory.

## Hook execution result (transient; logging only)

Per command: `{index, total, command (truncated for log), exit_code | "timeout" |
"error", duration_s, stdout/stderr (bounded)}`. Never returned to the mission
pipeline — the mission's own success/failure is unaffected by hook results.

## Constants (new, in `mission_hooks.py` or `constants.py`)

| Name | Suggested value | Meaning |
|---|---|---|
| `MISSION_HOOK_TIMEOUT` | 300 s | per-command wall-clock cap |
| `_MAX_HOOKS_PER_LIST` | 20 | max commands per resolved list |
| `_MAX_HOOK_CMD_LEN` | 1000 | max chars per command string |
| `_MAX_HOOK_LOG_CHARS` | 4000 | bound on captured stdout/stderr per command |
