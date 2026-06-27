# Component Spec — CLI Provider Abstraction

**Package:** `koan/app/provider/` (`base.py`, `claude.py`, `cline.py`, `copilot.py`,
`__init__.py`) + `cli_provider.py` (legacy re-export facade)

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
       └─ copilot.py   → CopilotProvider (with tool-name mapping)
```

## Key types & functions

| Symbol | Contract |
|---|---|
| `base.CLIProvider` | The contract: build command, run, stream, tool-name vocabulary. |
| `base.supports_usage_tracking()` / `record_usage()` | Per-provider usage hooks. Not all CLIs surface usage the same way. |
| `__init__.run_command()` / `run_command_streaming()` | The single invocation entry points. Callers should not spawn provider subprocesses directly. |
| `__init__.build_full_command()` | Assembles the provider-specific argv. |
| Provider resolution | Order: `KOAN_CLI_PROVIDER` env (fallback `CLI_PROVIDER`) → `projects.yaml`/`config.yaml` → default. Centralized in `utils.get_cli_provider_env()`. |

## Invariants

- **One invocation lock per uid.** Provider auth state is per-user, so the subprocess
  lock lives under `koan_tmp_dir()` (per-uid), not a fixed `/tmp` path.
- **Provider resolution has a fixed precedence** (env → config → default). New
  resolution inputs slot into that order; do not add parallel resolution paths.
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
