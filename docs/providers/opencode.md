# OpenCode Go via the Claude CLI

Run Koan's **default Claude provider** against an
[OpenCode Go](https://opencode.ai) subscription (Kimi, DeepSeek, Qwen,
MiniMax, …) — without changing any Koan code. Koan keeps invoking the
Claude CLI; a committed wrapper, `bin/oc-claude`, routes that invocation
through the **ocgo** Anthropic-compatibility proxy.

> Same mechanism as [OpenRouter via the Claude CLI](openrouter.md): a thin
> wrapper binary set via `KOAN_CLAUDE_CLI_PATH`. `cli_provider` stays `claude`.

## Architecture

```
run.py ──spawn──> bin/oc-claude   (KOAN_CLAUDE_CLI_PATH)
                     │  strips --model, re-emits it for ocgo
                     ▼
                  ocgo launch claude --model <model> -- <claude args>
                     │  Anthropic Messages API ⇄ OpenCode Go
                     ▼
                  OpenCode Go subscription ──> kimi / deepseek / qwen / ...
```

## Prerequisites

1. **An OpenCode Go subscription.**
2. **Install ocgo** (the Anthropic-compatibility proxy):

   ```bash
   brew install emanuelcasco/tap/ocgo
   ```

3. **Authenticate / set up the proxy token:**

   ```bash
   ocgo setup
   ```

4. **Confirm it works on its own** before wiring Koan in:

   ```bash
   ocgo list                                   # available models
   ocgo launch claude --model kimi-k2.7-code -- -p "Explain this repo"
   ```

## Wire Koan to the wrapper

Point `KOAN_CLAUDE_CLI_PATH` at the committed wrapper in your `.env`:

```bash
KOAN_CLAUDE_CLI_PATH=/absolute/path/to/koan/bin/oc-claude
```

The wrapper is resolved by the Claude provider (see
[claude.md → Custom CLI Binary](claude.md#advanced-configuration)). No other
Koan config changes are required.

## Model selection

The wrapper picks the OpenCode model in this order:

1. `--model <name>` that Koan emits from a project's `models:` config
2. `$OC_CLAUDE_MODEL` (set in `.env`)
3. `kimi-k2.7-code` (hard default)

Koan also emits `--fallback-model` (default `sonnet`), but OpenCode has no
fallback concept. The wrapper **drops** that flag so the Anthropic tier name
never reaches `ocgo` — there is nothing to configure.

Use **OpenCode model slugs** (from `ocgo list`) — not Anthropic tier names —
in `projects.yaml`:

```yaml
# projects.yaml
projects:
  fast-repo:
    path: "/path/to/fast-repo"
    models:
      mission: "kimi-k2.7-code"
  heavy-repo:
    path: "/path/to/heavy-repo"
    models:
      mission: "deepseek-v4-pro"
```

Or set one default for everything in `.env`:

```bash
OC_CLAUDE_MODEL=kimi-k2.7-code
```

## Pin the autonomous mode (no subscription %)

OpenCode Go has no Anthropic-style "quota %", so Koan's quota-driven mode
engine has nothing to measure. Pin it the same way as OpenRouter:

```yaml
# config.yaml
usage:
  unlimited_quota: true   # disables quota gating → mode pins to DEEP
```

## Caveats

- **`ocgo` must stay reachable.** If the proxy/token is not set up, every
  mission fails fast — the wrapper exits non-zero with an `ocgo`-not-found
  message that Koan surfaces as a real error.
- **Cost figures are Anthropic-priced.** Token *counts* survive, but any
  reported `cost_usd` is computed against Anthropic pricing and is wrong for
  OpenCode models. Treat cost numbers as unreliable here.
- **Tool-use is model-dependent.** Some cheaper OpenCode models implement
  Anthropic tool-use / streaming less faithfully in `-p` mode; prefer a
  code-capable model (e.g. `kimi-k2.7-code`) for missions.
- **Re-apply exec bit after clone if needed.** Git preserves the `+x` bit, but
  restrictive umask environments may strip it. Re-apply with
  `chmod +x bin/oc-claude` if the wrapper fails to start.

## Verify

1. **Wrapper alone** (stub-free, real ocgo):

   ```bash
   ./bin/oc-claude -p "Reply DONE." --model kimi-k2.7-code --output-format json
   ```

   Expect valid JSON with `is_error: false`.

2. **End-to-end in Koan** — with `KOAN_CLAUDE_CLI_PATH` set, queue a trivial
   mission and watch `make logs` confirm it reaches **Done**.
