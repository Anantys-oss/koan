# Component Spec — CLI Provider Abstraction

**Package:** `koan/app/provider/` (`base.py`, `claude.py`, `cline.py`, `codex.py`,
`copilot.py`, `__init__.py`) + `cli_provider.py` (legacy re-export facade)

## Purpose

Decouple the agent loop from any single AI CLI. Kōan invokes an external coding CLI as
a subprocess; this layer abstracts *which* CLI, its flags, its tool-name vocabulary, and
its usage-tracking quirks behind one `CLIProvider` contract.

## Architecture

```
provider/__init__.py  → registry + resolution (env → config → default) + cached singleton
       │                 convenience: run_command(), run_command_streaming(), build_full_command()
       ├─ base.py      → CLIProvider ABC + tool-name constants + usage hooks
       ├─ claude.py    → ClaudeProvider (Claude Code CLI)
       ├─ cline.py     → ClineProvider
       ├─ codex.py     → CodexProvider (quota via stream-json summary only)
       └─ copilot.py   → CopilotProvider (with tool-name mapping)
```

## Key types & functions

| Symbol | Contract |
|---|---|
| `base.CLIProvider` | The contract: build command, run, stream, tool-name vocabulary. |
| `base.supports_usage_tracking()` / `record_usage()` | Per-provider usage hooks. Not all CLIs surface usage the same way. |
| `__init__.run_command()` / `run_command_streaming()` | The single invocation entry points. Callers should not spawn provider subprocesses directly. |
| `__init__.build_full_command()` | Assembles the provider-specific argv. |
| `__init__.get_provider_display()` / `get_cli_binary_name()` / `get_review_cli_binary_name()` | Display helpers. `get_provider_display()` returns `"<name>"` or `"<name> (<binary>)"` when `KOAN_CLAUDE_CLI_PATH` points at a different binary; when `KOAN_CLAUDE_CLI_FOR_REVIEW_PATH` is set its basename is appended as a `review:` hint (e.g. `claude (cheap-claude, review: review-claude)`). Single source of truth for the provider line shown by the startup banner and `/status` — both knobs are observable so a review-only binary is never a silent config. |
| `__init__.review_cli_override()` / `review_cli_override_active()` | Context-scoped flag. While active, `ClaudeProvider.binary()` prefers `KOAN_CLAUDE_CLI_FOR_REVIEW_PATH` over `KOAN_CLAUDE_CLI_PATH`, so a review-only binary can be pinned without affecting other missions. Activated solely by `review_runner._run_claude_review()` (the single review-path Claude chokepoint). |
| Provider resolution | Order: `KOAN_CLI_PROVIDER` env (fallback `CLI_PROVIDER`) → `projects.yaml`/`config.yaml` → default. Centralized in `utils.get_cli_provider_env()`. |
| `ClaudeProvider.binary()` | Resolves the Claude binary: `KOAN_CLAUDE_CLI_FOR_REVIEW_PATH` (only inside a `review_cli_override()` context — ignored otherwise so it never leaks into other missions) → then `KOAN_CLAUDE_CLI_PATH` handled as absolute → as-is / relative → `normpath(join(KOAN_ROOT, …))` / bare name → PATH lookup / unset-empty → `"claude"`. `KOAN_CLAUDE_CLI_PATH` relative paths root at `KOAN_ROOT` (not CWD — the agent runs from `KOAN_ROOT/koan`); bare names are never re-rooted. The review override is returned as-is. |

## Invariants

- **One invocation lock per uid.** Provider auth state is per-user, so the subprocess
  lock lives under `koan_tmp_dir()` (per-uid), not a fixed `/tmp` path.
- **Provider resolution has a fixed precedence** (env → config → default). New
  resolution inputs slot into that order; do not add parallel resolution paths.
- **`KOAN_CLAUDE_CLI_PATH` relative paths root at `KOAN_ROOT`, not CWD.** The
  agent runs from `KOAN_ROOT/koan` (the Makefile does `cd koan`), so a naive
  relative path would resolve to the wrong place. Joining against `KOAN_ROOT`
  in `binary()` is what makes the portable `.env` form work; a future
  simplification that re-targets the join at CWD silently breaks every such
  setup. Bare command names stay PATH lookups and are never re-rooted.
- **Claude binary resolution is gated by review context.** `KOAN_CLAUDE_CLI_FOR_REVIEW_PATH`
  wins over `KOAN_CLAUDE_CLI_PATH` *only* while `review_cli_override()` is active; outside
  a review it is invisible. New per-call binary overrides must follow this context-gated
  pattern (a `ContextVar` activated at the call site), never an unconditional env read.
- **Tool-name vocabularies differ per provider.** Copilot maps its own names; the
  abstraction must translate, not leak provider-specific tool names upward.
- **Quota/usage extraction is provider-specific.** Claude exposes usage in
  `modelUsage` (no top-level `model` field); codex surfaces quota only via the
  stream-json summary (`rate_limit_rejected`, stdout JSONL — never stderr). Detectors
  read the summary stream, not assistant text.

## Integration points

- Invoked by `run.run_claude_task()` and skill runners.
- Usage flows to `usage_tracker.py` / `burn_rate.py` via the `record_usage()` hook.
- Per-project provider override from `projects_config.get_project_cli_provider()`.
- `devcontainer.py` wraps the provider argv with `devcontainer exec` (claude-only
  credential steps).

## Known debt / watch-outs

- `cli_provider.py` is a legacy re-export — prefer importing from `provider` directly.
- `ClaudeProvider` has no `detect_auth_failure()` override, so auth signals like
  "Please run /login" must be caught by the shared `_AUTH_RE` patterns against
  `[cli]`-prefixed runtime lines before delegating to the provider.
- Adding a provider means: subclass `CLIProvider`, register it, add tool-name mapping,
  and define usage extraction — partial implementations silently degrade usage tracking.

## Change protocol

A new provider or a change to the `CLIProvider` contract updates this spec, adds a
provider doc under `docs/providers/`, and verifies usage extraction against a recorded
sample of that CLI's output format.
