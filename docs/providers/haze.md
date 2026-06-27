# Haze CLI Provider

The Haze provider lets Kōan use [Haze](https://github.com/DenizOkcu/haze) as the
underlying AI agent. Haze is a minimal terminal agent that works with any
OpenAI-compatible provider — OpenRouter, OpenAI, Z.ai, local endpoints (LM
Studio, Ollama), or a proxy — so a single Haze install gives Kōan access to many
model backends.

## How It Works

Unlike Claude, Codex, Cline, and Copilot, **Haze ships as an interactive
terminal UI and has no native non-interactive / print mode**. Its CLI exposes
only `--debug`, `--continue`, and `--no-session` — there is no `-p`, `--model`,
`--output-format`, or `--json` flag.

Kōan requires a scripted, headless agent invocation. To bridge this gap, the
Haze provider ships a small Node.js runner (`koan/app/provider/haze_headless.mjs`)
that imports Haze's installed package internals and drives the agent core
(`runAgentTurn`) directly:

1. Kōan writes the mission prompt and invokes the bridge via `node`.
2. The bridge reads the prompt, runs Haze's full tool-loop (file tools, bash,
   grep, fetch, subagents, skills, and any configured MCP/LSP tools), and
   emits Kōan-compatible JSONL progress events to stdout.
3. The final assistant text is written to a `--last-message` file that Kōan
   reads back as the mission result.

Because the bridge drives the same agent core the interactive UI uses, missions
get the same model behavior, tool access, and context-file loading (`CLAUDE.md`
/ `AGENTS.md`) as an interactive Haze session.

```
node haze_headless.mjs --haze-root <haze-pkg> --prompt "<mission>" \
    [--model <selector>] --last-message <result-file>
```

## Quick Setup

### 1. Install Node.js and Haze CLI

Haze requires Node.js ≥ 20.

```bash
# Install Haze globally
npm install -g @denizokcu/haze

# Verify
haze --version
node --version
```

### 2. Configure Haze

Haze configures its model provider interactively. Run Haze once in your
project (or any directory) to set up a provider and model:

```bash
cd /path/to/project
haze
```

Inside the Haze TUI:

```txt
/provider   # choose or add an OpenAI-compatible provider (name, base URL, key)
/model      # select the model Haze should use
/exit       # quit
```

`/provider` opens setup for any OpenAI-compatible endpoint — OpenRouter, OpenAI,
LM Studio, Ollama, or a proxy. You will be asked for a provider name, base URL,
optional API key, and model names. `/model` selects the active model.

Haze stores its configuration in `~/.haze/settings.json` (providers, API keys,
models, MCP servers). There are no environment variables to set — everything is
configured from inside Haze.

> **Verify the configuration works before pointing Kōan at it.** Run the bridge
> directly with a simple prompt to confirm Haze can reach your backend:
>
> ```bash
> haze --version  # confirm the binary is on PATH
> ```

### 3. Configure Kōan

**Option A: config.yaml** (persistent)

```yaml
cli_provider: "haze"
```

**Option B: Environment variable** (per-session)

```bash
export KOAN_CLI_PROVIDER=haze
```

The env var overrides `config.yaml` if both are set.

### 4. Model Selection

Kōan passes a model selector to the headless bridge, which applies it as a
one-shot override (it never persists to `~/.haze/settings.json`). The selector
follows Haze's `/model` syntax:

- A bare model id that is unique across configured providers, e.g. `glm-5.2`.
- A `providerName:modelId` pair when the model id is ambiguous.

Set the model in your `config.yaml` `models:` section using identifiers from the
backend you configured in Haze:

```yaml
models:
  mission: "glm-5.2"             # Main mission execution
  chat: "glm-5.2"                # Chat responses
  lightweight: "glm-5.2"         # Low-cost calls
  review_mode: "glm-5.2"         # Review mode
  fallback: ""                   # Not supported by Haze (ignored)
```

If you configured multiple providers in Haze, use the `providerName:modelId`
form:

```yaml
models:
  mission: "openrouter:anthropic/claude-sonnet-4"
```

When no model is set, Haze uses whatever is active in `~/.haze/settings.json`.

## Feature Mapping

