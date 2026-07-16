# Providers

* [CLI reference](claude-cli-commands-official.md) - Official upstream Claude Code CLI reference listing all commands and flags.
* [Claude Code CLI Provider](claude.md) - Setup and configuration guide for Kōan's default Claude Code CLI provider, including models, tools, per-role CLI config, MCP, and devcontainer mode.
* [Cline CLI Provider](cline.md) - Setup and feature-mapping guide for using Cline CLI as Kōan's underlying multi-backend AI provider.
* [OpenAI Codex CLI Provider](codex.md) - Setup and behavior guide for using OpenAI's Codex CLI as Kōan's provider, including quota/usage handling and troubleshooting.
* [GitHub Copilot CLI Provider](copilot.md) - Setup guide and feature/tool-mapping differences for using GitHub Copilot CLI as Kōan's provider.
* [Haze CLI Provider](haze.md) - Setup and behavior guide for using haze (multi-backend agentic CLI) as Kōan's provider, including stream-json integration, usage accounting, capabilities and limitations.
* [Grok Build CLI Provider](grok.md) - Setup and behavior guide for using xAI's Grok Build CLI as Kōan's provider, including headless streaming-json, auth, models, and limitations.
* [Local LLM Provider (removed)](local.md) - Explains that the `local` Ollama provider was removed and points to `ollama-launch` or a custom Claude CLI endpoint as the supported replacements.
* [Ollama Launch Provider](ollama-launch.md) - Documents the `ollama-launch` provider, which runs the Claude Code CLI through `ollama launch claude` for full tool-use/streaming parity with native Claude.
* [Local Ollama via the Claude CLI](ollama-wrapper.md) - Describes the `bin/ollama-claude` wrapper that routes Koan's default `claude` provider through a local Ollama model via `ollama launch claude`, without changing `cli_provider`.
* [OpenCode Go via the Claude CLI](opencode.md) - Describes the `bin/oc-claude` wrapper that routes Koan's Claude CLI invocations through the `ocgo` proxy to run against an OpenCode Go subscription (Kimi, DeepSeek, Qwen, etc.).
* [OpenRouter via Claude Code CLI](openrouter.md) - Explains how to run Koan's Claude CLI provider against OpenRouter models via a local `claude-code-router` (CCR) translation server, including setup, model routing, and caveats.
* [Z.ai (GLM) via the Claude CLI](zai.md) - Documents the `bin/zai-claude` wrapper that points the real Claude CLI at Z.ai's Anthropic-compatible endpoint and maps Anthropic model tiers to GLM models.
