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
- stop when clean, no fix is produced, a provider/push error occurs,
  `private_review_gate.max_rounds` is reached, or a fix round makes no progress
  (the round's findings are identical to the previous round's — a convergence
  bail that avoids burning the remaining rounds re-fixing the same findings).

Because it reuses `build_review_prompt`, the gate's review sees the same project
memory as `/review`: filtered learnings plus human-curated context/priorities
(always), and optionally recent typed session memory when `review_memory` is
enabled. The owning skill threads its known `project_name` through so memory is
scoped to the right project rather than guessed from the directory name.

The gate must not post GitHub review comments, issue comments, review verdicts,
or PR-close decisions. Its configuration lives under
`private_review_gate` in `config.yaml`, with per-project overrides in
`projects.yaml`. It is **opt-in** (disabled by default during the testing
phase): set `private_review_gate.enabled: true` to turn it on.

Two cost controls keep the gate quota-aware (both default on, toggled via
`budget_aware` / `dedup`):

- **Budget preflight** — before running, the gate consults the usage governor
  (`usage_tracker.UsageTracker` / `burn_rate.BurnRateSnapshot`) and scales its
  round budget to the current mode: `deep` → full `max_rounds`, `implement` → 2,
  `review` → 1, `wait` or near-exhaustion (time-to-exhaustion below
  `BURN_RATE_DOWNGRADE_THRESHOLD_MIN`) → skip entirely. Unlimited/disabled quota
  bypasses the check.
- **Head-SHA dedup** — a tracker (`instance/.private-review-gate-tracker.json`,
  same pattern as `ci_dispatch`) records each PR head reviewed clean. A re-run
  on an unchanged head (e.g. a repeated `/rebase`) is skipped rather than
  re-reviewed.

The gate's review/reflection calls run through `run_command_streaming`, whose
per-call token usage is now **accumulated** into the skill-dispatch usage
sidecar (`KOAN_STREAM_USAGE_FILE`) rather than overwritten. The mission's
post-mission accounting therefore reflects the gate's review cost (and the main
skill work), keeping the usage governor's view honest.

## Documentation Contract

When adding, removing, or changing a core skill:

- update `docs/users/user-manual.md`;
- update `docs/users/skills.md`;
- keep `CLAUDE.md`, `AGENTS.md`, and `.github/copilot-instructions.md` guidance
  aligned when core skill rules change;
- run the relevant core skill tests.

The full authoring guide remains in `koan/skills/README.md`.
