# Skills System

Skills are Koan's command extension mechanism. Core skills live under
`koan/skills/core/`; custom skills load from `instance/skills/<scope>/`.

## Skill Definition

Each skill has a `SKILL.md` file with YAML-style frontmatter. Core skills must
define `name`, `description`, `group`, `commands`, and `audience`. Optional
fields control aliases, worker execution, GitHub exposure, context-aware
dispatch, combo skills, and other behavior.

Skill names, aliases, and directories use underscores, not hyphens.

## Dispatch Paths

- `skills.py` discovers skills, parses frontmatter, builds command registries,
  and executes handlers.
- `command_handlers.py` routes bridge slash commands.
- `skill_dispatch.py` runs selected slash-command missions directly from the
  agent loop when no full provider session is needed.
- `external_skill_dispatch.py` executes custom integration skills in process for
  GitHub and Jira originated commands.

Prompt-only skills omit `handler.py`; their Markdown prompt body is sent through
the agent path.

## Documentation Contract

When adding, removing, or changing a core skill:

- update `docs/users/user-manual.md`;
- update `docs/users/skills.md`;
- keep `CLAUDE.md`, `AGENTS.md`, and `.github/copilot-instructions.md` guidance
  aligned when core skill rules change;
- run the relevant core skill tests.

The full authoring guide remains in `koan/skills/README.md`.
