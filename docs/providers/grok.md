---
type: doc
title: "Grok Build CLI Provider"
description: "Setup and behavior guide for using xAI's Grok Build CLI as Kōan's provider, including headless streaming-json, auth, models, and limitations."
tags: [providers]
created: 2026-07-15
updated: 2026-07-15
---

# Grok Build CLI Provider

The Grok provider lets Kōan use [xAI Grok Build](https://x.ai/cli) as the
underlying AI coding agent. Grok Build is xAI's terminal coding agent (TUI +
headless), powered by Grok models (e.g. Grok 4.5).

**Official docs:** [Grok Build overview](https://docs.x.ai/build/overview) ·
[Headless & scripting](https://docs.x.ai/build/cli/headless-scripting)

**Minimum verified version:** Grok Build **0.2.101** (stable). Other versions
with the same headless flags should work; re-capture stream samples if the
NDJSON schema changes.

## Quick Setup

### 1. Install Grok Build

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
grok --version
```

### 2. Authenticate

```bash
# Browser login (interactive)
grok

# Or API key for headless / servers
export XAI_API_KEY="xai-..."
```

### 3. Configure Kōan

**Option A: `instance/config.yaml`**

```yaml
cli_provider: "grok"
```

**Option B: environment**

```bash
export KOAN_CLI_PROVIDER=grok
```

The env var overrides `config.yaml` when both are set.

### 4. Model selection (optional)

```yaml
cli_provider: "grok"
models:
  grok:
    mission: "grok-4.5"
    chat: "grok-4.5"
    lightweight: "grok-4.5"
    review_mode: "grok-4.5"
    fallback: ""   # ignored — Grok Build has no fallback-model flag
```

When unset, Grok Build uses its own default model (`~/.grok/config.toml` /
interactive `/model`). Confirm available models with `grok inspect` after
install.

Per-role binary pin (same pattern as other flavors):

```yaml
cli:
  default:
    review_mode: grok:/path/to/custom-grok
```

## How Kōan drives Grok

Kōan invokes headless Grok Build with streaming output:

```text
grok --always-approve|--permission-mode acceptEdits \
     [--tools …] [-m <model>] --output-format streaming-json \
     [--max-turns N] [-p "<prompt>"]
```

| Concern | Behavior |
|---|---|
| Prompt | `-p` / `--single` (argv). Stdin prompt passing is not used. |
| Stream | `--output-format streaming-json` → NDJSON `thought` / `text` / `end` |
| Final text | Concatenated `text` deltas (`data` fields); `end` has usage, not body |
| Usage | Snake_case `usage` on `end` + optional `modelUsage` map |
| Permissions | `skip_permissions: true` → `--always-approve`; otherwise `--permission-mode acceptEdits` (avoids interactive hangs) |
| Tools | `--tools` / `--disallowed-tools` (comma-separated) |
| System prompt | `--rules` (append). File paths are inlined into `--rules` |
| Effort | `--reasoning-effort` when configured |
| Sessions | `--resume <id>` when resume is requested |
| Concurrency | Invocation lock `grok-cli` (shared `~/.grok/` state) |

Live progress lines appear as `[cli] …` in `make logs` (thinking, text
previews, terminal `end`).

## Capabilities and limitations

**Supported (MVP):**

- Headless missions and skill runners via `run_command` / `run_command_streaming`
- Stream progress + token usage accounting
- Model override (`-m`)
- Tool allow/deny lists (as supported by Grok Build)
- Max turns, reasoning effort, session resume
- Quota / auth detection (stderr + failed stdout); soft pre-flight probe

**Not supported / ignored with a notice:**

- Fallback model (`models.*.fallback`)
- MCP config via CLI flags
- Plugin directory flags
- Claude-identical tool vocabulary (pass Claude names; Grok maps or ignores unknown tool ids)

**Safety note:** `--always-approve` auto-approves tool execution. Prefer
restricting tools for read-only roles (e.g. `/review` already passes
`Read,Glob,Grep` only). Headless without skip still uses `acceptEdits` so the
agent does not block forever on permission prompts.

## Troubleshooting

| Symptom | Check |
|---|---|
| `Unknown CLI provider: grok` | Update to a build that includes the Grok provider; restart `run`/`awake` |
| Provider not ready / not installed | `which grok` and reinstall from https://x.ai/cli |
| Auth failures | `export XAI_API_KEY=…` or run interactive `grok` login once |
| Rate limits pause Koan | Expected; wait or switch provider / raise quota |
| Empty skill output | Confirm `--output-format streaming-json` events still match samples in `koan/tests/grok_samples.py` |

## Related

- Design contract: `specs/components/providers.md` (Grok Build headless contract)
- Architecture: [Provider Architecture](../architecture/providers.md)
- Recorded fixtures: `koan/tests/grok_samples.py`
- Issue: https://github.com/Anantys-oss/koan/issues/2400
