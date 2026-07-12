---
type: doc
title: "Haze CLI Provider"
description: "Setup and behavior guide for using haze (multi-backend agentic CLI) as Kōan's provider, including stream-json integration, usage accounting, capabilities and limitations."
tags: [providers]
created: 2026-07-10
updated: 2026-07-10
---

# Haze CLI Provider

The haze provider lets Kōan use [haze](https://github.com/DenizOkcu/haze) as the
underlying AI agent. Haze is a minimal multi-backend agentic CLI (OpenAI,
OpenRouter, local endpoints such as LM Studio or Ollama) with a focused built-in
toolset for terminal development workflows.

**Official haze documentation: https://denizokcu.github.io/haze/** — the
authoritative reference for haze itself (installation, slash commands,
providers/models, skills, MCP/LSP). This page covers only the Kōan
integration.

**Minimum supported haze version: 0.7.0** — the first release with
`--output stream-json`, which Kōan uses as its primary integration mode. Older
versions fail with a normal CLI error when Kōan requests streaming output.

## Quick Setup

1. **Install haze** (Node.js required):

   ```bash
   npm install -g @denizokcu/haze
   haze --version   # must be >= 0.7.0
   ```

2. **Configure a model backend inside haze.** Haze deliberately reads no
   environment variables — providers, API keys, and model lists live in
   `~/.haze/settings.json`, managed interactively:

   ```bash
   haze          # start interactive mode, then:
   /provider     # add a provider (base URL + API key + models)
   /model        # pick the active model
   ```

3. **Point Kōan at haze** in `instance/config.yaml`:

   ```yaml
   cli_provider: "haze"
   ```

   or via environment: `KOAN_CLI_PROVIDER=haze`.

4. **Verify**: `make status` (or the onboarding wizard) shows the haze provider
   as detected; queue a small mission and watch live `[cli]` progress lines in
   `make logs`.

## How Kōan drives haze

Kōan invokes haze in one-shot headless mode with streaming output:

```
haze [-m <provider:model>] --output stream-json -p "<prompt>"
```

- **Prompt via `-p`** — for now the prompt is passed as a command-line
  argument. Stdin delivery (which would keep very large mission prompts clear
  of OS argument-size limits) is implemented but disabled: haze's stdin
  fallback currently never fires for pipes — it checks
  `process.stdin.isTTY === false` while Node reports `undefined` for
  non-TTY input — so piped runs drop into the interactive UI. Kōan will flip
  to stdin delivery once upstream fixes that gate. Until then, extremely
  large prompts can hit OS per-argument limits (~128KB per argument on
  Linux).
- **Live progress** — haze streams NDJSON events (`turn_start`, message and
  tool lifecycle, `retry`, `context_overflow`, `turn_end`); Kōan renders each
  as a `[cli]` progress line, so liveness watchdogs and stagnation detection
  work exactly as with other providers.
- **Result & usage** — the final stream line is a result envelope
  `{type, status, result, usage}`. `status` is authoritative
  (`complete` = success; `failed`/`aborted` = failure) and token usage
  (`inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`,
  `reasoningTokens`) feeds Kōan's usage accounting. `reasoningTokens` is a
  subset of `outputTokens` and is accounted within it.

## Model Configuration

Haze model selectors take the form `provider:model` (or an unambiguous bare
model name). Configure per-role models via the standard `models:` section:

```yaml
cli_provider: "haze"
models:
  haze:
    mission: "openai:gpt-5"
    chat: "openai:gpt-5-mini"
```

When no model is configured, haze uses its own active model (`/model`).
A bad or ambiguous selector fails fast with a precise error before the agent
runs. **There is no fallback model** — a configured `fallback:` (often
inherited from `models.default.fallback`) is noted once per process at info
level and ignored; set `models: haze: fallback: ""` to silence the note
entirely.

Per-project overrides and the per-role `cli:` section work like any other
provider (see the [user manual](../users/user-manual.md)).

## Tool Configuration

Haze ships a fixed built-in toolset (file operations, bash, grep, fetch,
optional LSP navigation) with **no per-tool CLI control and no confirmation
gates**:

- Kōan tool allow/deny lists are warned about (once per process) and ignored.
- **Permission gating cannot be enforced** — every haze run executes with full
  tool access. Kōan logs a warning when `skip_permissions` is off so this is
  never silent. Factor this into which projects you route to haze.
- File tools are workspace-restricted and respect `.gitignore` by default;
  `fetch` is SSRF-guarded upstream.

## Capabilities & limitations

| Feature | Support |
|---|---|
| Streaming progress (`--output stream-json`) | ✅ primary mode (haze ≥ 0.7.0) |
| Token usage accounting | ✅ from the result envelope |
| Quota/rate-limit + auth failure detection | ✅ backend-agnostic patterns |
| Pre-flight quota probe | ✅ tiny real "ok" run (consumes a few tokens) |
| Model override per run (`-m`) | ✅ never mutates haze settings |
| Custom binary path (`cli: haze:path` / `binary_path`) | ✅ |
| Fallback model | ❌ warned + ignored |
| Session resume | ❌ headless haze is one-shot by design |
| Per-tool allow/deny, MCP flags, plugin dirs, max-turns, effort | ❌ warned + ignored |
| System prompt flag | ❌ prepended to the prompt text instead |

MCP servers and LSP integrations are configured *inside* haze (`/mcp`, `/lsp`),
not through Kōan flags.

## Quota & usage behavior

Haze fronts metered API backends, so Kōan treats it as quota-bearing:

- **Quota exhaustion** (HTTP 429, `insufficient_quota`, billing limits) pauses
  Kōan per its standard quota policy. Haze reports no reset timestamp, so the
  default no-reset pause applies.
- **Auth failures** (401, invalid/missing API key) trigger the standard
  launch/auth fallback policy instead of a quota pause.
- **Pre-flight probe**: before missions, Kōan may run a minimal one-shot
  `haze --output json` probe ("ok") to surface quota/auth problems early.
  This consumes a small number of tokens; probe errors or timeouts never
  block real work. The probe runs from a fresh empty scratch directory —
  never the project — so haze doesn't ingest the repo's `CLAUDE.md`/
  `AGENTS.md` context (~12K tokens) for a two-word check.

## Operational notes

- **Expected `[info] [haze]` log lines**: capability notices for features the
  agent loop always passes but haze doesn't support (tool restrictions, max
  turns, fallback model) are logged once per mission subprocess at info
  level — they describe static provider capabilities, not problems.
  Operator-actionable cases (MCP config, plugin dirs, effort, session resume)
  and the no-permission-gates notice log at warning level.
