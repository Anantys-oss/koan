---
type: doc
title: "Gemini CLI Provider"
description: "Setup and behavior guide for using Google's Gemini CLI as Kōan's provider, including headless stream-json, auth, models, and limitations."
tags: [providers]
created: 2026-09-01
updated: 2026-09-01
---

# Gemini CLI Provider

The Gemini provider lets Kōan use
[Gemini CLI](https://github.com/google-gemini/gemini-cli) as the underlying AI
coding agent. Gemini CLI is Google's open-source terminal agent (interactive
REPL + headless), powered by Gemini models.

**Official docs:**
[Headless mode](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md) ·
[CLI reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/cli-reference.md)

> **Status: documented, not measured.** This adapter was built from the
> upstream event schema and flag reference, not from a live run on a Kōan host.
> The flags and stream shapes below match upstream source
> (`packages/core/src/output/types.ts`, `packages/cli/src/nonInteractiveCli.ts`)
> as of 2026-09-01. If your `gemini` build behaves differently, that is a bug in
> this adapter — please report it with the raw stream. Contrast with
> [grok.md](grok.md) and [haze.md](haze.md), which were verified live.

## Quick setup

```bash
npm install -g @google/gemini-cli   # or: brew install gemini-cli
gemini                              # first run: "Login with Google" OAuth
# …or use an API key instead of OAuth:
export GEMINI_API_KEY="…"
```

Then in `instance/config.yaml`:

```yaml
cli_provider: gemini

# Recommended for autonomous missions — see "Permissions" below.
skip_permissions: true

models:
  gemini:
    mission: "pro"          # or a concrete id: gemini-2.5-pro
    chat: "flash"
    lightweight: "flash-lite"
    review_mode: "pro"
```

`KOAN_CLI_PROVIDER=gemini` also works and overrides `config.yaml`.

**Model names must be Gemini's.** The aliases are `auto` (default), `pro`,
`flash`, `flash-lite`; concrete ids like `gemini-2.5-pro` also work. If a Claude
tier alias (`haiku`/`sonnet`/`opus`) leaks in from `models.default`, Kōan drops
`-m` with a one-time warning rather than sending Gemini a model id it will
reject — you get Gemini's default instead of a hard failure.

## How Kōan drives Gemini

| Kōan input | Gemini flag |
|---|---|
| prompt | `-p <prompt>` (argv) |
| model | `-m <model>` |
| streaming | `--output-format stream-json` |
| probe / one-shot | `--output-format json` |
| `skip_permissions: true` | `--approval-mode yolo` |
| session resume | `--resume <id>` |

Kōan reads the NDJSON event stream and renders each event as a `[cli] …`
progress line (visible in `make logs` and `/live`):

| Event | Meaning |
|---|---|
| `init` | session id + model |
| `message` (`role: assistant`, `delta: true`) | assistant text chunks — concatenated, not newline-joined |
| `tool_use` | tool call + a bounded input preview |
| `tool_result` | tool outcome; failures carry a reason excerpt |
| `error` | non-fatal warning |
| `result` | terminal envelope: `status` + `stats` |

**Usage accounting** comes from `result.stats`, which is where Gemini puts
tokens (not under `usage` like most other CLIs):
`input_tokens` / `output_tokens` / `cached` / `total_tokens` plus a per-model
`models` map. `cached` is a subset of `input_tokens`, so Kōan subtracts it —
input counts exclude cache hits, matching every other provider.

**Final text** is the concatenation of assistant `message` deltas. The terminal
`result` event carries statistics only, no assistant body, so a stream that dies
mid-flight still returns whatever text arrived.

## Permissions

Kōan emits `--approval-mode yolo` **only** when `skip_permissions: true`.

With permissions on, no approval flag is passed and Kōan logs a one-time
warning: a headless run cannot answer a confirmation prompt, so a tool call that
would prompt may fail. That is deliberate — the adapter will not silently
escalate past the posture you configured. For autonomous missions
(`/implement`, `/fix`, …) set `skip_permissions: true`.

## Capabilities and limitations

| Feature | Status |
|---|---|
| Headless stream-json | ✅ |
| Usage / token accounting | ✅ (`result.stats`) |
| Model selection | ✅ (`-m`, Gemini ids/aliases only) |
| Session resume | ✅ (`--resume`) |
| Quota / auth detection | ✅ (429 / `RESOURCE_EXHAUSTED`, 401 / invalid key) |
| Pre-flight quota probe | ✅ (tiny `-p ok` run from an empty scratch dir) |
| Per-tool allow/deny | ❌ upstream `--allowed-tools` is deprecated (Policy Engine); not wired |
| **Read-only roles (`/review`)** | ❌ **refused** — see below |
| Max turns | ❌ no headless flag |
| MCP config files | ❌ register servers with `gemini mcp add` instead |
| Plugin dirs | ❌ use `gemini extensions` |
| Reasoning effort | ❌ no flag |
| Fallback model | ❌ |
| System-prompt file | ❌ prepended to the user prompt instead |
| stdin prompt passing | ❌ Gemini *appends* `-p` to stdin rather than replacing it |

Every unsupported input warns once per process and is skipped — never silently
accepted.

**`/review` is refused on Gemini.** A read-only role is a security boundary, and
Kōan only claims it for providers where the enforcement was *measured*. Gemini
has `--approval-mode plan` and `--sandbox`, and its `--allowed-tools` is a
pre-approval list rather than a withholding one — none of that has been verified
to withhold write access, so the invocation fails closed with a configuration
error. Pin `cli.review_mode` to `claude`, `codex`, or `ollama-launch`; Gemini
stays available for every other role:

```yaml
cli:
  default:
    mission: gemini
    review_mode: claude
```

## Troubleshooting

**"CLI executable not found: gemini"** — `npm install -g @google/gemini-cli`, or
point `cli_provider: gemini:/path/to/gemini` at the binary.

**Every mission fails on a tool call** — set `skip_permissions: true`; headless
Gemini cannot answer approval prompts.

**Missions run with the wrong model** — check the log for the "is a Claude alias
unknown to Gemini CLI" warning and set `models.gemini.*` to Gemini ids.

**Kōan pauses for quota unexpectedly** — Gemini's free tier has daily caps;
`RESOURCE_EXHAUSTED` / HTTP 429 on stderr pauses the loop by design. Stdout is
scanned only on a non-zero exit, so assistant prose about rate limits cannot
trigger a false pause.

## Related

- [Provider index](index.md)
- [Grok Build provider](grok.md) — the closest-shaped adapter (headless NDJSON)
- `specs/components/providers.md` — the durable contract ("Gemini CLI headless
  contract")
- `koan/tests/gemini_samples.py` — the recorded stream shapes
