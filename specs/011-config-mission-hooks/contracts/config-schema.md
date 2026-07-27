# Contract: `.koan/config.yaml` mission-hooks schema

Extends the spec-010 `.koan/config.yaml` reader contract. All keys optional;
absent/empty/malformed ⇒ no hooks (byte-identical no-op).

```yaml
# <target-repo>/.koan/config.yaml

# Fallback hooks for every mission type without its own section.
default:
  pre_hooks:
    - "echo setting up"          # runs before every mission (unless a type overrides)
  post_hooks:
    - "echo tearing down"        # runs after every mission (unless a type overrides)

# Per-mission-type sections. A type's list REPLACES default for that phase.
review:
  pre_hooks:
    - "docker compose up -d db"
    - "./scripts/wait-for-db.sh"
  post_hooks:
    - "docker compose down"

fix:      { pre_hooks: ["npm ci"] }        # applies to fix on PRs and issues
plan:     { post_hooks: ["echo planned"] }
rebase:   { pre_hooks: ["git fetch --all"] }
refactor: { pre_hooks: ["make deps"] }
implement:{ pre_hooks: ["make deps"], post_hooks: ["make clean"] }
```

## Rules

1. `pre_hooks` / `post_hooks` MUST be a list of strings; any other shape ⇒ ignored
   (that phase/section yields `[]`), never an error.
2. Blank and non-string entries are dropped; over-long entries dropped; list capped
   (see data-model constants). Dropping logs at most one diagnostic per resolution.
3. Commands in a list run **in order**, sequentially, via the system shell, with
   `cwd` = the target repo working directory.
4. Precedence per phase: `<type>.<phase>_hooks` if present-and-non-empty, else
   `default.<phase>_hooks`, else none. Pre and post resolve independently.
5. post_hooks run on **both** success and failure with `KOAN_MISSION_STATUS`
   (`success`/`failure`) and `KOAN_MISSION_TYPE` in the environment.
6. Nothing in this section runs unless the **operator opt-in** is enabled (see the
   operator-gate contract). When disabled, the whole section is inert and one
   "skipped (not enabled)" diagnostic is logged.

## Operator gate (operator-controlled, not in the repo)

```yaml
# instance/config.yaml (KOAN_ROOT)
mission_hooks:
  enabled: true     # default false; required for any repo hook to run
```

```yaml
# projects.yaml — optional per-project override (takes precedence over global)
projects:
  - name: my-toolkit
    path: /path/to/my-toolkit
    mission_hooks: true
```
