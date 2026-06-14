# OpenRouter via Claude Code CLI

This page explains how to run Koan's **default Claude provider** against
[OpenRouter](https://openrouter.ai) models — a mix of Anthropic models (billed
and failed-over through OpenRouter) and cheaper non-Anthropic models (qwen,
deepseek, etc.) — without changing any Koan code.

> **Not the same as the Cline provider.** Koan's [Cline provider](cline.md) is a
> separate CLI that talks to OpenRouter natively. This page keeps the **Claude
> Code CLI** (Koan's most capable provider, with all its tool-use and session
> features) and routes its traffic to OpenRouter through a local translation
> server. Use this page when you want Claude Code's behavior but OpenRouter's
> model catalog and billing.

## Why a router (and not the native endpoint)

Koan always invokes the Claude CLI in non-interactive print mode
(`claude -p --output-format json`). In that mode the CLI demands strict
Anthropic-style streaming (SSE) and tool-use semantics. **Many non-Anthropic
OpenRouter models do not faithfully implement those semantics**, so pointing the
CLI straight at OpenRouter's Anthropic-compatible endpoint causes `-p` missions
to fail for those models — even though interactive chat may look fine.

The fix is a local router that *repairs* tool-use and streaming per provider.
After evaluating the options:

| Option | Verdict |
|--------|---------|
| OpenRouter native Anthropic endpoint | No tool-use repair → same `-p` breakage on non-Anthropic models. Fine for Anthropic-only. |
| [y-router](https://github.com/luohy15/y-router) | Archived (Jan 2026). Avoid. |
| [claude-relay-service](https://github.com/Wei-Shaw/claude-relay-service) | An Anthropic *account pooler*, not a model router. Cannot use arbitrary OpenRouter models. |
| **[CCR / claude-code-router](https://github.com/musistudio/claude-code-router)** | **Recommended.** Local server with `tooluse` / `enhancetool` transformers + SSE rewriting built to make tool-calling work on models that don't natively support it. Actively maintained, per-route model mapping. |

This page uses **CCR**.

## Architecture

```
run.py ──spawn──> claude (real CLI, located via KOAN_CLAUDE_CLI_PATH or PATH)
                     │  ANTHROPIC_BASE_URL=http://127.0.0.1:3456
                     ▼
                  CCR server (ccr start — run as a background service)
                     │  Anthropic Messages API → OpenAI/OpenRouter,
                     │  repairs tool-use + streaming per provider
                     ▼
                  OpenRouter ──> anthropic/* , qwen/* , deepseek/* , ...
```

Koan needs **no code changes**: it inherits its environment into the CLI
subprocess, `KOAN_CLAUDE_CLI_PATH` lets you swap in a wrapper, and per-project
`models:` strings are passed verbatim as `--model`.

## Setup

### 1. Install and configure CCR

```bash
npm install -g @musistudio/claude-code-router
```

Create `~/.claude-code-router/config.json` with one OpenRouter provider and a
routing table:

```json
{
  "Providers": [
    {
      "name": "openrouter",
      "api_base_url": "https://openrouter.ai/api/v1/chat/completions",
      "api_key": "sk-or-...",
      "models": [
        "anthropic/claude-sonnet-4",
        "qwen/qwen3.7-plus",
        "minimax/minimax-m3"
      ],
      "transformer": { "use": ["openrouter"] }
    }
  ],
  "Router": {
    "default":     "openrouter,anthropic/claude-sonnet-4",
    "background":  "openrouter,qwen/qwen3.7-plus",
    "think":       "openrouter,minimax/minimax-m3",
    "longContext": "openrouter,anthropic/claude-sonnet-4"
  }
}
```

If a specific cheap model still mangles tool calls in `-p` mode, add CCR's
`tooluse` and/or `enhancetool` transformers to that provider's `transformer.use`
list — that is exactly what they exist for.

### 2. Run CCR as a persistent background service

Koan is a long-lived autonomous agent, so CCR must stay up across reboots — run
the server (`ccr start`), **not** the interactive `ccr code` wrapper.

**macOS (launchd)** — `~/Library/LaunchAgents/ai.openrouter.ccr.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.openrouter.ccr</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/ccr</string>
    <string>start</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/ccr.out.log</string>
  <key>StandardErrorPath</key><string>/tmp/ccr.err.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/ai.openrouter.ccr.plist
```

**Linux (systemd user unit)** — `~/.config/systemd/user/ccr.service`:

```ini
[Unit]
Description=Claude Code Router
After=network-online.target

[Service]
ExecStart=/usr/bin/ccr start
Restart=always

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now ccr.service
```

CCR binds `127.0.0.1:3456` by default.

### 3. Point Koan at CCR

The recommended approach is a thin **wrapper binary**, because it isolates the
OpenRouter routing to Koan (it does not repoint every other `claude` invocation
on your machine) and gives you one place for any future model-string rewriting.

Save a wrapper, e.g. `~/.local/bin/claude-openrouter`:

```bash
#!/usr/bin/env bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:3456"
export ANTHROPIC_AUTH_TOKEN="ccr"   # CCR-side token; any non-empty value
export ANTHROPIC_API_KEY=""         # MUST be empty — otherwise the CLI tries real Anthropic auth
exec claude "$@"
```

```bash
chmod +x ~/.local/bin/claude-openrouter
```

Then in Koan's `.env`:

```bash
KOAN_CLAUDE_CLI_PATH=/home/you/.local/bin/claude-openrouter
```

`KOAN_CLAUDE_CLI_PATH` is resolved by the Claude provider (see
[claude.md → Custom CLI Binary](claude.md#advanced-configuration)). The provider
otherwise behaves exactly as normal — `cli_provider` stays `claude`.

**Bare-env alternative (no wrapper):** because Koan inherits its environment into
the CLI subprocess, you can instead set these directly in Koan's `.env`. This
repoints every `claude` run launched in that environment, so prefer the wrapper
unless you want that:

```bash
# ANTHROPIC_BASE_URL=http://127.0.0.1:3456
# ANTHROPIC_AUTH_TOKEN=ccr
# ANTHROPIC_API_KEY=
```

### 4. Map models per project (the "mix" lever)

Per-project `models:` strings are passed verbatim as `--model`, so use them to
pick Anthropic vs cheap models per project. Use CCR's `provider,model` form:

```yaml
# projects.yaml
projects:
  claude-repo:
    path: "/path/to/claude-repo"
    models:
      mission:  "openrouter,anthropic/claude-sonnet-4"
      fallback: "openrouter,anthropic/claude-haiku"

  cheap-repo:
    path: "/path/to/cheap-repo"
    models:
      mission:  "openrouter,qwen/qwen3.7-plus"
      fallback: "openrouter,minimax/minimax-m3"
```

Leaving a project's `models` empty makes the CLI send no `--model`, so CCR falls
back to its `Router.default`.

#### How model selection actually works

CCR — not the wrapper — picks the model. It reads the `model` field of each
incoming request and decides:

- **Contains a comma** (`provider,model`, e.g. `openrouter,qwen/qwen3.7-plus`) →
  CCR treats it as an **explicit override** and routes there verbatim.
- **Plain name** (`sonnet`, `haiku`, `claude-sonnet-4-6`, …) → CCR **ignores your
  intent** and applies its own `Router` table (`default` / `background` / `think`
  / `longContext`).

Verified live against this setup:

| What the CLI sends to CCR | What CCR routes to |
|---|---|
| `openrouter,minimax/minimax-m3` | `minimax/minimax-m3` ✅ exact |
| `openrouter,qwen/qwen3.7-plus` | `qwen/qwen3.7-plus` ✅ exact |
| `claude-sonnet-4-6` (what `--model sonnet` becomes) | whatever CCR's Router picks ❌ not yours |

So the takeaways:

- **Always use the `openrouter,<slug>` form** in Koan's `models:` config. Plain
  tier names get hijacked by CCR's Router.
- **The wrapper sets no model env vars.** You do *not* need `ANTHROPIC_MODEL` or
  `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` — `--model` already reaches CCR and
  takes precedence, and explicit slugs make tier-alias remapping pointless.
- **Background/small calls** (Claude Code's auxiliary haiku-tier requests for
  summaries, etc.) don't carry your `--model`. Control them with CCR's
  `Router.background` (already set above) — not the wrapper.

### 5. Pin the autonomous mode

On pay-per-token OpenRouter there is no subscription "quota %", so Koan's
quota-driven mode engine (REVIEW / IMPLEMENT / DEEP / WAIT) has nothing
meaningful to measure. Pin it with the existing `unlimited_quota` switch — no new
config keys:

```yaml
# config.yaml
usage:
  unlimited_quota: true   # disables quota gating → mode pins to DEEP (full capability)
```

`unlimited_quota: true` disables all proactive gating (mode downgrades,
burn-rate warnings, preflight probes), and with no budget pressure the mode
settles on **`deep`** every iteration.

If you want a **cheaper fixed tier** instead, add focus mode, which caps
`deep → implement`:

```bash
# .env
KOAN_FOCUS=1            # or set `focus: true` in config.yaml
```

> Focus mode is more than a mode cap: it also restricts the agent to
> missions-only (no autonomous GitHub issue pickup) and skips
> contemplative/reflection sessions. Use it only if you want those effects too.

## Caveats

- **Cost figures are Anthropic-priced.** Koan reads token counts from the CLI's
  own `~/.claude/projects/<path>/*.jsonl` session logs; token *counts* survive,
  but any reported `cost_usd` is computed against Anthropic pricing and will be
  wrong for OpenRouter models. Treat cost numbers as unreliable here.
- **`ANTHROPIC_API_KEY` must be empty**, or the CLI may try to authenticate
  against real Anthropic instead of CCR. If a cached OAuth session interferes,
  run `claude` once and `/logout`.
- **Tool-use is still model-dependent.** If a cheap model mangles tool calls in
  `-p`, add CCR's `tooluse` / `enhancetool` transformer for that provider before
  giving up on the model.
- **CCR must stay running.** If the launchd/systemd service is down, every
  mission fails fast with a connection error to `127.0.0.1:3456`.

## Verify

1. **CCR is up:**

   ```bash
   curl -s http://127.0.0.1:3456/ >/dev/null && echo "CCR reachable"
   ```

2. **Raw CLI through CCR in print mode** (the exact path that was breaking) —
   confirm tool-use and streaming survive translation for a non-Anthropic model:

   ```bash
   ANTHROPIC_BASE_URL=http://127.0.0.1:3456 ANTHROPIC_AUTH_TOKEN=ccr ANTHROPIC_API_KEY= \
   claude -p "Use the Bash tool to run: echo TOOLS_WORK > proof.txt . Then reply DONE." \
     --model "openrouter,qwen/qwen3.7-plus" --output-format json \
     --allowedTools Bash --dangerously-skip-permissions
   ```

   Expect valid JSON with `is_error: false` / `subtype: "success"`, `num_turns: 2`,
   and a `proof.txt` that actually contains `TOOLS_WORK` — proving the tool-use
   round-trip survived translation. This path has been verified against
   `qwen/qwen3.7-plus` and `minimax/minimax-m3`.

3. **Through the wrapper** — same prompt via `claude-openrouter` to confirm the
   env isolation works.

4. **End-to-end in Koan** — with `KOAN_CLAUDE_CLI_PATH` and the `projects.yaml`
   model mapping set, queue a trivial mission to `cheap-repo` and one to
   `claude-repo`, then watch `make logs` to confirm each used the intended model
   and reached **Done**.

5. **Mode pin** — with `usage.unlimited_quota: true`, `make logs` should show
   `mode=deep` every iteration regardless of `usage.md`; adding `KOAN_FOCUS=1`
   caps it to `mode=implement`.
