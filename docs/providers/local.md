# Local LLM Provider (removed)

> **The `local` provider has been removed.** It plugged an Ollama
> OpenAI-compatible endpoint directly into Kōan via a homegrown agentic
> loop, which produced unreliable results.

## Run local models the supported way

- **[`ollama-launch`](ollama-launch.md)** — drives the Claude CLI through
  `ollama launch claude`, so local models run behind a battle-tested
  harness. Set `KOAN_CLI_PROVIDER=ollama-launch`.
- **Claude CLI against a custom endpoint** — point the Claude CLI at any
  local/OpenAI-compatible server. See [claude.md](claude.md) (Custom CLI
  Binary) and [openrouter.md](openrouter.md).

Ollama itself: https://github.com/ollama/ollama
