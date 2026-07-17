---
type: doc
title: "Grok Build CLI Provider"
description: "Setup and behavior guide for using xAI's Grok Build CLI as Kōan's provider, including headless streaming-json, auth, models, and limitations."
tags: [providers]
created: 2026-07-15
updated: 2026-07-17
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
skip_permissions: true   # recommended — see Permissions below
models:
  grok:
    mission: "grok-4.5"
    chat: "grok-composer-2.5-fast"
    lightweight: "grok-composer-2.5-fast"
    review_mode: "grok-4.5"
    reflect: "grok-4.5"
    fallback: ""   # ignored — Grok Build has no fallback-model flag
```

**Option B: environment**

```bash
export KOAN_CLI_PROVIDER=grok
```

The env var overrides `config.yaml` when both are set.

Confirm available models with `grok models` after install. Do **not** leave
Claude tier names (`haiku` / `sonnet` / `opus`) in `models.default` when
running Grok without a `models.grok` override — Kōan will omit invalid `-m`
values, but lightweight paths work better with real Grok ids.

Per-role binary pin (same pattern as other flavors):

```yaml
cli:
  default:
    review_mode: grok:/path/to/custom-grok
```

## How Kōan drives Grok

Kōan invokes headless Grok Build with streaming output:

```text
grok --always-approve \
     [--tools …] [-m <model>] --output-format streaming-json \
     [--max-turns N] (-p "<prompt>" | --prompt-file <path>)
```

| Concern | Behavior |
|---|---|
| Prompt | Short prompts: `-p` / `--single`. Large prompts: `--prompt-file` (temp file). Stdin not used. |
| Stream | `--output-format streaming-json` → NDJSON `thought` / `text` / `end` |
| Final text | Concatenated `text` deltas (`data` fields, join with `""`); `end` has usage, not body. Skill steps (`run_claude` / `/rebase`) use the same delta join as mission streaming. |
| Usage | Snake_case `usage` on `end` + optional `modelUsage` map |
| Permissions | **Always** `--always-approve` for headless (see below) |
| Tools | Claude names mapped to Grok ids on `--tools` / `--disallowed-tools` |
| System prompt | `--rules` (append). File paths are inlined into `--rules` |
| Effort | `--reasoning-effort` when configured |
| Sessions | `--resume <id>` when resume is requested |
| Concurrency | Invocation lock `grok-cli` (shared `~/.grok/` state) |
| Cancelled | `end` with `stopReason: Cancelled` is a **hard failure** |

Live progress lines appear as `[cli] …` in `make logs` (thinking, text
previews, terminal `end`).

### Permissions (required reading)

Grok Build headless **cannot** answer interactive permission prompts. Grok’s
own docs state that the CLI `--permission-mode` flag only effectively applies
`bypassPermissions` and `default` — passing `acceptEdits` does **not** enable
that policy. Any tool call that would prompt is **cancelled immediately**
(`stopReason: Cancelled`, `permission_cancelled`), which is how `/implement`
failed with “No committed changes after two passes” while `/plan` (mostly
read-only tools) still worked.

Kōan therefore always passes `--always-approve` for Grok headless invokes.
Set `skip_permissions: true` in `config.yaml` to match other providers and
silence the once-per-process notice. Restrict tools for read-only roles
(e.g. `/review` / `/plan` already pass narrow allowlists).

### Tool name mapping

| Kōan / Claude | Grok `--tools` id |
|---|---|
| `Read` | `read_file` |
| `Write` | `write` |
| `Edit` | `search_replace` |
| `Bash` | `run_terminal_cmd` |
| `Grep` | `grep` |
| `Glob` | `list_dir` |
| `WebFetch` | `web_fetch` |
| `Skill` | omitted (no Grok peer) |

## Capabilities and limitations

**Supported (MVP):**

- Headless missions and skill runners via `run_command` / `run_command_streaming`
- Stream progress + token usage accounting
- Model override (`-m`), with Claude-alias rejection
- Tool allow/deny lists with Claude→Grok mapping
- Max turns, reasoning effort, session resume
- Large-prompt `--prompt-file` delivery
- Quota / auth detection (stderr + failed stdout); soft pre-flight probe

**Not supported / ignored with a notice:**

- Fallback model (`models.*.fallback`)
- MCP config via CLI flags
- Plugin directory flags

**Safety note:** `--always-approve` auto-approves tool execution. Prefer
restricting tools for read-only roles rather than relying on interactive
permission prompts (which headless cannot honor).

## Troubleshooting

| Symptom | Check |
|---|---|
| `Unknown CLI provider: grok` | Update to a build that includes the Grok provider; restart `run`/`awake` |
| Provider not ready / not installed | `which grok` and reinstall from https://x.ai/cli |
| Auth failures | `export XAI_API_KEY=…` or run interactive `grok` login once |
| Rate limits pause Koan | Expected; wait or switch provider / raise quota |
| `Couldn't set model 'haiku'` | Set `models.grok.lightweight` (etc.) to a real Grok id; avoid Claude defaults |
| `stopReason: Cancelled` / no commits after `/implement` | Headless permission cancel — ensure provider emits `--always-approve` (fixed in current Grok provider); set `skip_permissions: true` |
| Empty skill output | Confirm `--output-format streaming-json` events still match samples in `koan/tests/grok_samples.py` |
| Rebase/PR replies look like one-token-per-line bullets | Fixed: skill-path stream parse must join Grok `text` deltas with `""` (not newlines). If it returns, check `run_claude` stream accumulation vs `run_command_streaming`. |
| Burn-rate alerts look absurd (e.g. 200–300%/h) | Token accounting is correct; the % is vs `usage.session_token_limit` (Claude-style estimate, default 500k/5h). Grok API billing is pay-as-you-go — either tune `session_token_limit` to your real budget, or set `usage.unlimited_quota: true` / `budget_mode: disabled` to skip proactive % gating. Burn-rate soft-throttles only (never forces wait) and needs ≥15 min of samples. |

## Related

- Design contract: `specs/components/providers.md` (Grok Build headless contract)
- Architecture: [Provider Architecture](../architecture/providers.md)
- Recorded fixtures: `koan/tests/grok_samples.py`
- Issue: https://github.com/Anantys-oss/koan/issues/2400
