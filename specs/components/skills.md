# Component Spec — Skills System

**Modules:** `koan/app/skills.py`, `koan/app/skill_dispatch.py`,
`koan/app/external_skill_dispatch.py`, `koan/skills/core/<name>/`,
`instance/skills/<scope>/<name>/`

> Per-skill specs live in `specs/skills/`. This spec covers the **system** that
> discovers, routes, and executes skills.

## Purpose

An extensible command-plugin system. A "skill" is a `/command` with a `SKILL.md`
(frontmatter contract) and an optional `handler.py`. Skills are how both humans (Telegram,
dashboard) and external systems (GitHub/Jira @mentions) drive Kōan.

## Architecture

```
skills.py            → registry: discover SKILL.md, parse frontmatter (lite YAML),
       │                map commands/aliases → skills, execute_skill()
skill_dispatch.py    → agent-loop direct execution: /command missions bypass the Claude
       │                agent, route to registered runners (plan/rebase/recreate/check/...)
external_skill_dispatch.py → in-process dispatch for custom skills triggered via Jira/GitHub
```

## Skill anatomy

```
koan/skills/core/<name>/
  ├─ SKILL.md      # frontmatter: name, description, group, commands, aliases, flags
  └─ handler.py    # optional: def handle(ctx: SkillContext) -> Optional[str]
```

- **Handler return contract:** string → Telegram reply; `""` → already handled; `None`
  → no message.
- **Prompt-only skills:** omit `handler`, put prompt text after frontmatter → sent to
  Claude directly.

## Frontmatter flags (the contract)

| Flag | Meaning |
|---|---|
| `group:` | **Mandatory.** One of: missions, code, pr, status, config, ideas, system (core); `integrations` reserved for custom skills. Drives `/help`. |
| `worker: true` | Blocking skill (Claude/API) → runs in a background thread. |
| `github_enabled: true` | Triggerable via GitHub @mention (Jira reuses it; no separate `jira_enabled`). |
| `github_context_aware: true` | Accepts extra context after the command. |
| `sub_commands:` | Combo skill — decomposes into multiple sub-missions (discovered by `collect_combo_skills()`). |
| `forward_result: true` (+ `title_markers:`) | Opt-in result forwarding, resolved dynamically — **the pattern for "core recognizes a custom skill" without hardcoding names**. |
| `model_key:` | Selects the model tier (e.g. `mission`). |

## Invariants

- **Names/aliases/dirs use underscores, never hyphens** — Telegram truncates at `-`.
- **No hardcoded skill-name lists in `koan/app/`.** When core must recognize a specific
  custom skill, drive it off SKILL.md frontmatter flags (see `collect_forward_result_markers`).
- **Skill stdout is DATA.** Runners emit structured transcripts; `mission_executor`
  passes `trust_stdout=False` so transcripts aren't misread as CLI errors.
- **No private identifiers leak** into core skills, tests, or docs — use generic
  placeholders (`my_fix`, `my_team`, `PROJ-NNN`).
- **Order-sensitive combos insert atomically** — one locked multi-entry write
  (`insert_pending_missions`), never two top-inserts (TOCTOU + reversed order).

## Adding a core skill (full checklist)

1. `koan/skills/core/<name>/SKILL.md` (+ `handler.py` if needed) with a `group:`.
2. If agent-loop-run: register in `_SKILL_RUNNERS` + `_COMMAND_BUILDERS` +
   `validate_skill_args()` in `skill_dispatch.py`.
3. Add to the CLAUDE.md "Core skills" list (alphabetical).
4. Update `docs/users/user-manual.md` and `docs/users/skills.md`.
5. Add the per-skill spec in `specs/skills/<name>.md`.
6. `TestCoreSkillGroupEnforcement` must pass (fails if `group:` is missing).

## Integration points

- Bridge dispatch (`command_handlers.py`) and agent-loop dispatch (`mission_executor`)
  both call into `skills.py`.
- Custom skills under `instance/skills/<scope>/` can be cloned Git repos for team sharing
  (`skill_manager.py`).
- GitHub/Jira @mentions route through `external_skill_dispatch.py`.

## Known debt / watch-outs

- Frontmatter is parsed by a custom lite YAML parser (no PyYAML) — keep frontmatter
  simple; exotic YAML will not parse.
- ~80 of ~91 skills lack per-skill specs (phase 1 ships 10 exemplars).

## Change protocol

Changing the SKILL.md contract, dispatch routing, or the group enumeration updates this
spec, the README authoring guide (`koan/skills/README.md`), and the group-enforcement
test.