- **`.haze/tasks.json` in your workspace**: haze persists its task-list state
  into the working directory during runs. Add `.haze/` to the target
  project's `.gitignore` (Kōan's own repo ignores it) so agent branches never
  commit it.
- **Telegram message formatting may fall back to plain text**: haze's startup
  plus context load (~10s+) can exceed Kōan's 30s lightweight-formatting call;
  the fallback is graceful (unformatted message). If formatted messages
  matter, route the lightweight role to a faster provider via the `cli:`
  section, e.g. `cli: { default: { lightweight: claude } }`.

## Troubleshooting

- **"No model provider configured. Run /provider …"** — configure a backend
  inside haze (step 2 above). Kōan classifies this as a launch/config failure,
  not quota.
- **"No configured model named …"** — the `-m` selector doesn't match a model
  configured in haze; check `models.haze.*` values against `/model` output.
- **Unknown `--output` value / streaming errors** — haze is older than 0.7.0;
  upgrade (`npm update -g @denizokcu/haze`).
- **Runs look slow to start** — the invocation lock (`haze-cli`) serializes
  haze runs per user because they share `~/.haze/settings.json` state.
- **Debug logs** — run haze manually with `--debug` to write detailed JSONL
  under `~/.haze/logs/`. Kōan never enables this itself (those logs capture
  full prompts).
