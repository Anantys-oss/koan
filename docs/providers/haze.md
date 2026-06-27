# Haze CLI Provider

The Haze provider lets Kōan use the [Haze CLI](https://github.com/DenizOkcu/haze)
as the underlying agent. Haze is multi-backend: a per-run model is selected as
`provider:model` (e.g. `anthropic:claude-sonnet-4-6`, `openai:gpt-5.5`).

## Quick Setup

1. Install Haze (see https://github.com/DenizOkcu/haze) and verify `haze --version`.
2. Authenticate per Haze's own docs (backend API keys / login).
3. Configure Kōan — one of:
   - `config.yaml`: `cli_provider: "haze"`
   - `.env`: `KOAN_CLI_PROVIDER=haze` (env wins)
4. Verify: `haze --output json -p "Hello"`.

## Model Selection

Set models in `config.yaml` as `provider:model`, e.g.:

```yaml
models:
  mission: "anthropic:claude-sonnet-4-6"
  chat: "anthropic:claude-sonnet-4-6"
```

An empty string uses Haze's configured default backend — Kōan ships the
`haze` model block empty so you pick the `provider:model` your Haze install
is set up for.

## Flag mapping & limitations

| Kōan concept     | Haze flag           | Notes                                       |
|------------------|---------------------|---------------------------------------------|
| Prompt           | `-p "<text>"`       | One-shot, non-interactive (final flag)      |
| Model            | `-m provider:model` | No fallback model                           |
| JSON output      | `--output json`     | Final envelope `{type,status,result,usage}` |
| Tool restriction | —                   | Not supported (ignored)                     |
| Max turns        | —                   | Runs to completion                          |
| MCP servers      | —                   | Configure in Haze, not via Kōan             |
| System prompt    | —                   | Prepended to the user prompt                |
| Reasoning effort | —                   | Not supported (ignored)                     |
| Token usage      | `usage` envelope    | `prompt_tokens`/`completion_tokens` feed budget + burn-rate gating |

Unsupported features are logged once and ignored — the mission still runs.

Kōan reads the final envelope's `usage` block for budget and burn-rate
accounting. Backends that report OpenAI-style `prompt_tokens`/`completion_tokens`
are recognized alongside the Anthropic-style `input_tokens`/`output_tokens`, so
metered quota gating works regardless of the selected backend.

## Timeouts

Haze prints a single final result envelope (no streaming progress). Kōan
therefore uses the wall-clock `mission_timeout` as the authoritative timeout
and does **not** apply the stdout-tail stagnation heuristic to haze, so a long
but legitimately quiet session is never killed prematurely. (The stagnation
monitor hashes the tail of subprocess stdout to spot a stuck-in-a-loop run; a
silent-but-alive single-envelope provider would hash identically and trip a
false kill, so Kōan exempts it via the `emits_incremental_progress()`
provider capability.)

## Troubleshooting

- `haze: command not found` → install Haze and ensure it's on `PATH`.
- Auth/quota failures → Kōan pauses on rate-limit / HTTP 429 and surfaces
  401 / invalid-API-key errors through the normal CLI error path.