| Kōan Feature           | Haze Support | Notes                                                      |
|------------------------|--------------|------------------------------------------------------------|
| Model selection        | ✅           | One-shot settings override in the bridge (`providerName:id` or bare id) |
| Fallback model         | ❌           | Silently ignored                                           |
| System prompt          | ⚠️           | Prepended to user prompt (no native flag)                  |
| Per-tool allow/disallow| ❌           | Haze exposes a fixed built-in toolset                      |
| Max turns              | ❌           | Haze's tool-loop runs to completion (guarded by its own idle timeout and loop detection) |
| MCP servers            | ⚠️           | Configure inside Haze via `/mcp` (not CLI flags)          |
| Plugin directories     | ❌           | Haze uses Markdown skills (`~/.haze/skills`), not plugin dirs |
| Output format (JSON)   | ✅           | The bridge emits JSONL progress + result events            |
| Final message capture  | ✅           | Bridge writes final assistant text to a `--last-message` file |
| Quota check            | ✅           | Minimal probe via the bridge                               |
| Session resume         | ❌           | Headless runs are single-turn by design                    |

## Per-Project Override

You can use Haze for specific projects while keeping another provider as the
default. In `projects.yaml`:

```yaml
projects:
  my-haze-project:
    path: "/path/to/project"
    cli_provider: "haze"
    models:
      mission: "glm-5.2"
```

## AGENTS.md / Context Files

Haze loads project instructions from `AGENTS.md` and `CLAUDE.md` files (from the
filesystem root down to the workspace). At the same scope, `AGENTS.md` overrides
`CLAUDE.md`. If your project already has a `CLAUDE.md`, consider adding an
`AGENTS.md` for Haze-specific guidance, or symlink them:

```bash
ln -s CLAUDE.md AGENTS.md
```

## MCP Configuration

Haze configures MCP servers through its own `/mcp` picker (persisted in
`~/.haze/settings.json` under `mcpServers`), supporting `http`, `sse`, and
`stdio` transports. Kōan's `--mcp-config` flags are silently ignored when using
the Haze provider. Configure MCP servers directly inside Haze:

```txt
/mcp
# add server -> context7          (preset)
# add server -> custom -> http    (remote)
# add server -> custom -> stdio   (local)
```

MCP tools load at the start of each agent turn and never shadow Haze's
built-in tools.

## Quota Detection

Haze is model-agnostic: the backend (OpenRouter, OpenAI, Z.ai, local, etc.)
enforces the real quota. Quota detection therefore uses generic patterns that
work across providers:

- Rate limit / too many requests messages
- HTTP 429 status codes
- Quota exceeded / insufficient quota errors

Kōan's quota detector scans the bridge's stderr and structured JSONL error
events for these patterns and will pause + requeue missions when quota
exhaustion is detected.

## Troubleshooting

### "Haze package not found"

The provider locates Haze by following the `haze` binary symlink to its
`node_modules` package directory. Ensure both are installed:

```bash
which haze        # should print a path
which node        # Node.js >= 20
haze --version
```

If Haze is installed in a non-standard location not reachable from the `haze`
binary on `PATH`, point Kōan at the package directory directly:

```bash
export KOAN_HAZE_PKG_PATH=/path/to/@denizokcu/haze
```

### Authentication / "No model provider configured"

Haze reads its provider and API key from `~/.haze/settings.json`. If Kōan runs
as a background service, ensure the service user has the same `HOME` (so
`~/.haze/settings.json` is visible) and `PATH` as your interactive shell.
Reconfigure with `haze` → `/provider` → `/model` if needed, then restart Kōan.

Kōan detects the "No model provider configured" message as an auth/provider
failure and pauses for configuration instead of repeatedly failing missions.

### Rate limits

Haze shares quota with your configured backend. If you hit limits, Kōan's
quota detection will pause and notify you. Use `/quota` from Telegram to check
current usage status.

### System prompt not taking effect

Haze has no native system-prompt flag. System prompts are prepended to the user
prompt as a workaround, so they do not benefit from separate instruction
caching that some backends offer.

### Mission hangs or produces no output

Haze's tool-loop is bounded by its own idle timeout and loop-detection
guardrails, but a misbehaving model or MCP server can stall a turn. The bridge
emits `tool_start`/`tool_end` events to stdout so `/live` shows activity. If a
backend is unreachable, Kōan's mission timeout will eventually terminate the
turn and mark the mission failed.

### Debugging the headless path

Run the bridge manually with the same prompt-passing style Kōan uses to confirm
Haze can reach your backend outside the daemon:

```bash
node koan/app/provider/haze_headless.mjs \
  --haze-root "$(dirname "$(dirname "$(readlink -f "$(which haze)")")")" \
  --prompt "say hello" \
  --debug
```

The bridge prints JSONL events on stdout and diagnostic lines on stderr.
