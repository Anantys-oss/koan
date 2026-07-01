# Local Ollama via the Claude CLI

Run Koan's **default Claude provider** against a local
[Ollama](https://ollama.com) model — without changing `cli_provider`. Koan
keeps invoking the Claude CLI; a committed wrapper, `bin/ollama-claude`,
routes that invocation through `ollama launch claude`.

> Same mechanism as [OpenCode Go via the Claude CLI](opencode.md): a thin
> wrapper binary set via `KOAN_CLAUDE_CLI_PATH`. `cli_provider` stays `claude`.

## Wrapper vs. native `ollama-launch` provider

Koan ships **two** ways to use Ollama:

| Path | Config | When to use |
|------|--------|-------------|
| **This wrapper** (`bin/ollama-claude`) | `cli_provider: claude` + `KOAN_CLAUDE_CLI_PATH` | You already run the Claude provider and want a one-line opt-in, or mix Ollama into a Claude-first setup without touching `cli_provider`. |
| **Native provider** ([ollama-launch.md](ollama-launch.md)) | `cli_provider: ollama-launch` | You want Ollama as a first-class provider with per-project `models:` overrides and Ollama-specific quota detection. |

Both ultimately run `ollama launch claude --model <model> -- <claude args>`.

## Architecture

```
run.py ──spawn──> bin/ollama-claude   (KOAN_CLAUDE_CLI_PATH)
                     │  strips --model / --fallback-model, re-emits model
                     ▼
                  ollama launch claude --model <model> -- <claude args>
                     │  Ollama manages ANTHROPIC_BASE_URL + server lifecycle
                     ▼
                  local Ollama server ──> qwen / llama / deepseek / ...
```

## Prerequisites

1. **Ollama v0.16.0+** (needs `ollama launch claude`):

   ```bash
   brew install ollama          # macOS, or https://ollama.com/download
   ollama --version             # must be v0.16.0+
   ollama launch claude --help
   ```

2. **Pull a code-capable model**:

   ```bash
   ollama pull qwen2.5-coder:14b
   ollama list
   ```

## Wire Koan to the wrapper

Point `KOAN_CLAUDE_CLI_PATH` at the committed wrapper in your `.env`:

```bash
KOAN_CLAUDE_CLI_PATH=/absolute/path/to/koan/bin/ollama-claude
```

`cli_provider` stays `claude`. No other Koan config changes are required.

## Model selection

The wrapper picks the Ollama model in this order:

1. `--model <name>` that Koan emits from a project's `models:` config
2. `$OLLAMA_CLAUDE_MODEL` (set in `.env`)
3. `qwen2.5-coder:14b` (hard default)

Koan also emits `--fallback-model` (default `sonnet`), but the local Ollama
server has no Anthropic tier to fall back to. The wrapper **drops** that flag
so `sonnet` never reaches `ollama launch claude`.

Use **Ollama model tags** (from `ollama list`) in `projects.yaml`:

```yaml
projects:
  side-project:
    path: "/path/to/side"
    models:
      mission: "qwen2.5-coder:14b"
```

Or set one default for everything in `.env`:

```bash
OLLAMA_CLAUDE_MODEL=qwen2.5-coder:14b
```

## Pin the autonomous mode (no subscription %)

Local Ollama has no Anthropic-style quota %, so Koan's quota-driven mode
engine has nothing to measure. Pin it:

```yaml
# config.yaml
usage:
  unlimited_quota: true   # disables quota gating → mode pins to DEEP
```

## Caveats

- **Ollama must stay reachable.** If `ollama` is missing, every mission
  fails fast — the wrapper exits non-zero with an `ollama`-not-found message
  that Koan surfaces as a real error.
- **Tool-use is model-dependent.** Prefer a code-capable model (14B+, e.g.
  `qwen2.5-coder:14b`) for missions; smaller models implement Anthropic
  tool-use / streaming less faithfully.
- **Re-apply exec bit after clone if needed.** Git preserves `+x`, but a
  restrictive umask may strip it: `chmod +x bin/ollama-claude`.

## Verify

1. **Wrapper alone** (real Ollama):

   ```bash
   ./bin/ollama-claude -p "Reply DONE." --model qwen2.5-coder:14b \
     --output-format json
   ```

   Expect valid JSON with `is_error: false`.

2. **End-to-end in Koan** — with `KOAN_CLAUDE_CLI_PATH` set, queue a trivial
   mission and watch `make logs` confirm it reaches **Done**.
