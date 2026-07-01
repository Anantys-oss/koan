# Z.ai (GLM) via the Claude CLI

Run Koan's **default Claude provider** against a
[Z.ai](https://z.ai) subscription (GLM models) — without changing any Koan
code. Koan keeps invoking the Claude CLI; a committed wrapper, `bin/zai-claude`,
points that invocation at Z.ai's Anthropic-compatible endpoint and maps Koan's
model tiers to GLM models.

> Same mechanism as [OpenCode Go via the Claude CLI](opencode.md) and
> [Local Ollama via the Claude CLI](ollama-wrapper.md): a thin wrapper binary
> set via `KOAN_CLAUDE_CLI_PATH`. `cli_provider` stays `claude`.

Unlike those two wrappers, the backend here **is** the real `claude` binary —
there is no proxy or launcher in between. The wrapper only sets the Z.ai
endpoint/auth via env vars and translates model tiers, then `exec`s `claude`.

## Architecture

```
run.py ──spawn──> bin/zai-claude   (KOAN_CLAUDE_CLI_PATH)
                     │  loads Z.ai key, maps --model tiers -> GLM, exports env
                     ▼
                  claude <args>   (the real Claude Code CLI, on PATH)
                     │  ANTHROPIC_BASE_URL=api.z.ai  +  ANTHROPIC_AUTH_TOKEN
                     ▼
                  Z.ai ──> glm-4.5 / glm-5.2[1m] / ...
```

## Prerequisites

1. **A Z.ai account + API key** (from the Z.ai console).

2. **The Claude Code CLI installed and on PATH** — the wrapper execs the real
   `claude` binary, it does not bundle one:

   ```bash
   npm install -g @anthropic-ai/claude-code
   claude --version
   ```

## Provide the Z.ai key

The wrapper resolves the key in this order:

1. `$KOAN_ZAI_KEY` — the key value, read from `.env` (recommended).
2. `$KOAN_ROOT/.zai.key` — a file at the root of your Kōan instance containing
   just the key (whitespace/newlines are trimmed).

Put one of these in your `.env`:

```bash
KOAN_ZAI_KEY=sk-zai-...               # option A: key value
```

…or create the file:

```bash
printf 'sk-zai-...\n' > "$KOAN_ROOT/.zai.key"
chmod 600 "$KOAN_ROOT/.zai.key"
```

`.zai.key` is gitignored (see `.gitignore`) — but treat it like any other secret
and never commit it.

## Wire Koan to the wrapper

Point `KOAN_CLAUDE_CLI_PATH` at the committed wrapper in your `.env`. A path
**relative to `KOAN_ROOT`** is recommended — it keeps the config portable
across installs, copies, and machines (no hard-coded absolute path):

```bash
KOAN_CLAUDE_CLI_PATH=bin/zai-claude
```

The Claude provider resolves a relative value against `KOAN_ROOT`, so this
finds `bin/zai-claude` inside your Koan checkout regardless of where it lives.
An absolute path still works if you prefer it. The wrapper is resolved by the
Claude provider (see
[claude.md → Custom CLI Binary](claude.md#advanced-configuration)). No other
Koan config changes are required.

## Model selection — your config does not change

This is the key simplification: **keep using Anthropic tier names** in your Koan
config exactly as you would for a normal Claude subscription. The wrapper maps
each tier to a Z.ai GLM model:

| Koan tier (`--model`) | Z.ai model |
|-----------------------|------------|
| `haiku`               | `glm-4.5`  |
| `sonnet`              | `glm-5.2[1m]` |
| `opus`                | `glm-5.2[1m]` |

So your existing `config.yaml` works as-is:

```yaml
# config.yaml — identical to a standard Claude setup
models:
  default:
    mission: ""          # -> glm-5.2[1m]  (empty = default = sonnet tier)
    lightweight: "haiku" # -> glm-4.5      (cheap calls)
    fallback: "sonnet"   # -> glm-5.2[1m]
```

And per-project overrides in `projects.yaml` keep using tiers too:

```yaml
projects:
  heavy-repo:
    path: "/path/to/heavy-repo"
    models:
      mission: "opus"        # -> glm-5.2[1m]
      review_mode: "sonnet"  # -> glm-5.2[1m]
```

**Naming GLM models directly** also works: any `--model` value that is *not* a
tier alias (`haiku`/`sonnet`/`opus`) passes through unchanged. So this is
equally valid:

```yaml
models:
  claude:
    mission: "glm-5.2[1m]"
    lightweight: "glm-4.5"
    fallback: "glm-5.2[1m]"
```

### Overriding the tier → model mapping

Each tier is overridable via its `ANTHROPIC_DEFAULT_*_MODEL` env var (set in
`.env`). Useful when Z.ai ships a new model and you want to test it without
editing the wrapper:

```bash
ANTHROPIC_DEFAULT_SONNET_MODEL=glm-6.0
ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-4.8
```

These feed both the `--model` translation and the `ANTHROPIC_DEFAULT_*_MODEL`
exports, so Claude Code's internal tier selection stays consistent.

### Concurrency

Z.ai enforces per-model concurrency caps (see the rate-limits page on your
API-key dashboard; values are plan-dependent). The shipped defaults both sit
at the high end of the scale, so out-of-the-box throughput is balanced:

| Tier | GLM model | Typical concurrency |
|------|-----------|---------------------|
| `haiku` (lightweight) | `glm-4.5` | high (~10) |
| `sonnet` / `opus` (mission) | `glm-5.2[1m]` | high (~10) |

Kōan's agent loop is single-threaded (one mission, one `claude` subprocess at
a time), so a single instance rarely stresses even a modest concurrency cap.
You only need to think about it if you run **multiple Kōan instances** or
**parallel subagents** against the same key.

If you do retune a tier, keep two things in mind:

- Prefer a **text** model, not a `V` variant (e.g. `glm-4.6V`). The `V` means
  vision/multimodal; the lightweight tier (formatting, mission picking,
  contemplation — all text-only) never sends images, so a vision model just
  adds cost and latency. `glm-4.5` is a good text choice; `glm-5.1` if you'd
  trade cost for a smarter lightweight model.
- Lower-tier "flash"/"air" variants (e.g. `glm-4.7-Flash`) trade concurrency
  for price — check your dashboard's cap before swapping them in.

```bash
# .env — override any tier (here, a smarter lightweight model)
ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-5.1
```

## Environment reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `KOAN_CLAUDE_CLI_PATH` | — | Path to the wrapper in `.env`. Relative (e.g. `bin/zai-claude`) resolves against `KOAN_ROOT`; absolute works too. |
| `KOAN_ZAI_KEY` | — | Z.ai API key value (highest priority). |
| `$KOAN_ROOT/.zai.key` | — | Fallback key file (read when `KOAN_ZAI_KEY` is unset). |
| `KOAN_ZAI_CLAUDE_BIN` | `claude` | The backend binary to `exec`. Override if your `claude` lives elsewhere. |
| `ANTHROPIC_BASE_URL` | `https://api.z.ai/api/anthropic` | Z.ai endpoint. Override only for a mirror/proxy. |
| `API_TIMEOUT_MS` | `9000000` (150 min) | Long per-request timeout for big missions. |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | `1000000` | Raised auto-compact window. |
| `ANTHROPIC_DEFAULT_HAIKU/SONNET/OPUS_MODEL` | `glm-4.5` / `glm-5.2[1m]` / `glm-5.2[1m]` | Override any tier's GLM model. |

## Caveats

- **`claude` must stay on PATH.** If the CLI is missing, every mission fails
  fast — the wrapper exits `127` with a `claude`-not-found message that Kōan
  surfaces as a real error. (The wrapper resolves the *real* `claude` binary on
  PATH; it is not named `claude` itself, so there is no recursion.)
- **Interactive shell functions are irrelevant here.** If you have a personal
  `claude-zai` / `claude-full` shell function for interactive use, it is *not*
  loaded in Kōan's non-interactive subprocess — the wrapper always finds the
  real `claude` binary.
- **Cost figures are Anthropic-priced.** Token *counts* survive, but any
  reported `cost_usd` is computed against Anthropic pricing and is approximate
  at best for GLM models. Treat cost numbers as unreliable here.
- **Re-apply exec bit after clone if needed.** Git preserves `+x`, but a
  restrictive umask may strip it: `chmod +x bin/zai-claude`.
- **Transient `529 "overloaded"` responses are retried automatically.** Z.ai's
  gateway is sometimes unstable and returns `API Error: 529 [The service may be
  temporarily overloaded…]` *inside the streamed output while still exiting 0*
  (so it looks like a success). Kōan detects this and retries the run on the
  `cli_retry` schedule (default 5 attempts, cooldown `10s → 20s → 40s → 60s → 90s`).
  On a mission, exhaustion requeues it to Pending; on a review/plan/streaming run
  it raises after the schedule. Tune via the optional `cli_retry:` section in
  `config.yaml`. This applies to every provider, not just Z.ai.

## Verify

1. **Arg translation only (no API call, no quota spent)** — point the wrapper
   at a printer to confirm the tier mapping and env exports:

   ```bash
   cat > /tmp/zai-probe.sh <<'EOF'
   #!/usr/bin/env bash
   echo "ARGS: $*"
   echo "AUTH: ${ANTHROPIC_AUTH_TOKEN:+set}"
   echo "URL: $ANTHROPIC_BASE_URL"
   echo "HAIKU: $ANTHROPIC_DEFAULT_HAIKU_MODEL  SONNET: $ANTHROPIC_DEFAULT_SONNET_MODEL"
   EOF
   chmod +x /tmp/zai-probe.sh

   KOAN_ZAI_KEY=test KOAN_ZAI_CLAUDE_BIN=/tmp/zai-probe.sh \
     ./bin/zai-claude --model haiku --fallback-model sonnet -p "hi"
   # expect: ARGS: --model glm-4.5 --fallback-model glm-5.2[1m] -p hi
   ```

   `--model glm-5.2[1m]` (a real model id) should pass through unchanged.

2. **End-to-end in Kōan** — with `KOAN_CLAUDE_CLI_PATH` set and a valid key,
   queue a trivial mission and watch `make logs` confirm it reaches **Done**.
   `/status` shows the provider as `claude (zai-claude)`.
