# Research: Haze CLI Provider

**Date**: 2026-07-10 | **Spec**: [spec.md](./spec.md)

All findings below were verified directly against haze main (v0.8.0, `src/cli/commands/runCommand.ts`), the haze CHANGELOG, legacy PR Anantys-oss/koan#2211's diff, and Kōan's provider package. No NEEDS CLARIFICATION items remain (two spec-level questions were resolved in `/speckit-clarify`, session 2026-07-10).

## R1. Haze headless contract (external dependency)

**Decision**: Target haze ≥ 0.7.0; invoke as `haze [-m <selector>] --output stream-json` with the prompt piped via stdin.

**Rationale**: Haze 0.7.0 (2026-06-29) added `--output stream-json` explicitly *for harnesses*: NDJSON progress events (`turn_start`, `message_start`/`message_update`/`message_end`, `tool_start`/`tool_end`, `retry`, `context_overflow`, `turn_end`, each with ISO-8601 `at`), terminated by a line **byte-identical to the `--output json` envelope**: `{"type":"result","status":"complete|aborted|failed","result":"<text>","usage":{inputTokens,outputTokens,cacheReadTokens,cacheWriteTokens,reasoningTokens}}`. Exit code is 0 only for `complete` and is authoritative (driven by terminal agent state, not reply parsing). Headless runs are one-shot: no durable session, `--continue` ignored. Tool events deliberately omit raw inputs/outputs, so captured stdout is CI/journal-safe. Model/provider credentials are configured only inside haze (`/provider`, `/model` → `~/.haze/settings.json`); haze reads no environment variables by design (removed in 0.5.0). A bad/ambiguous `-m` selector produces a precise stderr error + non-zero exit *before* the agent runs.

