# Feature Specification: Haze CLI Provider (stream-json compatible)

**Feature Branch**: `006-haze-cli-provider`

**Created**: 2026-07-10

**Status**: Draft

**Input**: User description: "I want to provide a full cli implementation for haze harness. There is already a pending PR https://github.com/Anantys-oss/koan/pull/2211 that could be used as a starting plan. But it's incomplete and does not match the recent format update from haze to support stream json format. The PR from https://github.com/DenizOkcu/haze/pull/9 was since updated on haze main branch. Look at documentation and what looks like the haze format from https://github.com/DenizOkcu/haze, then understand how it drifts from the current implementation, and let's start a fresh implementation fully compatible with haze harness. We should close the legacy PR 2211 and link it to our new PR."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run missions through the haze harness (Priority: P1)

An operator configures Kōan to use haze as their CLI provider (globally via `cli_provider: haze` / `KOAN_CLI_PROVIDER=haze`, per-role via the `cli:` section, or per-project) and Kōan executes missions end-to-end through haze exactly as it does with any other provider. Because haze now streams progress events live (haze ≥ 0.7.0 stream output), Kōan's existing liveness and stagnation monitoring observes real activity during the run — a long but healthy haze run is never mistaken for a hung one and killed (the original concern of issue #2206), and a genuinely stuck run is still detected and stopped.

**Why this priority**: This is the core value — without mission execution through haze, nothing else matters. It replaces the drifted approach of legacy PR #2211, which predates haze's streaming output and carried an invasive "no incremental progress" workaround layer through the agent loop.

**Independent Test**: Configure haze as the provider, queue a simple mission, and observe: the run produces live per-event progress lines, completes, and the mission is marked Done with the final assistant text captured.

**Acceptance Scenarios**:

1. **Given** haze is installed and configured as the active provider, **When** a mission runs, **Then** the agent loop invokes haze in one-shot headless mode with streaming output and the mission completes with the final result text recorded.
2. **Given** a haze mission is running, **When** haze emits progress events (turn start, message updates, tool activity), **Then** each event surfaces as a short human-readable progress line so the liveness watchdog sees continuous activity.
3. **Given** a haze run that stalls with no stream activity beyond the stagnation thresholds, **When** the stagnation monitor evaluates it, **Then** the run is treated exactly like any other provider's stalled run (killed and requeued per existing policy).
4. **Given** a model override is configured for a mission, **When** the haze command is built, **Then** the override is passed per-run without mutating haze's persistent settings.

---

### User Story 2 - Accurate accounting and failure classification (Priority: P2)

An operator running missions through haze gets truthful usage tracking and failure reporting: token usage reported by haze is recorded into Kōan's usage accounting, and failed or aborted runs are classified correctly (quota exhaustion, authentication failure, or generic failure) — never silently reported as success and never silently dropping usage data.

**Why this priority**: Kōan's autonomous mode decisions and pause behavior depend on usage and quota signals. The providers contract explicitly forbids partial integrations that silently degrade usage tracking. Haze reports usage in its own field naming (camelCase token fields) and its own terminal status vocabulary (`complete` / `aborted` / `failed`), which the current accounting does not understand.

**Independent Test**: Replay recorded haze output samples (success, failure, quota-style error, auth-style error) through the provider and assert the recorded usage totals and the resulting classification.

**Acceptance Scenarios**:

1. **Given** a completed haze run reporting token usage, **When** post-mission accounting runs, **Then** the recorded usage reflects haze's reported input/output/cache/reasoning token counts (all reported fields accounted for, none silently dropped).
2. **Given** a haze run that terminates with status `failed` or `aborted` (non-zero exit), **When** the run is finalized, **Then** the mission is treated as failed — never as a success — and the operator-visible outcome names the terminal status.
3. **Given** haze output containing a rate-limit/quota-exhaustion error from its underlying model backend, **When** the failure is classified, **Then** Kōan detects quota exhaustion and pauses per its standard quota policy.
4. **Given** haze output containing an authentication error (invalid/missing key), **When** the failure is classified, **Then** Kōan reports an auth failure (and applies its standard launch/auth fallback policy) rather than a quota pause.
5. **Given** haze reports no usable usage data for a run, **When** accounting runs, **Then** the absence is handled gracefully (no crash, no fabricated numbers).

