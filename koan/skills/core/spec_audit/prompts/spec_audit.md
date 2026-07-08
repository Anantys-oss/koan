You are performing a **spec-drift audit** of the **{PROJECT_NAME}** project. Your goal is to find divergences between documentation and code, then produce a structured report.

## Instructions

### Phase 1 — Inventory

1. **List all core skills**: Use Glob to find every `koan/skills/core/*/SKILL.md`. For each, extract `name`, `description`, `commands` (with aliases), and `group` from the frontmatter.
2. **Read the Quick Reference**: Read `docs/users/user-manual.md` and find the Quick Reference table. Extract every command listed there.
3. **Read CLAUDE.md**: Find the "Core skills" list in the Skills system section.
4. **Read docs/messaging/github-commands.md**: Extract all documented GitHub @mention commands.
5. **Read docs/users/skills.md**: Note the skill authoring conventions documented there.

### Phase 2 — Cross-Reference

Check for these categories of drift:

#### A. Missing from Docs
- Skills that exist in `koan/skills/core/` but are NOT listed in `docs/users/user-manual.md` Quick Reference.
- Skills present in code but missing from the CLAUDE.md "Core skills" list.
- GitHub-enabled skills (`github_enabled: true` in SKILL.md) not documented in `docs/messaging/github-commands.md`.

#### B. Missing from Code
- Commands listed in `docs/users/user-manual.md` Quick Reference that have no matching `SKILL.md` in `koan/skills/core/`.
- Skills referenced in CLAUDE.md "Core skills" list that don't exist as directories under `koan/skills/core/`.

#### C. Description Mismatches
- Skill descriptions in `docs/users/user-manual.md` that differ significantly from the `description` field in the corresponding `SKILL.md`.
- Command aliases in docs that don't match the `aliases` list in `SKILL.md`.
- Skill groups in SKILL.md that don't match the tier/section where they appear in the user manual.

#### D. Behavioral Drift
- Check `koan/app/github_command_handler.py` for hardcoded command lists and verify they match `docs/messaging/github-commands.md`.
- Check `koan/app/command_handlers.py` for `_CORE_COMMAND_HELP` and verify it matches the actual commands.
- Check `koan/app/skill_dispatch.py` for `_CANONICAL_RUNNERS` and verify each registered skill exists.

#### E. Wiki Hygiene
This project's `docs/` and the durable half of `specs/` (`specs/components/`, `specs/skills/`) are indexed as an LLM Wiki — see `wiki/SCHEMA.md` for full conventions. Check:
- **Missing/stale frontmatter**: any file under `docs/`, `specs/components/`, or `specs/skills/` (excluding `specs/skills/SKILL_SPEC_TEMPLATE.md`) that lacks the `type`/`title`/`tags`/`created`/`updated` YAML block, or whose `updated:` date is clearly older than the file's actual last git-log modification date.
- **Stale `wiki/index.md` entries**: pages that exist but have no `wiki/index.md` entry, entries pointing at pages that no longer exist, or one-line summaries that no longer match the page's actual content after a substantive edit.
- **Speckit feature status drift**: for each `specs/<NNN-slug>/tasks.md`, recompute the checkbox ratio (`grep -cE '^\s*-\s*\[[xX]\]'` vs `^\s*-\s*\[[ ]\]'`) and compare against the status `wiki/index.md` currently records under "Specs — Active Features" (`draft` / `in-progress` / `shipped`). Flag any mismatch.
- **Shipped-but-not-reconciled**: a `specs/<NNN-slug>/` folder whose tasks are ~100% checked (or whose branch has clearly merged) but whose corresponding `specs/components/<group>.md` shows no matching update — per the mandatory "Specs discipline" rule in `CLAUDE.md`, this is incomplete.
- Do **not** flag `specs/<NNN-slug>/*.md` files themselves for missing frontmatter — that's a deliberate exemption documented in `wiki/SCHEMA.md`, not drift.

### Phase 3 — Produce the Report

Output a structured report in this exact format:

```
Spec-Drift Report — {PROJECT_NAME}

## Summary

[2-3 sentence overview of the documentation health]

**Drift Score**: [1-10]/10

(1 = perfectly aligned, 10 = severely drifted)

## Findings

### Missing from Docs

[Numbered list of skills/commands missing from documentation, with the specific doc file that needs updating]

### Missing from Code

[Numbered list of documented commands that don't exist in code]

### Description Mismatches

[Numbered list of description/alias discrepancies between docs and SKILL.md files]

### Behavioral Drift

[Numbered list of code behavior that doesn't match documentation]

### Wiki Hygiene

[Numbered list of missing/stale frontmatter, stale index.md entries, or speckit status drift, with the specific file(s) to fix]

## Suggested Missions

1. [Most impactful fix — one sentence with specific files to update]
2. [Second most impactful fix]
3. [Third most impactful fix]
```

## Rules

- **Read-only.** Do not modify any files. This is a pure analysis task.
- **Be specific.** Always name the exact file and section where drift was found.
- **Ignore minor wording.** Only flag description mismatches that could mislead a user. Cosmetic phrasing differences are not drift.
- **Limit scope.** Report at most 10 findings total across all categories. Focus on the most impactful divergences.
- **Suggested missions must be self-contained.** Each should be fixable in a single focused session by updating documentation or adding missing SKILL.md fields.
