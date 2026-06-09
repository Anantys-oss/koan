# Ollama Launch Provider

The `ollama-launch` provider uses Ollama v0.16.0+ ``ollama launch claude`` to run
the Claude Code CLI through an Ollama-managed server. Ollama handles the
``ANTHROPIC_BASE_URL`` environment variable and server lifecycle automatically,
so no manual configuration is needed.

Because everything after the ``--`` separator is forwarded to the Claude Code
CLI verbatim, this provider supports the full Claude feature set: native tool
calling, JSONL streaming, session resume, MCP servers, effort/thinking levels,
and Claude-style quota detection.

## Quick Setup

### 1. Install Ollama

```bash
# macOS
brew install ollama

# Or download from https://ollama.com/download
```

Verify the version supports ``launch claude``:

```bash
ollama --version        # Must be v0.16.0 or later
ollama launch claude --help
```

### 2. Pull a Model

```bash
ollama pull qwen2.5-coder:14b
# Or any model you prefer
ollama list
```

### 3. Configure Koan

In `instance/config.yaml`:

```yaml
cli_provider: "ollama-launch"

ollama_launch:
  model: "qwen2.5-coder:14b"
```

Or via environment variable (in `.env`):

```bash
KOAN_CLI_PROVIDER=ollama-launch
KOAN_OLLAMA_LAUNCH_MODEL=qwen2.5-coder:14b
```

Environment variables override `config.yaml` values.

### 4. Start Koan

```bash
make start
```

Unlike the `local` provider, you do **not** need to run `ollama serve`
separately. The `ollama launch claude` command starts the Ollama server
on demand.

To stop:

```bash
make stop
```

### 5. Verify

Send a test mission via Telegram or check the logs:

```bash
make logs
```

You should see the CLI command built as:

```
ollama launch claude --model <model> -- -p <prompt> ...
```

## Per-Project Configuration

Use `ollama-launch` for specific projects while keeping Claude for others:

```yaml
# projects.yaml
defaults:
  cli_provider: "claude"

projects:
  critical-app:
    path: "/path/to/app"
    # Uses Claude (default)

  side-project:
    path: "/path/to/side"
    cli_provider: "ollama-launch"
    models:
      mission: "qwen2.5-coder:14b"
```

## Provider-Specific Model Configuration

In `instance/config.yaml`:

```yaml
models:
  ollama-launch:
    mission: "qwen2.5-coder:14b"
    chat: "qwen2.5-coder:14b"
    lightweight: "qwen2.5-coder:7b"
    fallback: ""
    review_mode: "qwen2.5-coder:14b"
    reflect: "qwen2.5-coder:7b"

  default:
    mission: ""
    chat: ""
    lightweight: "haiku"
    fallback: "sonnet"
```

Provider names may use hyphens or underscores (`ollama-launch` or
`ollama_launch`).

## How It Works

The command structure is:

```bash
ollama launch claude --model <model> -- <claude-flags>
```

- **Before `--`**: Ollama args (`launch`, `claude`, `--model`)
- **After `--`**: Claude Code CLI args (`-p`, `--allowedTools`,
  `--output-format stream-json --verbose`, `--resume`, `--append-system-prompt`,
  `--effort`, `--mcp-config`, etc.)

Because the Claude side is built by the same code as the native `claude`
provider, feature parity is automatic.

## Feature Comparison

| Feature | `local` | `ollama-launch` |
|---------|---------|-----------------|
| Server management | Manual (`ollama serve`) | Automatic |
| Tool protocol | OpenAI function calling | Claude native |
| Streaming (`stream-json`) | ❌ Raw text | ✅ JSONL |
| System prompt file | ❌ Prepend only | ✅ `--append-system-prompt-file` |
| Session resume | ❌ | ✅ `--resume` |
| MCP support | ❌ | ✅ `--mcp-config` |
| Effort / thinking | ❌ | ✅ `--effort` |
| Quota detection | N/A | ✅ Claude-style |
| Cost | Free (hardware) | Free (hardware) |

## Troubleshooting

### "ollama launch claude: command not found"

Your Ollama version is too old. Upgrade to v0.16.0+:

```bash
brew upgrade ollama   # macOS
```

### Model not found

Pull the model first:

```bash
ollama pull <model-name>
ollama list           # Verify it's available
```

### Koan still uses Claude after config change

Check the env var override. `.env` takes priority over `config.yaml`:

```bash
grep KOAN_CLI_PROVIDER .env
```

### `make ollama` starts an extra server

The `make ollama` target is for the `local` provider (which needs a standalone
`ollama serve`). For `ollama-launch`, use `make start` only — the server is
started on demand by `ollama launch claude`.

### Poor quality results

Local models vary in tool-use capability. If the agent ignores tools or
produces garbled output:

1. Try a larger model (14B+ recommended)
2. Try a different model family (Qwen2.5-Coder works well)
3. Keep a Claude project around for complex architectural work