---

### User Story 3 - Operator onboarding, visibility, and documentation (Priority: P3)

An operator discovering or adopting haze finds it treated as a first-class provider: it is offered/validated wherever providers are listed (onboarding, status surfaces, dashboard provider selection, configuration validation), and a dedicated provider document explains setup (install, configure providers/models inside haze, point Kōan at it), capabilities, limitations, and troubleshooting.

**Why this priority**: A provider that works but is invisible or undocumented does not get adopted. This story makes the integration complete rather than merely functional.

**Independent Test**: List known providers via existing surfaces (validation, status, docs index) and confirm haze appears with accurate capability notes; follow the new provider doc from scratch to a working configuration.

**Acceptance Scenarios**:

1. **Given** a fresh configuration, **When** the operator sets the provider to `haze`, **Then** configuration validation accepts it as a known provider and status surfaces display it.
2. **Given** the documentation set, **When** the operator looks up haze, **Then** a provider page exists (linked from the providers index) covering setup, model configuration, capabilities/limitations, and troubleshooting, following the same structure as other provider docs.
3. **Given** features haze does not support in headless mode (per-tool restriction, fallback model, session resume, external tool-server configuration, max-turns), **When** an operator has these configured, **Then** each is loudly skipped with a warning — never a crash, never silent acceptance.

---

### Edge Cases

- Haze binary not installed / not on PATH → provider reports unavailable; standard launch-failure fallback applies.
- Haze has no model provider configured (its setup is interactive-only) → precise error surfaces to the operator with non-zero exit; classified as a launch/configuration failure, not quota.
- A stream line that is not valid JSON → skipped without aborting the run (consistent with existing stream handling).
- Run killed mid-stream (timeout, signal) → partial assistant text accumulated from streamed message events is surfaced instead of an empty result.
- Terminal status `aborted` (interrupt) → treated as failure, distinctly labeled.
- Older haze without stream output support (< 0.7.0) → out of scope; documentation states the minimum supported haze version and the failure mode is a normal CLI error.
- Prompts containing content that could be misparsed as flags → prompt passing remains safe (prompt delivered via the documented prompt flag as the final argument).
- Unsupported Kōan features (tool allow/deny lists, MCP configs, plugin dirs, fallback model, max turns, session resume, reasoning-effort control) → warn-and-skip, per acceptance scenario 3.3.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Operators MUST be able to select haze as a CLI provider by the name `haze` through every existing provider-selection mechanism (global config, environment variable, per-role `cli:` section, per-project override), with the same resolution and fallback semantics as other providers.
- **FR-002**: The system MUST invoke haze in one-shot headless mode: prompt passed via haze's prompt flag, optional per-run model override, streaming JSON output requested, and the system prompt merged into the prompt text (haze has no separate system-prompt input).
- **FR-003**: The system MUST consume haze's newline-delimited streaming events during the run and render each as a concise human-readable progress line, so existing liveness/stagnation monitoring functions without any provider-specific bypass in the agent loop.
- **FR-004**: The system MUST capture the final assistant text from haze's terminal result envelope, and MUST fall back to text accumulated from streamed message events when the run dies before the terminal envelope.
- **FR-005**: The system MUST record token usage from haze's reported usage fields (input, output, cache read, cache write, reasoning) into Kōan's usage accounting, translating haze's field naming so no reported quantity is silently dropped.
- **FR-006**: The system MUST map haze's terminal status to mission outcome: `complete` (exit 0) is success; `failed` and `aborted` (non-zero exit) are failures with the status visible in the reported outcome.
- **FR-007**: The system MUST detect quota/rate-limit exhaustion and authentication failures from haze output using backend-agnostic patterns (haze fronts multiple model backends), feeding Kōan's standard quota-pause and auth-fallback policies; detection MUST NOT false-positive on benign assistant prose in successful runs.
- **FR-008**: For capabilities haze does not support in headless mode (per-tool allow/deny, external tool-server configs, plugin dirs, max turns, fallback model, session resume, reasoning-effort control), the system MUST warn and skip — never crash, never silently pretend support.
- **FR-009**: The provider MUST report availability (binary present) and support a best-effort pre-flight quota probe consistent with other providers, where probe errors never block real work.
- **FR-010**: The integration MUST include automated tests covering command construction, stream event handling, result/usage extraction, status mapping, and quota/auth detection — using recorded haze output samples, never a live haze invocation.
- **FR-011**: Documentation MUST be updated: a haze provider page (setup, models, capabilities, limitations, minimum haze version, troubleshooting) linked from the providers index, plus the user-facing surfaces that enumerate providers; the durable providers component contract MUST be updated contract-first as part of the implementation change, declared as architectural per the specs discipline.
- **FR-012**: Process requirement: when the replacement pull request is opened, legacy PR #2211 MUST be closed with a comment linking to the new PR (and the new PR must reference issue #2206), so reviewers can trace the lineage and the drift rationale.

