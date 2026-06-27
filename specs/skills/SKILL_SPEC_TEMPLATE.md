# Skill Spec — `<skill_name>`

> Copy this file to `specs/skills/<skill_name>.md` and fill every section. Keep it
> tight — a skill spec is a contract, not a tutorial (tutorials live in
> `docs/users/skills.md`). Delete this quote block when done.

## Command(s)

- **Primary:** `/<command> <usage>`
- **Aliases:** `<alias>`, …
- **Group:** `<missions|code|pr|status|config|ideas|system|integrations>`

## Purpose

One or two sentences: what this skill does and *why it exists* (the problem it solves).

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| e.g. PR URL | command arg | yes | parsed by `github_url_parser` |
| flags | command arg | no | `--now`, `--force`, … |
| trailing context | command arg | no | passed through to the mission |

## Outputs / side effects

- What the user sees (Telegram reply / dashboard).
- What it writes (mission queued? PR created? tracker issue? journal?).
- Whether it runs sync (inline reply) or async (`worker: true` background thread / queued
  mission run by the agent loop).

## Error cases

| Condition | Behavior |
|---|---|
| invalid/missing URL | … |
| project not found / alias unresolved | … |
| target closed/merged | … (and any `--force` override) |
| provider/quota failure | … |

## Integration hooks

- **Handler:** `koan/skills/core/<name>/handler.py` (or prompt-only).
- **Runner:** registered in `skill_dispatch._SKILL_RUNNERS`? (yes/no — agent-loop vs bridge).
- **GitHub/Jira:** `github_enabled`? `github_context_aware`?
- **Combo:** `sub_commands`?
- **Async:** `worker: true`?
- **Auto-merge / security review:** does the produced PR flow through them?

## Invariants

- The non-obvious rules a change must preserve (e.g. "always draft PR", "dedup via
  fingerprint", "alias resolved before tag built").

## Known debt / watch-outs

- Quirks, edge cases, or fragile couplings worth flagging before a change.
