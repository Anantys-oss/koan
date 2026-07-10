# Internal Contract: HazeProvider within Kōan

How `koan/app/provider/haze.py` fulfills the `CLIProvider` ABC (`koan/app/provider/base.py`) and what it demands from shared code. Capability values: see the profile table in [data-model.md](../data-model.md).

## Provider obligations (implemented in `haze.py`)

| ABC member | HazeProvider behavior |
|---|---|
| `binary()` | `_binary_override` → `"haze"` |
| `build_prompt_args(prompt)` | `["-p", prompt]` (final args; probe & non-stdin callers) |
| `rewrite_prompt_for_stdin(cmd, marker)` | **Override**: locate `-p <prompt>`, REMOVE both tokens (haze reads stdin only when `-p` is absent), return `(cmd_without_flag, prompt)`; return `(cmd, None)` when no `-p` value present. The base marker-substitution is unusable for haze (marker would be read as the literal prompt). |
| `build_model_args(model, fallback)` | `["-m", model]` when set; `fallback` → warn + skip |
| `build_output_args(fmt)` | `"stream-json"` → `["--output", "stream-json"]`; `"json"` → `["--output", "json"]`; else `[]` |
| `build_tool_args` / `build_mcp_args` / `build_plugin_args` / `build_max_turns_args` / `build_effort_args` | `[]`; non-empty input → `log_safe` warning (FR-008: loud skip, never crash) |
| `build_command(...)` | Compose: binary → model → output → prompt; system prompt prepended to prompt text (base fallback; no system-prompt flag); `system_prompt_file` → warn + inline fallback; `resume_session_id` → warn + skip |
| `supports_stream_json()` | `True` — the load-bearing capability (replaces PR #2211's `emits_incremental_progress()` inversion) |
| `invocation_lock_name()` | `"haze-cli"` (shared `~/.haze/settings.json` state) |
| `detect_quota_exhaustion(stdout, stderr, exit_code)` | stderr: full `_HAZE_QUOTA_PATTERNS`; stdout: only when exit ≠ 0 AND line passes `_line_has_error_marker()` |
| `detect_auth_failure(stdout, stderr, exit_code)` | exit 0 → False; else `_HAZE_AUTH_PATTERNS` over stderr, then stdout lines |
| `check_quota_available(project_path, timeout=15)` | Minimal `--output json` "ok" probe via `run_cli` (stdin piping, lock honored); classify via the two detectors; ANY probe error/timeout → `(True, "")` |
| `get_session_data(project_path)` | `None` — usage arrives via the stream-usage sidecar / stdout token parsing, not session files (headless haze writes none) |
| `has_api_quota()` | `True` |

## Demands on shared code (shape-based, no provider names)

1. `provider/__init__.py::_usage_snapshot_from_event()` — accept the camelCase usage shape (mapping table in data-model.md). Feeds `KOAN_STREAM_USAGE_FILE` sidecar accumulation unchanged.
2. `token_parser.py` — same camelCase shape in dict/JSONL extraction (mission-stdout usage path via `usage_estimator.cmd_update`).
3. `provider/__init__.py::_summarize_stream_event()` — render haze event types per the data-model treatment column; every rendered line is a watchdog liveness signal (do not silence).
4. `provider/__init__.py::_extract_assistant_text_chunks()` — collect non-hidden `message_end` text (keyed by `id`) as the partial-death fallback; never accumulate `message_update` (cumulative snapshots would duplicate).
5. `provider/__init__.py` error preview — treat `turn_end` with non-`complete` status and `context_overflow` with `recovered:false` as error-context candidates for `_format_cli_error()`.
6. Registry: `_PROVIDERS["haze"] = HazeProvider` + top import. Everything else (`known_providers()`, config validation, dashboard forms, per-role `cli:` resolution, fallback) derives automatically.

## Explicit non-changes (contract of restraint)

- `run.py`, `mission_executor.py`, `stagnation_monitor.py`, `quota_handler.py`, `reset_parser.py`, `cli_errors.py`: **zero haze-specific edits** (constitution Principle IV). If implementation appears to need one, the design is wrong — stop and re-plan.
- `base.py`: no new capability hooks.

## Durable-contract delta (declared architectural)

`specs/components/providers.md`: add haze to the registry enumeration + a capability row (stream-json: yes; resume: no; usage: envelope-based via shared camelCase translation; quota: generic multi-backend patterns; lock: `haze-cli`). Land contract-first per constitution Principle II with the PR's "Architectural change" box checked (`scripts/spec_change_guard.py` enforces).