### Key Entities

- **Haze provider**: the integration unit selectable by name `haze`; owns command construction, capability declarations, and failure classification for the haze harness.
- **Stream event**: one newline-delimited JSON object emitted live by haze (turn lifecycle, message lifecycle, tool lifecycle, retry, context-overflow), each timestamped; drives progress display and liveness.
- **Result envelope**: haze's terminal output line carrying `status` (`complete`/`aborted`/`failed`), the final `result` text, and the pinned `usage` token counts; identical shape whether streaming or single-envelope output is used.
- **Usage snapshot**: the normalized token-usage record Kōan persists for accounting, translated from haze's usage field vocabulary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With haze configured, a queued mission runs to completion with live progress visible during the run and the final text recorded — with zero provider-specific branches added to the agent loop's monitoring path.
- **SC-002**: For a completed run, the usage recorded by Kōan matches the totals haze reported for that run (100% of reported fields accounted for).
- **SC-003**: Replayed quota-exhaustion output pauses Kōan under the same policy as existing providers; replayed auth-failure output triggers auth handling, not a quota pause; a successful run with prose mentioning "rate limit" does not trigger either.
- **SC-004**: `failed` and `aborted` runs are reported as failures 100% of the time (no false success), with the terminal status visible to the operator.
- **SC-005**: The full existing test suite passes unchanged (zero regressions), and the new provider's behaviors are covered by tests runnable without haze installed.
- **SC-006**: An operator can go from "haze installed and configured" to "first mission executed through haze" using only the new documentation page.
- **SC-007**: Legacy PR #2211 is closed with a cross-link to the replacement PR, and the replacement PR references issue #2206.

## Assumptions

- Minimum supported haze version is 0.7.0 (first release with streaming JSON output); current is 0.8.0. Older haze versions are out of scope.
- Haze's model/provider credentials and model lists are configured out-of-band by the operator inside haze itself (interactive settings; haze deliberately reads no environment variables). Kōan only selects a per-run model override when configured.
- Headless haze is one-shot and never creates durable sessions, so session resume is out of scope by design, not omission.
- Kōan's existing streaming-output plumbing is the integration surface; the legacy PR's "no incremental progress" workaround layer (capability flag + agent-loop bypasses) is intentionally not carried over.
- Haze exposes its own fixed toolset with no per-tool CLI control; Kōan's tool restriction configuration therefore cannot apply and is handled per FR-008.
- The durable providers component contract update happens contract-first within the implementation change (declared architectural), not in this ephemeral speckit artifact.
