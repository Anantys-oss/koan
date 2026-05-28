# GitHub Copilot CLI Provider

The Copilot provider lets Koan use GitHub Copilot's CLI as the
underlying AI agent. This is useful if you have a Copilot subscription
and want to use it instead of (or alongside) Claude.

## Quick Setup

### 1. Install GitHub Copilot CLI

Copilot CLI is available as a standalone binary or via the GitHub CLI
extension.

**Option A: Standalone binary (preferred)**

```bash
# macOS (Homebrew)
brew install github/gh/copilot

# Verify
copilot --version
```

**Option B: Via GitHub CLI**

```bash
# Install gh if needed
brew install gh

# Install the Copilot extension
gh extension install github/gh-copilot

# Verify
gh copilot --version
```

Koan auto-detects which variant is available, preferring the standalone
`copilot` binary over `gh copilot`.

### 2. Authenticate

```bash
# Standalone
copilot auth login

# Or via gh
gh auth login
```

You need a GitHub account with an active Copilot subscription
(Individual, Business, or Enterprise).

### 3. Configure Koan

In `config.yaml`:

```yaml
cli_provider: "copilot"
```

Or via environment variable (in `.env`):

```bash
KOAN_CLI_PROVIDER=copilot
```

### 4. Verify

```bash
# Standalone
copilot -p "Hello, what model are you?"

# Or via gh
gh copilot -p "Hello, what model are you?"
```

## Per-Project Configuration

You can use Copilot for specific projects while keeping Claude as the
default. In `projects.yaml`:

```yaml
defaults:
  cli_provider: "claude"  # Default for all projects

projects:
  my-github-project:
    path: "/path/to/project"
    cli_provider: "copilot"  # This project uses Copilot
```

## Provider Differences

Copilot CLI has some differences from Claude Code CLI that affect
behavior:

| Feature | Claude Code | Copilot CLI |
|---------|------------|-------------|
| Tool naming | `Bash`, `Read`, `Write` | `shell`, `read_file`, `write_file` |
| Tool restriction | `--allowedTools` / `--disallowedTools` | `--allow-tool` (per tool) |
| Fallback model | `--fallback-model` | Not supported |
| Output format | `--output-format json` | Not supported |
| Max turns | `--max-turns N` | Not supported |
| MCP support | Yes | Yes (same config format) |
| Model selection | `--model <name>` | `--model <name>` |

Koan handles these differences transparently through the provider
abstraction — you don't need to worry about flag translation.

### Tool Name Mapping

Koan automatically translates tool names between providers:

| Koan (canonical) | Claude Code | Copilot |
|-----------------|-------------|---------|
| `Bash` | `Bash` | `shell` |
| `Read` | `Read` | `read_file` |
| `Write` | `Write` | `write_file` |
| `Edit` | `Edit` | `edit_file` |
| `Glob` | `Glob` | `glob` |
| `Grep` | `Grep` | `grep` |

### Limitations

- **No fallback model**: Copilot doesn't support `--fallback-model`.
  If the primary model is unavailable, the request fails.
- **No output format control**: Copilot always returns plain text.
  Koan parses output as-is instead of using structured JSON.
- **No max turns**: Copilot conversations run until the model's
  response is complete. Koan cannot limit tool-use rounds.
- **Tool restriction is allow-list only**: Copilot uses
  `--allow-tool <name>` per tool. When Koan needs to *disallow*
  specific tools, it computes the inverse (allow everything except
  the disallowed ones).

## Model Configuration

Copilot model selection works the same way as Claude:

```yaml
models:
  mission: ""       # Empty = default Copilot model
  chat: ""
  lightweight: ""   # Copilot may not support all model tiers
```

Available models depend on your Copilot subscription tier. Check
Copilot documentation for current model availability.

## Troubleshooting

### "copilot: command not found" / "gh: command not found"

Install the CLI (see Quick Setup above). If using `gh copilot`, make
sure both `gh` and the Copilot extension are installed.

### Authentication issues

```bash
# Re-authenticate
gh auth login

# Check status
gh auth status
```

### Copilot subscription required

Copilot CLI requires an active GitHub Copilot subscription. Check your
subscription at https://github.com/settings/copilot.
