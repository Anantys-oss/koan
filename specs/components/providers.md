---
type: component-spec
title: "Component Spec — CLI Provider Abstraction"
description: "Design contract for the CLI provider abstraction that decouples the agent loop from any single AI coding CLI (Claude, Cline, Codex, Copilot, Haze, Grok) behind one `CLIProvider` contract."
tags: [providers]
created: 2026-06-27
updated: 2026-07-17
---

# Component Spec — CLI Provider Abstraction

**Package:** `koan/app/provider/` (`base.py`, `claude.py`, `cline.py`, `codex.py`,
`copilot.py`, `fake.py`, `haze.py`, `grok.py`, `__init__.py`) + `cli_provider.py` (legacy re-export facade)

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
       ├─ copilot.py   → CopilotProvider (with tool-name mapping)
       ├─ fake.py      → FakeProvider (fail-closed test/dev stub; never a real LLM)
       ├─ haze.py      → HazeProvider (haze ≥0.7.0 headless stream-json)
       └─ grok.py      → GrokProvider (xAI Grok Build headless streaming-json)
```

## Key types & functions

| Symbol | Contract |
|---|---|
| `base.CLIProvider` | The contract: build command, run, stream, tool-name vocabulary. |
| `base.supports_usage_tracking()` / `record_usage()` | Per-provider usage hooks. Not all CLIs surface usage the same way. |
| `__init__.run_command()` / `run_command_streaming()` | The single invocation entry points. Both accept an optional `mcp_configs` list; callers pass it only for roles opted into MCP via `config.mcp_roles` (resolved through `config.mcp_configs_for_role(role, project_name)`). Omitted/`None` → no `--mcp-config` is emitted. Callers should not spawn provider subprocesses directly. |
| `__init__.build_full_command()` | Assembles the provider-specific argv. |
| `__init__.get_provider_display()` / `get_cli_binary_name()` | Display helpers. `get_provider_display()` returns `"<name>"` or `"<name> (<binary>)"` when `KOAN_CLAUDE_CLI_PATH` points at a different binary. Single source of truth for the global provider line shown by the startup banner and `/status`. Per-role provider overrides are summarized separately by `describe_cli_roles()`. |
| `base.custom_binary_name()` / `__init__.provider_cli_display(provider)` | Per-instance attribution helpers. `custom_binary_name()` returns the basename of a pinned custom binary (per-role `_binary_override` from `cli.<role>: flavor:path`; Claude also surfaces the global `KOAN_CLAUDE_CLI_PATH`), or `''` when no override is configured. `provider_cli_display(provider)` returns that basename or, failing that, the provider flavor name — used by `review_runner._review_attribution()` so the review footer shows the CLI that actually ran (e.g. `claude-deep`), not just the flavor. Only real overrides count: a provider's natural fallback (Copilot's `gh`) is never surfaced as "custom". |
| `__init__.get_provider_for_role(role, project_name)` / `get_fallback_provider(project_name)` / `resolve_role_provider(role, project_name)` | Per-role provider selection (the `cli:` config section). `get_provider_for_role` returns the **global cached singleton** when the role is unset (parity) or a **fresh** `_PROVIDERS[flavor](binary_path=path)` otherwise — never written to `_cached_provider`. `get_fallback_provider` returns the single section-wide `cli.fallback` instance (or `None`). `resolve_role_provider` is the stateless-helper entry point: it pre-flight-swaps to the fallback when the role binary is unavailable. |
| `cli:` config / `config.get_cli_config()` / `get_cli_fallback()` | New config section parallel to `models:`. `cli.default.<role>` (+ per-project flat `cli.<role>`) maps a mission role (`mission`/`chat`/`lightweight`/`review_mode`/`reflect`) to a `flavor` or `flavor:path`; a single `cli.fallback` provider is used on launch/auth failure. The role's MODEL resolves against that provider's `models.<provider>.<role>` block (`get_model_config(role_providers=…)`). Replaces the removed `KOAN_CLAUDE_CLI_FOR_REVIEW_PATH`. |
| `effort:` config / `config.get_effort(mode, mission_type)` / `CLIProvider.build_effort_args()` | Reasoning-effort control for the Claude `--effort` flag (low/medium/high/max). `effort:` mapping keys are **mission types** (the `session_tracker.classify_mission_type` taxonomy: plan/review/implement/audit/…), not budget modes. Resolution in `get_effort()`: `effort.<mission_type>` → `effort.<autonomous_mode>` (legacy) → `_DEFAULT_EFFORT_MAP[mode]` (the dynamic default). The dynamic default — review→low, deep→high, else none — is preserved verbatim when `effort:` is absent; a per-type pin only layers on top. `build_mission_command()` classifies the mission type and passes it through, so a pin only reaches `get_effort()` for missions that run through the main agent loop — **not** for skill-dispatched commands (`/review`, `/plan`, …), which bypass `build_mission_command()` (see reach caveat below); `get_effort_for_mode()` is the type-unaware wrapper for callers outside the mission build path. `extended thinking` short-circuits effort to `max`. |
| Provider resolution | Order: `KOAN_CLI_PROVIDER` env (fallback `CLI_PROVIDER`) → `projects.yaml`/`config.yaml` → default. Centralized in `utils.get_cli_provider_env()`. This resolves the GLOBAL provider; `cli.<role>` layers per-role selection on top via `get_provider_for_role`. |
| `CLIProvider(binary_path="")` / `ClaudeProvider.binary()` | The base class takes an optional per-instance `binary_path` override (the replacement for the removed review ContextVar); `_resolve_binary_path()` is the shared resolver (absolute → as-is / relative → `normpath(join(KOAN_ROOT, …))` / bare name → PATH lookup). `ClaudeProvider.binary()`: `_binary_override` if set → else `KOAN_CLAUDE_CLI_PATH` → else `"claude"`. Every provider's `binary()` honors the override so `flavor:path` works uniformly. Relative paths root at `KOAN_ROOT` (not CWD — the agent runs from `KOAN_ROOT/koan`); bare names are never re-rooted. |
| `build_command(..., project_context=True)` / `build_project_context_args` / `build_full_command(..., project_context=…)` | When `project_context=False`, the provider must suppress **project-scope** tooling loaded from cwd (Claude: `--setting-sources user`). Default `True` preserves mission/project CLAUDE.md / skills. Other providers may no-op. Callers that run with `cwd=KOAN_ROOT` (Telegram chat, **dashboard web chat**, contemplative, rituals, outbox formatting) **must** pass `False`. Do not implement isolation by mutating the worktree (`skip-worktree` / quarantine) on this path. |

### MCP per-role boundary (safety contract)

MCP servers are loaded per **execution role**, not globally. `config.mcp_roles`
(default `["mission", "contemplative", "plan"]`; per-project override in
projects.yaml replaces the list) is the allowlist of roles that receive
`--mcp-config`. Conversational roles consuming untrusted input (`chat`,
`github_reply`) are excluded by default and opt-in only. `mcp_roles: []` is a
kill switch: no runner passes `--mcp-config`. Loading a server never grants its
tools — MCP tools must still be allowlisted via qualified names
(`mcp__<server>` / `mcp__<server>__<tool>`) in the role's `tools:` list unless
`skip_permissions` is set. Callers resolve configs through
`config.mcp_configs_for_role(role, project_name)` rather than
`get_mcp_configs()` directly so the gate and kill switch always apply.

## Invariants

- **KOAN_ROOT runtime sessions must not load contributor project tooling.**
  Telegram chat, dashboard web chat, contemplative, rituals, and outbox
  formatting run with `cwd=KOAN_ROOT` on a deployed clone (dashboard may
  also target a selected project path — only the KOAN_ROOT case requires
  `False`). They must pass `project_context=False` so Claude does not
  auto-load root `CLAUDE.md` / `AGENTS.md` / `.claude/skills` (e.g. `brain`,
  `speckit-*`) into operator-facing output. Mission sessions keep the default
  (`True`) so `workspace/` project guidance still loads. Isolation is at the
  **CLI flag boundary**, not by relocating tracked files on disk.
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
- **The `effort:` absence contract is the dynamic default.** With no `effort:`
  section, `get_effort()` returns `_DEFAULT_EFFORT_MAP[mode]` (review→low,
  deep→high, else `""`) — the historical budget-mode-driven behavior, untouched.
  Per-mission-type pins only layer on top: `effort.<mission_type>` (a
  `classify_mission_type` category) wins over `effort.<mode>`, which wins over
  the dynamic default. **Reach caveat:** this path is wired only into
  `build_mission_command()`, which the main agent loop calls for missions that
  are *not* dispatched to a dedicated skill runner. Skill-dispatched commands
  (`/review`, `/plan`, `/rebase`, `/recreate`, `/implement`, `/fix`, `/audit`,
  `/check`, …) are routed to their own runners before this path and are not
  governed by `effort:` — so a `review: low` pin has no effect on `/review`,
  which runs in `review_runner`. Slash commands *without* a dedicated runner
  don't reach it either: `/refactor`/`/pr` are handled by their bridge-side
  handler or failed as an unknown skill in `_handle_skill_dispatch` before
  `build_mission_command`. In practice the only pins that fire are
  **`autonomous`** and **`freetext`** (non-slash missions). A partial dict
  leaves unlisted modes on the dynamic default (not disabled), preserving the
  absence contract per-mode.
  `get_effort_for_mode()` is the type-unaware wrapper and must stay equivalent
  to `get_effort(mode, "")`. `config_validator` accepts any `effort.*` key (the
  mission-type set is open) but validates every value — dict entries *and* the
  scalar shorthand (`effort: "high"`) — against `_VALID_EFFORT_LEVELS`, so a
  typo'd level warns rather than silently dropping the flag.
- **Footer attribution shows the binary that ran, then falls back to the flavor.**
  `review_runner._review_attribution()` is the single source of truth for the review
  footer's CLI label: `provider_cli_display()` surfaces the basename of a pinned
  review binary (`cli.review_mode: flavor:path`, or Claude's `KOAN_CLAUDE_CLI_PATH`)
  so the signature reads e.g. `claude-deep`, not `Claude`; with no override it falls
  back to the provider flavor. `pr_footer._provider_label()` title-cases known
  provider flavors (`claude` → `Claude`) but renders custom binary basenames
  verbatim — they are technical identifiers, not brand names.
- **Provider fallback is launch/auth only, never quota/transient.** The single
  `cli.fallback` provider is substituted only on binary-not-found (exit 127 /
  `is_available()` False) or `ErrorCategory.AUTH`, and (on the mission path) only
  when no commits were produced. Quota still pauses; transient errors still use
  the in-place retry. Do not widen this to quota — that would double-spend across
  subscriptions and change the pause contract.
- **Root handling for `skip_permissions` is Claude-specific.** The Claude CLI
  refuses `--dangerously-skip-permissions` under root/sudo, so
  `ClaudeProvider.build_permission_args()` (inherited by `OllamaLaunchProvider`)
  drops the flag under euid 0 with a once-per-process warning.
  `config.get_skip_permissions()` stays a pure config read — moving the root
  check there would silently strip Codex full access and Cline auto-approve
  for root deployments, whose CLIs accept the setting.
- **The `fake` provider is fail-closed by construction.** `FakeProvider` is a
  test/dev stub that never invokes a real LLM. Its `__init__` raises
  `FakeProviderNotAllowed` (a `RuntimeError`) unless `KOAN_ALLOW_FAKE_PROVIDER`
  is truthy (`1`/`true`/`yes`/`on`). The guard lives in the constructor — not at
  selection time — so **every** resolution path that instantiates a provider
  (`get_provider`, `get_provider_by_name`, `get_provider_for_role`,
  `get_fallback_provider`, all via `_PROVIDERS[name]()`) fails closed identically.
  Selecting `fake` without the flag must **error**, never silently fall back to a
  real provider. `binary()` returns the POSIX no-op `true` (never `claude`) so the
  built command runs harmlessly with empty output; `is_available()` is
  unconditionally `True` (no external binary) and `has_api_quota()` is `False`
  (no budget gating). The `RuntimeError` base means the broad `except Exception`
  guards in `quota_handler._detect_quota_for_provider` /
  `cli_errors._detect_auth_for_provider` degrade conservatively when a name-based
  lookup of `fake` is attempted without the flag. **`get_fallback_provider` is the
  one exception to "fail loud":** it is contractually `Optional` and is called on
  *any* non-zero mission exit (`mission_executor._maybe_fallback_provider_rerun`),
  so a `cli.fallback: fake` without the flag must return `None` (decline the
  fallback), not raise — otherwise an unrelated real-provider failure would crash
  during finalization. This is still not a silent swap: no work is routed to `fake`,
  and the primary selection paths (`get_provider`/`get_provider_for_role`) still
  error loudly. Response routing (canned/scripted output) is out of scope for this
  foundation — `build_command` is a no-op stub. `FakeProvider` sets the base-class
  `test_only = True` flag: it stays in `_PROVIDERS` (so `known_providers`,
  name-based lookup, and config validation resolve it), but UI-facing pickers use
  `selectable_providers()` — which filters `test_only` flavors — so `fake` never
  appears as a selectable option in the dashboard provider dropdown. The refusal
  message derives its real-provider hint from the registry (`known_providers()`
  minus `fake`) so it does not drift as providers are added.
- **Quota/usage extraction is provider-specific.** Claude exposes usage in
  `modelUsage` (no top-level `model` field); codex surfaces quota only via the
  stream-json summary (`rate_limit_rejected`, stdout JSONL — never stderr); haze
  reports usage only in its terminal result envelope with **camelCase** fields
  (`inputTokens`/`outputTokens`/`cacheReadTokens`/`cacheWriteTokens`/`reasoningTokens`);
  Grok Build reports **snake_case** `usage` on the terminal ``end`` event
  (`input_tokens`/`output_tokens`/`cache_read_input_tokens`/…) plus optional
  `modelUsage` map (camelCase per model id). Shared extractors are shape-keyed
  on field names. Detectors read the summary stream, not assistant text.
- **`tool_use` summary grammar carries an optional input preview.**
  `_summarize_stream_event()` renders a `tool_use` block as
  `[cli] assistant — tool_use: <name>[: <input-preview>]`. The optional
  `: <preview>` suffix is a bounded first-line excerpt of the tool input
  (see `_tool_input_preview` / `_TOOL_PREVIEW_KEYS`) and is additive:
  consumers that key off `tool_use: <name>` (substring) or off the quota
  markers (`rate_limit_rejected`, session-limit phrasing) are unaffected.
  The display-side `log_fmt.py` splits name from preview on the first `": "`.
  Free-text preview values (tool-input and `text:` excerpts) never contain the
  `", "` part delimiter — `_summarize_stream_event()` collapses it to a bare
  comma (`_drop_part_sep`) so the display splitter (`log_fmt._PART_SEP`) can
  never mis-split a preview into a spurious part.
- **Shared stream parsers extend by event SHAPE, never by provider name.** The
  central summarizer/text/usage extractors in `provider/__init__.py` (and the
  mission-stdout path in `token_parser.py`) branch on field presence
  (e.g. `inputTokens` ⇒ camelCase usage) so the agent loop never learns which
  provider is running (Provider Isolation). Adding a provider must not add
  `if provider == …` branches to shared code.
- **Haze headless contract (haze ≥ 0.7.0).** `HazeProvider` targets haze's
  documented harness mode: `--output stream-json` NDJSON progress events
  (`turn_start`/`message_*`/`tool_*`/`retry`/`context_overflow`/`turn_end`)
  terminated by a result envelope `{type:"result", status, result, usage}` that is
  byte-identical to `--output json`; exit code 0 ⇔ status `complete`
  (`failed`/`aborted` are failures, never success). Because haze streams,
  it uses the standard `supports_stream_json()` path — **no**
  incremental-progress capability flag and **no** agent-loop bypass may be
  (re)introduced for it. Prompt delivery: the *target* design is stdin via a
  flag-REMOVAL `rewrite_prompt_for_stdin()` (haze reads stdin only when `-p`
  is absent; the base marker substitution would send the marker as the literal
  prompt), but stdin passing is **disabled**
  (`supports_stdin_prompt_passing()` False) until upstream fixes its stdin
  gate — haze checks `process.stdin.isTTY === false` and Node reports
  `undefined` for pipes/files, so piped runs fall into the interactive UI
  (verified live 2026-07-10). Until then the prompt rides argv as `-p`
  (subject to OS per-argument limits); the dormant rewrite stays implemented
  and tested so the flip is one line. Headless haze is one-shot (no session
  resume) and exposes no
  per-tool/MCP/plugin/max-turns/fallback-model/effort controls — those inputs
  are skipped but never silently: a two-tier notice policy applies, deduped
  once per process. Static capabilities driven by Kōan's OWN defaults
  (per-tool allow/deny, max turns, fallback model — passed unconditionally by
  the loop; the operator cannot act) log at **info**; operator-actionable
  config (MCP, plugins, effort, resume, system-prompt file — removable) and
  the safety-relevant no-permission-gates notice log at **warning**.
  Quota/auth detection uses
  backend-agnostic patterns (haze fronts OpenAI/OpenRouter/local backends):
  stderr trusted fully, stdout only on non-zero exit with an error-marker gate.
  Pre-flight quota check is a minimal token-consuming `--output json` probe
  (cline precedent) run from a fresh EMPTY scratch directory — never the
  project dir, whose CLAUDE.md/AGENTS.md context haze would ingest (~12K
  tokens per probe); probe errors never block work. Invocation lock:
  `haze-cli` (shared `~/.haze/settings.json` state).
- **Grok Build headless contract (verified 0.2.101).** `GrokProvider` targets
  xAI Grok Build headless mode: `grok` with `--output-format streaming-json`
  (Koan internal name `stream-json` maps to CLI spelling `streaming-json`).
  NDJSON event vocabulary is shape-keyed: `thought`/`text` carry incremental
  `data` deltas; terminal `end` carries `stopReason`, `usage` (snake_case),
  `num_turns`, and optional `modelUsage` — **not** the final assistant body.
  Final text is the concatenation of `text.data` deltas (joined with `""`, not
  newlines). `--output-format json` returns a single object with top-level
  `text` + `usage` (probe mode).
  **Permissions (headless invariant):** Koan **always** passes
  `--always-approve` for Grok headless invokes. Grok’s CLI `--permission-mode`
  flag only effectively applies `bypassPermissions` and `default`; passing
  `acceptEdits` is a no-op on the flag. In headless mode, any tool call that
  would prompt is **cancelled immediately** (`stopReason: Cancelled`,
  `cancellation_category: permission_cancelled`) — shell tools such as
  `run_terminal_command` then fail, so `/implement` lands no commits. Do not
  reintroduce `acceptEdits` as a “safer” headless default. Operators who want
  to signal intent still set `skip_permissions: true`; when it is false, Grok
  still emits `--always-approve` and logs a once-per-process notice.
  **Tools:** Claude/Koan tool names are mapped to Grok internal IDs before
  `--tools` / `--disallowed-tools` (e.g. `Read`→`read_file`, `Edit`→
  `search_replace`, `Bash`→`run_terminal_cmd`, `Grep`→`grep`, `Glob`→
  `list_dir`, `Write`→`write`, `WebFetch`→`web_fetch`). Unknown names warn
  once; unmapped `Skill` is dropped. Max turns → `--max-turns`; system prompt
  append → `--rules` (file prompts are inlined); effort → `--reasoning-effort`;
  resume → `--resume`. **Models:** Claude tier aliases (`haiku`/`sonnet`/
  `opus`/…) are never passed as `-m` — warn once and omit so Grok’s default
  model is used. **Cancelled is hard failure:** a terminal `end` with
  `stopReason` Cancelled/canceled raises (never soft-success with partial
  text). **Prompt delivery:** large prompts use `--prompt-file` (temp file +
  cleanup); stdin prompt passing stays off. Unsupported inputs (MCP flags,
  plugin dirs, fallback model) warn once and are skipped — never silently
  accepted. Quota/auth: stderr trusted; stdout only on non-zero exit with an
  error-marker gate. Pre-flight probe uses `--output-format json -p ok` from an
  empty scratch dir. Invocation lock: `grok-cli` (shared `~/.grok/` state).
  Recorded samples: `koan/tests/grok_samples.py`. Operator docs:
  `docs/providers/grok.md`.

## Integration points

- **Startup availability gate.** `app.cli_health.check_primary_cli()` wraps
  `get_provider().is_available()` (`shutil.which(binary())`) as the single probe used by
  `startup_manager.check_cli_binary()` (enters an in-memory degraded/no-mission mode on a
  miss — see `specs/components/agent-loop.md`), the `/status` skill, and the `/doctor`
  diagnostics (`environment_check` / `connectivity_check`, which resolve the real
  `provider.binary()` rather than a hardcoded provider→binary map). `provider.missing_binary_message()`
  is the shared constructor for the actionable "CLI executable not found" error raised by
  `run_command_streaming` and (as an exit-127 failure) by `run.run_claude_task`.
- Invoked by `run.run_claude_task()` and skill runners.
- Usage flows to `usage_tracker.py` / `burn_rate.py` via the `record_usage()` hook.
  Structured per-call events are written to `instance/usage/*.jsonl` by
  `cost_tracker.record_usage()`, which now carries an optional `mission_id`
  (resolved best-effort from `.api-missions.json` in `mission_runner._record_cost_event`).
  `cost_tracker.aggregate_mission_usage(instance_dir, mission_id, mission_text=…)`
  is the per-mission read path used by `GET /v1/missions/{id}`.
- **Skill-dispatch token capture**: streaming skill runs persist per-call token
  totals to `KOAN_STREAM_USAGE_FILE` (summed across calls), appended to the stdout
  capture so `_ensure_tokens` parses real tokens. When that sidecar is empty,
  `_record_cost_event` backfills `input/output/cost` from the provider session tail
  (`get_session_data`) so command-missions do not record placeholder zeros.
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
