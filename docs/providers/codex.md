# OpenAI Codex CLI Provider

The Codex provider lets Kōan use OpenAI's Codex CLI as the underlying
AI agent. This is useful if you have a ChatGPT Pro (or Plus/Business/
Enterprise) subscription and want to use Codex models (GPT-5.4,
GPT-5.3-Codex, etc.) for planning and autonomous work.

## Quick Setup

### 1. Install Codex CLI

```bash
# npm (all platforms)
npm install -g @openai/codex

# macOS (Homebrew)
brew install --cask codex

# Verify
codex --version
```

### 2. Authenticate

```bash
# Browser-based login (default)
codex

# API key login
printenv OPENAI_API_KEY | codex login --with-api-key

# Headless / SSH
codex login --device-auth
```

You need a ChatGPT account with an active subscription that includes
Codex access (Plus, Pro, Business, Edu, or Enterprise).

### 3. Configure Kōan

**Option A: config.yaml** (persistent)

```yaml
cli_provider: "codex"
```

**Option B: Environment variable** (per-session)

```bash
export KOAN_CLI_PROVIDER=codex
```

The env var overrides config.yaml if both are set.

### 4. Model Selection

Set the model in your config.yaml `models:` section. Codex models use
their full names:

```yaml
models:
  mission: "gpt-5.4"           # Main mission execution
  chat: "gpt-5.4-mini"         # Chat responses (faster, cheaper)
  lightweight: "gpt-5.4-mini"  # Low-cost calls
  review_mode: "gpt-5.3-codex" # Autonomous review mode and /review analysis
  fallback: ""                  # Not supported by Codex (ignored)
```

Available models (as of March 2026):
- `gpt-5.4` — Flagship frontier model (recommended)
- `gpt-5.4-mini` — Fast, cost-effective for lighter tasks
- `gpt-5.3-codex` — Industry-leading coding model
- `gpt-5.3-codex-spark` — Near-instant iteration (Pro only)

## How It Works

Kōan invokes Codex in **non-interactive mode** via `codex exec`:

```
codex exec --sandbox workspace-write --model gpt-5.4 "Your prompt here"
```

This runs Codex as a scripted agent that reads the project, generates
a plan, executes it, and returns the result. Streaming skill calls use
`--json` for progress events and `--output-last-message` for the final
assistant response, so Kōan can show live activity without relying on
Codex event shapes for the final answer.

### Execution Modes

| Kōan Setting          | Codex Flag       | Behavior                        |
|-----------------------|------------------|---------------------------------|
| `skip_permissions: false` | `--sandbox workspace-write` | Workspace writes, but `.git` may be read-only |
| `skip_permissions: true`  | `--dangerously-bypass-approvals-and-sandbox` | No approvals, no sandbox |

### Feature Mapping

| Kōan Feature           | Codex Support | Notes                                   |
|------------------------|---------------|-----------------------------------------|
| Model selection        | ✅            | `--model` flag                          |
| Fallback model         | ❌            | Silently ignored                        |
| System prompt          | ⚠️            | Prepended to user prompt (no native flag) |
| Per-tool allow/disallow| ❌            | Codex uses sandbox policies instead     |
| Max turns              | ❌            | Codex exec runs to completion           |
| MCP servers            | ⚠️            | Configure in `~/.codex/config.toml`     |
| Plugin directories     | ❌            | Codex uses skills instead               |
| Output format (JSON)   | ✅            | Used for live progress; final text is read from `--output-last-message` |
| Quota check            | ✅            | Minimal probe via `codex exec "ok"`     |

### Usage Estimation And Internal Budget Gates

Koan tracks token usage from Codex JSON output when available. This internal
estimate drives autonomous mode downgrades (`deep` -> `implement` -> `review`
-> `wait`) but is separate from hard provider quota detection.

For Codex subscription accounts where you want to ignore internal estimates and
only react to real provider quota/session-limit errors, set:

```yaml
usage:
  budget_mode: disabled
```

With `budget_mode: disabled`, Koan still detects provider quota exhaustion from
Codex stderr and structured error events, and will still pause + requeue on
hard quota failures.

## Per-Project Override

You can use Codex for specific projects while keeping Claude as the
default. In `projects.yaml`:

```yaml
projects:
  my-openai-project:
    path: "/path/to/project"
    cli_provider: "codex"
    models:
      mission: "gpt-5.4"
      chat: "gpt-5.4-mini"
```

## MCP Configuration

Codex configures MCP servers via `~/.codex/config.toml` (not CLI flags):

```toml
[mcp_servers.github]
command = ["npx", "-y", "@modelcontextprotocol/server-github"]
```

Kōan's `--mcp-config` flags are silently ignored when using the Codex
provider. Configure MCP servers directly in Codex's config.

## AGENTS.md

Codex reads `AGENTS.md` files from the project root (similar to
Claude's `CLAUDE.md`). If your project already has a `CLAUDE.md`,
consider symlinking or adapting it:

```bash
ln -s CLAUDE.md AGENTS.md
```

## Troubleshooting

### "codex: command not found"

Install the CLI: `npm install -g @openai/codex`

### Authentication errors

Re-authenticate: `codex login --device-auth`

When debugging Kōan, test the same non-interactive path Kōan uses. A short
plain command like `codex exec 'say hello'` can succeed while the daemon path
fails during JSON streaming, stdin prompt passing, or final-message capture:

```bash
printf 'say hello' | codex exec --json --output-last-message /tmp/koan-codex-last-message --sandbox workspace-write -
```

If Kōan runs as a background service, also verify that the service user has the
same `HOME`, `PATH`, `.codex/auth.json`, and `.env` values as your interactive
shell. Restart Kōan after re-authenticating so the daemon uses the refreshed
Codex auth state.

Kōan serializes its own Codex CLI subprocesses to avoid concurrent token-refresh
races between preflight probes, message formatting, and long-running review
sessions. If Codex still reports `401 Unauthorized` or refresh-token reuse, Kōan
will pause for auth and requeue the active mission instead of marking it failed.

### Rate limits

Codex shares quota with your ChatGPT subscription. If you hit limits,
Kōan's quota detection will pause and notify you. Codex quota detection is
provider-specific: Kōan trusts Codex/OpenAI error events and stderr, but does
not scan normal command output for generic billing or credit words. Token
accounting failures and quota detection are separate: if usage extraction
fails for a mission, Koan still runs quota detection for that mission.

### Tool restrictions not working

Codex does not support per-tool allow/disallow flags. Tool access is
controlled by sandbox policies. Use `skip_permissions: true` (maps to
`--dangerously-bypass-approvals-and-sandbox`) for full access, or the
default `--sandbox workspace-write` for workspace-scoped writes. In some
deployments, `workspace-write` allows source edits but mounts `.git`
read-only; use full access only when Kōan already runs in a trusted
external sandbox and Codex should create branches, commits, pushes, and PRs.

### System prompt not taking effect

Codex does not have a `--append-system-prompt` flag. System prompts
are prepended to the user prompt as a workaround. This means they
don't benefit from Codex's separate instruction caching.
