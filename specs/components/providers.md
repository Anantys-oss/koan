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
| `__init__.get_provider_display()` / `get_cli_binary_name()` | Display helpers. `get_provider_display()` returns `"<name>"` or `"<name> (<binary>)"` when `KOAN_CLAUDE_CLI_PATH` points at a different binary. Single source of truth for the global provider line shown by the startup banner and `/status`. Per-role provider overrides are summarized separately by `describe_cli_roles()`. |
| `__init__.get_provider_for_role(role, project_name)` / `get_fallback_provider(project_name)` / `resolve_role_provider(role, project_name)` | Per-role provider selection (the `cli:` config section). `get_provider_for_role` returns the **global cached singleton** when the role is unset (parity) or a **fresh** `_PROVIDERS[flavor](binary_path=path)` otherwise — never written to `_cached_provider`. `get_fallback_provider` returns the single section-wide `cli.fallback` instance (or `None`). `resolve_role_provider` is the stateless-helper entry point: it pre-flight-swaps to the fallback when the role binary is unavailable. |
| `cli:` config / `config.get_cli_config()` / `get_cli_fallback()` | New config section parallel to `models:`. `cli.default.<role>` (+ per-project flat `cli.<role>`) maps a mission role (`mission`/`chat`/`lightweight`/`review_mode`/`reflect`) to a `flavor` or `flavor:path`; a single `cli.fallback` provider is used on launch/auth failure. The role's MODEL resolves against that provider's `models.<provider>.<role>` block (`get_model_config(role_providers=…)`). Replaces the removed `KOAN_CLAUDE_CLI_FOR_REVIEW_PATH`. |
| Provider resolution | Order: `KOAN_CLI_PROVIDER` env (fallback `CLI_PROVIDER`) → `projects.yaml`/`config.yaml` → default. Centralized in `utils.get_cli_provider_env()`. This resolves the GLOBAL provider; `cli.<role>` layers per-role selection on top via `get_provider_for_role`. |
| `CLIProvider(binary_path="")` / `ClaudeProvider.binary()` | The base class takes an optional per-instance `binary_path` override (the replacement for the removed review ContextVar); `_resolve_binary_path()` is the shared resolver (absolute → as-is / relative → `normpath(join(KOAN_ROOT, …))` / bare name → PATH lookup). `ClaudeProvider.binary()`: `_binary_override` if set → else `KOAN_CLAUDE_CLI_PATH` → else `"claude"`. Every provider's `binary()` honors the override so `flavor:path` works uniformly. Relative paths root at `KOAN_ROOT` (not CWD — the agent runs from `KOAN_ROOT/koan`); bare names are never re-rooted. |

## Invariants

- **One invocation lock per uid.** Provider auth state is per-user, so the subprocess
  lock lives under `koan_tmp_dir()` (per-uid), not a fixed `/tmp` path.
- **Provider resolution has a fixed precedence** (env → config → default) for the
  GLOBAL provider. Per-role selection (`cli.<role>`) layers on top via
  `get_provider_for_role`; it does not introduce a second GLOBAL resolution path.
- **`KOAN_CLAUDE_CLI_PATH` and `cli: flavor:path` relative paths root at `KOAN_ROOT`, not CWD.** The
  agent runs from `KOAN_ROOT/koan` (the Makefile does `cd koan`), so a naive
  relative path would resolve to the wrong place. The shared `_resolve_binary_path()`
  joins against `KOAN_ROOT`; a future simplification that re-targets the join at
  CWD silently breaks every such setup. Bare command names stay PATH lookups and
  are never re-rooted.
- **Per-role provider instances must never poison the global singleton.**
  `get_provider_for_role`/`get_fallback_provider` construct a fresh
  `_PROVIDERS[flavor](binary_path=path)` and return it directly; they must never
  assign `_cached_provider`. `get_provider()` (role-less) stays the cached
  singleton. A path-bearing instance leaking into the cache would silently
  rebind every role-less caller to a custom binary.
- **The `cli:` absence contract is exact parity.** With no `cli:` section, every
  role resolves to `(get_provider_name(), "")` and `get_model_config(role_providers=None)`
  is byte-for-byte the historical behavior. Changes here must preserve that.
- **Provider fallback is launch/auth only, never quota/transient.** The single
  `cli.fallback` provider is substituted only on binary-not-found (exit 127 /
  `is_available()` False) or `ErrorCategory.AUTH`, and (on the mission path) only
  when no commits were produced. Quota still pauses; transient errors still use
  the in-place retry. Do not widen this to quota — that would double-spend across
  subscriptions and change the pause contract.
- **Tool-name vocabularies differ per provider.** Copilot maps its own names; the
  abstraction must translate, not leak provider-specific tool names upward.
- **Quota/usage extraction is provider-specific.** Claude exposes usage in
  `modelUsage` (no top-level `model` field); codex surfaces quota only via the
  stream-json summary (`rate_limit_rejected`, stdout JSONL — never stderr). Detectors
  read the summary stream, not assistant text.

## Integration points

- Invoked by `run.run_claude_task()` and skill runners.
- Usage flows to `usage_tracker.py` / `burn_rate.py` via the `record_usage()` hook.
- Per-role provider selection from the `cli:` section (`config.get_cli_config()`),
  threaded into `mission_runner.build_mission_command()` (mission/review roles),
  the `run_command*` helpers (their `model_key` role), and
  `contemplative_runner.build_contemplative_command()` (lightweight role). The
  launch/auth fallback re-run lives in `mission_executor._maybe_fallback_provider_rerun()`.
- `devcontainer.py` wraps the provider argv with `devcontainer exec` (claude-only
  credential steps); the fallback re-run re-applies this wrap.

## Known debt / watch-outs

- `cli_provider.py` is a legacy re-export — prefer importing from `provider` directly.
- `projects_config.get_project_cli_provider()` (the old per-project global-provider
  accessor) is still NOT wired into `get_provider()`; the `cli:` section (incl. its
  per-project flat form) is the supported per-project provider mechanism going forward.
- The stateless `run_command*` helpers fall back on *launch* failure (binary
  unavailable) via `resolve_role_provider`'s pre-flight `is_available()` swap;
  full AUTH-triggered fallback exists only on the stateful mission path.
- `ClaudeProvider` has no `detect_auth_failure()` override, so auth signals like
  "Please run /login" must be caught by the shared `_AUTH_RE` patterns against
  `[cli]`-prefixed runtime lines before delegating to the provider.
- Adding a provider means: subclass `CLIProvider`, register it, add tool-name mapping,
  and define usage extraction — partial implementations silently degrade usage tracking.

## Change protocol

A new provider or a change to the `CLIProvider` contract updates this spec, adds a
provider doc under `docs/providers/`, and verifies usage extraction against a recorded
sample of that CLI's output format.
