# Using Ollama with Kōan

Kōan supports local Ollama models through the `ollama-launch` provider, which
uses `ollama launch claude` to run Claude Code CLI through a local Ollama server.
This is the recommended approach for local model usage — it delegates process
management, model loading, and API routing to Ollama directly.

> **Removed provider**: The `local` provider (direct OpenAI-compatible API loop)
> has been removed. Results were poor and it misled users into thinking a raw
> Ollama connection was the right approach. Use `ollama-launch` instead — it
> routes through Claude Code CLI, the battle-tested harness.

## Quick Setup

### 1. Install Ollama

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh
```

### 2. Pull a model compatible with Claude Code

```bash
ollama pull qwen2.5-coder:14b
```

Ollama's `launch claude` integration requires a model that Ollama's Claude
Code bridge supports. Check [Ollama's documentation](https://ollama.com/docs)
for the current list of supported models.

### 3. Configure Kōan

In `config.yaml`:

```yaml
cli_provider: "ollama-launch"

ollama_launch:
  model: "qwen2.5-coder:14b"
```

Or via environment variables (in `.env`):

```bash
KOAN_CLI_PROVIDER=ollama-launch
KOAN_OLLAMA_LAUNCH_MODEL=qwen2.5-coder:14b
```

### 4. Start Kōan

```bash
make start
```

The `ollama-launch` provider calls `ollama launch claude` internally — it
manages the Ollama server lifecycle. You do not need to run `ollama serve`
separately.

## How It Works

The `ollama-launch` provider builds commands of the form:

```
ollama launch claude --model qwen2.5-coder:14b -- -p "..." --allowedTools ...
```

Everything before `--` is Ollama's responsibility (model selection, server
management). Everything after `--` is passed through to Claude Code CLI verbatim.

## Configuration Reference

### config.yaml

```yaml
cli_provider: "ollama-launch"

ollama_launch:
  model: "qwen2.5-coder:14b"
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KOAN_CLI_PROVIDER` | `claude` | Set to `ollama-launch` to enable |
| `KOAN_OLLAMA_LAUNCH_MODEL` | (none) | Model name on your Ollama server |

## Troubleshooting

### "ollama not found in PATH"

Install Ollama: https://ollama.com/download

### Model not found or unsupported

```bash
# Pull the model
ollama pull qwen2.5-coder:14b

# List available models
ollama list
```

### Claude Code CLI not available through Ollama

`ollama launch claude` requires Ollama v0.16.0+. Check your version:

```bash
ollama --version
```

If upgrading doesn't help, see the [Ollama changelog](https://github.com/ollama/ollama/releases)
for the current status of the Claude Code integration.