**Alternatives considered**: `--output json` (single terminal envelope, PR #2211's target) — rejected as primary mode: silent-until-done output defeats Kōan's stdout-liveness watchdog and stagnation monitor, which is precisely why PR #2211 needed its workaround layer. `--output text` — no structured result/usage. Supporting haze < 0.7.0 — rejected (spec assumption); the failure mode is haze erroring on the unknown `stream-json` output value, surfaced as a normal CLI error.

## R2. Drift analysis: why legacy PR #2211 is superseded, what survives

**Decision**: Fresh implementation; salvage PR #2211's quota/auth regex pattern sets and its cline-style probe; discard its capability flag and agent-loop changes.

**Rationale** (drift, item by item):

1. **`emits_incremental_progress()` + run.py workaround layer (~239 lines + `mission_executor`/`quota_handler`/`reset_parser`/`deep_research`/`review_runner`/status-skill adaptations)** — built for a haze that emitted one silent envelope. Haze now streams, so `supports_stream_json() = True` routes haze through the existing `run_command_streaming()` path (`koan/app/provider/__init__.py:1021`) and the watchdog sees per-event `[cli]` lines. Carrying the bypass would violate constitution Principle IV (loop branching on provider behavior) for no benefit. This also resolves issue #2206's "detect timeout without killing the cli".
2. **Usage schema** — PR #2211 predates the pinned camelCase usage contract; Kōan's `_usage_snapshot_from_event()` (`provider/__init__.py:926`) and `token_parser.extract_tokens()` read snake_case only. Without translation, haze usage silently drops — forbidden by `specs/components/providers.md` ("partial implementations silently degrade usage tracking").
3. **Event vocabulary** — `_summarize_stream_event()` (`provider/__init__.py:713`) and `_extract_assistant_text_chunks()` (`:821`) know Claude/Codex/Copilot shapes; haze's `message_*`/`tool_*` events fall through to generic tags (no text captured, poor `/live` output). The terminal envelope, however, already parses: `_extract_result_text()` (`:872`) matches `type:"result"` + string `result`.
4. **Status semantics** — haze reports `status: complete|aborted|failed` where Claude uses `subtype: success|error_max_turns`. Exit-code mapping (0 ⇔ complete) means the existing non-zero-exit failure path fires correctly; the summarizer should render the status for operator visibility.
5. **What survives from PR #2211's `haze.py`**: the multi-backend `_HAZE_QUOTA_PATTERNS`/`_HAZE_AUTH_PATTERNS` regex sets, the stdout error-marker gating (`_line_has_error_marker` + exit-code guard so benign prose never false-positives), `invocation_lock_name()` = `haze-cli`, and the `check_quota_available()` "ok" probe skeleton. These are format-independent and were well-reviewed.

**Alternatives considered**: Rebase/extend PR #2211 — rejected by user decision and by the analysis above (its architectural core is obsolete; unwinding it inside the branch costs more than a fresh start). Keep `emits_incremental_progress()` as a general capability "for future silent CLIs" — rejected: YAGNI (constitution VII); no current provider needs it.

## R3. Prompt delivery (spec clarification #1)

**Decision**: `supports_stdin_prompt_passing() = True` with a haze-specific `rewrite_prompt_for_stdin()` override that **removes** `-p <prompt>` from argv entirely (haze reads stdin only when `-p` is absent), returning the extracted prompt for Kōan's temp-file→stdin piping.

**Rationale**: Kōan universally moves prompts from argv to stdin when the provider allows it (`cli_exec.prepare_prompt_file()` → `run_cli`/`popen_cli`, `koan/app/cli_exec.py:135-230`). Mission prompts embed the full system prompt (haze has no system-prompt flag, so it is prepended per the base fallback), routinely large; Linux caps a single argv entry at ~128KB (`MAX_ARG_STRLEN`). The base rewrite (replace the `-p` value with the `@stdin` marker) does NOT work for haze — haze would treat the marker as the literal prompt — hence the override that drops the flag, mirroring how `codex.py:116` overrides for its positional prompt (marker `-`). `build_prompt_args()` still returns `["-p", prompt]` so `build_command()` composes normally and non-stdin callers (probe) stay correct.

**Alternatives considered**: Always `-p` argv (PR #2211's choice, `supports_stdin_prompt_passing() = False`) — rejected in clarification: argv-size failure mode on large prompts. Threshold-based hybrid — rejected: two code paths for one job; stdin is already the universal Kōan mechanism.

**Live-validation addendum (2026-07-10)**: piped-stdin runs against a real haze install drop into the interactive Ink UI and crash — haze gates its stdin fallback on `process.stdin.isTTY === false`, and Node reports `undefined` (not `false`) for pipes/files, so the gate never fires (source identical on upstream main; affects all versions). Decision amended contract-first: the flag-removal rewrite ships implemented+tested but **dormant** (`supports_stdin_prompt_passing()` returns False with the bug documented); delivery uses `-p` argv until upstream fixes the gate.

## R4. Pre-flight quota/auth probe (spec clarification #2)

**Decision**: Cline-style minimal live probe: `haze --output json` with prompt `ok` piped per R3, classified through `detect_quota_exhaustion()`/`detect_auth_failure()`; any probe error/timeout returns available (never blocks work).

**Rationale**: Haze exposes no free usage introspection (no usage files, no status subcommand). `ClineProvider.check_quota_available()` (`koan/app/provider/cline.py:203`) is the established precedent for exactly this situation (multi-backend CLI, generic error patterns, "NOTE: consumes a small number of tokens"). `--output json` (not stream-json) keeps the probe output a single parseable line. `has_api_quota()` returns True (metered semantics — haze fronts metered API backends).

**Alternatives considered**: No probe — rejected in clarification (quota exhaustion discovered only mid-mission). Version probe (`haze --version`) as availability check — insufficient: proves install, not auth/quota.

## R5. Usage translation (two shared paths, shape-based)

**Decision**: Add camelCase usage recognition in exactly two places: (a) `_usage_snapshot_from_event()` in `provider/__init__.py` — accept `inputTokens`/`outputTokens`/`cacheReadTokens`/`cacheWriteTokens`/`reasoningTokens` alongside the snake_case branch, mapping `cacheReadTokens` → `cache_read_input_tokens`, `cacheWriteTokens` → `cache_creation_input_tokens`, and folding `reasoningTokens` into output accounting; (b) `token_parser.py` dict/JSONL extraction for the mission-stdout path (`usage_estimator.cmd_update` → `extract_tokens`). Both branches key on **field shape**, not provider name.

**Rationale**: Usage flows through two independent pipelines: skill-dispatch/streaming runs persist via the `KOAN_STREAM_USAGE_FILE` sidecar (`_persist_stream_usage_snapshot`, summed across calls), while plain mission runs parse the captured stdout file (`mission_runner.update_usage` → `token_parser`). Fixing only one silently degrades the other. Shape-based branches keep Principle IV intact (no provider-name leakage into shared parsers) and automatically benefit any future camelCase-reporting CLI. haze's usage is cumulative for the run (pinned envelope), matching the sidecar's "snapshot per provider call" accumulation model.

**Alternatives considered**: `get_session_data()` implementation reading haze's `~/.haze/logs` — rejected: logs exist only under `--debug`, and headless runs create no session files; the envelope is the documented, always-present source. Normalizing inside `HazeProvider` via a provider hook that rewrites events — rejected: no such hook exists; inventing one is a durable-contract change for something two small shape branches solve.

## R6. Stream event rendering & text extraction

**Decision**: Extend `_summarize_stream_event()` with haze's event vocabulary (shape-keyed): `turn_start`/`turn_end` (+status), `message_start`, `message_update` (elide or first-line preview — high frequency), `message_end` (text preview, skip `hidden`), `tool_start`/`tool_end` (name, success, durationMs, error), `retry` (attempt/maxAttempts/error), `context_overflow` (recovered flag). Extend `_extract_assistant_text_chunks()` to collect `message_end` text (non-hidden) keyed by `id` so mid-stream death still yields partial output; rely on the existing `_extract_result_text()` for the terminal envelope (already compatible). Treat `context_overflow` with `recovered: false` and `turn_end` with non-complete status as error-preview candidates for `_format_cli_error()` context.

**Rationale**: Every stdout line printed by the summarizer is the load-bearing liveness signal for run.py's watchdog (see the PR #1372 note inside `run_command_streaming`). `message_update` events carry cumulative text and fire per streaming chunk — summarize cheaply (type tag) rather than printing full text repeatedly. `message_end` is the stable per-segment text carrier (haze's own session recorder drops `message_update` for the same reason). Choosing `message_end` (not `message_update`) for fallback text avoids duplicating cumulative snapshots.

**Alternatives considered**: Leaving haze events to the generic fallbacks — rejected: `message_update`/`message_end` would render as bare `[cli] event: message_update` (no text, weak `/live` UX) and partial-death fallback would return empty. A haze-specific parser inside `haze.py` — rejected: stream parsing is centralized by design (`provider/__init__.py` owns it for all providers); shape-based extension is the established pattern (Codex/Copilot shapes live there today).

## R7. Failure classification & quota handling

**Decision**: Reuse PR #2211's backend-agnostic pattern sets: stderr trusted for full quota/auth regexes; stdout scanned only when exit ≠ 0 AND the line carries an error marker. `detect_quota_exhaustion()` feeds the existing quota pause; `detect_auth_failure()` feeds launch/auth fallback. No `quota_handler.py`/`reset_parser.py` changes: haze publishes no reset timestamps (multi-backend), so the default no-reset-time pause path applies as-is.

**Rationale**: Haze fronts OpenAI/OpenRouter/local backends, so provider-specific quota formats don't exist — generic patterns (429, insufficient_quota, billing, retry-after; 401, invalid api key) are the only honest signal, same rationale as cline. Existing `cli_errors.py` retryable/terminal classification already matches haze's error text styles (HTTP 5xx, timeouts) without modification — PR #2211's `cli_errors.py` additions were tied to its non-streaming architecture and are not needed.

**Alternatives considered**: Parsing haze `retry` events for quota state — rejected: retries are transient by definition; terminal state arrives via `turn_end`/envelope status. Adding haze reset-time parsing — nothing to parse; rejected.

## R8. Surfaces: registry, onboarding, docs, durable contract

**Decision**:
- Registry: import + `_PROVIDERS["haze"] = HazeProvider` in `provider/__init__.py` (single source of truth; `known_providers()`/`is_known_provider()`/dashboard forms/config validation derive automatically).
- Onboarding (`koan/app/onboarding.py`): add `"haze": "haze"` to the two provider→binary maps and the choice list `("haze", "haze (multi-backend agentic CLI)")` — the file enumerates providers in three literal structures that do not derive from the registry.
- Docs: new `docs/providers/haze.md` following the established structure (Quick Setup → Model Configuration → Tool Configuration → Advanced → Troubleshooting), linked from `docs/providers/index.md`; provider mention in `docs/users/user-manual.md`; `/brain sync` for wiki bookkeeping.
- Durable contract: `specs/components/providers.md` gains the haze row/registry entry and any invariant notes — **contract-first, declared** ("Architectural change" box checked; ideally the spec delta is reviewable as its own commit at the top of the branch), per constitution Principle II and the spec-change guard.

**Rationale**: Everything except onboarding derives from `_PROVIDERS`; onboarding's literal maps are a known pattern (PR #2211 touched the same three spots). The providers.md change protocol (`specs/components/providers.md:156-163`) explicitly requires: subclass, register, tool-name translation, usage extraction defined, spec updated, doc added, usage verified against a recorded sample.

**Alternatives considered**: Deriving onboarding lists from the registry — attractive but scope creep for this feature; note as future cleanup, don't do it here (spec's "no scope creep" gate).

