# Provider Architecture

CLI provider code lives under `koan/app/provider/`. New provider behavior should
extend that package rather than adding provider-specific branching throughout the
daemon.

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
  modules.

## Resolution Flow

Provider selection is resolved from environment and configuration helpers. Global
configuration can be overridden per project through `projects.yaml`, including
models, tool restrictions, and provider-specific options.

`provider/__init__.py` exposes the registry, cached provider resolution, and
convenience functions. `cli_provider.py` remains a legacy facade; new code should
prefer importing from `koan.app.provider`.

## Current Providers

- Claude provider: Claude Code CLI integration.
- Codex provider: OpenAI Codex CLI integration.
- Copilot provider: GitHub Copilot CLI integration with tool-name mapping.
- Local provider: local model server integration.

Setup details live in [Provider Setup](../providers/).
