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

## Private Implementation Review Gate

`/fix`, `/implement`, and `/rebase` can call the shared private review gate to
run a backend-only challenge loop:

- fetch current PR context and analyze it through the same structured review
  prompt/schema/reflection path as `/review`;
- filter findings to the configured minimum severity (`warning`/Important by
  default);
- run a write-capable fix step on the same branch, commit and push fixes with
  the caller's branch update strategy, then re-review;
- stop when clean, no fix is produced, a provider/push error occurs, or
  `private_review_gate.max_rounds` is reached.

Because it reuses `build_review_prompt`, the gate's review sees the same project
memory as `/review`: filtered learnings plus human-curated context/priorities
(always), and optionally recent typed session memory when `review_memory` is
enabled. The owning skill threads its known `project_name` through so memory is
scoped to the right project rather than guessed from the directory name.

The gate must not post GitHub review comments, issue comments, review verdicts,
or PR-close decisions. Its configuration lives under
`private_review_gate` in `config.yaml`, with per-project overrides in
`projects.yaml`.

## Documentation Contract

When adding, removing, or changing a core skill:

- update `docs/users/user-manual.md`;
- update `docs/users/skills.md`;
- keep `CLAUDE.md`, `AGENTS.md`, and `.github/copilot-instructions.md` guidance
  aligned when core skill rules change;
- run the relevant core skill tests.

The full authoring guide remains in `koan/skills/README.md`.