## R9. Testing strategy

**Decision**: New `koan/tests/test_haze_provider.py` modeled on `test_codex_provider.py`: command construction (stdin-mode argv has no `-p`; probe argv does), `rewrite_prompt_for_stdin` edge cases, output/model/unsupported-feature flag builders (warn-and-skip via `log_safe`), quota/auth detection tables (positive + benign-prose negatives + exit-0 gating), probe behavior (mock `subprocess.run`; timeout → available). Extend: `test_provider_modules.py` (registry membership, `is_known_provider("haze")`), `test_cli_provider.py` (resolution), `test_token_parser.py` + a stream-usage test with recorded camelCase fixtures, and a `run_command_streaming` fixture test replaying a recorded haze NDJSON transcript end-to-end (summaries printed, result text extracted, sidecar usage written). Fixtures are **recorded samples** of real haze output (checked-in strings), satisfying the providers.md "verify usage extraction against a recorded sample" protocol; no live `haze` invocation ever (constitution Testing discipline).

**Rationale**: Mirrors how every existing provider is tested; the recorded-transcript replay is the highest-value test because it exercises the exact drift points (event shapes, camelCase usage, status mapping) through the real shared-parser code path.

**Alternatives considered**: Reusing PR #2211's `test_haze_provider.py` wholesale — partially; its detection-table tests carry over, its `build_command`/output-format assertions do not (different architecture).
