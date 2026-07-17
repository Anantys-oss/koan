---
type: doc
title: "Provider Architecture"
description: "Documents the CLI provider abstraction layer, provider responsibilities, resolution flow, and the current supported providers (Claude, Cline, Codex, Copilot, Haze, Grok, Ollama-launch)."
tags: [architecture]
created: 2026-05-28
updated: 2026-07-16
---

# Provider Architecture

CLI provider code lives under `koan/app/provider/`. New provider behavior should
extend that package rather than adding provider-specific branching throughout the
daemon. See `specs/components/providers.md` for the design contract (the
`CLIProvider` ABC, per-role resolution, and invariants a new provider must honor).

## Responsibilities

Providers are responsible for:

- resolving the executable and authentication assumptions;
- mapping Koan tool permissions to provider-specific flags;
- building commands for print or streaming execution;
- declaring how prompts can be moved from argv to stdin;
- declaring whether invocations must be serialized to protect shared provider
  state such as rotating auth tokens;
- normalizing output handling enough for mission execution code;
- exposing provider capabilities without leaking provider details into unrelated
  modules;
- optionally suppressing project-scope tooling via `project_context=False`
  (Claude: `--setting-sources user`) for KOAN_ROOT runtime sessions — see
  [claude.md](../providers/claude.md) and `specs/components/providers.md`.

Post-CLI text extraction is shared: `mission_runner.parse_claude_output()`
unwraps provider stream/json envelopes (NDJSON `stream-json` /
`streaming-json`, single-object `result`/`text`/`content` keys) into plain
assistant text. Call sites that parse structured model payloads — including
`complexity_classifier._parse_tier_response` — must unwrap first so Grok/Haze
framing does not hide the payload and force fallbacks.

## Resolution Flow

Provider selection is resolved from environment and configuration helpers. Global
configuration can be overridden per project through `projects.yaml`, including
models, tool restrictions, and provider-specific options.

`provider/__init__.py` exposes the registry, cached provider resolution, and
convenience functions. `cli_provider.py` remains a legacy facade; new code should
prefer importing from `koan.app.provider`.

## Current Providers

- Claude provider: Claude Code CLI integration.
- Cline provider: Cline CLI multi-backend integration.
- Codex provider: OpenAI Codex CLI integration.
- Copilot provider: GitHub Copilot CLI integration with tool-name mapping.
- Haze provider: multi-backend agentic CLI with stream-json.
- Grok provider: xAI Grok Build CLI (`cli_provider: grok`) with headless
  `streaming-json`, Claude→Grok tool-name mapping, and always-on
  `--always-approve` for headless tool execution.
- Ollama Launch provider: Claude CLI driven via `ollama launch claude`.

Setup details live in [Provider Setup](../providers/).
