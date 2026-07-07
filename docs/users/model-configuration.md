---
type: doc
title: "Model Configuration"
tags: [users]
created: 2026-06-07
updated: 2026-06-30
---

# Model Configuration

Kōan lets you choose which model handles each type of call (mission execution,
chat, low-cost helpers, fallback, review mode, reflection). Configuration lives
in `instance/config.yaml` under the `models:` section, with optional per-project
overrides in `projects.yaml`.

## Structure

```yaml
models:
  default:                  # Global fallback — applies to every provider
    mission: ""             # Main mission execution (empty = subscription default)
    chat: ""                # Telegram/dashboard chat responses
    lightweight: "haiku"    # Low-cost calls: format_outbox, pick_mission, contemplative
    fallback: "sonnet"      # Fallback when primary model is overloaded
    review_mode: ""         # Override model for REVIEW mode (cheaper audits)
    reflect: ""             # Review reflection pass (empty = lightweight)

  claude:                   # Provider-specific overrides for the Claude harness
    mission: "opus"
    review_mode: "opus"

  cline:                    # Provider-specific overrides for the Cline harness
    mission: "claude-sonnet-4-20250514"
    chat: "claude-3-5-haiku-20241022"

  codex:                    # Provider-specific overrides for the Codex harness
    mission: "gpt-5.3-codex"
    chat: "gpt-5.5"
```

- `models.default` applies to all providers.
- `models.{provider}` overrides specific roles for that provider only.
- Provider names may use hyphens or underscores as literal keys
  (`ollama-launch` or `ollama_launch` both work).

## Resolution order

For each role, the first value found wins:

1. `projects.yaml` → `models.{role}` for the active project
2. `config.yaml` → `models.{provider}.{role}` (current provider)
3. `config.yaml` → `models.default.{role}`
4. Built-in default

When both a provider override and `models.default` set the same role, the
provider value wins.

## CLI provider per role (the `cli:` section)

By default every role runs on the single global provider (`cli_provider` /
`KOAN_CLI_PROVIDER`). To run a **different provider per role** — e.g. routine
work on Codex but reviews on Claude — add a `cli:` section parallel to `models:`:

```yaml
cli:
  default:
    mission: codex
    review_mode: claude:/path/to/review-claude   # flavor or flavor:path
  fallback: claude                               # used only when a role's CLI can't launch
```

The role's **model** is then read from that role's provider block above. With
`review_mode: claude`, the review model comes from `models.claude.review_mode`
(then `models.default.review_mode`) — so `cli:` selects the provider and
`models.<provider>.<role>` selects the model for it. Per-project overrides go in
`projects.yaml` as a flat `cli:` block. Full details, including the single
`cli.fallback` provider (launch/auth-failure recovery), are in
[../providers/claude.md](../providers/claude.md).

## Reasoning effort per mission type (the `effort:` section)

The Claude provider accepts an `--effort` flag (`low`/`medium`/`high`/`max`)
that trades cost for reasoning depth. By default Kōan picks effort **dynamically
from the current budget mode**: `review` → `low`, `deep` → `high`, otherwise no
flag (provider default). This keeps cheap audits cheap and deep reasoning deep,
and is left untouched when you omit the `effort:` section.

To pin effort **per mission type**, add an `effort:` section. Keys are mission
types — the categories Kōan classifies internally — not budget modes:

```yaml
effort:
  autonomous: low      # keep background autonomous work cheap
  freetext: medium     # plain human-text missions
```

A pinned type wins over the dynamic default regardless of the budget mode the
mission happens to run in. Resolution order: `effort.<mission_type>` →
`effort.<budget_mode>` (legacy) → dynamic default. A single string
(`effort: high`) applies to all missions; an empty value (`effort: ""` or
`effort: { refactor: "" }`) disables the flag.

**Valid mission-type keys** (the full `classify_mission_type` taxonomy):
`autonomous`, `freetext`, `plan`, `review`, `rebase`, `recreate`, `implement`,
`refactor`, `audit`, `check`, `maintenance`, `pr`, `chat`, `incident`. Any key
outside this set never matches a mission, so a pin on it is silently inert.
Because the taxonomy is open (new skills add types), the config validator
accepts any key and only checks that the value is a real level — a typo'd key
will not warn, it just never fires. Of these, only the types listed under
**Scope** below can actually take effect today.

> **Scope.** A per-type pin only takes effect for missions that reach the main
> agent loop's `build_mission_command` — in practice only **`autonomous`**
> (background autonomous work) and **`freetext`** (plain human-text missions).
> Every named slash-command type is handled *before* that point and is **not**
> governed by the `effort:` section: commands with a dedicated skill runner —
> `/review`, `/plan`, `/rebase`, `/recreate`, `/implement`, `/fix`, `/audit`,
> `/check`, … — run in their own runners, and commands with no runner (e.g.
> `/refactor`, `/pr`) are handled by their bridge-side handler or fail as an
> unknown skill. So pinning `review: low` — or `refactor: high` — has no
> effect. To control effort for one of those, configure that skill's runner
> directly.

> Note: effort is a no-op when extended thinking is active for a mission
> (thinking already implies max effort) and is ignored by providers whose CLI
> has no `--effort` flag.

## Migrating from the legacy layout

Earlier versions used a flat `models:` block plus top-level `models_for_{provider}`
keys. Both still work, but emit a one-time `DEPRECATED` warning at startup.

| Legacy                         | New                              |
| ------------------------------ | -------------------------------- |
| `models.mission`, `models.chat`| `models.default.mission`, …      |
| `models_for_claude:`           | `models.claude:`                 |
| `models_for_cline:`            | `models.cline:`                  |
| `models_for_codex:`            | `models.codex:`                  |
| `models_for_ollama_launch:`    | `models.ollama-launch:`          |

If a legacy flat key and a new `models.default` are both present during a
partial migration, the explicit `models.default` wins — leftover flat keys never
clobber the new structure.

To silence the warning, move every legacy key into the nested form above.
