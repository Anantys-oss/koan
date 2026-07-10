# Data Model: Haze CLI Provider

**Date**: 2026-07-10 | **Spec**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

No persistent storage is introduced. The "data model" is the set of in-flight shapes exchanged between haze and Kōan, and their translation rules.

## Entities

### HazeProvider (capability profile)

The provider instance selectable by name `haze`. Declared capabilities (each maps to a `CLIProvider` hook):

| Capability | Value | Notes |
|---|---|---|
| `name` | `"haze"` | Registry key; config/env value |
| `binary()` | `"haze"` (override via constructor `binary_path`) | Availability = `shutil.which` |
| `supports_stream_json()` | `True` | Routes through shared streaming path |
| `supports_stdin_prompt_passing()` | `True` | With custom rewrite (drop `-p`) |
| `supports_session_resume()` | `False` | Headless haze is one-shot by design |
| `supports_system_prompt_file()` | `False` | System prompt prepended to prompt text |
| `supports_last_message_file()` | `False` | Result text comes from the envelope |
| `invocation_lock_name()` | `"haze-cli"` | Serializes shared `~/.haze` settings access |
| `has_api_quota()` | `True` | Metered backends behind haze |
| Tool args / MCP / plugins / max-turns / effort / fallback model | none | Warn-and-skip (FR-008) |

### Command line (constructed argv)

```
haze [-m <provider:model>] --output stream-json -p <prompt>
```
then, for streaming/mission execution, `cli_exec` rewrites via the provider override to:
```
haze [-m <provider:model>] --output stream-json          # prompt piped on stdin
```
The probe path uses `--output json` and the same stdin piping. Validation rules: `-m` emitted only when a model is configured (haze's active model otherwise); `fallback` model logged + skipped; `--output` mapping: `stream-json` → `stream-json`, `json` → `json`, empty → none (text).

### Stream event (haze → Kōan, one NDJSON line each)

Discriminated union on `type` (all events carry ISO-8601 `at`):

| `type` | Fields | Kōan treatment |
|---|---|---|
| `turn_start` | `request` | Summary line |
| `turn_end` | `request`, `status` | Summary line incl. status; non-`complete` status is an error-preview candidate |
| `message_start` | `id`, `role:"assistant"` | Summary line |
| `message_update` | `id`, `text` (cumulative) | Cheap summary (type tag / first-line preview); NOT accumulated as text |
| `message_end` | `id`, `text`, `hidden?` | Text preview; non-hidden `text` collected as partial-output fallback, keyed by `id` |
| `tool_start` | `id`, `name` | Summary line (raw inputs omitted upstream by design) |
| `tool_end` | `id`, `name`, `success`, `durationMs`, `error?` | Summary line incl. success/duration |
| `retry` | `attempt`, `maxAttempts`, `delayMs`, `error` | Summary line |
| `context_overflow` | `recovered`, `error` | Summary line; `recovered:false` is an error-preview candidate |

State transitions per run: `turn_start` → (message/tool/retry/context_overflow)* → `turn_end` → result envelope (final line). Headless haze emits exactly one `turn_start`/`turn_end` pair (nested retry turns are collapsed upstream).

### Result envelope (terminal line; identical for `json` and `stream-json`)

```json
{"type":"result","status":"complete|aborted|failed","result":"<final assistant text>","usage":{"inputTokens":0,"outputTokens":0,"cacheReadTokens":0,"cacheWriteTokens":0,"reasoningTokens":0}}
```

Invariants: always the last line; all five usage fields always present (pinned upstream); `status` is authoritative and agrees with the exit code (`0` ⇔ `complete`). Already compatible with `_extract_result_text()` (`type:"result"` + string `result`).

### Usage snapshot (translation rule — the core data mapping)

| haze field (camelCase) | Kōan snapshot field | Rule |
|---|---|---|
| `inputTokens` | `input_tokens` | minus cache-read (mirrors existing snake_case branch's cached-input subtraction) |
| `outputTokens` | `output_tokens` | direct |
| `cacheReadTokens` | `cache_read_input_tokens` | direct |
| `cacheWriteTokens` | `cache_creation_input_tokens` | direct |
| `reasoningTokens` | accounted within `output_tokens` (subset) | AI-SDK reporting: `reasoningTokens` is a subset of `outputTokens` (OpenAI `completion_tokens_details` semantics) — adding it on top would double-count (FR-005 satisfied by subset accounting) |
| (absent) | `model` | `"unknown"` unless a `-m` override was passed (envelope carries no model) |

Applied shape-based (fields present ⇒ branch taken) in **both** pipelines: `_usage_snapshot_from_event()` (stream sidecar) and `token_parser` dict/JSONL extraction (mission stdout).

### Outcome mapping

| haze terminal state | exit code | Kōan mission outcome |
|---|---|---|
| `complete` | 0 | success |
| `failed` | ≠0 | failure; classified via quota → auth → retryable/terminal patterns |
| `aborted` | ≠0 | failure, labeled aborted (interrupt) |
| no envelope (killed mid-stream) | n/a | failure path; partial text from collected `message_end` chunks |

### Failure-classification patterns (provider-owned)

`_HAZE_QUOTA_PATTERNS` / `_HAZE_AUTH_PATTERNS` (backend-agnostic regex sets, salvaged from PR #2211): stderr trusted fully; stdout lines consulted only when exit ≠ 0 AND `_line_has_error_marker()` matches — benign assistant prose on successful runs can never trigger a pause (SC-003).
