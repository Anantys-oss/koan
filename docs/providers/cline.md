# Cline CLI Provider

The Cline provider lets Kōan use Cline CLI as the underlying AI agent.
Cline is a multi-backend AI coding assistant that supports OpenRouter,
Anthropic, OpenAI, and other providers through a unified interface.

## Quick Setup

### 1. Install Cline CLI

```bash
# npm (all platforms)
npm install -g cline

# Verify
cline --version
```

### 2. Authenticate

Cline authenticates with your chosen provider. Configure your API key
via environment variables or Cline's settings:

```bash
# For Anthropic (Claude models)
export ANTHROPIC_API_KEY=your-key

# For OpenAI
export OPENAI_API_KEY=your-key

# For OpenRouter (multi-model access)
export OPENROUTER_API_KEY=your-key
```

Or run Cline interactively once to configure:

```bash
cline
```

### 3. Configure Kōan

**Option A: config.yaml** (persistent)

```yaml
cli_provider: "cline"
```

**Option B: Environment variable** (per-session)

```bash
export KOAN_CLI_PROVIDER=cline
```

The env var overrides config.yaml if both are set.

### 4. Model Selection

Set the model in your config.yaml `models:` section. Cline uses model
identifiers from your chosen backend:

```yaml
models:
  mission: "claude-sonnet-4-20250514"    # Main mission execution
  chat: "claude-3-5-haiku-20241022"      # Chat responses
  lightweight: "claude-3-5-haiku-20241022"  # Low-cost calls
  fallback: ""                            # Not supported by Cline
  review_mode: "claude-sonnet-4-20250514"   # Review mode
```

When using OpenRouter, you can access many models:

```yaml
models:
  mission: "anthropic/claude-sonnet-4"   # OpenRouter model ID
  chat: "anthropic/claude-3.5-haiku"
```

## How It Works

Kōan invokes Cline with the `--json` flag for JSONL output and
`--auto-approve` for unattended execution:

```
cline --auto-approve true --json --model claude-sonnet-4-20250514 "Your prompt"
```

The `--auto-approve` flag prevents Cline from blocking on interactive
tool-approval prompts during headless execution.

### Execution Modes

| Kōan Setting             | Cline Flag                 | Behavior                           |
|--------------------------|----------------------------|------------------------------------|
| `skip_permissions: false`| `--auto-approve false`     | Explicit disable (prevents deadlock) |
| `skip_permissions: true` | `--auto-approve true`      | Auto-approve all tool calls        |

### Feature Mapping

| Kōan Feature           | Cline Support | Notes                                   |
|------------------------|---------------|-----------------------------------------|
| Model selection        | ✅            | `--model` flag                          |
| Fallback model         | ❌            | Silently ignored                        |
| System prompt          | ⚠️            | Prepended to user prompt (no native flag) |
| Per-tool allow/disallow| ❌            | Use `CLINE_COMMAND_PERMISSIONS` env var  |
| Max turns              | ❌            | Cline runs to completion                |
| MCP servers            | ⚠️            | Configure in Cline's own config         |
| Plugin directories     | ❌            | Not supported                           |
| Output format (JSON)   | ✅            | `--json` for JSONL events               |
| Extended thinking      | ✅            | `--thinking` flag                       |
| Quota check            | ✅            | Minimal probe via `cline --json "ok"`   |

### Extended Thinking

Cline supports extended thinking mode via the `--thinking` flag. Enable
it in Kōan by passing thinking-related parameters through the provider
interface. This activates Claude-style extended reasoning when using
Claude models through Cline.

## Per-Project Override

You can use Cline for specific projects while keeping another provider
as the default. In `projects.yaml`:

```yaml
projects:
  my-cline-project:
    path: "/path/to/project"
    cli_provider: "cline"
    models:
      mission: "claude-sonnet-4-20250514"
      chat: "claude-3-5-haiku-20241022"
```

## Tool Permissions

Cline does not support per-tool allow/disallow flags on the command line.
Instead, control tool access via the `CLINE_COMMAND_PERMISSIONS` environment
variable. Refer to Cline documentation for the permission schema.

## MCP Configuration

Cline configures MCP servers through its own configuration system, not
CLI flags. Kōan's `--mcp-config` flags are silently ignored when using
the Cline provider. Configure MCP servers directly in Cline's config.

## Quota Detection

Cline is a multi-backend client, so quota detection uses generic patterns
that work across Anthropic, OpenAI, OpenRouter, and other providers:

- Rate limit / too many requests messages
- HTTP 429 status codes
- Quota exceeded / insufficient quota errors

Kōan's quota detector scans stderr and structured error events for these
patterns and will pause + requeue missions when quota exhaustion is detected.

## Troubleshooting

### "cline: command not found"

Install the CLI: `npm install -g cline`

### Authentication errors

Verify your API keys are set correctly:

```bash
# Check environment variables
echo $ANTHROPIC_API_KEY
echo $OPENAI_API_KEY
echo $OPENROUTER_API_KEY
```

Test Cline directly:

```bash
cline --auto-approve true --json "hello"
```

### Rate limits

Cline shares quota with your backend provider. If you hit limits,
Kōan's quota detection will pause and notify you. Use `/quota` from
Telegram to check current usage status.

### System prompt not taking effect

Cline does not have a native system prompt flag. System prompts are
prepended to the user prompt as a workaround. This means they don't
benefit from separate instruction caching that some providers offer.

### Headless execution hangs

If Cline appears to hang during headless execution, ensure the
`--auto-approve` flag is being passed. Kōan always passes this flag
explicitly (true or false) to prevent interactive approval prompts
from blocking the daemon.

### Provider selection issues

To verify which backend Cline is using, check Cline's configuration
or pass the `--provider` flag explicitly:

```bash
cline --provider anthropic --json "test"
```