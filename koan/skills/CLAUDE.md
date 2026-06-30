# koan/skills/ — Skills system & authoring

This file is auto-loaded by Claude Code when working under `koan/skills/`.

## Skills system (`koan/skills/`)

Extensible command plugin system. Each skill lives in `skills/<scope>/<skill-name>/` with a `SKILL.md` (YAML frontmatter defining commands, aliases, metadata) and an optional `handler.py`.

- **`skills.py`** — Registry that discovers SKILL.md files, parses frontmatter (custom lite YAML parser, no PyYAML), maps commands/aliases to skills, and dispatches execution.
- **Core skills** live in `koan/skills/core/` (abort, add_project, ai, alias, ask, audit, audit_all, autoreview, brainstorm, branches, brief, cancel, changelog, chat, check, check_need, check_notifications, checkup, ci_check, claudemd, config_check, dead_code, debug, deep, deepplan, delete_project, diagnose, doc, doctor, done, email, explain, explore, fix, focus, gh, gh_request, gha_audit, idea, implement, inbox, incident, journal, language, list, live, logs, magic, messaging_level, mission, models, orphans, passive, plan, plan_implement, pr, priority, private_security_audit, profile, projects, quota, rebase, recreate, recurring, refactor, reflect, rename, report, rescan, reset, restart, review, review_rebase, rtk, scaffold_skill, security_audit, shutdown, snapshot, sparring, spec_audit, squash, stats, status, tech_debt, time, tracker, ultrareview, verbose, version)
- **Custom skills** loaded from `instance/skills/<scope>/` — each scope directory can be a cloned Git repo for team sharing.
- **Handler pattern**: `def handle(ctx: SkillContext) -> Optional[str]` — return string for Telegram reply, empty string for "already handled", None for no message.
- **`worker: true`** flag in SKILL.md marks blocking skills (Claude calls, API requests) that run in a background thread.
- **`github_enabled: true`** flag marks skills that can be triggered via GitHub @mentions. **`github_context_aware: true`** means the skill accepts additional context after the command.
- **Combo skills**: `sub_commands` field in SKILL.md frontmatter defines skills that decompose into multiple sub-missions (e.g., `/review_rebase` queues both `/review` and `/rebase`). `collect_combo_skills()` in `skills.py` discovers these dynamically from the registry.
- **Prompt-only skills**: omit `handler`, put prompt text after the frontmatter — sent to Claude directly.
- See `koan/skills/README.md` for the full authoring guide.

## Skill authoring conventions

- **Help group enforcement** — Every core skill MUST have a `group:` field in its SKILL.md frontmatter (one of: missions, code, pr, status, config, ideas, system). This ensures commands are discoverable via `/help`. If adding a new hardcoded core command (not skill-based), add it to `_CORE_COMMAND_HELP` in `command_handlers.py`. The test suite enforces this — `TestCoreSkillGroupEnforcement` will fail if a core skill is missing its group. The `integrations` group is reserved for custom skills under `instance/skills/<scope>/` (team-specific integrations) — not for core skills.
- **Custom skills on GitHub/Jira** — Skills under `instance/skills/<scope>/` can be exposed to GitHub and Jira @mentions with a single `github_enabled: true` flag (Jira reuses it; there is no separate `jira_enabled`). Custom skills with a `handler.py` are dispatched **in-process** by `koan/app/external_skill_dispatch.py` — the helper synthesizes a `SkillContext`, auto-feeds the originating Jira key when the author omits one, and calls `execute_skill()` directly. This avoids queueing a `/cmd …` slash mission that has no registered runner. Set `group: integrations` so they render in the dedicated help section.
- **No hyphens in skill names or aliases** — Skill command names, aliases, and directory names MUST use underscores (`_`), never hyphens (`-`). Hyphens break Telegram command parsing because Telegram treats the hyphen as a word boundary, cutting the command short. Example: use `dead_code` not `dead-code`, `scaffold_skill` not `scaffold-skill`.
- **Adding a new core skill** — Every core skill requires ALL of the following. Missing any step leaves the skill broken or undiscoverable:
  1. **Skill directory**: Create `koan/skills/core/<skill_name>/SKILL.md` with frontmatter including `name`, `description`, `group` (one of: missions, code, pr, status, config, ideas, system), `commands`, and `audience`. Add `handler.py` if the skill needs Python logic (omit for prompt-only skills).
  2. **Runner registration** (if the skill runs via the agent loop): Add an entry in `_SKILL_RUNNERS` dict in `skill_dispatch.py` mapping the command name to its runner module. Also add any needed command builder in `_COMMAND_BUILDERS` and validation in `validate_skill_args()`. (Quota-detection handling for skill stdout is already centralized in `mission_executor.py` — see the `skill_dispatch.py` note in the module map; nothing per-runner is required.)
  3. **Core skills list**: Update the "Core skills" line in the Skills system section above to include the new skill name (keep alphabetical order).
  4. **User manual and skills reference**: Update `docs/users/user-manual.md` and `docs/users/skills.md` — add the skill to the appropriate tier section and the quick-reference appendix.
  5. **Tests**: The `TestCoreSkillGroupEnforcement` test will fail if the SKILL.md is missing or lacks a `group:` field — run the test suite to verify.
     See `koan/skills/README.md` for the full SKILL.md format and handler conventions.
